"""Cuts the figures of the paper deck (xr-client/pages/paper) out of the paper's PDF, the code release and the poster.

Paper: Owens & Efros, "Audio-Visual Scene Analysis with Self-Supervised Multisensory Features", arXiv 1804.03641v2.
Code:  github.com/andrewowens/multisensory (its doc/ folder holds the masked demo frames).
Poster: the authors' poster for the paper (one page, PDF), which the deck follows section by section.
Needs pdfimages (poppler) and Pillow.

    python presentation/paper/extract_figures.py 1804.03641v2.pdf multisensory-master/doc multisensory-poster.pdf
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

pdf, doc, poster = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out = Path(__file__).parents[2] / "xr-client/pages/paper"
tmp = Path(tempfile.mkdtemp())


def embedded(page):
    """The images embedded in one page of the PDF, in the order pdfimages lists them."""
    subprocess.run(["pdfimages", "-f", str(page), "-l", str(page), "-j", str(pdf), str(tmp / f"p{page}")], check=True)
    return [Image.open(f).convert("RGB") for f in sorted(tmp.glob(f"p{page}-*.jpg"))]


# "What does the network learn?" on the poster: class activation maps, as 7 strips of 9 frames (342 x 230 each)
# for speech and 32 square frames for 8 action classes, 4 each. A few of them, large, are enough on a slide. Picked:
# one adult, mouth or action easy to see, no caption with a name, no children.
subprocess.run(["pdfimages", "-png", str(poster), str(tmp / "poster")], check=True)
on_poster = lambda i: Image.open(tmp / f"poster-{i:03d}.png").convert("RGB")
BG, FIRST_STRIP, FIRST_SQUARE = (7, 11, 22), 56, 63

speech = [(1, 0), (1, 1), (1, 4), (1, 5),      # (strip, frame in the strip): 4 x 3 faces
          (1, 6), (2, 1), (2, 3), (2, 6),
          (4, 5), (5, 0), (5, 3), (5, 5)]
W, H, GAP = 340, 230, 8
grid = Image.new("RGB", (4 * W + 3 * GAP, 3 * H + 2 * GAP), BG)
for n, (strip, k) in enumerate(speech):
    grid.paste(on_poster(FIRST_STRIP + strip).crop((342 * k + 1, 0, 342 * k + 341, 230)), (n % 4 * (W + GAP), n // 4 * (H + GAP)))
grid.save(out / "cam_speech.jpg", quality=84)

# One frame for each of four classes, two by two; under every row 40 px are left free for the class names, which
# the page writes there (.motions in index.html uses the same numbers).
motions = [0, 5, 12, 25]   # chopping wood, shuffling cards, dribbling basketball, playing bass guitar
S, BETWEEN, LABEL = 224, 20, 40
grid = Image.new("RGB", (2 * S + BETWEEN, 2 * (S + LABEL)), BG)
for n, k in enumerate(motions):
    grid.paste(on_poster(FIRST_SQUARE + k), (n % 2 * (S + BETWEEN), n // 2 * (S + LABEL)))
grid.save(out / "cam_grid.jpg", quality=88)

# Fig. 1c (page 2): the predicted on-screen sound for the whole frame (image 12), with the right half masked
# (13, the left speaker is left) and with the left half masked (11). Dark on white in the paper; here light on
# dark, and for the masked cases the kept sound in green over the whole frame's sound in grey.
specs = embedded(2)
ink = lambda im: ImageOps.invert(im.convert("L"))                      # 0 = silence, 255 = loud
tint = lambda im, rgb, k=1.0: Image.merge("RGB", [im.point(lambda v, c=c: int(v * k * c / 255)) for c in rgb])
both = ink(specs[12])
tint(both, (226, 232, 240)).save(out / "spec_both.jpg", quality=88)
for name, i in (("spec_left_masked", 11), ("spec_right_masked", 13)):
    ImageChops.add(tint(both, (148, 163, 184), 0.42), tint(ink(specs[i]), (74, 222, 128))).save(out / f"{name}.jpg", quality=88)

# The demo frames of the code release (sep_video.py --mask l / r paints half of the frame grey). The news ticker
# under the two speakers is cut off: the lower 30 % of the frame.
for name, src in (("frame_both", "crossfire_input"), ("frame_left_masked", "crossfire_l"), ("frame_right_masked", "crossfire_r")):
    im = Image.open(doc / f"{src}.jpg").convert("RGB")
    im.crop((0, 0, im.width, int(im.height * 0.70))).save(out / f"{name}.jpg", quality=86)

print(*sorted(p.name for p in out.glob("*.jpg")))
