"""Play every gesture once through the MotionController (no headset needed).

    python scripts/demo_gestures.py            # all gestures
    python scripts/demo_gestures.py nod shake  # selected ones
"""

import sys
import time

from reachy_meetings_robot.config import load_config
from reachy_meetings_robot.connection import connect
from reachy_meetings_robot.motion import GESTURES, MotionController
from reachy_meetings_robot.state import StateStore

names = sys.argv[1:] or sorted(GESTURES)
cfg = load_config()

with connect(cfg.robot) as mini:
    motion = MotionController(mini, cfg, StateStore())
    motion.start()
    try:
        for name in names:
            print("->", name)
            motion.play_gesture(name, duration=1.2)
            time.sleep(1.8)
    finally:
        motion.stop()
