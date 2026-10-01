"""Smoke test: connect, wake up, print pose, small nod + antenna wave, go to sleep.

    reachy-mini-daemon            # in another terminal (or: reachy-mini-daemon --sim)
    python scripts/check_connection.py
"""

import time

import numpy as np
from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

with ReachyMini() as mini:
    print("Connected. Waking up…")
    mini.wake_up()
    print("Head pose (4x4):\n", np.round(mini.get_current_head_pose(), 3))

    for pitch in (15, -10, 0):
        mini.goto_target(head=create_head_pose(pitch=pitch, degrees=True), duration=0.5)
    mini.goto_target(antennas=np.deg2rad([40, -40]), duration=0.4)
    mini.goto_target(antennas=np.deg2rad([0, 0]), duration=0.4)

    time.sleep(0.5)
    print("OK – going to sleep.")
    mini.goto_sleep()
