"""Entry points.

Two ways to run the same Avatar:
  1. CLI (development):        reachy-meetings [--config my.yaml] [--no-media]
  2. Reachy Mini dashboard app: installed via the "reachy_mini_apps" entry point (pyproject.toml),
                                started from http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import logging
import threading

from .bridge import BridgeServer, Handlers
from .config import Config, load_config
from .motion import GESTURES, HeadsetPose, MotionController
from .state import StateStore

log = logging.getLogger("reachy_meetings")


class Avatar:
    """Wires motion, media and the bridge together around one ReachyMini instance."""

    def __init__(self, mini, cfg: Config, with_media: bool = True) -> None:
        self.mini = mini
        self.cfg = cfg
        self.state = StateStore()
        self.motion = MotionController(mini, cfg, self.state)

        self.camera = self.mic = self.speaker = None
        if with_media:
            from .media import CameraStreamer, MicListener, SpeakerPlayer

            self.camera = CameraStreamer(mini, cfg.camera, self.state)
            self.mic = MicListener(mini, cfg.audio, self.state)
            self.speaker = SpeakerPlayer(mini, self.state)
            # TODO: subscribe camera frames -> video transport to xr-client
            # TODO: subscribe mic audio      -> backend (STT / translation)

        self.bridge = BridgeServer(cfg.bridge, self.state, Handlers(
            on_head_pose=lambda m: self.motion.mirror.push(HeadsetPose(m.roll, m.pitch, m.yaw, m.t)),
            on_gesture=lambda m: self.motion.play_gesture(m.name, m.duration),
            on_say=lambda m: self.speaker and self.speaker.say(m.to_float32(), m.sample_rate),
            on_stop_speaking=lambda: self.speaker and self.speaker.clear(),
            gesture_names=sorted(GESTURES),
        ))

    def _parts(self):
        return [p for p in (self.motion, self.camera, self.mic, self.speaker, self.bridge) if p]

    def start(self) -> None:
        for p in self._parts():
            p.start()
        log.info("Avatar running")

    def stop(self) -> None:
        for p in reversed(self._parts()):
            try:
                p.stop()
            except Exception:
                log.exception("stopping %s failed", type(p).__name__)

    def run_until(self, stop_event: threading.Event) -> None:
        self.start()
        try:
            stop_event.wait()
        finally:
            self.stop()


# ---------------------------------------------------------------- dashboard app
try:
    from reachy_mini import ReachyMini, ReachyMiniApp

    class MeetingAvatarApp(ReachyMiniApp):
        custom_app_url: str | None = None  # TODO: small settings UI later?

        def run(self, reachy_mini: ReachyMini, stop_event: threading.Event) -> None:
            Avatar(reachy_mini, load_config()).run_until(stop_event)

except ImportError:  # SDK not installed (e.g. CI for pure-logic tests)
    MeetingAvatarApp = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------- CLI
def cli() -> None:
    ap = argparse.ArgumentParser(description="Reachy Mini Meetings – robot side")
    ap.add_argument("--config", help="YAML file overriding default_config.yaml")
    ap.add_argument("--no-media", action="store_true", help="motion + bridge only (no camera/audio)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args.config)

    from .connection import connect

    stop = threading.Event()
    with connect(cfg.robot) as mini:
        avatar = Avatar(mini, cfg, with_media=not args.no_media)
        try:
            avatar.run_until(stop)
        except KeyboardInterrupt:
            log.info("Stopping…")


if __name__ == "__main__":
    if MeetingAvatarApp is not None:
        app = MeetingAvatarApp()
        try:
            app.wrapped_run()
        except KeyboardInterrupt:
            app.stop()
    else:
        cli()
