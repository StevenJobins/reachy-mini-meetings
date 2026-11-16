"""The app's one window, laid out like a video meeting: a stage with the person in the headset (shown as a little
Reachy, lit while they speak), the last caption under it, a row of round buttons (share the screen, captions, other
networks, people, translation key, end), and a side panel with who may connect, the pairing code and the state of
the parts (what start_meeting.sh shows in the terminal).

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
from .supervisor import MEETING_LANGS, Status, Supervisor

SIDE = 320            # the side panel
BAR = 76              # the row of buttons
CAPTION = 50          # the caption line
CAPTION_S = 8.0       # a caption stays this long after the last one
GREEN, GREY, AMBER = "systemGreenColor", "tertiaryLabelColor", "systemOrangeColor"
BG, TILE, PANEL, BUTTON = "#0e1116", "#1b2029", "#161a21", "#2f3541"
ACCENT, RED, SPEAKING = "#ff9500", "#e5484d", "#4ade80"   # the project's orange (xr-client/pages)


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


def draw_text(text: str, cx: float, top: float, size: float, color, bold: bool = False, mono: bool = False) -> float:
    """Centred at cx with its upper edge at `top`; returns the lower edge."""
    font = (AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, AppKit.NSFontWeightSemibold) if mono
            else AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
    attrs = {AppKit.NSFontAttributeName: font, AppKit.NSForegroundColorAttributeName: color}
    s = text_size(text, attrs)
    AppKit.NSString.stringWithString_(text).drawAtPoint_withAttributes_(NSMakePoint(cx - s.width / 2, top - s.height), attrs)
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


def draw_reachy(cx: float, cy: float, a: float, alpha: float, speaking: bool, gaze: tuple[float, float]) -> None:
    """A little Reachy, `a` across: who is in the headset. gaze (-1..1 right, up) moves the eyes; nothing sets it
    yet, it is there for the headset's eye tracking."""
    oval(cx, cy, a / 2, rgb("#2a303b", alpha))
    light, shade, dark = rgb("#f4f5f7", alpha), rgb("#d3d7de", alpha), rgb("#14171c", alpha)
    for side in (-1, 1):   # antennas: up a little more while speaking
        line = AppKit.NSBezierPath.bezierPath()
        tip = (cx + side * a * (0.25 if speaking else 0.21), cy + a * (0.41 if speaking else 0.38))
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


class Stage(AppKit.NSView):
    names: list = []        # headsets connected now
    speaking = False        # the person in the headset speaks
    sharing = False
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
        if self.names:
            draw_reachy(cx, y + h * 0.53, min(w, h) * 0.5, 1.0, self.speaking, self.gaze)
            pill(", ".join(self.names), x + 14, y + 14, rgb("#000000", 0.45), rgb("#ffffff"))
        else:
            a = min(w, h) * 0.3
            draw_reachy(cx, y + h * 0.7, a, 0.45, False, self.gaze)
            top = draw_text("Waiting for the headset", cx, y + h * 0.7 - a / 2 - 18, 19, rgb("#ffffff"), bold=True)
            top = draw_text("New headset? Open Reachy Meetings in it and enter this code:", cx, top - 8, 12.5,
                            rgb("#9aa1ad"))
            top = draw_text(self.code or "– – –", cx, top - 6, 40, rgb("#ffffff"), mono=True)
            draw_text(self.hint, cx, top - 4, 11.5, rgb("#9aa1ad"))
        if self.sharing:
            attrs = {AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(12, AppKit.NSFontWeightMedium)}
            s = text_size("The headset sees this screen", attrs)
            pill("The headset sees this screen", x + 14, y + h - s.height - 24, rgb(ACCENT), rgb("#1a1205"))


class RoundButton(AppKit.NSView):
    """A round button in the row at the foot: an icon, lit while what it stands for is on."""

    symbols: tuple = ()
    active = False
    danger = False
    pressed = False
    on_click = None

    def acceptsFirstMouse_(self, event) -> bool:
        return True

    def drawRect_(self, rect) -> None:
        b = self.bounds()
        r = b.size.height / 2
        fill = rgb(RED) if self.danger else rgb(ACCENT) if self.active else rgb(BUTTON)
        (fill.highlightWithLevel_(0.2) if self.pressed else fill).setFill()
        AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(b, r, r).fill()
        image = tinted_symbol(self.symbols, 17, rgb("#1a1205") if self.active and not self.danger else rgb("#ffffff"))
        if image is not None:
            s = image.size()
            image.drawInRect_(NSMakeRect((b.size.width - s.width) / 2, (b.size.height - s.height) / 2, s.width, s.height))

    def mouseDown_(self, event) -> None:
        self.pressed = True
        self.setNeedsDisplay_(True)

    def mouseUp_(self, event) -> None:
        inside = AppKit.NSPointInRect(self.convertPoint_fromView_(event.locationInWindow(), None), self.bounds())
        self.pressed = False
        self.setNeedsDisplay_(True)
        if inside and self.on_click:
            self.on_click()


def round_button(symbols: tuple, tip: str, on_click, danger: bool = False):
    b = RoundButton.alloc().initWithFrame_(NSMakeRect(0, 0, 46, 46))
    b.symbols, b.danger, b.on_click = symbols, danger, on_click
    b.setToolTip_(tip)
    b.setTranslatesAutoresizingMaskIntoConstraints_(False)
    b.widthAnchor().constraintEqualToConstant_(70 if danger else 46).setActive_(True)
    b.heightAnchor().constraintEqualToConstant_(46).setActive_(True)
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
        return self

    # ------------------------------------------------------------ building
    @objc.python_method
    def build_side(self):
        self.rows = {name: Row() for name in ("robot", "camera", "backend", "door", "remote")}
        self.people = stack(True, 8)

        # the language Reachy speaks for the headset user: the room knows it best
        self.lang_menu = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 240, 26), False)
        self.lang_menu.addItemsWithTitles_([name for _, name in MEETING_LANGS])
        self.lang_menu.setTarget_(self)
        self.lang_menu.setAction_("chooseMeetingLang:")
        self.lang_hint = label("", 11, color="secondaryLabelColor")
        self.lang = stack(True, 4, label("Reachy speaks for the headset in", 12, color="secondaryLabelColor"),
                          self.lang_menu, self.lang_hint)

        self.code = label("", 26, mono=True)
        self.code_hint = label("", 11, color="secondaryLabelColor")
        code = stack(True, 2, label("Pairing code for a new headset", 12, color="secondaryLabelColor"), self.code,
                     self.code_hint)
        self.notice = label("", 11, color="systemOrangeColor")
        self.update_text = label("", 12)
        restart = AppKit.NSButton.buttonWithTitle_target_action_("Restart now", self, "restartForUpdate:")
        restart.setControlSize_(AppKit.NSControlSizeSmall)
        restart.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self.update = stack(True, 6, self.update_text, restart)

        # First start of the app with its own Python: the libraries are being fetched
        self.setup_bar = AppKit.NSProgressIndicator.alloc().init()
        self.setup_bar.setIndeterminate_(False)
        self.setup_bar.setMinValue_(0.0)
        self.setup_bar.setMaxValue_(1.0)
        self.setup_bar.widthAnchor().constraintEqualToConstant_(SIDE - 40).setActive_(True)
        self.setup_text = label("", 11, color="secondaryLabelColor")
        self.setup = stack(True, 6, label("Setting up, once: fetching the speech and vision libraries (about 1.6 GB)",
                                          12, bold=True), self.setup_bar, self.setup_text)

        parts = stack(True, 7, label("This laptop", 12, bold=True),
                      *(self.rows[n].view for n in ("camera", "backend", "door", "remote")))
        content = stack(True, 16, self.setup, label("People", 15, bold=True), self.people, rule(), code, rule(),
                        self.lang, rule(), parts, self.notice, self.update)
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
        self.clock = label("", 13, color="secondaryLabelColor", width=260)

        self.buttons = {
            "share": round_button(("rectangle.inset.filled.and.person.filled", "rectangle.on.rectangle", "display"),
                                  "Share this screen with the headset", self.toggleShare),
            "captions": round_button(("captions.bubble", "text.bubble"), "Captions", self.toggleCaptions),
            "remote": round_button(("globe",), "Let headsets on other networks (at home) connect", self.toggleRemote),
            "people": round_button(("person.2", "person.2.fill"), "People, pairing code and the state of the parts",
                                   self.toggleSide),
            "key": round_button(("gearshape", "gear"), "Translation key (DeepL)", lambda: self.translationKey_(None)),
            "end": round_button(("phone.down.fill", "xmark"), "End: stops the parts and quits", self.end, danger=True),
        }
        row = stack(False, 14, *self.buttons.values())
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
        self.clock.centerYAnchor().constraintEqualToAnchor_(bar.centerYAnchor()).setActive_(True)

        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable
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
    def show(self, s: Status) -> None:
        self.status = s
        rows = self.rows
        self.setup.setHidden_(not s.setup)
        self.setup_bar.setDoubleValue_(s.setup_fraction)
        self.setup_text.setStringValue_(s.setup)
        if s.robot == "running":
            rows["robot"].set(GREEN, "Reachy: running")
        elif s.robot == "starting":
            rows["robot"].set(GREY, "Reachy: Reachy Mini Control is starting …")
        elif s.robot:
            rows["robot"].set(AMBER, f"Reachy: {s.robot}" + (f" ({s.robot_error})" if s.robot_error else ""))
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
        codes = [c for c, _ in MEETING_LANGS]
        self.lang_menu.selectItemAtIndex_(codes.index(s.meeting_lang) if s.meeting_lang in codes else 0)
        self.lang_hint.setStringValue_(
            "With Translate me on in the headset, Reachy says the headset user's words in this language."
            if s.meeting_lang == "auto" else
            "With Translate me on in the headset, Reachy says the headset user's words in this language, "
            "whatever the room speaks.")

        hint = ("On this Wi-Fi, once. After that it also connects from other networks." if s.remote_wanted and s.code
                else "The headset must be on the same Wi-Fi as this laptop." if s.code
                else "The code appears once the door is open.")
        self.code.setStringValue_(s.code or "– – –")
        self.code_hint.setStringValue_(hint)

        people = (list(s.known), list(s.connected))
        if people != self.shown_people:
            self.shown_people = people
            for view in list(self.people.arrangedSubviews()):
                self.people.removeArrangedSubview_(view)
                view.removeFromSuperview()
            self.people.addArrangedSubview_(rows["robot"].view)
            if not s.known:
                self.people.addArrangedSubview_(label("No headset paired yet.", 12, color="secondaryLabelColor"))
            for i, name in enumerate(s.known):
                button = AppKit.NSButton.buttonWithTitle_target_action_("Remove", self, "removeHeadset:")
                button.setTag_(i)
                button.setControlSize_(AppKit.NSControlSizeSmall)
                button.setFont_(AppKit.NSFont.systemFontOfSize_(11))
                row = Row()
                row.set(GREEN if name in s.connected else GREY, name)
                row.text.setPreferredMaxLayoutWidth_(SIDE - 150)
                line = stack(False, 10, row.view, button)
                line.setAlignment_(AppKit.NSLayoutAttributeCenterY)
                self.people.addArrangedSubview_(line)

        notes = []
        if s.problem:
            notes.append(s.problem)
        if s.share_note:
            notes.append(s.share_note)
        if not s.translation_key and s.backend:
            notes.append("No DeepL key: captions are not translated. Set it with the gear button.")
        if not s.video_patch:
            notes.append("The sharper-video patch is missing (an update of Reachy Mini Control removes it):\n"
                         "python robot/scripts/patch_daemon_video.py, then restart Reachy Mini Control.")
        self.notice.setStringValue_("\n\n".join(notes))
        self.notice.setHidden_(not notes)
        self.update_text.setStringValue_(f"Version {s.update} is ready. It is used from the next start.")
        self.update.setHidden_(not s.update)
        self.window.setTitle_(f"Reachy Meetings {s.version}".strip())

        stage = self.stage
        stage.names, stage.code, stage.hint, stage.sharing = list(s.connected), s.code, hint, s.sharing
        if not s.connected:
            stage.speaking = False
        stage.setNeedsDisplay_(True)
        self.clock.setStringValue_(time.strftime("%H:%M") + (f"   ·   {s.address}" if s.address else ""))
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
        self.after(self.supervisor.set_sharing, not self.status.sharing)

    @objc.python_method
    def toggleRemote(self) -> None:
        self.after(self.supervisor.set_remote, not self.status.remote_wanted)

    @objc.python_method
    def toggleCaptions(self) -> None:
        self.captions_on = not self.captions_on
        self.caption_height.setConstant_(CAPTION if self.captions_on else 0)
        self.show(self.status)

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

    def removeHeadset_(self, sender) -> None:
        names = self.shown_people[0] if self.shown_people else []
        if not 0 <= sender.tag() < len(names):
            return
        name = names[sender.tag()]
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(f"Remove “{name}”?")
        alert.setInformativeText_("It can no longer connect to Reachy. To use it again, it needs the pairing code once.")
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
        alert.setMessageText_("Translation key (DeepL)")
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


def run(supervisor: Supervisor, snapshot: str = "", demo: Status = None) -> None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    controller = Controller.alloc().initWithSupervisor_(supervisor)
    if snapshot:   # build, show one status, write the picture, leave: nothing is started
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        controller.build()
        status = demo or supervisor.status()
        if demo and demo.connected:   # the meeting as it looks while the person in the headset speaks
            controller.caption = ("Headset: Können wir das Budget noch einmal anschauen?",
                                  "Can we take another look at the budget?")
            controller.caption_at = time.time()
            controller.stage.speaking = True
        controller.show(status)
        controller.snapshot(snapshot)
        return
    app.setDelegate_(controller)
    AppHelper.runEventLoop()
