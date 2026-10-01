"""Test: Reachy dreht sich in Richtung des Sprechers (Direction of Arrival, ReSpeaker-Mikro-Array).

DoA kommt vom Daemon (/api/state/doa): Winkel in rad, 0 = links, π/2 = vorne, π = rechts
(relativ zum Kopf, vorne/hinten nicht unterscheidbar).
Fliessende Regelschleife: jede Sprach-Messung setzt das Ziel neu, Kopf und Körper drehen gleichzeitig.
„I want to speak"-Knopf: Enter im Terminal → rechte Antenne winkt (linke bleibt ruhig),
Reachy dreht sich dabei zur Mitte der letzten Sprecher-Richtungen, Körper schwingt ±27° darum.
Start:  python robot/turn_to_speaker.py      Beenden: Ctrl+C
Voraussetzung: Reachy Mini Control App (Daemon auf localhost:8000) läuft, `pip install reachy-mini numpy`.
"""

import json
import random
import sys
import threading
import time
import urllib.request

import numpy as np

from reachy_mini import ReachyMini
from reachy_mini.utils import create_head_pose

DOA_URL = "http://localhost:8000/api/state/doa"
DT = 0.04
MAX_YAW = np.deg2rad(150)  # Kopf-Weltwinkel (Körper-Limit ±160°)
MAX_HEAD_BODY = np.deg2rad(50)  # Kopf relativ zum Körper (Limit 65°)
DEADBAND = np.deg2rad(10)  # kleinere Abweichungen ignorieren
MAX_SPEED = np.deg2rad(200)  # °/s
DOA_LATENCY = 0.15  # s, DoA bezieht sich auf die Kopfstellung von vor so langer Zeit
WAVE_S = 3.0  # so lange winkt die Antenne nach Knopfdruck
WAVE_BODY = np.deg2rad(27)  # Körper-Schwung beim Winken (± um die Mitte)
SPEAKER_MEMORY_S = 60.0  # Sprecher-Richtungen der letzten so vielen Sekunden
SPEAKER_BIN = np.deg2rad(20)  # Richtungen in Sektoren; jeder Sektor zählt einmal
HEAD_BODY_LIMIT = np.deg2rad(60)  # hartes Limit Kopf↔Körper beim Schwingen (Hardware 65°)

wave_start = wave_until = 0.0
wave_center = 0.0
speaker_dirs = []  # (t, Welt-Yaw) jeder Sprach-Messung


def speakers_center(now):
    """Mitte aller kürzlich gehörten Sprecher-Richtungen (jeder Sektor gleich gewichtet)."""
    recent = [y for ts, y in speaker_dirs if now - ts < SPEAKER_MEMORY_S]
    if not recent:
        return 0.0
    bins = {round(y / SPEAKER_BIN) for y in recent}
    return float(np.mean([np.mean([y for y in recent if round(y / SPEAKER_BIN) == b]) for b in bins]))


def button_loop():
    """„I want to speak"-Knopf: jede Enter-Taste im Terminal startet das Winken."""
    global wave_start, wave_until, wave_center
    for _ in sys.stdin:
        now = time.time()
        if now > wave_until:
            wave_start = now
            wave_center = speakers_center(now)
        wave_until = now + WAVE_S
        print(f"\n🙋 I want to speak! (Mitte der Sprecher: {np.degrees(wave_center):+.0f}°)")


def antennas_at(t):
    """[rechts, links]: rechts winkt (schnell, etwas unregelmässig), links ruhig."""
    if t > wave_until:
        return [0.0, 0.0]
    a = np.deg2rad(32) * np.sin(2 * np.pi * 4.5 * t) + np.deg2rad(random.uniform(-6, 6))
    return [float(a), 0.0]


def body_wave_at(t):
    """Körper-Offset beim Winken: schwingt ±WAVE_BODY symmetrisch, sanft ein-/ausgeblendet."""
    if t > wave_until:
        return 0.0
    env = min(1.0, (t - wave_start) / 0.3, (wave_until - t) / 0.3)
    return float(env * WAVE_BODY * np.sin(2 * np.pi * 1.0 * (t - wave_start)))


def read_doa():
    try:
        d = json.load(urllib.request.urlopen(DOA_URL, timeout=0.5))
        return (d["angle"], d["speech_detected"]) if d else None
    except Exception:  # noqa: BLE001
        return None


def main():
    if read_doa() is None:
        print("Keine DoA vom Daemon – läuft die Reachy Mini Control App mit Mikro?")
        return
    with ReachyMini(media_backend="no_media") as mini:
        mini.goto_target(head=create_head_pose(), antennas=[0, 0], body_yaw=0.0, duration=1.0)
        time.sleep(1.0)
        yaw = body = t_yaw = 0.0
        hist = []  # (t, yaw) für Latenz-Kompensation
        threading.Thread(target=button_loop, daemon=True).start()
        print("Sprich mit Reachy aus verschiedenen Richtungen … Enter = I want to speak, Ctrl+C beendet")
        try:
            while True:
                now = time.time()
                r = read_doa()
                if r is not None:
                    angle, speech = r
                    rel = np.pi / 2 - angle  # relativ zum Kopf, + = links (Roboter-Yaw)
                    y0 = next((y for ts, y in reversed(hist) if ts <= now - DOA_LATENCY), yaw)
                    if speech:
                        speaker_dirs.append((now, float(np.clip(y0 + rel, -MAX_YAW, MAX_YAW))))
                        del speaker_dirs[:-2000]
                    if speech and abs(rel) > DEADBAND:
                        t_yaw = speaker_dirs[-1][1]
                    print(f"\rDoA {np.degrees(angle):6.1f}°  rel {np.degrees(rel):+6.1f}°  "
                          f"Sprache={'JA ' if speech else 'nein'}  Ziel {np.degrees(t_yaw):+5.0f}°  "
                          f"Kopf {np.degrees(yaw):+5.0f}°  Körper {np.degrees(body):+5.0f}°", end="", flush=True)
                if now <= wave_until:
                    t_yaw = wave_center  # beim Winken: alle Sprecher ansprechen
                step = MAX_SPEED * DT
                yaw += float(np.clip(0.3 * (t_yaw - yaw), -step, step))
                body += float(np.clip(0.2 * (yaw - body), -step, step))  # Körper dreht gleich mit
                body = float(np.clip(body, yaw - MAX_HEAD_BODY, yaw + MAX_HEAD_BODY))
                hist = (hist + [(now, yaw)])[-50:]
                mini.set_target(head=create_head_pose(yaw=yaw, degrees=False), antennas=antennas_at(now),
                                body_yaw=float(np.clip(body + body_wave_at(now),
                                                       yaw - HEAD_BODY_LIMIT, yaw + HEAD_BODY_LIMIT)))
                time.sleep(max(0.0, DT - (time.time() - now)))
        except KeyboardInterrupt:
            print("\nZurück zur Mitte.")
        finally:
            mini.goto_target(head=create_head_pose(), antennas=[0, 0], body_yaw=0.0, duration=1.2)
            time.sleep(1.2)


if __name__ == "__main__":
    main()
