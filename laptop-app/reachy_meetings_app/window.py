"""The app's one window: what start_meeting.sh shows in the terminal, plus the switch for other networks and the
list of headsets. Native (AppKit through pyobjc, which the backend's environment already has), light and dark by
itself. The supervisor is asked off the main thread every two seconds; the window only shows what it says."""

from __future__ import annotations

import threading

import AppKit
import objc
from Foundation import NSObject, NSTimer
from PyObjCTools import AppHelper

from . import updates
from .supervisor import Status, Supervisor

W = 520
GREEN, GREY, AMBER = "systemGreenColor", "tertiaryLabelColor", "systemOrangeColor"


def label(text: str = "", size: float = 13, bold: bool = False, color: str = "labelColor", mono: bool = False):
    t = AppKit.NSTextField.labelWithString_(text)
    t.setFont_(AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(size, AppKit.NSFontWeightSemibold) if mono
               else AppKit.NSFont.boldSystemFontOfSize_(size) if bold else AppKit.NSFont.systemFontOfSize_(size))
    t.setTextColor_(getattr(AppKit.NSColor, color)())
    t.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
    t.setMaximumNumberOfLines_(0)
    t.setPreferredMaxLayoutWidth_(W - 80)
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
    box.widthAnchor().constraintEqualToConstant_(W - 52).setActive_(True)
    return box


class Row:
    """A dot (green = fine, grey = not yet, orange = look here) and a line of text."""

    def __init__(self) -> None:
        self.dot, self.text = label("●", 13), label()
        self.view = stack(False, 8, self.dot, self.text)

    def set(self, color: str, text: str) -> None:
        self.dot.setTextColor_(getattr(AppKit.NSColor, color)())
        if self.text.stringValue() != text:
            self.text.setStringValue_(text)


class Controller(NSObject):
    def initWithSupervisor_(self, supervisor: Supervisor):
        self = objc.super(Controller, self).init()
        if self is None:
            return None
        self.supervisor = supervisor
        self.status = Status()
        self.shown_headsets = None
        self.busy = False
        self.run_parts = True
        self.ticks = 0
        self.settled = False
        return self

    # ------------------------------------------------------------ building
    @objc.python_method
    def build(self) -> None:
        self.rows = {name: Row() for name in ("robot", "camera", "backend", "door", "remote", "headset")}
        self.remote_switch = AppKit.NSSwitch.alloc().init()
        self.remote_switch.setTarget_(self)
        self.remote_switch.setAction_("toggleRemote:")
        remote_line = stack(False, 10, self.rows["remote"].view, self.remote_switch)
        remote_line.setAlignment_(AppKit.NSLayoutAttributeCenterY)

        self.code_title = label("New headset? Open Reachy Meetings in it and enter this code:", 12, color="secondaryLabelColor")
        self.code = label("", 34, mono=True)
        self.code_hint = label("", 11, color="secondaryLabelColor")
        self.headsets_title = label("Headsets that may connect", 12, bold=True)
        self.headsets = stack(True, 6)
        self.notice = label("", 11, color="systemOrangeColor")
        self.update_text = label("", 12)
        restart = AppKit.NSButton.buttonWithTitle_target_action_("Restart now", self, "restartForUpdate:")
        restart.setControlSize_(AppKit.NSControlSizeSmall)
        restart.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self.update = stack(False, 12, self.update_text, restart)
        self.update.setAlignment_(AppKit.NSLayoutAttributeCenterY)

        # First start of the app with its own Python: the libraries are being fetched
        self.setup_bar = AppKit.NSProgressIndicator.alloc().init()
        self.setup_bar.setIndeterminate_(False)
        self.setup_bar.setMinValue_(0.0)
        self.setup_bar.setMaxValue_(1.0)
        self.setup_bar.widthAnchor().constraintEqualToConstant_(W - 52).setActive_(True)
        self.setup_text = label("", 11, color="secondaryLabelColor")
        self.setup = stack(True, 6, label("Setting up, once: fetching the speech and vision libraries (about 1.6 GB)", 12, bold=True),
                           self.setup_bar, self.setup_text)

        parts = stack(True, 8, *(self.rows[n].view for n in ("robot", "camera", "backend", "door")), remote_line,
                      self.rows["headset"].view)
        code = stack(True, 2, self.code_title, self.code, self.code_hint)
        self.content = stack(True, 18, self.setup, parts, rule(), code, rule(), self.headsets_title, self.headsets,
                             self.notice, self.update)
        self.content.setEdgeInsets_(AppKit.NSEdgeInsetsMake(22, 26, 22, 26))

        style = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable | AppKit.NSWindowStyleMaskMiniaturizable)
        self.window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, W, 460), style, AppKit.NSBackingStoreBuffered, False)
        self.window.setTitle_("Reachy Meetings")
        self.window.setReleasedWhenClosed_(False)
        self.window.setContentView_(self.content)
        self.content.widthAnchor().constraintEqualToConstant_(W).setActive_(True)
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
            rows["robot"].set(GREEN, "Robot: running")
        elif s.robot == "starting":
            rows["robot"].set(GREY, "Robot: Reachy Mini Control is starting …")
        elif s.robot:
            rows["robot"].set(AMBER, f"Robot: {s.robot}" + (f" ({s.robot_error})" if s.robot_error else ""))
        else:
            rows["robot"].set(AMBER, "Robot: Reachy Mini Control does not answer")
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
        self.remote_switch.setState_(AppKit.NSControlStateValueOn if s.remote_wanted else AppKit.NSControlStateValueOff)
        self.remote_switch.setEnabled_(not self.busy)
        rows["headset"].set(GREEN if s.connected else GREY,
                            "Connected: " + ", ".join(s.connected) if s.connected else "No headset connected")

        self.code.setStringValue_(s.code or "– – –")
        self.code_hint.setStringValue_(
            "On this Wi-Fi, once. After that it also connects from other networks." if s.remote_wanted and s.code
            else "The headset must be on the same Wi-Fi as this laptop." if s.code else "The code appears once the door is open.")

        if s.known != self.shown_headsets:
            self.shown_headsets = list(s.known)
            for view in list(self.headsets.arrangedSubviews()):
                self.headsets.removeArrangedSubview_(view)
                view.removeFromSuperview()
            if not s.known:
                self.headsets.addArrangedSubview_(label("None yet.", 12, color="secondaryLabelColor"))
            for i, name in enumerate(s.known):
                button = AppKit.NSButton.buttonWithTitle_target_action_("Remove", self, "removeHeadset:")
                button.setTag_(i)
                button.setControlSize_(AppKit.NSControlSizeSmall)
                button.setFont_(AppKit.NSFont.systemFontOfSize_(11))
                line = stack(False, 12, label(name), button)
                line.setAlignment_(AppKit.NSLayoutAttributeCenterY)
                self.headsets.addArrangedSubview_(line)

        notes = []
        if s.problem:
            notes.append(s.problem)
        if not s.translation_key and s.backend:
            notes.append("No DeepL key: captions are not translated. Set it under Reachy Meetings > Translation Key… "
                         "in the menu bar.")
        if not s.video_patch:
            notes.append("The sharper-video patch is missing (an update of Reachy Mini Control removes it):\n"
                         "python robot/scripts/patch_daemon_video.py, then restart Reachy Mini Control.")
        self.notice.setStringValue_("\n\n".join(notes))
        self.notice.setHidden_(not notes)
        self.update_text.setStringValue_(f"Version {s.update} is ready. It is used from the next start.")
        self.update.setHidden_(not s.update)
        self.window.setTitle_(f"Reachy Meetings {s.version}".strip())
        self.window.setContentSize_(self.content.fittingSize())

    # ------------------------------------------------------------ running
    def applicationDidFinishLaunching_(self, note) -> None:
        self.menu()
        self.build()
        self.show(Status(remote_wanted=self.supervisor.remote))
        self.window.makeKeyAndOrderFront_(None)
        AppKit.NSApp.activateIgnoringOtherApps_(True)
        if self.run_parts:
            threading.Thread(target=self.supervisor.start, daemon=True).start()
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

    def toggleRemote_(self, sender) -> None:
        on = sender.state() == AppKit.NSControlStateValueOn
        threading.Thread(target=self.supervisor.set_remote, args=(on,), daemon=True).start()

    def removeHeadset_(self, sender) -> None:
        names = self.shown_headsets or []
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
        field = AppKit.NSSecureTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 300, 24))
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
        self.supervisor.stop()

    # ------------------------------------------------------------ for looking at it without starting anything
    @objc.python_method
    def snapshot(self, path: str) -> None:
        self.window.setContentSize_(self.content.fittingSize())
        self.content.layoutSubtreeIfNeeded()
        bounds = self.content.bounds()
        rep = self.content.bitmapImageRepForCachingDisplayInRect_(bounds)
        self.content.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        # the views only: the picture has no background, lay it on a dark or light one to look at it
        rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(path, True)


def run(supervisor: Supervisor, snapshot: str = "", demo: Status = None) -> None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
    controller = Controller.alloc().initWithSupervisor_(supervisor)
    if snapshot:   # build, show one status, write the picture, leave: nothing is started
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
        controller.build()
        controller.show(demo or supervisor.status())
        controller.snapshot(snapshot)
        return
    app.setDelegate_(controller)
    AppHelper.runEventLoop()
