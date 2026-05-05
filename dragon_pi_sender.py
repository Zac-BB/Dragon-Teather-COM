"""
dragon_pi_sender.py — Run this on the Raspberry Pi aboard Dragon.

Reads camera frames, sensor data, and sends over TCP to the GCS.
Receives controller commands and drives motors via Arduino serial.

Dependencies:
    pip install opencv-python smbus2 pyserial

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

try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

try:
    import RPi.GPIO as GPIO
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False
    print("[WARN] RPi.GPIO not available — running in print-only mode")

# ── Config ────────────────────────────────────────────────────────────────────

TCP_HOST = "0.0.0.0"   # listen on all interfaces
TCP_PORT = 5000
CAMERA_AVAILABLE = True
CAMERA_WIDTH  = 640
CAMERA_HEIGHT = 480
CAMERA_JPEG_QUALITY = 30
TELEMETRY_HZ = 10   # times per second
IMAGE_HZ     = 30    # frames per second

# ── Servo / ESC GPIO pins (BCM numbering) ────────────────────────────────────
PIN_THRUST  = 16   # thruster ESC
PIN_SERVO_1 = 20   # servo 1
PIN_SERVO_2 = 21   # servo 2

# Flip this to switch servo PWM range
SERVO_MODE = True   # True = hobby servo (500–2500 µs) | False = ESC (800–2200 µs)

MIN_THRUST,  MAX_THRUST  = 1000, 2000          # thrust ESC always uses ESC range
if SERVO_MODE:
    MIN_SERVO_1, MAX_SERVO_1 = 500, 2500       # standard hobby servo
    MIN_SERVO_2, MAX_SERVO_2 = 500, 2500
else:
    MIN_SERVO_1, MAX_SERVO_1 = 800, 2200       # ESC / vectoring thruster
    MIN_SERVO_2, MAX_SERVO_2 = 800, 2200
DEADBAND = 50        # µs either side of 1500 that snaps to neutral
ALPHA    = 0.89      # low-pass filter gain (same as Arduino)

# Filter state — persists between apply_control() calls
_filt_thrust  = 1500.0
_filt_servo_1 = 1500.0
_filt_servo_2 = 1500.0
_servo_lock   = threading.Lock()


_servos = {}
if GPIO_AVAILABLE:
    for pin in (PIN_THRUST, PIN_SERVO_1, PIN_SERVO_2):
        GPIO.setup(pin, GPIO.OUT)
        pwm = GPIO.PWM(pin, 50)   # 50 Hz for servos/ESCs
        pwm.start(7.5)            # start at neutral (1500 µs ≈ 7.5% duty)
        _servos[pin] = pwm

def _set_servo(pin, pw):
    """Write pulse-width (µs) to a GPIO pin via RPi.GPIO PWM."""
    # Convert pulse width (µs) to duty cycle (%) at 50 Hz
    # Period at 50 Hz = 20,000 µs, so duty% = (pw / 20000) * 100
    duty = (pw / 20000.0) * 100
    if GPIO_AVAILABLE and pin in _servos:
        _servos[pin].ChangeDutyCycle(duty)
    else:
        print(f"[PWM] pin {pin} → {int(pw)} µs ({duty:.2f}% duty)")

def _apply_deadband(val, dead=DEADBAND):
    """Snap values within ±dead of 1500 to exactly 1500."""
    if 1500 - dead < val < 1500 + dead:
        return 1500
    return val

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
filtered_power_draw  =0

def read_sensors():

    # TODO: replace with real I2C reads
    alpha = 0.89
    t = time.time()
    pressure = 0.0
    depth    = 0.0
    temp     = 0.0
    current = 0.0
    
    # current_draw = analogRead(PIN_CURRENT)# read the current pin
    # current_draw = (current_draw/1023)*5;           # scale from analog input to voltage
    # current_draw = 187.5 * ((current_draw / 3.26) - 0.1) # convert from voltage to current reading
    # power_draw = current_draw * (4.2*4)            # convert from amps to watts assuming fully charged 4s battery
    # filtered_power_draw = alpha * power_draw + (1.0 - alpha) * filtered_power_draw
    return {
        "type": "telemetry",
        "battery": 10,
        "current": current,
        
        
    }


# ── Motor driver ──────────────────────────────────────────────────────────────

def apply_control(cmd: dict):
    """
    Translate GCS command into servo/ESC PWM signals.

    GCS sends -1.0 .. 1.0 for each axis:
      throttle   → main thruster
      up/down    → pitch  (left stick Y)
      left/right → yaw    (left stick X)

    Mixing (same as Arduino_Drive.ino):
      servo_1 = pitch - yaw   (clamped ±1)
      servo_2 = -pitch - yaw  (clamped ±1)
    """
    global _filt_thrust, _filt_servo_1, _filt_servo_2
    print(cmd)
    thro  = cmd.get("throttle",    0.0)
    pitch = cmd.get("up/down",     0.0)
    yaw   = cmd.get("left/right",  0.0)

    # Mixing
    s1 = max(-1.0, min(1.0,  pitch - yaw))
    s2 = max(-1.0, min(1.0, -pitch - yaw))

    # Scale -1..1 to µs pulse-width
    thrust_pw  = int((thro + 1.0) / 2.0 * (MAX_THRUST  - MIN_THRUST)  + MIN_THRUST)
    # Servos are reversed: +1 → min, -1 → max (matches Arduino map(x, 100, -100, min, max))
    servo_1_pw = int((-s1 + 1.0) / 2.0 * (MAX_SERVO_1 - MIN_SERVO_1) + MIN_SERVO_1)
    servo_2_pw = int((-s2 + 1.0) / 2.0 * (MAX_SERVO_2 - MIN_SERVO_2) + MIN_SERVO_2)

    # Deadband — snap to neutral if within ±DEADBAND µs of 1500
    thrust_pw  = _apply_deadband(thrust_pw)
    servo_1_pw = _apply_deadband(servo_1_pw)
    servo_2_pw = _apply_deadband(servo_2_pw)

    # Low-pass filter (same alpha as Arduino)
    with _servo_lock:
        _filt_thrust  = ALPHA * thrust_pw  + (1.0 - ALPHA) * _filt_thrust
        _filt_servo_1 = ALPHA * servo_1_pw + (1.0 - ALPHA) * _filt_servo_1
        _filt_servo_2 = ALPHA * servo_2_pw + (1.0 - ALPHA) * _filt_servo_2

        _set_servo(PIN_THRUST,  _filt_thrust)
        _set_servo(PIN_SERVO_1, _filt_servo_1)
        _set_servo(PIN_SERVO_2, _filt_servo_2)

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
    if GPIO_AVAILABLE:
        for pwm in _servos.values():
            pwm.stop()
        GPIO.cleanup()
finally:
    print("\n[Dragon] Shutting down.")
    if CAMERA_AVAILABLE:
        cam.release()
    with _conn_lock:
        if _conn:
            _conn.close()
    _server.close()
    if GPIO_AVAILABLE:
        for pwm in _servos.values():
            pwm.stop()
        GPIO.cleanup()