"""
dragon_pi_sender.py — Run this on the Raspberry Pi aboard Dragon.

Reads camera frames, sensor data, and sends over serial to the GCS.
Receives controller commands and drives motors.

Dependencies:
    pip install pyserial picamera2 smbus2

Customize the sensor reads and motor driver sections for your hardware.
"""

import serial
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

SERIAL_PORT = "/dev/ttyAMA0"   # or /dev/ttyUSB0 for USB tether
CAMERA_AVAILABLE = True
SERIAL_BAUD = 115200
CAMERA_WIDTH  = 640
CAMERA_HEIGHT = 480
CAMERA_JPEG_QUALITY = 70
TELEMETRY_HZ = 10   # times per second
IMAGE_HZ     = 30    # frames per second

# MS5837 pressure/temp sensor (Bar30) — I2C address 0x76
MS5837_ADDR = 0x76

# ── Serial ────────────────────────────────────────────────────────────────────

ser = serial.Serial(SERIAL_PORT, SERIAL_BAUD, timeout=0.1)

def send(data: dict):
    line = json.dumps(data) + "\n"
    ser.write(line.encode())

import cv2

# ── Camera ──────────────────────────────────────────────────────────

CAMERA_AVAILABLE = True

cam = cv2.VideoCapture(0)  # 0 = default webcam

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

        # Encode as JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), CAMERA_JPEG_QUALITY]
        success, jpeg = cv2.imencode(".jpg", frame, encode_param)

        if not success:
            raise RuntimeError("JPEG encoding failed")

        return jpeg.tobytes()

    else:
        # Synthetic fallback (same as before)
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

_depth = 0.0
_pressure = 1.013
_temp = 20.0

def read_sensors():
    """
    Replace with actual I2C reads for your sensor suite.
    Bar30 (MS5837): pressure + temp → compute depth
    """
    global _depth, _pressure, _temp
    # TODO: replace with real I2C reads
    t = time.time()
    _pressure = 1.013 + 1.1 * abs(math.sin(t * 0.1))
    _depth    = (_pressure - 1.013) / 0.0981  # approx depth in m (freshwater)
    _temp     = 18.5 + 0.5 * math.sin(t * 0.07)

def read_heading():
    """Replace with IMU read (BNO055, ICM-20689, etc.)"""
    return (time.time() * 10) % 360, 0.0, 0.0   # heading, roll, pitch

def read_battery():
    """Replace with ADC read for battery voltage."""
    return 87.0

# ── Motor driver ──────────────────────────────────────────────────────────────

def apply_control(cmd: dict):
    """
    Translate GCS command into thruster PWM signals.
    cmd keys: surge, sway, ascend, yaw, throttle, lights
    """
    surge  = cmd.get("surge",  0.0)
    sway   = cmd.get("sway",   0.0)
    ascend = cmd.get("ascend", 0.0)
    yaw    = cmd.get("yaw",    0.0)
    lights = cmd.get("lights", False)

    # TODO: replace with your ESC / motor driver calls
    # Example for BlueRobotics T200 thrusters via pigpio or RPi.GPIO PWM:
    #   FL = surge + sway + yaw
    #   FR = surge - sway - yaw
    #   RL = surge - sway + yaw
    #   RR = surge + sway - yaw
    #   VL = ascend
    #   VR = ascend
    # Scale to 1100–1900 µs PWM and write to ESCs.
    pass

# ── RX thread ─────────────────────────────────────────────────────────────────

_partial = ""

def rx_loop():
    global _partial
    while True:
        try:
            raw = ser.read(4096).decode("utf-8", errors="replace")
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
        except Exception:
            time.sleep(0.05)

threading.Thread(target=rx_loop, daemon=True).start()

# ── Main loop ─────────────────────────────────────────────────────────────────

send({"type": "status", "message": "Dragon online"})

tele_interval = 1.0 / TELEMETRY_HZ
image_interval = 1.0 / IMAGE_HZ
last_tele  = 0.0
last_image = 0.0

print("[Dragon] Sender running. Ctrl+C to stop.")
try:
    while True:
        now = time.time()

        if now - last_tele >= tele_interval:
            read_sensors()
            h, roll, pitch = read_heading()
            batt = read_battery()
            send({
                "type":     "telemetry",
                "depth":    round(_depth, 3),
                "pressure": round(_pressure, 4),
                "temp":     round(_temp, 2),
                "heading":  round(h, 1),
                "roll":     round(roll, 2),
                "pitch":    round(pitch, 2),
                "battery":  round(batt, 1),
            })
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
        cam.stop()
