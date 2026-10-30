"""Packs three.js and the Reachy model into one script, so the deck also runs when opened as a file.

Browsers block imports and fetch() on file:// pages, but a plain <script src> still loads.

    python presentation/midterm/build_deck_data.py
"""

import base64
import json
from pathlib import Path

deck = Path(__file__).parents[2] / "xr-client/pages/midterm"
b64 = lambda path: base64.b64encode(path.read_bytes()).decode("ascii")
(deck / "vendor/deck-data.js").write_text(
    f"window.DECK_THREE = {json.dumps(b64(deck / 'vendor/three.module.js'))};\n"
    f"window.DECK_META = {(deck / 'reachy.json').read_text()};\n"
    f"window.DECK_BIN = {json.dumps(b64(deck / 'reachy.bin'))};\n"
)
