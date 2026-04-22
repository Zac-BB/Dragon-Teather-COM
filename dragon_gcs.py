"""
DRAGON GCS — Ground Control Station
ROV telemetry display, camera feed, controller input, and data logging.

Protocol (from Pi → PC, newline-delimited JSON):
  {"type": "telemetry", "depth": 12.3, "pressure": 1.2, "temp": 18.5, "heading": 270, "roll": 2.1, "pitch": -1.4, "battery": 87}
  {"type": "image", "data": "<base64-encoded JPEG>"}
  {"type": "status", "message": "Motors armed"}

Protocol (PC → Pi, newline-delimited JSON):
  {"type": "control", "throttle": 0.5, "yaw": -0.2, "surge": 0.8, "sway": 0.0, "ascend": 0.0, "lights": true}
"""

import pygame
import pygame.font
import socket
import json
import threading
import time
import base64
import io
import os
import math
import struct
from datetime import datetime
from PIL import Image
import sys


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

WINDOW_W = 1400
WINDOW_H = 900
FPS = 60
TCP_HOST = "192.168.208.10"
TCP_PORT = 5000
LOG_DIR = os.path.expanduser("~/dragon_logs")

# Colors — deep-sea industrial palette
C_BG        = (8,  14,  22)
C_PANEL     = (12, 22,  36)
C_BORDER    = (0,  180, 220)
C_BORDER2   = (0,  80,  110)
C_TEXT      = (200, 230, 240)
C_TEXT_DIM  = (80,  120, 140)
C_ACCENT    = (0,   200, 180)
C_WARN      = (255, 180,  30)
C_DANGER    = (255,  60,  60)
C_GREEN     = (40,  220, 100)
C_OVERLAY   = (8,   14,  22, 200)   # RGBA for overlay surfaces

JOYSTICK_DEADZONE = 0.08


# ─────────────────────────────────────────────────────────────────────────────
# LOGGER
# ─────────────────────────────────────────────────────────────────────────────

class DataLogger:
    def __init__(self):
        os.makedirs(LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.path = os.path.join(LOG_DIR, f"dragon_{ts}.jsonl")
        self._lock = threading.Lock()
        self._file = open(self.path, "w", buffering=1)
        self.log_event("session_start", {"gcs_version": "1.0"})

    def log_event(self, event_type: str, data: dict):
        entry = {"ts": datetime.utcnow().isoformat(), "event": event_type, **data}
        line = json.dumps(entry)
        with self._lock:
            self._file.write(line + "\n")

    def close(self):
        self.log_event("session_end", {})
        self._file.close()


# ─────────────────────────────────────────────────────────────────────────────
# TCP MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class TCPManager:
    def __init__(self, logger: DataLogger):
        self.logger = logger
        self.host = None
        self.port = None
        self.sock = None
        self.connected = False
        self._lock = threading.Lock()
        self._rx_thread = None
        self._running = False
        self.on_telemetry = None   # callback(dict)
        self.on_image = None       # callback(bytes)
        self.on_status = None      # callback(str)
        self._partial = ""

    def connect(self, host: str, port: int):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(5.0)
            self.sock.connect((host, port))
            self.sock.settimeout(None)
            self.host = host
            self.port = port
            self.connected = True
            self._running = True
            self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
            self._rx_thread.start()
            self.logger.log_event("tcp_connect", {"host": host, "port": port})
            return True
        except Exception as e:
            self.logger.log_event("tcp_error", {"error": str(e)})
            return False

    def disconnect(self):
        self._running = False
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.logger.log_event("tcp_disconnect", {})

    def send(self, data: dict):
        if not self.connected:
            return
        try:
            line = json.dumps(data) + "\n"
            with self._lock:
                self.sock.sendall(line.encode())
        except Exception as e:
            self.logger.log_event("tcp_tx_error", {"error": str(e)})
            self.connected = False

    def _rx_loop(self):
        self.sock.settimeout(0.1)
        while self._running:
            try:
                raw = self.sock.recv(4096).decode("utf-8", errors="replace")
                if not raw:
                    self.connected = False
                    break
                self._partial += raw
                while "\n" in self._partial:
                    line, self._partial = self._partial.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        self._dispatch(msg)
                    except json.JSONDecodeError:
                        pass
            except socket.timeout:
                continue
            except Exception:
                time.sleep(0.05)

    def _dispatch(self, msg: dict):
        t = msg.get("type")
        if t == "telemetry":
            self.logger.log_event("telemetry", msg)
            if self.on_telemetry:
                self.on_telemetry(msg)
        elif t == "image":
            try:
                img_bytes = base64.b64decode(msg["data"])
                self.logger.log_event("image_received", {"size_bytes": len(img_bytes)})
                if self.on_image:
                    self.on_image(img_bytes)
            except Exception:
                pass
        elif t == "status":
            self.logger.log_event("status", {"message": msg.get("message", "")})
            if self.on_status:
                self.on_status(msg.get("message", ""))


# ─────────────────────────────────────────────────────────────────────────────
# CONTROLLER INPUT
# ─────────────────────────────────────────────────────────────────────────────

class ControllerInput:
    def __init__(self):
        pygame.joystick.init()
        self.joystick = None
        self.axes = {}
        self.buttons = {}
        self.lights_on = False
        self._init_joystick()

    def _init_joystick(self):
        count = pygame.joystick.get_count()
        if count > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()

    def refresh(self):
        if self.joystick is None:
            self._init_joystick()
            return None
        pygame.event.pump()
        axes = {}
        for i in range(self.joystick.get_numaxes()):
            v = self.joystick.get_axis(i)
            axes[i] = v if abs(v) > JOYSTICK_DEADZONE else 0.0
        buttons = {}
        for i in range(self.joystick.get_numbuttons()):
            buttons[i] = self.joystick.get_button(i)

        # Toggle lights on button 3
        if buttons.get(3) and not self.buttons.get(3):
            self.lights_on = not self.lights_on

        self.axes = axes
        self.buttons = buttons

        # Map axes to ROV axes (customize per your controller)
        return {
            "type":    "control",
            "up/down":   round(-axes.get(1, 0.0), 3),   # left stick Y → forward/back
            "left/right":    round( axes.get(0, 0.0), 3),   # left stick X → strafe
            "throttle":  round(-axes.get(3, 0.0), 3),   # right stick Y → up/down
            # "yaw":     round( axes.get(2, 0.0), 3),   # right stick X → yaw
            # "throttle":round( max(0, -axes.get(5, -1.0)) / 2 + 0.5, 3),  # L2 trigger
            # "lights":  self.lights_on,
        }

    @property
    def connected(self):
        return self.joystick is not None


# ─────────────────────────────────────────────────────────────────────────────
# OVERLAY SYSTEM — add new overlays here easily
# ─────────────────────────────────────────────────────────────────────────────

class Overlay:
    """Base class. Override draw(surface, state)."""
    def __init__(self, rect: pygame.Rect, visible=True):
        self.rect = rect
        self.visible = visible

    def draw(self, surface, state: dict):
        pass


class TelemetryOverlay(Overlay):
    """Depth, pressure, temperature, battery readout."""
    def __init__(self, fonts, rect):
        super().__init__(rect)
        self.fonts = fonts

    def draw(self, surface, state):
        if not self.visible:
            return
        tele = state.get("telemetry", {})
        panel = pygame.Surface((self.rect.w, self.rect.h), pygame.SRCALPHA)
        panel.fill((8, 14, 22, 210))
        pygame.draw.rect(panel, C_BORDER2, panel.get_rect(), 1)

        rows = [
            ("DEPTH",    f"{tele.get('depth',    0.0):6.1f} m",   C_ACCENT),
            ("PRESSURE", f"{tele.get('pressure', 0.0):6.2f} bar", C_TEXT),
            ("TEMP",     f"{tele.get('temp',     0.0):6.1f} °C",  C_TEXT),
            ("HEADING",  f"{tele.get('heading',  0.0):6.1f}°",    C_TEXT),
            ("ROLL",     f"{tele.get('roll',     0.0):+6.1f}°",   C_TEXT),
            ("PITCH",    f"{tele.get('pitch',    0.0):+6.1f}°",   C_TEXT),
        ]

        batt = tele.get("battery", None)
        if batt is not None:
            col = C_GREEN if batt > 50 else (C_WARN if batt > 20 else C_DANGER)
            rows.append(("BATTERY", f"{batt:5.0f}%", col))

        y = 10
        lbl_f = self.fonts["small"]
        val_f = self.fonts["mono"]
        for label, value, color in rows:
            lbl_surf = lbl_f.render(label, True, C_TEXT_DIM)
            val_surf = val_f.render(value, True, color)
            panel.blit(lbl_surf, (10, y))
            panel.blit(val_surf, (self.rect.w - val_surf.get_width() - 10, y))
            y += 26
            if y + 26 > self.rect.h:
                break

        surface.blit(panel, self.rect.topleft)


class ArtificialHorizonOverlay(Overlay):
    """Simple artificial horizon ball."""
    def __init__(self, fonts, center, radius):
        r = pygame.Rect(center[0]-radius-2, center[1]-radius-2, radius*2+4, radius*2+4)
        super().__init__(r)
        self.center = center
        self.radius = radius
        self.fonts = fonts

    def draw(self, surface, state):
        if not self.visible:
            return
        tele = state.get("telemetry", {})
        roll = math.radians(tele.get("roll", 0.0))
        pitch = tele.get("pitch", 0.0)

        cx, cy = self.center
        r = self.radius

        # Clip to circle
        clip_surf = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        pygame.draw.circle(clip_surf, (0,80,160,220), (r, r), r)

        # Horizon line offset by pitch
        pitch_offset = int(pitch * r / 45)
        horizon_y = r + pitch_offset

        # Sky / water fill
        sky_rect = pygame.Rect(0, 0, r*2, max(0, horizon_y))
        water_rect = pygame.Rect(0, min(r*2, horizon_y), r*2, r*2 - min(r*2, horizon_y))

        sky_surf = pygame.Surface((r*2, r*2), pygame.SRCALPHA)
        if sky_rect.height > 0:
            pygame.draw.rect(sky_surf, (0, 60, 120, 200), sky_rect)
        if water_rect.height > 0:
            pygame.draw.rect(sky_surf, (0, 150, 80, 200), water_rect)

        # Rotate by roll
        rotated = pygame.transform.rotate(sky_surf, math.degrees(roll))
        rr = rotated.get_rect(center=(r, r))
        clip_surf.blit(rotated, rr.topleft, special_flags=pygame.BLEND_RGBA_MIN)

        # Horizon line
        line_len = r
        cos_r, sin_r = math.cos(roll), math.sin(roll)
        hx1 = int(r - cos_r * line_len + sin_r * pitch_offset)
        hy1 = int(r - sin_r * line_len - cos_r * pitch_offset)
        hx2 = int(r + cos_r * line_len + sin_r * pitch_offset)
        hy2 = int(r + sin_r * line_len - cos_r * pitch_offset)
        pygame.draw.line(clip_surf, C_WARN, (hx1, hy1), (hx2, hy2), 2)

        # Center crosshair
        pygame.draw.line(clip_surf, (255,255,255,200), (r-12, r), (r-4, r), 2)
        pygame.draw.line(clip_surf, (255,255,255,200), (r+4, r), (r+12, r), 2)

        surface.blit(clip_surf, (cx-r, cy-r))
        pygame.draw.circle(surface, C_BORDER, (cx, cy), r, 2)

        lbl = self.fonts["tiny"].render("AH", True, C_TEXT_DIM)
        surface.blit(lbl, (cx - lbl.get_width()//2, cy + r + 4))


class ControllerOverlay(Overlay):
    """Shows live controller axis values."""
    def __init__(self, fonts, rect):
        super().__init__(rect)
        self.fonts = fonts

    def draw(self, surface, state):
        if not self.visible:
            return
        ctrl = state.get("control", {})
        panel = pygame.Surface((self.rect.w, self.rect.h), pygame.SRCALPHA)
        panel.fill((8, 14, 22, 200))
        pygame.draw.rect(panel, C_BORDER2, panel.get_rect(), 1)

        title = self.fonts["small"].render("CONTROLS", True, C_TEXT_DIM)
        panel.blit(title, (10, 6))

        rows = [
            ("Up/Down",   ctrl.get("up/down",  0)),
            ("L/R",    ctrl.get("left/right",   0)),
            ("Throttle",  ctrl.get("throttle", 0)),

        ]
        y = 28
        for label, val in rows:
            lbl = self.fonts["tiny"].render(label, True, C_TEXT_DIM)
            panel.blit(lbl, (10, y))
            bar_x, bar_y, bar_w, bar_h = 70, y+2, 80, 12
            pygame.draw.rect(panel, (20,40,60), (bar_x, bar_y, bar_w, bar_h))
            filled = int((val + 1) / 2 * bar_w)
            col = C_ACCENT if abs(val) < 0.5 else C_WARN
            pygame.draw.rect(panel, col, (bar_x, bar_y, filled, bar_h))
            pygame.draw.rect(panel, C_BORDER2, (bar_x, bar_y, bar_w, bar_h), 1)
            y += 22
            if y + 22 > self.rect.h:
                break

        surface.blit(panel, self.rect.topleft)


class StatusBarOverlay(Overlay):
    """Bottom status bar with connection info, log path, timestamps."""
    def __init__(self, fonts, rect):
        super().__init__(rect)
        self.fonts = fonts
        self.messages = []   # list of (timestamp_str, message_str)

    def add_message(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.messages.append(f"[{ts}] {msg}")
        if len(self.messages) > 5:
            self.messages.pop(0)

    def draw(self, surface, state):
        panel = pygame.Surface((self.rect.w, self.rect.h), pygame.SRCALPHA)
        panel.fill((6, 10, 18, 230))
        pygame.draw.rect(panel, C_BORDER2, (0, 0, self.rect.w, 1))

        x = 10
        # TCP status
        tcp_ok = state.get("tcp_connected", False)
        col = C_GREEN if tcp_ok else C_DANGER
        dot = self.fonts["small"].render("●", True, col)
        panel.blit(dot, (x, 6))
        x += 16
        addr = state.get("tcp_addr", "---")
        txt = self.fonts["small"].render(f"TCP {addr}", True, C_TEXT)
        panel.blit(txt, (x, 6))
        x += txt.get_width() + 20

        # Controller status
        ctrl_ok = state.get("controller_connected", False)
        col2 = C_GREEN if ctrl_ok else C_TEXT_DIM
        dot2 = self.fonts["small"].render("●", True, col2)
        panel.blit(dot2, (x, 6))
        x += 16
        txt2 = self.fonts["small"].render("CONTROLLER", True, C_TEXT)
        panel.blit(txt2, (x, 6))
        x += txt2.get_width() + 20

        # Log path
        log = state.get("log_path", "")
        ltxt = self.fonts["tiny"].render(f"LOG: {log}", True, C_TEXT_DIM)
        panel.blit(ltxt, (x, 8))

        # Clock
        now = datetime.now().strftime("%H:%M:%S UTC")
        ct = self.fonts["small"].render(now, True, C_ACCENT)
        panel.blit(ct, (self.rect.w - ct.get_width() - 10, 6))

        # Messages
        if self.messages:
            msg_surf = self.fonts["tiny"].render(self.messages[-1], True, C_TEXT_DIM)
            panel.blit(msg_surf, (10, self.rect.h // 2 + 2))

        surface.blit(panel, self.rect.topleft)


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTION SELECTOR DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class ConnectionSelector:
    def __init__(self, screen, fonts):
        self.screen = screen
        self.fonts = fonts
        self.options = [f"{TCP_HOST}:{TCP_PORT}", "DEMO MODE"]
        self.selected = 0
        self.done = False
        self.choice = None

    def handle_event(self, ev):
        if ev.type == pygame.KEYDOWN:
            if ev.key == pygame.K_UP:
                self.selected = max(0, self.selected - 1)
            elif ev.key == pygame.K_DOWN:
                self.selected = min(len(self.options)-1, self.selected+1)
            elif ev.key == pygame.K_RETURN:
                self.choice = self.options[self.selected]
                self.done = True
            elif ev.key == pygame.K_ESCAPE:
                self.done = True
        if ev.type == pygame.MOUSEBUTTONDOWN:
            mx, my = ev.pos
            for i, opt in enumerate(self.options):
                ry = 220 + i * 44
                if ry <= my <= ry + 36:
                    self.selected = i
                    self.choice = opt
                    self.done = True

    def draw(self):
        w, h = self.screen.get_size()
        self.screen.fill(C_BG)
        title = self.fonts["title"].render("DRAGON GCS", True, C_ACCENT)
        sub = self.fonts["body"].render("Select connection", True, C_TEXT_DIM)
        self.screen.blit(title, (w//2 - title.get_width()//2, 80))
        self.screen.blit(sub, (w//2 - sub.get_width()//2, 140))

        for i, opt in enumerate(self.options):
            ry = 220 + i * 44
            col = C_ACCENT if i == self.selected else C_PANEL
            pygame.draw.rect(self.screen, col, (w//2-200, ry, 400, 36), border_radius=4)
            pygame.draw.rect(self.screen, C_BORDER2, (w//2-200, ry, 400, 36), 1, border_radius=4)
            txt = self.fonts["body"].render(opt, True, C_BG if i == self.selected else C_TEXT)
            self.screen.blit(txt, (w//2 - txt.get_width()//2, ry + 8))

        hint = self.fonts["tiny"].render("↑↓ to navigate · ENTER to connect · ESC to quit", True, C_TEXT_DIM)
        self.screen.blit(hint, (w//2 - hint.get_width()//2, h - 60))
        pygame.display.flip()


# ─────────────────────────────────────────────────────────────────────────────
# DEMO DATA GENERATOR (no hardware needed for testing)
# ─────────────────────────────────────────────────────────────────────────────

class DemoGenerator:
    def __init__(self, on_telemetry, on_image, on_status):
        self.on_telemetry = on_telemetry
        self.on_image = on_image
        self.on_status = on_status
        self._running = False
        self._t = 0

    def start(self):
        self._running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            self._t += 0.05
            t = self._t
            tele = {
                "type": "telemetry",
                "depth":    max(0, 12.5 + 2*math.sin(t*0.3)),
                "pressure": 2.23 + 0.1*math.sin(t*0.3),
                "temp":     18.2 + 0.5*math.sin(t*0.1),
                "heading":  (180 + 30*math.sin(t*0.1)) % 360,
                "roll":     3*math.sin(t*0.7),
                "pitch":    -2*math.cos(t*0.5),
                "battery":  max(0, 85 - t*0.1),
            }
            if self.on_telemetry:
                self.on_telemetry(tele)

            # Send a synthetic image every 10 iterations
            if int(t*20) % 1 == 0:
                img = self._make_demo_image(t)
                if self.on_image:
                    self.on_image(img)

            time.sleep(0.05)

    def _make_demo_image(self, t):
        """Generate a simple demo image using PIL."""
        w, h = 640, 480
        img = Image.new("RGB", (w, h), (0, 20, 40))
        # Simple gradient / ripple pattern
        pixels = img.load()
        for y in range(h):
            for x in range(0, w, 4):
                v = int(127 + 80*math.sin(x*0.03 + t) * math.cos(y*0.03 - t*0.5))
                pixels[x, y] = (0, v//3, v//2)
                if x+1 < w: pixels[x+1, y] = pixels[x, y]
                if x+2 < w: pixels[x+2, y] = pixels[x, y]
                if x+3 < w: pixels[x+3, y] = pixels[x, y]
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        return buf.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

class DragonGCS:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("DRAGON GCS")
        self.screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), pygame.RESIZABLE)
        self.clock = pygame.time.Clock()

        self.fonts = self._load_fonts()
        self.logger = DataLogger()
        self.tcp = TCPManager(self.logger)
        self.controller = ControllerInput()
        self.demo = None

        # State
        self.state = {
            "telemetry": {},
            "control": {},
            "tcp_connected": False,
            "tcp_addr": "---",
            "controller_connected": False,
            "log_path": self.logger.path,
        }

        # Camera feed
        self._camera_lock = threading.Lock()
        self._camera_surface = None
        self._last_image_ts = None

        # Status messages
        self._status_msgs = []

        # Wire TCP callbacks
        self.tcp.on_telemetry = self._on_telemetry
        self.tcp.on_image = self._on_image
        self.tcp.on_status = self._on_status_msg

        # Build overlays
        self.overlays = self._build_overlays()

        # Control send rate
        self._last_ctrl_send = 0
        self._ctrl_send_interval = 0.05  # 20 Hz

        # Keyboard control state
        self._keys = set()

    def _load_fonts(self):
        try:
            mono = "Courier New"
            body = None  # will use default
            fonts = {
                "title": pygame.font.SysFont("Arial", 36, bold=True),
                "body":  pygame.font.SysFont(body, 18),
                "small": pygame.font.SysFont(body, 14),
                "tiny":  pygame.font.SysFont(mono, 12),
                "mono":  pygame.font.SysFont(mono, 14),
                "large": pygame.font.SysFont(mono, 22, bold=True),
                "hud":   pygame.font.SysFont(mono, 28, bold=True),
            }
        except Exception:
            f = pygame.font.Font(None, 18)
            fonts = {k: f for k in ["title","body","small","tiny","mono","large","hud"]}
        return fonts

    def _build_overlays(self):
        W, H = WINDOW_W, WINDOW_H
        telemetry = TelemetryOverlay(self.fonts, pygame.Rect(10, 10, 200, 200))
        horizon   = ArtificialHorizonOverlay(self.fonts, (W - 80, 80), 55)
        controller= ControllerOverlay(self.fonts, pygame.Rect(10, H - 190, 180, 150))
        statusbar = StatusBarOverlay(self.fonts, pygame.Rect(0, H - 36, W, 36))
        self._statusbar = statusbar  # keep ref for adding messages
        return [telemetry, horizon, controller, statusbar]

    # ── Callbacks ────────────────────────────────────────────────────────────

    def _on_telemetry(self, data: dict):
        self.state["telemetry"] = data

    def _on_image(self, img_bytes: bytes):
        try:
            import numpy as np
            import cv2

            np_arr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if frame is None:
                return

            W, H = self.screen.get_size()
            target_w = W
            target_h = H - 36

            h, w = frame.shape[:2]

            scale = min(target_w / w, target_h / h)
            nw = int(w * scale)
            nh = int(h * scale)

            frame = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            surf = pygame.image.frombuffer(frame.tobytes(), (nw, nh), "RGB")

            with self._camera_lock:
                self._camera_surface = surf
                self._last_image_ts = time.time()

        except Exception:
            print("Failed to decode image data", file=sys.stderr)

    def _on_status_msg(self, msg: str):
        self._statusbar.add_message(msg)

    # ── Connection selector ───────────────────────────────────────────────────

    def _run_connection_selector(self):
        selector = ConnectionSelector(self.screen, self.fonts)
        while not selector.done:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    return None
                selector.handle_event(ev)
            selector.draw()
            self.clock.tick(30)
        return selector.choice

    # ── Connect ───────────────────────────────────────────────────────────────

    def _connect(self, choice: str):
        if choice == "DEMO MODE":
            self.demo = DemoGenerator(self._on_telemetry, self._on_image, self._on_status_msg)
            self.demo.start()
            self.state["tcp_connected"] = True
            self.state["tcp_addr"] = "DEMO"
            self._statusbar.add_message("Demo mode active — no hardware required")
        else:
            host, port = TCP_HOST, TCP_PORT
            ok = self.tcp.connect(host, port)
            self.state["tcp_connected"] = ok
            self.state["tcp_addr"] = f"{host}:{port}" if ok else "ERR"
            if ok:
                self._statusbar.add_message(f"Connected to {host}:{port}")
            else:
                self._statusbar.add_message(f"Failed to connect to {host}:{port}")

    # ── Control send ─────────────────────────────────────────────────────────

    def _keyboard_axes(self):
        ud  = (1.0 if pygame.K_w in self._keys else 0.0) - (1.0 if pygame.K_s in self._keys else 0.0)
        lr  = (1.0 if pygame.K_d in self._keys else 0.0) - (1.0 if pygame.K_a in self._keys else 0.0)
        thr = (1.0 if pygame.K_r in self._keys else 0.0) - (1.0 if pygame.K_f in self._keys else 0.0)
        return ud, lr, thr

    def _send_control(self):
        now = time.time()
        if now - self._last_ctrl_send < self._ctrl_send_interval:
            return
        self._last_ctrl_send = now

        ctrl = self.controller.refresh()
        self.state["controller_connected"] = self.controller.connected

        if ctrl is None:
            ctrl = {"type": "control", "up/down": 0.0, "left/right": 0.0, "throttle": 0.0}

        kb_ud, kb_lr, kb_thr = self._keyboard_axes()
        ctrl["up/down"]    = max(-1.0, min(1.0, ctrl["up/down"]    + kb_ud))
        ctrl["left/right"] = max(-1.0, min(1.0, ctrl["left/right"] + kb_lr))
        ctrl["throttle"]   = max(-1.0, min(1.0, ctrl["throttle"]   + kb_thr))

        self.state["control"] = ctrl
        if self.tcp.connected and self.demo is None:
            self.tcp.send(ctrl)
        self.logger.log_event("control_sent", ctrl)

    # ── Draw ─────────────────────────────────────────────────────────────────

    def _draw(self):
        W, H = self.screen.get_size()
        self.screen.fill(C_BG)

        # Camera feed
        with self._camera_lock:
            cam = self._camera_surface
        if cam:
            cw, ch = cam.get_size()
            cx = (W - cw) // 2
            cy = (H - 36 - ch) // 2
            self.screen.blit(cam, (cx, cy))
            age = time.time() - self._last_image_ts
            if age > 2:
                age_txt = self.fonts["tiny"].render(f"IMAGE {age:.0f}s OLD", True, C_WARN)
                self.screen.blit(age_txt, (W//2 - age_txt.get_width()//2, 10))
        else:
            msg = self.fonts["large"].render("NO VIDEO FEED", True, C_TEXT_DIM)
            self.screen.blit(msg, (W//2 - msg.get_width()//2, H//2 - msg.get_height()//2))
            sub = self.fonts["small"].render("Waiting for image data from Dragon...", True, C_TEXT_DIM)
            self.screen.blit(sub, (W//2 - sub.get_width()//2, H//2 + 30))

        # Scanline effect (subtle)
        scanline = pygame.Surface((W, 2), pygame.SRCALPHA)
        scanline.fill((0, 0, 0, 25))
        for y in range(0, H, 4):
            self.screen.blit(scanline, (0, y))

        # All overlays
        for ov in self.overlays:
            ov.draw(self.screen, self.state)

        # Corner branding
        brand = self.fonts["hud"].render("DRAGON", True, C_BORDER)
        self.screen.blit(brand, (W - brand.get_width() - 14, H - 36 - brand.get_height() - 10))

        pygame.display.flip()

    # ── Resize ───────────────────────────────────────────────────────────────

    def _handle_resize(self, W, H):
        self.overlays[0].rect = pygame.Rect(10, 10, 200, 200)
        self.overlays[1].center = (W - 80, 80)
        self.overlays[1].rect = pygame.Rect(W-140, 10, 120, 150)
        self.overlays[2].rect = pygame.Rect(10, H-190, 180, 150)
        self.overlays[3].rect = pygame.Rect(0, H-36, W, 36)

    # ── Key bindings ─────────────────────────────────────────────────────────

    def _handle_key(self, key):
        if key == pygame.K_t:
            self.overlays[0].visible = not self.overlays[0].visible
        elif key == pygame.K_h:
            self.overlays[1].visible = not self.overlays[1].visible
        elif key == pygame.K_c:
            self.overlays[2].visible = not self.overlays[2].visible
        elif key == pygame.K_F11:
            pygame.display.toggle_fullscreen()

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(self):
        choice = self._run_connection_selector()
        if choice is None:
            pygame.quit()
            return

        self._connect(choice)

        running = True
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.VIDEORESIZE:
                    W, H = ev.w, ev.h
                    self._handle_resize(W, H)
                elif ev.type == pygame.KEYDOWN:
                    self._keys.add(ev.key)
                    if ev.key == pygame.K_ESCAPE:
                        running = False
                    else:
                        self._handle_key(ev.key)
                elif ev.type == pygame.KEYUP:
                    self._keys.discard(ev.key)

            # Sync TCP connected state
            self.state["tcp_connected"] = self.tcp.connected or (self.demo is not None)

            self._send_control()
            self._draw()
            self.clock.tick(FPS)

        # Cleanup
        if self.demo:
            self.demo.stop()
        self.tcp.disconnect()
        self.logger.close()
        pygame.quit()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = DragonGCS()
    app.run()
