"""The app's one window, laid out like a video meeting: a stage with the person in the headset (shown as a little
Reachy, lit while they speak), the last caption under it, a row of round buttons with a word under each (share the
screen, captions, other networks, headsets, notes, help, end), and a side panel: whether everything is ready, the
headsets that may connect, the code for a new one, the language Reachy speaks.

Written for someone who does not know what a gateway or a backend is: the panel says in plain words what is not
ready and offers a button where one helps (Start again, Fix). What start_meeting.sh shows in the terminal (the
parts, addresses, log lines) is behind Show details. The first start, while the libraries are fetched, is one large
progress bar over the whole window (Setup).

Native (AppKit through pyobjc, Cocoa only: nothing the app's own Python lacks), always dark. The supervisor is asked
off the main thread every two seconds; live.py brings the backend's captions and who speaks. The window only shows
what they say. Everything is drawn in drawRect, so --snapshot gives the real picture."""

from __future__ import annotations

import threading
import time

import AppKit
import objc
from Foundation import NSMakePoint, NSMakeRect, NSMakeSize, NSObject, NSTimer
from PyObjCTools import AppHelper

from . import updates
from .live import Live
from .supervisor import MEETING_LANGS, PLANS_URL, Status, Supervisor, friendly_names

SIDE = 320            # the side panel
BAR = 92              # the row of buttons, each with its word under it
CAPTION = 50          # the caption line
CAPTION_S = 8.0       # a caption stays this long after the last one
GREEN, GREY, AMBER = "systemGreenColor", "tertiaryLabelColor", "systemOrangeColor"
BG, TILE, PANEL, BUTTON = "#0e1116", "#1b2029", "#161a21", "#2f3541"
# One look for the Mac app, the headset app (Ui.cs) and the website (xr-client/pages/index.html): dark surfaces, blue
# for what is on or can be pressed, red for leaving, green for speaking; orange belongs to Reachy (the icon, notes).
ACCENT, INK, RED, SPEAKING, WARM = "#4cc3ff", "#0b1220", "#e5484d", "#4ade80", "#ff9500"


def rgb(hex_: str, alpha: float = 1.0):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def label(text: str = "", size: float = 13, bold: bool = False, color: str = "labelColor", mono: bool = False,
          width: float = SIDE - 44):
    t = AppKit.NSTextField.labelWithString_(text)
    t.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, AppKit.NSFontWeightSemibold) if mono
               else AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
    t.setTextColor_(getattr(AppKit.NSColor, color)())
    t.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    t.setMaximumNumberOfLines_(0)
    t.setPreferredMaxLayoutWidth_(width)
    return t


def stack(vertical: bool, spacing: float, *views):
    s = AppKit.NSStackView.stackViewWithViews_(list(views))
    s.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical if vertical
                      else AppKit.NSUserInterfaceLayoutOrientationHorizontal)
    s.setAlignment_(AppKit.NSLayoutAttributeLeading if vertical else AppKit.NSLayoutAttributeFirstBaseline)
    s.setSpacing_(spacing)
    return s


def rule():
    box = AppKit.NSBox.alloc().init()
    box.setBoxType_(AppKit.NSBoxSeparator)
    box.widthAnchor().constraintEqualToConstant_(SIDE - 40).setActive_(True)
    return box


def text_size(text: str, attrs: dict):
    return AppKit.NSAttributedString.alloc().initWithString_attributes_(text, attrs).size()


def draw_text(text: str, cx: float, top: float, size: float, color, bold: bool = False, mono: bool = False,
              left: float = None) -> float:
    """Centred at cx (or starting at `left`) with its upper edge at `top`; returns the lower edge."""
    font = (AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, AppKit.NSFontWeightSemibold) if mono
            else AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
    attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color}
    s = text_size(text, attrs)
    x = cx - s.width / 2 if left is None else left
    AppKit.NSString.stringWithString_(text).drawAtPoint_withAttributes_(NSMakePoint(x, top - s.height), attrs)
    return top - s.height


def pill(text: str, x: float, y: float, fill, color) -> None:
    """A small rounded label with its lower left corner at (x, y)."""
    attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium),
             AppKit.NSForegroundColorAttributeName: color}
    s = text_size(text, attrs)
    box = NSMakeRect(x, y, s.width + 20, s.height + 10)
    fill.setFill()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(box, 8, 8).fill()
    AppKit.NSString.stringWithString_(text).drawAtPoint_withAttributes_(NSMakePoint(x + 10, y + 5), attrs)


def oval(cx: float, cy: float, r: float, color) -> None:
    color.setFill()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)).fill()


def box(cx: float, cy: float, w: float, h: float, r: float, color) -> None:
    color.setFill()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(cx - w / 2, cy - h / 2, w, h), r, r).fill()


def draw_reachy(cx: float, cy: float, a: float, alpha: float, speaking: bool, gaze: tuple[float, float],
                antennas: tuple[float, float] = (0.0, 0.0)) -> None:
    """A little Reachy, `a` across: who is in the headset. gaze (-1..1 right, up) moves the eyes; nothing sets it
    yet, it is there for the headset's eye tracking. antennas (left, right as drawn): -1 hanging down .. 1 up
    (gestures.py)."""
    oval(cx, cy, a / 2, rgb("#2a303b", alpha))
    light, shade, dark = rgb("#f4f5f7", alpha), rgb("#d3d7de", alpha), rgb("#14171c", alpha)
    for side, v in zip((-1, 1), antennas):   # antennas: up a little more while speaking
        line = AppKit.NSBezierPath.bezierPath()
        v += 0.5 if speaking else 0
        tip = (cx + side * a * (0.21 + (0.08 * v if v > 0 else -0.14 * v)), cy + a * (0.38 + (0.06 * v if v > 0 else 0.2 * v)))
        line.moveToPoint_(NSMakePoint(cx + side * a * 0.13, cy + a * 0.16))
        line.lineToPoint_(NSMakePoint(*tip))
        line.setLineWidth_(a * 0.026)
        line.setLineCapStyle_(AppKit.NSLineCapStyleRound)
        shade.setStroke()
        line.stroke()
        oval(tip[0], tip[1], a * 0.038, rgb(SPEAKING, alpha) if speaking else shade)
    box(cx, cy - a * 0.27, a * 0.34, a * 0.17, a * 0.07, shade)       # body
    box(cx, cy + a * 0.02, a * 0.58, a * 0.35, a * 0.13, light)       # head
    for side in (-1, 1):
        ex, ey = cx + side * a * 0.135 + gaze[0] * a * 0.03, cy + a * 0.035 + gaze[1] * a * 0.03
        oval(ex, ey, a * 0.078, dark)
        oval(ex + a * 0.026, ey + a * 0.026, a * 0.022, light)
    oval(cx, cy - a * 0.075, a * 0.018, dark)                          # the camera between the eyes


def tinted_symbol(names: tuple, points: float, color):
    """An SF Symbol in one colour; the first name this macOS knows."""
    image = next((i for i in (AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(n, None)
                              for n in names) if i is not None), None)
    if image is None:
        return None
    image = image.imageWithSymbolConfiguration_(
        AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(points, AppKit.NSFontWeightMedium))

    def draw(rect):
        image.drawInRect_(rect)
        color.set()
        AppKit.NSRectFillUsingOperation(rect, AppKit.NSCompositingOperationSourceAtop)
        return True
    return AppKit.NSImage.imageWithSize_flipped_drawingHandler_(image.size(), False, draw)


class Fill(AppKit.NSView):
    """One colour, behind everything in its part of the window."""

    color = BG

    def drawRect_(self, rect) -> None:
        rgb(self.color).setFill()
        AppKit.NSRectFill(self.bounds())


class Top(AppKit.NSView):
    """Holds the side panel's content at its top edge inside the scroll view."""

    def isFlipped(self) -> bool:
        return True


class Setup(AppKit.NSView):
    """The first start, over the whole window: one large bar while the libraries come, then a moving one while the
    backend fetches its models and starts for the first time. No names of packages: those are in setup.log."""

    fraction = 0.0      # of the libraries, 0..1; None while the models load (nothing measures that)
    failed = False
    at = None           # a fixed time for a still picture; None = now

    def drawRect_(self, rect) -> None:
        b = self.bounds()
        rgb(BG).setFill()
        AppKit.NSRectFill(b)
        cx, cy, grey = b.size.width / 2, b.size.height / 2, rgb("#9aa1ad")
        draw_reachy(cx, cy + 150, 150, 1.0, False, (0.0, 0.0), (0.0, 0.0) if self.failed else (0.35, 0.35))
        top = draw_text("Getting Reachy Meetings ready", cx, cy + 46, 26, rgb("#ffffff"), bold=True)
        if self.failed:
            top = draw_text("The setup could not finish. Is this Mac online?", cx, top - 16, 15, rgb(WARM))
            draw_text("Check the internet connection, then try again.", cx, top - 8, 12.5, grey)
            return
        w, h, y = min(560.0, b.size.width - 120), 14.0, top - 44
        box(cx, y, w, h, h / 2, rgb(BUTTON))
        if self.fraction is None:   # a piece of the bar that runs from left to right
            t = ((time.time() if self.at is None else self.at) % 1.8) / 1.8
            piece = w * 0.28
            left = cx - w / 2 + (w - piece) * t
            box(left + piece / 2, y, piece, h, h / 2, rgb(ACCENT))
            top = draw_text("Almost there: fetching the speech models", cx, y - 22, 15, rgb("#ffffff"))
        else:
            done = max(h, w * min(1.0, max(0.0, self.fraction)))
            box(cx - w / 2 + done / 2, y, done, h, h / 2, rgb(ACCENT))
            top = draw_text(f"{int(self.fraction * 100)} %", cx, y - 22, 15, rgb("#ffffff"), mono=True)
        top = draw_text("This happens once and takes a few minutes.", cx, top - 14, 12.5, grey)
        draw_text("Keep this Mac online and this window open.", cx, top - 3, 12.5, grey)

    def tick_(self, timer) -> None:
        if self.isHidden() or self.window() is None:
            timer.invalidate()
            self.timer = None
        elif self.fraction is None:
            self.setNeedsDisplay_(True)

    def run(self) -> None:
        """Keeps the moving bar moving while this is shown."""
        if getattr(self, "timer", None) is None:
            self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1 / 30, self, "tick:",
                                                                                                None, True)


class Stage(AppKit.NSView):
    names: list = []        # headsets connected now, as the window calls them
    paired = False          # a headset is known already: it connects by itself, without the code
    speaking = False        # the person in the headset speaks
    sharing = False
    share_label = ""        # what is shared, in words
    share_image = None      # a small picture of it (NSImage), renewed while shared
    code, hint = "", ""
    gaze = (0.0, 0.0)

    def drawRect_(self, rect) -> None:
        b = self.bounds()
        tile = AppKit.NSInsetRect(b, 14, 8)
        tile.origin.y += 4
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(tile, 18, 18)
        rgb(TILE).setFill()
        path.fill()
        if self.speaking and self.names:
            ring = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(AppKit.NSInsetRect(tile, 2, 2), 16, 16)
            ring.setLineWidth_(4)
            rgb(SPEAKING).setStroke()
            ring.stroke()
        x, y, w, h = tile.origin.x, tile.origin.y, tile.size.width, tile.size.height
        cx = x + w / 2
        if self.sharing and self.share_image is not None and self.names:   # room for the picture on the right
            cx = x + (w - min(w * 0.36, 360.0)) / 2
        if self.names:
            draw_reachy(cx, y + h * 0.53, min(w, h) * 0.5, 1.0, self.speaking, self.gaze)
            pill(", ".join(self.names), x + 14, y + 14, rgb("#000000", 0.45), rgb("#ffffff"))
        else:
            a = min(w, h) * 0.3
            draw_reachy(cx, y + h * 0.7, a, 0.45, False, self.gaze)
            grey, white = rgb("#9aa1ad"), rgb("#ffffff")
            top = draw_text("Waiting for the headset", cx, y + h * 0.7 - a / 2 - 18, 19, white, bold=True)
            if self.paired:   # it knows this laptop: nothing to type
                top = draw_text("Put on the headset and open Reachy Meetings. It connects by itself.", cx, top - 10,
                                13.5, rgb("#d5d9e0"))
                top = draw_text("A new headset? Enter this code in it:", cx, top - 22, 12.5, grey)
                top = draw_text(self.code or "– – –", cx, top - 4, 26, white, mono=True)
            else:
                steps = ("1   Put on the headset and open Reachy Meetings", "2   Enter this code in it")
                left = cx - text_size(steps[0], {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13.5)}).width / 2
                top = draw_text(steps[0], cx, top - 12, 13.5, rgb("#d5d9e0"), left=left)
                top = draw_text(steps[1], cx, top - 5, 13.5, rgb("#d5d9e0"), left=left)
                top = draw_text(self.code or "– – –", cx, top - 8, 40, white, mono=True)
            draw_text(self.hint, cx, top - 4, 11.5, grey)
        if self.sharing:   # what the headset sees: the name, and the picture itself in the corner
            text = f"The headset sees: {self.share_label or 'this screen'}"
            attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)}
            s = text_size(text, attrs)
            top = y + h - 14
            image = self.share_image
            if image is not None and image.size().width > 0:
                pw = min(w * 0.36, 360.0)
                ph = pw * image.size().height / image.size().width
                if ph > h * 0.5:
                    ph = h * 0.5
                    pw = ph * image.size().width / image.size().height
                frame = NSMakeRect(x + w - pw - 14, top - ph, pw, ph)
                AppKit.NSGraphicsContext.saveGraphicsState()
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(frame, 8, 8).addClip()
                image.drawInRect_(frame)
                AppKit.NSGraphicsContext.restoreGraphicsState()
                border = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(frame, 8, 8)
                border.setLineWidth_(3)
                rgb(ACCENT).setStroke()
                border.stroke()
                top -= ph + 8
                pill(text, min(x + w - s.width - 34, x + w - pw - 14), top - s.height - 10, rgb(ACCENT), rgb(INK))
            else:
                pill(text, x + 14, top - s.height - 10, rgb(ACCENT), rgb(INK))


class RoundButton(AppKit.NSView):
    """A round button in the row at the foot: an icon, lit while what it stands for is on, and its word under it."""

    symbols: tuple = ()
    title = ""
    active = False
    danger = False
    pressed = False
    on_click = None

    def acceptsFirstMouse_(self, event) -> bool:
        return True

    def drawRect_(self, rect) -> None:
        b = self.bounds()
        w = 70.0 if self.danger else ROUND
        circle = NSMakeRect((b.size.width - w) / 2, b.size.height - ROUND, w, ROUND)
        fill = rgb(RED) if self.danger else rgb(ACCENT) if self.active else rgb(BUTTON)
        (fill.highlightWithLevel_(0.2) if self.pressed else fill).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(circle, ROUND / 2, ROUND / 2).fill()
        image = tinted_symbol(self.symbols, 17, rgb(INK) if self.active and not self.danger else rgb("#ffffff"))
        if image is not None:
            s = image.size()
            image.drawInRect_(NSMakeRect(circle.origin.x + (w - s.width) / 2,
                                         circle.origin.y + (ROUND - s.height) / 2, s.width, s.height))
        draw_text(self.title, b.size.width / 2, 14, 10.5, rgb("#aab1bd"))

    def mouseDown_(self, event) -> None:
        self.pressed = True
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event) -> None:
        inside = AppKit.NSPointInRect(self.convertPoint_fromView_(event.locationInWindow(), None), self.bounds())
        self.pressed = False
        self.setNeedsDisplay_(True)
        if inside and self.on_click:
            self.on_click()


ROUND = 46.0   # the circle of a round button; its word needs 18 more below


def round_button(symbols: tuple, title: str, tip: str, on_click, danger: bool = False):
    b = RoundButton.alloc().initWithFrame_(NSMakeRect(0, 0, 56, ROUND + 18))
    b.symbols, b.title, b.danger, b.on_click = symbols, title, danger, on_click
    b.setToolTip_(tip)
    b.setTranslatesAutoresizingMaskIntoConstraints_(False)
    b.widthAnchor().constraintEqualToConstant_(70 if danger else 56).setActive_(True)
    b.heightAnchor().constraintEqualToConstant_(ROUND + 18).setActive_(True)
    return b


def small_button(title: str, target, action: str):
    b = AppKit.NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setControlSize_(AppKit.NSControlSizeSmall)
    b.setFont_(AppKit.NSFont.systemFontOfSize_(11))
    return b


class Row:
    """A dot (green = fine, grey = not yet, orange = look here) and a line of text."""

    def __init__(self, size: float = 12) -> None:
        self.dot, self.text = label("●", size), label(size=size, width=SIDE - 70)
        self.view = stack(False, 8, self.dot, self.text)

    def set(self, color: str, text: str) -> None:
        self.dot.setTextColor_(getattr(AppKit.NSColor, color)())
        if self.text.stringValue() != text:
            self.text.setStringValue_(text)


def pin(view, parent, top=None, bottom=None, leading=None, trailing=None) -> None:
    """Edges of `view` to anchors (anchor, distance); it lives in `parent`."""
    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    if view.superview() is None:
        parent.addSubview_(view)
    for mine, other in ((view.topAnchor(), top), (view.bottomAnchor(), bottom), (view.leadingAnchor(), leading),
                        (view.trailingAnchor(), trailing)):
        if other is not None:
            mine.constraintEqualToAnchor_constant_(other[0], other[1]).setActive_(True)


class Controller(NSObject):
    def initWithSupervisor_(self, supervisor: Supervisor):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.supervisor = supervisor
        self.status = Status()
        self.shown_people = None
        self.busy = False
        self.run_parts = True
        self.ticks = 0
        self.settled = False
        self.captions_on = True
        self.side_on = True
        self.caption = ("", "")      # the line and its translation
        self.caption_at = 0.0
        self.live = None
        self.has_notes = False
        self.gestures = None
        self.share_choices = []    # (source, label) per item of the share menu
        self.shown_issues = None
        self.setting_up = False    # the first start: the Setup view covers the window
        self.details_on = False
        return self

    # ------------------------------------------------------------ building
    @objc.python_method
    def build_side(self):
        # Is everything ready? One line, and under it only what is not, in plain words (issues)
        self.summary = Row(13)
        self.issues = stack(True, 12)
        self.update = stack(True, 6, label("An update is ready.", 12),
                            small_button("Restart now", self, "restartForUpdate:"))
        status = stack(True, 12, self.summary.view, self.issues, self.update)

        self.people = stack(True, 8)
        headsets = stack(True, 10, label("Headsets", 15, bold=True), self.people)

        self.code = label("", 26, mono=True)
        self.code_hint = label("", 11, color="secondaryLabelColor")
        # only while someone is connected: before that the stage shows the code, large
        self.code_box = stack(True, 8, rule(), stack(True, 2, label("Another headset? Enter this code in it", 12,
                                                                     color="secondaryLabelColor"),
                                                     self.code, self.code_hint))

        # the language Reachy speaks for the headset user: the room knows it best
        self.lang_menu = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 240, 26), False)
        self.lang_menu.addItemsWithTitles_([name for _, name in MEETING_LANGS])
        self.lang_menu.setTarget_(self)
        self.lang_menu.setAction_("chooseMeetingLang:")
        self.lang = stack(True, 4, label("Language Reachy speaks", 12, color="secondaryLabelColor"), self.lang_menu,
                          label("Used when the person in the headset switches on Translate me.", 11,
                                color="secondaryLabelColor"))

        # What start_meeting.sh shows in the terminal, for whoever looks after the setup: closed until asked for
        self.rows = {name: Row(11) for name in ("robot", "camera", "backend", "door", "remote")}
        self.detail_text = label("", 10.5, color="secondaryLabelColor")
        self.detail_text.setSelectable_(True)
        self.details = stack(True, 6, *(self.rows[n].view for n in ("robot", "camera", "backend", "door", "remote")),
                             self.detail_text)
        self.details.setHidden_(True)
        self.details_button = small_button("Show details", self, "toggleDetails:")
        self.details_button.setBordered_(False)
        self.details_button.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())

        content = stack(True, 16, status, rule(), headsets, self.code_box, rule(), self.lang, rule(),
                        self.details_button, self.details)
        content.setEdgeInsets_(AppKit.NSEdgeInsetsMake(20, 20, 20, 20))

        side = Fill.alloc().init()
        side.color = PANEL
        scroll = AppKit.NSScrollView.alloc().init()
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        doc = Top.alloc().init()
        scroll.setDocumentView_(doc)
        doc.setTranslatesAutoresizingMaskIntoConstraints_(False)
        pin(content, doc, top=(doc.topAnchor(), 0), bottom=(doc.bottomAnchor(), 0), leading=(doc.leadingAnchor(), 0),
            trailing=(doc.trailingAnchor(), 0))
        doc.widthAnchor().constraintEqualToConstant_(SIDE).setActive_(True)
        pin(scroll, side, top=(side.topAnchor(), 0), bottom=(side.bottomAnchor(), 0), leading=(side.leadingAnchor(), 0),
            trailing=(side.trailingAnchor(), 0))
        return side

    @objc.python_method
    def build(self) -> None:
        self.root = Fill.alloc().initWithFrame_(NSMakeRect(0, 0, 1040, 660))
        self.stage = Stage.alloc().init()
        self.caption_text = AppKit.NSTextField.labelWithString_("")
        self.caption_text.setAlignment_(AppKit.NSTextAlignmentCenter)
        self.caption_text.setMaximumNumberOfLines_(2)
        self.caption_text.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        # a long caption is cut, it does not push the window wider (a label wants the width of its whole text, and
        # that wish is stronger than the window keeping its size)
        self.caption_text.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal)
        self.clock = label("", 13, color="secondaryLabelColor", width=60)

        self.buttons = {
            "share": round_button(("rectangle.inset.filled.and.person.filled", "rectangle.on.rectangle", "display"),
                                  "Share", "Show a screen, a window or an app of this Mac in the headset",
                                  self.toggleShare),
            "captions": round_button(("captions.bubble", "text.bubble"), "Captions",
                                     "Show what is said, here under the picture", self.toggleCaptions),
            "remote": round_button(("globe",), "Remote", "Let a headset join from another place, like from home",
                                   self.toggleRemote),
            "people": round_button(("person.2", "person.2.fill"), "Headsets",
                                   "Show or hide the panel on the right: headsets, language, status", self.toggleSide),
            "mail": round_button(("envelope",), "Notes", "Mail the meeting notes: opens a draft in your mail program",
                                 self.mailNotes),
            "gestures": round_button(("questionmark", "questionmark.circle"), "Help", "What Reachy's gestures mean",
                                     self.showGestures),
            "key": round_button(("gearshape", "gear"), "Key", "The key for translation",
                                lambda: self.translationKey_(None)),
            "end": round_button(("phone.down.fill", "xmark"), "End", "End the meeting and quit", self.end, danger=True),
        }
        row = stack(False, 4, *self.buttons.values())
        row.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        bar = AppKit.NSView.alloc().init()
        self.side = self.build_side()

        root = self.root
        pin(self.side, root, top=(root.topAnchor(), 0), bottom=(root.bottomAnchor(), 0), trailing=(root.trailingAnchor(), 0))
        self.side_width = self.side.widthAnchor().constraintEqualToConstant_(SIDE)
        self.side_width.setActive_(True)
        pin(self.stage, root, top=(root.topAnchor(), 0), leading=(root.leadingAnchor(), 0),
            trailing=(self.side.leadingAnchor(), 0))
        pin(self.caption_text, root, top=(self.stage.bottomAnchor(), 4), leading=(root.leadingAnchor(), 28),
            trailing=(self.side.leadingAnchor(), -28))
        self.caption_height = self.caption_text.heightAnchor().constraintEqualToConstant_(CAPTION)
        self.caption_height.setActive_(True)
        pin(bar, root, top=(self.caption_text.bottomAnchor(), 0), bottom=(root.bottomAnchor(), 0),
            leading=(root.leadingAnchor(), 0), trailing=(self.side.leadingAnchor(), 0))
        bar.heightAnchor().constraintEqualToConstant_(BAR).setActive_(True)
        row.setTranslatesAutoresizingMaskIntoConstraints_(False)
        bar.addSubview_(row)
        row.centerXAnchor().constraintEqualToAnchor_(bar.centerXAnchor()).setActive_(True)
        row.centerYAnchor().constraintEqualToAnchor_(bar.centerYAnchor()).setActive_(True)
        self.clock.setTranslatesAutoresizingMaskIntoConstraints_(False)
        bar.addSubview_(self.clock)
        self.clock.leadingAnchor().constraintEqualToAnchor_constant_(bar.leadingAnchor(), 24).setActive_(True)
        self.clock.centerYAnchor().constraintEqualToAnchor_constant_(bar.centerYAnchor(), -9).setActive_(True)

        # the first start lies over all of it
        self.setup = Setup.alloc().init()
        pin(self.setup, root, top=(root.topAnchor(), 0), bottom=(root.bottomAnchor(), 0), leading=(root.leadingAnchor(), 0),
            trailing=(root.trailingAnchor(), 0))
        self.setup.setHidden_(True)
        self.setup_retry = AppKit.NSButton.buttonWithTitle_target_action_("Try again", self, "retrySetup:")
        self.setup_retry.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.setup.addSubview_(self.setup_retry)
        self.setup_retry.centerXAnchor().constraintEqualToAnchor_(self.setup.centerXAnchor()).setActive_(True)
        self.setup_retry.centerYAnchor().constraintEqualToAnchor_constant_(self.setup.centerYAnchor(), 60).setActive_(True)

        style =(AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable
                 | AppKit.NSWindowStyleMaskResizable)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 1040, 660), style, AppKit.NSBackingStoreBuffered, False)
        self.window.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        self.window.setTitle_("Reachy Meetings")
        self.window.setTitlebarAppearsTransparent_(True)
        self.window.setBackgroundColor_(rgb(BG))
        self.window.setReleasedWhenClosed_(False)
        self.window.setContentView_(self.root)
        self.window.setContentMinSize_(NSMakeSize(820, 560))
        self.window.center()

    @objc.python_method
    def menu(self) -> None:
        bar, item, app_menu = AppKit.NSMenu.alloc().init(), AppKit.NSMenuItem.alloc().init(), AppKit.NSMenu.alloc().init()
        key = app_menu.addItemWithTitle_action_keyEquivalent_("Translation Key…", "translationKey:", ",")
        key.setTarget_(self)
        app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
        app_menu.addItemWithTitle_action_keyEquivalent_("Hide Reachy Meetings", "hide:", "h")
        app_menu.addItemWithTitle_action_keyEquivalent_("Quit Reachy Meetings", "terminate:", "q")
        item.setSubmenu_(app_menu)
        bar.addItem_(item)
        AppKit.NSApp.setMainMenu_(bar)

    # ------------------------------------------------------------ showing
    @objc.python_method
    def issues_of(self, s: Status) -> tuple:
        """(what is still starting, what needs someone): the second as (text, button title, action) each."""
        waiting, look = [], []
        if s.robot == "starting":
            waiting.append("Reachy is starting …")
        elif s.robot != "running":
            look.append(("Reachy does not answer. Plug it in and switch it on, then open Reachy Mini Control."
                         if not s.robot else "Reachy is not ready. Start it in Reachy Mini Control.",
                         "Open Reachy Mini Control", "openControl:"))
        elif not s.camera:
            waiting.append("Reachy's camera is starting …")
        if s.stopped == "backend":
            look.append(("Captions stopped.", "Start again", "restartBackend:"))
        elif not s.backend:
            waiting.append("Captions are getting ready …")
        if s.stopped == "gateway":
            look.append(("Headsets cannot connect right now.", "Start again", "restartGateway:"))
        elif not s.door:
            waiting.append("Getting ready for headsets …")
        if s.remote_wanted and not s.remote_host:
            if s.remote_note:
                look.append(("Headsets in other places cannot connect yet. It keeps trying.", "", ""))
            else:
                waiting.append("Opening up for headsets in other places …")
        if s.share_note:
            look.append(("Sharing the screen stopped." if s.share_note.startswith("Sharing stopped") else s.share_note,
                         "", ""))
        if s.limit:
            look.append(("Today's free hour of translation and meeting notes is used up. Captions go on, untranslated.",
                         "More time", "moreTime:"))
        if not s.translation_key and s.backend:
            look.append(("Captions are not translated: there is no key for translation yet.", "Set the key",
                         "translationKey:"))
        if not s.notes_key and s.backend:
            look.append(("Meeting notes are off on this Mac.", "", ""))
        if s.video_note == "restart":
            look.append(("Quit Reachy Mini Control and open it again: then the picture is sharp.", "", ""))
        elif not s.video_patch and not s.video_note:
            look.append(("The picture in the headset is less sharp than it could be.", "Fix", "fixVideo:"))
        return waiting, look

    @objc.python_method
    def details_of(self, s: Status) -> str:
        """The lines under Show details that are not one of the parts: for whoever looks after the setup."""
        lines = [f"Version {s.version}"] if s.version else []
        if s.install:
            lines.append(f"Installation {s.install}")
        if s.robot_error:
            lines.append(f"Reachy: {s.robot_error}")
        if s.problem:
            lines.append(s.problem)
        if s.share_note:
            lines.append(s.share_note)
        if s.limit:
            lines.append(f"Longer meetings: {PLANS_URL}")
        if not s.notes_key:
            lines.append("No meeting notes: GEMINI_API_KEY is not set on this Mac. Free key from aistudio.google.com/apikey, "
                         "then add  export GEMINI_API_KEY=\"...\"  to ~/.zshrc and start again.")
        if not s.video_patch:
            lines.append("The sharper-video patch is missing (an update of Reachy Mini Control removes it): "
                         "python robot/scripts/patch_daemon_video.py, then restart Reachy Mini Control."
                         + (" It does not fit this version of Reachy Mini Control." if s.video_note == "failed" else ""))
        return "\n\n".join(lines)

    @objc.python_method
    def show(self, s: Status) -> None:
        self.status = s
        rows = self.rows

        # the first start: the bar over everything, until the captions run (or something stopped)
        if s.setup or s.setup_failed:
            self.setting_up = True
        elif s.backend or s.stopped:
            self.setting_up = False
        setup = self.setup
        setup.fraction, setup.failed = (s.setup_fraction if s.setup else None), s.setup_failed
        setup.setHidden_(not self.setting_up)
        self.setup_retry.setHidden_(not s.setup_failed)
        if self.setting_up:
            setup.run()
            setup.setNeedsDisplay_(True)

        # behind Show details: the parts, as start_meeting.sh names them
        if s.robot == "running":
            rows["robot"].set(GREEN, "Reachy: running")
        elif s.robot == "starting":
            rows["robot"].set(GREY, "Reachy: Reachy Mini Control is starting …")
        elif s.robot:
            rows["robot"].set(AMBER, f"Reachy: {s.robot}")
        else:
            rows["robot"].set(AMBER, "Reachy: Reachy Mini Control does not answer")
        rows["camera"].set(GREEN if s.camera else GREY, "Camera stream for the headset")
        rows["backend"].set(GREEN if s.backend else GREY, "Captions, translation, faces" +
                            (f": {s.faces}" if s.backend and s.faces else "" if s.backend
                             else ": after the setup" if s.setup else ": starting …"))
        rows["door"].set(GREEN if s.door else GREY, "Door for headsets on this Wi-Fi" + (f" ({s.address})" if s.address else ""))
        if not s.remote_wanted:
            rows["remote"].set(GREY, "Other networks (headsets at home): off")
        elif s.remote_host:
            rows["remote"].set(GREEN, "Other networks (headsets at home): open")
        else:
            rows["remote"].set(AMBER if s.remote_note else GREY, f"Other networks: {s.remote_note or 'opening …'}")
        self.detail_text.setStringValue_(self.details_of(s))
        codes = [c for c, _ in MEETING_LANGS]
        self.lang_menu.selectItemAtIndex_(codes.index(s.meeting_lang) if s.meeting_lang in codes else 0)

        hint = ("On this Wi-Fi, once. After that it also connects from other places." if s.remote_wanted and s.code
                else "The headset must be on the same Wi-Fi as this Mac." if s.code
                else "The code appears in a moment.")
        self.code.setStringValue_(s.code or "– – –")
        self.code_hint.setStringValue_(hint)
        self.code_box.setHidden_(not s.connected)
        shown = friendly_names(s.known)

        people = (list(s.known), list(s.connected))
        if people != self.shown_people:
            self.shown_people = people
            for view in list(self.people.arrangedSubviews()):
                self.people.removeArrangedSubview_(view)
                view.removeFromSuperview()
            if not s.known:
                self.people.addArrangedSubview_(label("None yet. The code for the first one is on the left.", 12,
                                                      color="secondaryLabelColor"))
            for i, name in enumerate(s.known):
                button = small_button("Remove", self, "removeHeadset:")
                button.setTag_(i)
                row = Row()
                row.set(GREEN if name in s.connected else GREY, shown[name] + ("" if name in s.connected else "  (not here)"))
                row.text.setPreferredMaxLayoutWidth_(SIDE - 150)
                line = stack(False, 10, row.view, button)
                line.setAlignment_(AppKit.NSLayoutAttributeCenterY)
                self.people.addArrangedSubview_(line)

        waiting, look = self.issues_of(s)
        if look:
            self.summary.set(AMBER, "Needs a look")
        elif waiting:
            self.summary.set(GREY, "Getting ready …")
        else:
            self.summary.set(GREEN, "Everything is ready")
        if (waiting, look) != self.shown_issues:
            self.shown_issues = (waiting, look)
            for view in list(self.issues.arrangedSubviews()):
                self.issues.removeArrangedSubview_(view)
                view.removeFromSuperview()
            for text, title, action in look:
                row = Row()
                row.set(AMBER, text)
                self.issues.addArrangedSubview_(stack(True, 6, row.view, small_button(title, self, action)) if title
                                                else row.view)
            for text in waiting:
                row = Row()
                row.set(GREY, text)
                row.text.setTextColor_(AppKit.NSColor.secondaryLabelColor())
                self.issues.addArrangedSubview_(row.view)
            self.issues.setHidden_(not (waiting or look))
        self.buttons["key"].setHidden_(bool(s.install))   # the hosted service has the keys: none to enter
        self.update.setHidden_(not s.update)

        stage = self.stage
        stage.names, stage.code, stage.hint, stage.sharing = [shown[n] for n in s.connected if n in shown], s.code, hint, s.sharing
        stage.paired = bool(s.known)
        stage.share_label = s.share_label
        stage.share_image = None
        if s.share_thumb:   # read anew each time: screencap replaces the file every second
            data = AppKit.NSData.dataWithContentsOfFile_(s.share_thumb)
            stage.share_image = AppKit.NSImage.alloc().initWithData_(data) if data is not None else None
        self.buttons["share"].setToolTip_("Stop sharing" if s.sharing
                                          else "Show a screen, a window or an app of this Mac in the headset")
        if not s.connected:
            stage.speaking = False
        stage.setNeedsDisplay_(True)
        self.clock.setStringValue_(time.strftime("%H:%M"))
        for name, on in (("share", s.sharing), ("captions", self.captions_on), ("remote", s.remote_wanted),
                         ("people", self.side_on)):
            if self.buttons[name].active != on:
                self.buttons[name].active = on
                self.buttons[name].setNeedsDisplay_(True)
        self.show_caption()

    @objc.python_method
    def show_caption(self) -> None:
        text, translation = self.caption if time.time() - self.caption_at < CAPTION_S else ("", "")
        s = AppKit.NSMutableAttributedString.alloc().init()
        para = AppKit.NSMutableParagraphStyle.alloc().init()
        para.setAlignment_(AppKit.NSTextAlignmentCenter)
        para.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        for part, size, color in ((text, 15, rgb("#ffffff")), (translation, 13, rgb("#9aa1ad"))):
            if part:
                s.appendAttributedString_(AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    ("\n" if s.length() else "") + part,
                    {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(size),
                     AppKit.NSForegroundColorAttributeName: color, AppKit.NSParagraphStyleAttributeName: para}))
        self.caption_text.setAttributedStringValue_(s)
        self.caption_text.setHidden_(not self.captions_on)

    @objc.python_method
    def heard(self, m: dict) -> None:
        """A message from the backend (live.py), on the main thread."""
        kind = m.get("type")
        if kind == "me_vad":
            self.stage.speaking = bool(m.get("speaking"))
            self.stage.setNeedsDisplay_(True)
        elif kind == "summary":
            self.has_notes = bool(m.get("summary") or m.get("actions") or m.get("next_steps"))
        elif kind in ("caption", "me") and m.get("text"):
            who = "Headset: " if kind == "me" else ""
            self.caption, self.caption_at = (who + m["text"], m.get("translation") or ""), time.time()
            self.show_caption()

    # ------------------------------------------------------------ running
    def applicationDidFinishLaunching_(self, note) -> None:
        self.menu()
        self.build()
        self.show(Status(remote_wanted=self.supervisor.remote))
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        if self.run_parts:
            threading.Thread(target=self.supervisor.start, daemon=True).start()
        self.live = Live(lambda m: AppHelper.callAfter(self.heard, m))
        self.live.start()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(2.0, self, "tick:", None, True)
        self.tick_(None)
        defaults = AppKit.NSUserDefaults.standardUserDefaults()
        if not defaults.boolForKey_("gesturesShown"):   # the first time: the room should know how to read Reachy
            defaults.setBool_forKey_(True, "gesturesShown")
            self.showGestures()

    def tick_(self, timer) -> None:
        self.ticks += 1
        if self.ticks % (6 * 3600 // 2) == 0:   # every six hours of running: is there a newer version?
            threading.Thread(target=self.supervisor.check_updates, daemon=True).start()
        if self.busy:
            return
        self.busy = True
        threading.Thread(target=self.gather, daemon=True).start()

    @objc.python_method
    def gather(self) -> None:
        try:
            status = self.supervisor.status()
        finally:
            self.busy = False
        AppHelper.callAfter(self.show, status)
        if not self.settled:   # the window is up and the parts answered: this code runs (an update that does not
            self.settled = True   # get here is not tried again, see updates.choose)
            updates.mark_ok(updates.running_root())

    @objc.python_method
    def after(self, work, *args) -> None:
        """Something the supervisor does (it may take a moment), then the new state at once."""
        def run():
            work(*args)
            AppHelper.callAfter(self.tick_, None)
        threading.Thread(target=run, daemon=True).start()

    @objc.python_method
    def toggleShare(self) -> None:
        if self.status.sharing:
            self.after(self.supervisor.set_sharing, False)
            return

        def look():   # screencap lists what can be shared (it may take a moment; the first time macOS asks)
            found = self.supervisor.shareable()
            AppHelper.callAfter(self.pick_share, found)
        threading.Thread(target=look, daemon=True).start()

    @objc.python_method
    def pick_share(self, found: dict) -> None:
        """A menu at the share button, as in a video call: a whole screen, all windows of an app, or one window."""
        if not found:   # an app without screencap: the whole screen, as before
            self.after(self.supervisor.set_sharing, True)
            return
        if found.get("error"):
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("Reachy Meetings may not record the screen yet")
            alert.setInformativeText_("Allow it under System Settings > Privacy & Security > Screen & System Audio "
                                      "Recording, then quit and open Reachy Meetings again.")
            alert.runModal()
            return
        self.share_choices = []
        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        def item(title: str, source: str, label: str, image=None, into=menu):
            it = into.addItemWithTitle_action_keyEquivalent_(title, "shareChosen:", "")
            it.setTarget_(self)
            it.setTag_(len(self.share_choices))
            self.share_choices.append((source, label))
            if image is not None:
                image.setSize_(NSMakeSize(18, 18))
                it.setImage_(image)
            return it

        def header(title: str):
            it = menu.addItemWithTitle_action_keyEquivalent_(title, None, "")
            it.setEnabled_(False)

        header("Share with the headset")
        screens = found.get("displays", [])
        for d in screens:
            name = d["name"] if len(screens) == 1 else f"{d['name']} ({d['width']}×{d['height']})"
            item(f"Whole screen: {name}", f"display:{d['id']}", f"the whole screen ({d['name']})",
                 AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_("display", None))
        apps = {}
        for w in found.get("windows", []):
            apps.setdefault((w["app"], w["pid"]), []).append(w)
        if apps:
            menu.addItem_(AppKit.NSMenuItem.separatorItem())
            header("An app or one window")
        for (app, pid), windows in sorted(apps.items(), key=lambda a: a[0][0].lower()):
            running = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
            icon = running.icon() if running is not None else None
            top = menu.addItemWithTitle_action_keyEquivalent_(app, None, "")
            if icon is not None:
                icon = icon.copy()
                icon.setSize_(NSMakeSize(18, 18))
                top.setImage_(icon)
            sub = AppKit.NSMenu.alloc().init()
            sub.setAutoenablesItems_(False)
            item(f"All {app} windows", f"app:{pid}", app, into=sub)
            sub.addItem_(AppKit.NSMenuItem.separatorItem())
            for w in windows:
                title = w["title"] or "Window without a title"
                item(title[:70], f"window:{w['id']}", f"{app}: {title}"[:80], into=sub)
            top.setSubmenu_(sub)
        button = self.buttons["share"]
        menu.popUpMenuPositioningItem_atLocation_inView_(None, NSMakePoint(0, button.bounds().size.height + 6), button)

    def shareChosen_(self, sender) -> None:
        source, label = self.share_choices[sender.tag()]
        self.after(lambda: self.supervisor.set_sharing(True, source=source, label=label))

    @objc.python_method
    def toggleRemote(self) -> None:
        self.after(self.supervisor.set_remote, not self.status.remote_wanted)

    @objc.python_method
    def toggleCaptions(self) -> None:
        self.captions_on = not self.captions_on
        self.caption_height.setConstant_(CAPTION if self.captions_on else 0)
        self.show(self.status)

    @objc.python_method
    def say(self, text: str) -> None:
        """A word from the app itself, where the captions are."""
        self.caption, self.caption_at = (text, ""), time.time()
        was, self.captions_on = self.captions_on, True
        self.show_caption()
        self.captions_on = was

    @objc.python_method
    def mailNotes(self) -> None:
        """The notes as a draft in this Mac's mail program: the backend runs here and opens it (mail_notes)."""
        if not (self.live and self.live.connected):
            self.say("No meeting notes: the captions are not running.")
        elif not self.has_notes:
            self.say("No meeting notes yet: they appear after the first minute of talking.")
        elif self.live.send({"type": "mail_notes"}):
            self.say("The meeting notes open as a draft in your mail program.")

    @objc.python_method
    def showGestures(self) -> None:
        from . import gestures

        if self.gestures and self.gestures.isVisible():
            self.gestures.close()
        else:
            self.gestures = gestures.open_window()

    @objc.python_method
    def toggleSide(self) -> None:
        self.side_on = not self.side_on
        self.side.setHidden_(not self.side_on)
        self.side_width.setConstant_(SIDE if self.side_on else 0)
        self.show(self.status)

    @objc.python_method
    def end(self) -> None:
        AppKit.NSApp.terminate_(None)

    def chooseMeetingLang_(self, sender) -> None:
        i = sender.indexOfSelectedItem()
        if 0 <= i < len(MEETING_LANGS):
            threading.Thread(target=self.supervisor.set_meeting_lang, args=(MEETING_LANGS[i][0],), daemon=True).start()

    def toggleDetails_(self, sender) -> None:
        self.details_on = not self.details_on
        self.details.setHidden_(not self.details_on)
        self.details_button.setTitle_("Hide details" if self.details_on else "Show details")

    def retrySetup_(self, sender) -> None:
        self.after(self.supervisor.retry_setup)

    def restartBackend_(self, sender) -> None:
        self.after(self.supervisor.restart, "backend")

    def restartGateway_(self, sender) -> None:
        self.after(self.supervisor.restart, "gateway")

    def openControl_(self, sender) -> None:
        self.after(self.supervisor.reopen_control)

    def fixVideo_(self, sender) -> None:
        self.after(self.supervisor.fix_video)

    def moreTime_(self, sender) -> None:
        AppKit.NSWorkspace.sharedWorkspace().openURL_(AppKit.NSURL.URLWithString_(PLANS_URL))

    def removeHeadset_(self, sender) -> None:
        names = self.shown_people[0] if self.shown_people else []
        if not 0 <= sender.tag() < len(names):
            return
        name = names[sender.tag()]
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"Remove “{friendly_names(names)[name]}”?")
        alert.setInformativeText_("It can no longer connect to Reachy. To use it again, it needs the code once.")
        alert.addButtonWithTitle_("Remove")
        alert.addButtonWithTitle_("Cancel")
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            self.supervisor.remove_headset(name)
            self.tick_(None)

    def restartForUpdate_(self, sender) -> None:
        bundle = self.supervisor.bundle()
        if bundle is None:
            return
        # opened again by a helper that outlives us, after the parts have let go of their ports
        import subprocess
        subprocess.Popen(["/bin/sh", "-c", 'sleep 3; /usr/bin/open "$0"', str(bundle)], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        AppKit.NSApp.terminate_(None)

    def translationKey_(self, sender) -> None:
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Key for translation (DeepL)")
        alert.setInformativeText_("From deepl.com > your account > API keys; the free plan is enough. It stays on this Mac, "
                                  "in the app's settings. Leave the field empty to remove a key set here.")
        field = AppKit.NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 300, 24))
        alert.setAccessoryView_(field)
        alert.addButtonWithTitle_("Save")
        alert.addButtonWithTitle_("Cancel")
        alert.window().setInitialFirstResponder_(field)
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            key = str(field.stringValue())
            threading.Thread(target=self.supervisor.set_deepl_key, args=(key,), daemon=True).start()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:
        return True

    def applicationWillTerminate_(self, note) -> None:
        if self.live:
            self.live.stop()
        self.supervisor.stop()

    # ------------------------------------------------------------ for looking at it without starting anything
    @objc.python_method
    def snapshot(self, path: str) -> None:
        self.root.layoutSubtreeIfNeeded()
        bounds = self.root.bounds()
        rep = self.root.bitmapImageRepForCachingDisplayInRect_(bounds)
        self.root.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(path, True)


def run(supervisor: Supervisor, snapshot: str = "", demo: Status = None, models: bool = False) -> None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    controller = Controller.alloc().initWithSupervisor_(supervisor)
    if snapshot:   # build, show one status, write the picture, leave: nothing is started
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        controller.build()
        status = demo or supervisor.status()
        if models:   # the first start after the libraries: the models are on their way
            status, controller.setting_up = Status(robot="running", camera=True, door=True), True
            controller.setup.at = 0.6
        if demo and demo.connected and demo.backend:   # the meeting as it looks while the person in the headset speaks
            controller.caption = ("Headset: Können wir das Budget noch einmal anschauen?",
                                  "Can we take another look at the budget?")
            controller.caption_at = time.time()
            controller.stage.speaking = True
        controller.show(status)
        controller.snapshot(snapshot)
        return
    app.setDelegate_(controller)
    AppHelper.runEventLoop()
