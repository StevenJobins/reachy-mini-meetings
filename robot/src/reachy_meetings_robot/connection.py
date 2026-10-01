"""Connecting to Reachy Mini Lite.

Lite = USB to your laptop. The daemon runs on the laptop:

    reachy-mini-daemon          # real robot over USB
    reachy-mini-daemon --sim    # MuJoCo simulation, no robot needed

The SDK then talks to it at http://localhost:8000.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from .config import RobotCfg

log = logging.getLogger(__name__)


@contextmanager
def connect(cfg: RobotCfg) -> Iterator[ReachyMini]:  # noqa: F821
    """Open a ReachyMini connection, wake it up, and put it to sleep on exit."""
    from reachy_mini import ReachyMini  # imported lazily so unit tests run without the SDK

    log.info("Connecting to Reachy Mini (mode=%s, media=%s)", cfg.connection_mode, cfg.media_backend)
    with ReachyMini(connection_mode=cfg.connection_mode, media_backend=cfg.media_backend) as mini:
        if cfg.wake_up_on_start:
            mini.wake_up()
        try:
            yield mini
        finally:
            if cfg.sleep_on_exit:
                try:
                    mini.goto_sleep()
                except Exception:  # robot may already be disconnected
                    log.exception("goto_sleep failed")
