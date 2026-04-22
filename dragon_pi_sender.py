"""
dragon_pi_sender.py — Run this on the Raspberry Pi aboard Dragon.

Reads camera frames, sensor data, and sends over TCP to the GCS.
Receives controller commands and drives motors.

Dependencies:
    pip install opencv-python smbus2

Customize the sensor reads and motor driver sections for your hardware.
"""

import socket
import json
import time
import base64
import io
import threading
import math

try:
    import smbus2
    I2C_AVAILABLE = True
except ImportError:
    I2C_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────────

TCP_HOST = "0.0.0.0"   # listen on all interfaces
TCP_PORT = 5000
CAMERA_AVAILABLE = True
CAMERA_WIDTH  = 640
CAMERA_HEIGHT = 480
CAMERA_JPEG_QUALITY = 70
TELEMETRY_HZ = 10   # times per second
IMAGE_HZ     = 30    # frames per second

# MS5837 pressure/temp sensor (Bar30) — I2C address 0x76
MS5837_ADDR = 0x76

# ── TCP Server ────────────────────────────────────────────────────────────────

_conn = None
_conn_lock = threading.Lock()

_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
_server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
_server.bind((TCP_HOST, TCP_PORT))
_server.listen(1)

def _accept_loop():
    global _conn
    while True:
        print(f"[Dragon] Waiting for GCS on port {TCP_PORT}...")
        try:
            conn, addr = _server.accept()
        except Exception as e:
            print(f"[Dragon] Accept error: {e}")
            time.sleep(1)
            continue
        print(f"[Dragon] GCS connected from {addr}")
        with _conn_lock:
            _conn = conn
        send({"type": "status", "message": "Dragon online"})
        threading.Thread(target=rx_loop, args=(conn,), daemon=True).start()

def send(data: dict):
    with _conn_lock:
        conn = _conn
    if conn is None:
        return
    try:
        line = json.dumps(data) + "\n"
        conn.sendall(line.encode())
    except Exception as e:
        print(f"[TX error] {e}")

# ── Camera ────────────────────────────────────────────────────────────────────

import cv2

cam = cv2.VideoCapture(0)

if not cam.isOpened():
    CAMERA_AVAILABLE = False
    print("[WARN] OpenCV camera not available, using synthetic data")
else:
    cam.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_WIDTH)
    cam.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_HEIGHT)
    time.sleep(1)


def capture_jpeg() -> bytes:
    if CAMERA_AVAILABLE:
        ret, frame = cam.read()
        if not ret:
            raise RuntimeError("Failed to capture frame")
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), CAMERA_JPEG_QUALITY]
        success, jpeg = cv2.imencode(".jpg", frame, encode_param)
        if not success:
            raise RuntimeError("JPEG encoding failed")
        return jpeg.tobytes()
    else:
        from PIL import Image
        t = time.time()
        img = Image.new(
            "RGB",
            (CAMERA_WIDTH, CAMERA_HEIGHT),
            (0, int(30+20*math.sin(t)), int(60+20*math.cos(t)))
        )
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        return buf.getvalue()

# ── Sensors ───────────────────────────────────────────────────────────────────

def read_sensors():

    # TODO: replace with real I2C reads
    t = time.time()
    pressure = 0.0
    depth    = 0.0
    temp     = 0.0
    current = 0.0
    return {
        "type": "telemetry",
        "battery": 10,
        "current": current,
        
        
    }


# ── Motor driver ──────────────────────────────────────────────────────────────

def apply_control(cmd: dict):
    """
    Translate GCS command into thruster PWM signals.
    cmd keys: surge, sway, ascend, yaw, throttle, lights
    """
    print(cmd)
    type  = cmd.get("control",  None)
    ud   = cmd.get("up/down",   0.0)
    lr = cmd.get("left/right", 0.0)
    thro    = cmd.get("throttle",    0.0)
    
    ud_pwm = ud * 1500 + 1500
    
    
    pass

# ── RX thread ─────────────────────────────────────────────────────────────────

_partial = ""

def rx_loop(conn):
    global _partial
    conn.settimeout(0.1)
    while True:
        try:
            raw = conn.recv(4096).decode("utf-8", errors="replace")
            if not raw:
                print("[Dragon] GCS disconnected.")
                break
            _partial += raw
            while "\n" in _partial:
                line, _partial = _partial.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("type") == "control":
                        apply_control(msg)
                except json.JSONDecodeError:
                    pass
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[RX error] {e}")
            break
    with _conn_lock:
        global _conn
        if _conn is conn:
            _conn = None
    print("[Dragon] GCS disconnected, waiting for reconnect...")

# ── Main ──────────────────────────────────────────────────────────────────────

threading.Thread(target=_accept_loop, daemon=True).start()

tele_interval  = 1.0 / TELEMETRY_HZ
image_interval = 1.0 / IMAGE_HZ
last_tele  = 0.0
last_image = 0.0

print("[Dragon] Sender running. Ctrl+C to stop.")
try:
    while True:
        now = time.time()

        if now - last_tele >= tele_interval:
            data = read_sensors()
            send(data)
            last_tele = now

        if now - last_image >= image_interval:
            try:
                jpeg = capture_jpeg()
                b64  = base64.b64encode(jpeg).decode()
                send({"type": "image", "data": b64})
            except Exception as e:
                print(f"[Camera] Error: {e}")
            last_image = now

        time.sleep(0.005)

except KeyboardInterrupt:
    print("\n[Dragon] Shutting down.")
    if CAMERA_AVAILABLE:
        cam.release()
    with _conn_lock:
        if _conn:
            _conn.close()
    _server.close()
