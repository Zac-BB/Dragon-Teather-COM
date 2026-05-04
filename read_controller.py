"""
Nintendo Switch Pro Controller — Dragon GCS input module.

Provides ControllerInput for use by dragon_gcs.py.
Run this file directly to get a live debug display of all axes and buttons.

Axis mapping (matches USB_Controller_to_Arduino_Serial reference):
  axis 0  left stick X   → left/right  (yaw)
  axis 1  left stick Y   → up/down     (pitch, negated)
  axis 3  right stick Y  → throttle    (thrust, negated)
"""

import sys
import os
import pygame

DEADZONE = 0.1

BUTTON_NAMES = {
    0:  "B",
    1:  "A",
    2:  "Y",
    3:  "X",
    4:  "L",
    5:  "R",
    6:  "ZL",
    7:  "ZR",
    8:  "-  (Minus)",
    9:  "+  (Plus)",
    10: "L3 (Left Stick Click)",
    11: "R3 (Right Stick Click)",
    12: "Home",
    13: "Capture",
}

AXIS_NAMES = {
    0: "Left  Stick X",
    1: "Left  Stick Y",
    2: "Right Stick X",
    3: "Right Stick Y",
}


# ─────────────────────────────────────────────────────────────────────────────
# GCS-facing class — import this in dragon_gcs.py
# ─────────────────────────────────────────────────────────────────────────────

class ControllerInput:
    """Reads a connected joystick and returns control dicts for the GCS."""

    def __init__(self):
        pygame.joystick.init()
        self.joystick = None
        self.axes = {}
        self.buttons = {}
        self._init_joystick()

    def _init_joystick(self):
        count = pygame.joystick.get_count()
        if count > 0:
            self.joystick = pygame.joystick.Joystick(0)
            self.joystick.init()

    def get_control(self):
        """
        Poll the controller and return a control dict, or None if disconnected.

        Returned keys: type, up/down, left/right, throttle  (all -1.0 .. 1.0)
        """
        if self.joystick is None:
            self._init_joystick()
            return None

        pygame.event.pump()

        axes = {}
        for i in range(self.joystick.get_numaxes()):
            v = self.joystick.get_axis(i)
            axes[i] = v if abs(v) > DEADZONE else 0.0

        buttons = {}
        for i in range(self.joystick.get_numbuttons()):
            buttons[i] = self.joystick.get_button(i)


        self.axes = axes
        self.buttons = buttons

        return {
            "type":       "control",
            "up/down":    round(-axes.get(1, 0.0), 3),  # left stick Y, negated
            "left/right": round( axes.get(0, 0.0), 3),  # left stick X
            "throttle":   round(-axes.get(3, 0.0), 3),  # right stick Y, negated
        }

    @property
    def connected(self):
        return self.joystick is not None


# ─────────────────────────────────────────────────────────────────────────────
# Standalone debug display (python read_controller.py)
# ─────────────────────────────────────────────────────────────────────────────

def _format_bar(value, width=20):
    mid = width // 2
    pos = int((value + 1.0) / 2.0 * width)
    pos = max(0, min(width - 1, pos))
    bar = ["-"] * width
    bar[mid] = "|"
    bar[pos] = "#"
    return "[" + "".join(bar) + "]"


def _debug_read_controller():
    pygame.init()
    pygame.joystick.init()

    print("Waiting for controller...", flush=True)
    joystick = None
    while joystick is None:
        pygame.joystick.quit()
        pygame.joystick.init()
        if pygame.joystick.get_count() > 0:
            joystick = pygame.joystick.Joystick(0)
            joystick.init()
        else:
            pygame.time.wait(500)

    name = joystick.get_name()
    num_axes = joystick.get_numaxes()
    num_buttons = joystick.get_numbuttons()
    num_hats = joystick.get_numhats()

    print(f"\nConnected: {name}")
    print(f"  Axes: {num_axes}  Buttons: {num_buttons}  Hats: {num_hats}")
    print("Press Ctrl+C to quit.\n")

    axis_lines = num_axes
    button_lines = (num_buttons + 3) // 4
    total_lines = axis_lines + button_lines + 4

    print("\n" * total_lines, end="")
    UP = f"\033[{total_lines + 1}A"

    try:
        while True:
            pygame.event.pump()

            axes = {}
            for i in range(num_axes):
                v = joystick.get_axis(i)
                axes[i] = v if abs(v) >= DEADZONE else 0.0

            buttons = {i: joystick.get_button(i) for i in range(num_buttons)}

            lines = []
            lines.append("── AXES ─────────────────────────────────────────")
            for i in range(num_axes):
                label = AXIS_NAMES.get(i, f"Axis {i}")
                v = axes[i]
                bar = _format_bar(v)
                lines.append(f"  {label:<20} {bar}  {v:+.3f}")

            lines.append("")
            lines.append("── BUTTONS ──────────────────────────────────────")
            row = []
            for i in range(num_buttons):
                label = BUTTON_NAMES.get(i, f"Btn{i}")
                state = "●" if buttons[i] else "○"
                row.append(f"{state} {label:<22}")
                if len(row) == 4:
                    lines.append("  " + "  ".join(row))
                    row = []
            if row:
                lines.append("  " + "  ".join(row))

            lines.append("")
            lines.append("── MAPPED CONTROLS ──────────────────────────────")
            lines.append(f"  up/down    {-axes.get(1, 0.0):+.3f}   left/right {axes.get(0, 0.0):+.3f}   throttle {-axes.get(3, 0.0):+.3f}")

            while len(lines) < total_lines:
                lines.append("")

            output = UP + "\n".join(f"\033[2K{l}" for l in lines[:total_lines])
            sys.stdout.write(output)
            sys.stdout.flush()

            pygame.time.wait(16)

    except KeyboardInterrupt:
        print("\n\nExiting.")
        pygame.quit()


if __name__ == "__main__":
    os.environ["SDL_VIDEODRIVER"] = "dummy"  # headless — no window needed
    _debug_read_controller()
