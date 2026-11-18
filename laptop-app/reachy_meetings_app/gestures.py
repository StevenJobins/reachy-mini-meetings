"""What Reachy's gestures mean, for the people in the room: a small window with one tile per gesture, each with a
little Reachy that shows it, moving. Opened with the ? button, and by itself the first time the app runs.

The gestures are the ones the headset app makes Reachy do (xr-client/unity/Assets/Scripts/Pose/Gestures.cs,
RobotControl.cs): keep this list in step with them.
"""

from __future__ import annotations

import math
import time

import AppKit
from Foundation import NSMakeRect, NSTimer

from .window import BG, TILE, box, draw_reachy, rgb

COLUMNS, TILE_W, TILE_H, GAP, HEAD = 3, 250, 196, 12, 62

# (title, what it means, what the little Reachy does)
GESTURES = [
    ("Turns its head", "The person in the headset looks there. Reachy follows their head.", "look"),
    ("Right antenna waves", "They would like to say something. It waves for three seconds.", "wave"),
    ("Both antennas spring up", "Thumbs up: they agree.", "up"),
    ("Both antennas sink", "Thumbs down: they do not agree.", "down"),
    ("Antennas move together", "They nod: yes.", "nod"),
    ("Antennas move against each other", "They shake their head: no.", "shake"),
    ("Antennas lift a little", "They smile.", "smile"),
    ("Reachy speaks", "Their words: in their own voice, or translated into the language of the room.", "speak"),
    ("Lies down, motors off", "They left the meeting. Reachy sleeps until they join again.", "sleep"),
]


def pose(kind: str, t: float) -> dict:
    """The little Reachy at time t: antennas as (left, right) seen from the room, each -1 (down) .. 1 (up).
    Reachy's right antenna is on the left for someone facing it."""
    cycle = t % 3.0
    still = dict(antennas=(0.0, 0.0), gaze=(0.0, 0.0), speaking=False, alpha=1.0)
    if kind == "look":
        return {**still, "gaze": (math.sin(t * 1.2), 0.0)}
    if kind == "wave":
        return {**still, "antennas": (0.5 + 0.5 * math.sin(t * 2 * math.pi * 1.5), 0.0)}
    if kind == "up":
        bounce = math.exp(-cycle * 2.5) * math.cos(cycle * 14) if cycle < 2 else 0
        return {**still, "antennas": ((1 - bounce,) * 2 if cycle < 2 else (0.0, 0.0))}
    if kind == "down":
        return {**still, "antennas": ((-min(1.0, cycle * 2.5),) * 2 if cycle < 2 else (0.0, 0.0))}
    if kind == "nod":
        return {**still, "antennas": (0.6 * math.sin(t * 5),) * 2}
    if kind == "shake":
        return {**still, "antennas": (0.6 * math.sin(t * 5), -0.6 * math.sin(t * 5))}
    if kind == "smile":
        return {**still, "antennas": (0.35,) * 2}
    if kind == "speak":
        return {**still, "speaking": cycle < 2}
    return {**still, "antennas": (-1.0, -1.0), "alpha": 0.35}


def wrapped(text: str, rect, size: float, color, bold: bool = False) -> None:
    para = AppKit.NSMutableParagraphStyle.alloc().init()
    para.setAlignment_(AppKit.NSTextAlignmentCenter)
    font = AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size)
    AppKit.NSString.stringWithString_(text).drawInRect_withAttributes_(rect, {
        AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color,
        AppKit.NSParagraphStyleAttributeName: para})


class GestureView(AppKit.NSView):
    at = None   # a fixed time for a still picture; None = now

    def isFlipped(self) -> bool:
        return False

    def drawRect_(self, rect) -> None:
        b = self.bounds()
        rgb(BG).setFill()
        AppKit.NSRectFill(b)
        t = time.time() if self.at is None else self.at
        top = b.size.height
        wrapped("What Reachy shows for the person in the headset", NSMakeRect(0, top - 34, b.size.width, 22), 16,
                rgb("#ffffff"), bold=True)
        wrapped("They see and hear the room through Reachy. This is how you read them.",
                NSMakeRect(0, top - 56, b.size.width, 18), 12.5, rgb("#9aa1ad"))
        for i, (title, text, kind) in enumerate(GESTURES):
            x = GAP + (i % COLUMNS) * (TILE_W + GAP)
            y = top - HEAD - (i // COLUMNS + 1) * (TILE_H + GAP) + GAP
            box(x + TILE_W / 2, y + TILE_H / 2, TILE_W, TILE_H, 14, rgb(TILE))
            draw_reachy(x + TILE_W / 2, y + TILE_H - 62, 104, **pose(kind, t))
            wrapped(title, NSMakeRect(x + 10, y + 50, TILE_W - 20, 18), 13, rgb("#ffffff"), bold=True)
            wrapped(text, NSMakeRect(x + 14, y + 8, TILE_W - 28, 40), 11.5, rgb("#aab1bd"))

    def tick_(self, timer) -> None:
        if self.window() is None or not self.window().isVisible():
            timer.invalidate()
            return
        self.setNeedsDisplay_(True)


def size() -> tuple[float, float]:
    rows = -(-len(GESTURES) // COLUMNS)
    return COLUMNS * (TILE_W + GAP) + GAP, HEAD + rows * (TILE_H + GAP) + GAP


def view(at: float | None = None):
    w, h = size()
    v = GestureView.alloc().initWithFrame_(NSMakeRect(0, 0, w, h))
    v.at = at
    return v


def open_window():
    """The window, moving; the caller keeps it."""
    w, h = size()
    style = AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
    window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, w, h), style, AppKit.NSBackingStoreBuffered, False)
    window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
    window.setTitle_("Reachy's gestures")
    window.setReleasedWhenClosed_(False)
    content = view()
    window.setContentView_(content)
    window.center()
    window.makeKeyAndOrderFront_(None)
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 30, content, "tick:", None, True)
    return window
