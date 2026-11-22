"""Packs the paper deck into one HTML file, fonts and pictures inside, to open without the repo or the internet.

    python presentation/paper/build_standalone.py paper-presentation.html
"""

import base64
import re
import sys
from pathlib import Path

pages = Path(__file__).parents[2] / "xr-client/pages"
deck, vendor = pages / "paper", pages / "midterm/vendor"
uri = lambda path, mime: f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"

fonts = re.sub(r"url\(([^)]+\.woff2)\)", lambda m: f"url({uri(vendor / m[1], 'font/woff2')})", (vendor / "fonts.css").read_text())
html = (deck / "index.html").read_text()
html, n = re.subn(r'<link href="\.\./midterm/vendor/fonts\.css" rel="stylesheet">.*', lambda m: f"<style>{fonts}</style>", html, count=1)
assert n == 1, "the deck no longer links the midterm deck's fonts.css"
html = re.sub(r'src="([\w-]+\.jpg)"', lambda m: f'src="{uri(deck / m[1], "image/jpeg")}"', html)
Path(sys.argv[1]).write_text(html)
