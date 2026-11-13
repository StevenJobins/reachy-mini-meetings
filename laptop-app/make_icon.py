"""Makes laptop-app/AppIcon.icns from the project's icon (xr-client/pages/icon.svg): the Mac app's icon in every size
macOS wants. Run it again when the drawing changes; the result is in the repository, so building the app needs no
drawing.   ~/.venvs/reachy-backend/bin/python laptop-app/make_icon.py

A Mac app icon leaves a margin around its rounded square (824 of 1024 points), so it sits like the others in the
Dock; the web icon fills its whole square.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

import AppKit

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "xr-client" / "pages" / "icon.svg"
INSET = 100 / 1024   # the margin on each side


def png(image, pixels: int, path: Path) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, pixels, pixels, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, 0, 0)
    rep.setSize_((pixels, pixels))
    AppKit.NSGraphicsContext.saveGraphicsState()
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    context.setImageInterpolation_(AppKit.NSImageInterpolationHigh)
    AppKit.NSGraphicsContext.setCurrentContext_(context)
    margin = pixels * INSET
    image.drawInRect_fromRect_operation_fraction_(
        AppKit.NSMakeRect(margin, margin, pixels - 2 * margin, pixels - 2 * margin), AppKit.NSZeroRect,
        AppKit.NSCompositingOperationSourceOver, 1.0)
    AppKit.NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {}).writeToFile_atomically_(str(path), True)


def main() -> None:
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(SOURCE))
    if image is None or not image.isValid():
        sys.exit(f"could not read {SOURCE}")
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for points in (16, 32, 128, 256, 512):
            png(image, points, iconset / f"icon_{points}x{points}.png")
            png(image, points * 2, iconset / f"icon_{points}x{points}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "AppIcon.icns")], check=True)
    print("wrote", HERE / "AppIcon.icns")


if __name__ == "__main__":
    main()
