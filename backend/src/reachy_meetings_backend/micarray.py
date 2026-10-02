"""Mic array tuning (XMOS XVF3800), set directly over USB: stronger noise suppression; the chip's own adaptive
beam stays on (it steers to whoever speaks). On exit the chip goes back to its defaults.

A fixed beam straight ahead was tried and dropped (A/B with a fixed sound source at the side, 2026-10-02):
with it, side voices were damped so much that the VAD no longer heard speech, so Reachy never turned to a new
speaker; with the adaptive beam it turned within ~5 s. Noise suppression alone gave the measured -8 dB.

Direct USB because the daemon's REST endpoint (/api/audio/config/apply) cannot write integer parameters such as
AEC_FIXEDBEAMSONOFF: it receives the values as floats and struct.pack("i", 1.0) fails (checked in
reachy_mini 1.11). Protocol and parameter ids as in reachy_mini/media/audio_control_utils.py and the XMOS
XVF3800 control command reference. The robot's DoA keeps working with the fixed beam (measured).
"""

from __future__ import annotations

import logging
import struct
import time

log = logging.getLogger(__name__)

# name: (resid, cmdid, count, type)
PARAMS = {
    "AEC_FIXEDBEAMSONOFF": (33, 37, 1, "int32"),
    "AEC_FIXEDBEAMSAZIMUTH_VALUES": (33, 81, 2, "float"),
    "AEC_FIXEDBEAMSELEVATION_VALUES": (33, 82, 2, "float"),
    "PP_MIN_NS": (17, 21, 1, "float"),
    "PP_MIN_NN": (17, 22, 1, "float"),
}

FOCUS = {
    "AEC_FIXEDBEAMSONOFF": [0],   # adaptive beam (see above)
    "PP_MIN_NS": [0.05],          # stationary noise gain floor, default 0.15 (lower = stronger)
    "PP_MIN_NN": [0.30],          # non-stationary noise gain floor, default 0.51
}

IDS = [(0x38FB, 0x1001), (0x2886, 0x001A)]   # Reachy Mini Audio, plain ReSpeaker XVF3800
OK, RETRY = 0, 64


class MicArray:
    def __init__(self) -> None:
        import usb.core
        import usb.util
        from libusb_package import get_libusb1_backend

        self._usb = usb
        backend = get_libusb1_backend()
        self.dev = next((d for vid, pid in IDS if (d := usb.core.find(idVendor=vid, idProduct=pid, backend=backend))), None)
        if self.dev is None:
            raise OSError("Reachy Mini Audio (XVF3800) not found on USB")

    def _type(self, direction: int) -> int:
        u = self._usb.util
        return direction | u.CTRL_TYPE_VENDOR | u.CTRL_RECIPIENT_DEVICE

    def read(self, name: str) -> list[float]:
        resid, cmdid, n, kind = PARAMS[name]
        for _ in range(100):
            resp = self.dev.ctrl_transfer(self._type(self._usb.util.CTRL_IN), 0, 0x80 | cmdid, resid, n * 4 + 1, 2000)
            if resp[0] == OK:
                return list(struct.unpack("<" + ("f" if kind == "float" else "i") * n, bytes(resp[1:1 + n * 4])))
            if resp[0] != RETRY:
                raise OSError(f"{name}: status {resp[0]}")
            time.sleep(0.01)
        raise OSError(f"{name}: no answer")

    def write(self, name: str, values: list[float]) -> None:
        resid, cmdid, n, kind = PARAMS[name]
        payload = struct.pack("<" + ("f" if kind == "float" else "i") * n,
                              *(float(v) if kind == "float" else int(v) for v in values))
        self.dev.ctrl_transfer(self._type(self._usb.util.CTRL_OUT), 0, cmdid, resid, payload, 2000)
        time.sleep(0.05)


# XVF3800 defaults (XMOS control command reference). Restoring to these, not to values read at start, also
# repairs the chip after a run that crashed before it could clean up.
DEFAULTS = {
    "AEC_FIXEDBEAMSONOFF": [0],
    "AEC_FIXEDBEAMSAZIMUTH_VALUES": [0.0, 0.0],
    "AEC_FIXEDBEAMSELEVATION_VALUES": [0.0, 0.0],
    "PP_MIN_NS": [0.15],
    "PP_MIN_NN": [0.51],
}


def focus_on():
    """Apply FOCUS; returns a function that puts the chip back to its defaults."""
    mic = MicArray()
    for name, values in FOCUS.items():
        mic.write(name, values)
    log.info("Mic array: stronger noise suppression (adaptive beam)")

    def restore() -> None:
        try:
            m = MicArray()
            for name, values in DEFAULTS.items():
                m.write(name, values)
            log.info("Mic array: back to defaults")
        except Exception as e:  # noqa: BLE001 - best effort on shutdown
            log.warning("Mic array: restore failed (%s)", e)

    return restore
