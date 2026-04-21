# DRAGON GCS — Ground Control Station

ROV ground control interface for the Dragon vehicle. Receives video and
telemetry over serial tether, displays it in real time, logs everything,
and sends controller inputs back to the Pi.

---

## Quick Start

### 1. Install dependencies (ground station PC)

```bash
pip install pygame pyserial pillow
```

### 2. Run the GCS

```bash
python dragon_gcs.py
```

You'll see a port selector. Pick your serial port, or choose **DEMO MODE**
to test the UI without hardware.

---

## On the Raspberry Pi

Install and run `dragon_pi_sender.py`:

```bash
pip install pyserial picamera2 pillow
python dragon_pi_sender.py
```

Edit the top of that file to set `SERIAL_PORT` (e.g. `/dev/ttyUSB0`)
and fill in the `apply_control()` function with your motor driver code.

---

## Serial Protocol

All messages are **newline-terminated JSON**.

### Pi → PC

| Type | Fields |
|------|--------|
| `telemetry` | depth, pressure, temp, heading, roll, pitch, battery |
| `image` | data (base64 JPEG) |
| `status` | message (string) |

### PC → Pi

| Type | Fields |
|------|--------|
| `control` | surge, sway, ascend, yaw, throttle, lights |

All axis values are **−1.0 to +1.0**. Positive surge = forward.

---

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `T` | Toggle telemetry overlay |
| `H` | Toggle artificial horizon |
| `C` | Toggle controller overlay |
| `L` | Toggle lights |
| `F11` | Fullscreen |
| `ESC` | Quit |

---

## Data Logging

Logs are written to `~/dragon_logs/dragon_YYYY-MM-DD_HH-MM-SS.jsonl`.

Each line is a JSON object:
```json
{"ts": "2025-01-01T12:00:00.000", "event": "telemetry", "depth": 12.3, ...}
```

---

## Adding New Overlays

1. Create a class that extends `Overlay`
2. Override `draw(self, surface, state)`
3. Add it to the list in `DragonGCS._build_overlays()`

The `state` dict contains:
- `state["telemetry"]` — latest telemetry dict
- `state["control"]` — latest sent control dict
- `state["serial_connected"]` — bool
- `state["controller_connected"]` — bool

---

## Controller Mapping (Default — Xbox/PS layout)

| Stick | Axis | ROV |
|-------|------|-----|
| Left Y | 1 | Surge (forward/back) |
| Left X | 0 | Sway (strafe) |
| Right Y | 3 | Ascend/descend |
| Right X | 2 | Yaw |
| L2 trigger | 5 | Throttle scale |
| Button 3 | — | Lights toggle |

Edit `ControllerInput.refresh()` to remap axes for your controller.
