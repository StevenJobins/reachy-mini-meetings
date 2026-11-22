"""Cuts the figures of the paper deck (xr-client/pages/paper) out of the paper's PDF and the code release.

Paper: Owens & Efros, "Audio-Visual Scene Analysis with Self-Supervised Multisensory Features", arXiv 1804.03641v2.
Code:  github.com/andrewowens/multisensory (its doc/ folder holds the masked demo frames).
Needs pdfimages (poppler) and Pillow.

    python presentation/paper/extract_figures.py 1804.03641v2.pdf multisensory-master/doc
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageOps

pdf, doc = Path(sys.argv[1]), Path(sys.argv[2])
out = Path(__file__).parents[2] / "xr-client/pages/paper"
tmp = Path(tempfile.mkdtemp())


def embedded(page):
    """The images embedded in one page of the PDF, in the order pdfimages lists them."""
    subprocess.run(["pdfimages", "-f", str(page), "-l", str(page), "-j", str(pdf), str(tmp / f"p{page}")], check=True)
    return [Image.open(f).convert("RGB") for f in sorted(tmp.glob(f"p{page}-*.jpg"))]


# Fig. 5 (page 7): one 3420x230 strip per Kinetics-Sounds class, ten frames each, 342 px apart with a white line
# between them. Rows and frames picked for the deck: the action is easy to see and no face fills the frame.
rows = embedded(7)
picks = [(2, (0, 3, 4, 5, 9)),    # dribbling basketball
         (0, (0, 1, 2, 3, 8)),    # chopping wood
         (3, (0, 2, 4, 6, 7)),    # playing guitar
         (1, (1, 2, 4, 5, 6))]    # shuffling cards
W, H, GAP = 338, 230, 8
grid = Image.new("RGB", (5 * W + 4 * GAP, 4 * H + 3 * GAP), (7, 11, 22))
for r, (row, frames) in enumerate(picks):
    for c, k in enumerate(frames):
        grid.paste(rows[row].crop((2 + 342 * k, 0, 2 + 342 * k + W, H)), (c * (W + GAP), r * (H + GAP)))
grid.save(out / "cam_grid.jpg", quality=84)

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
