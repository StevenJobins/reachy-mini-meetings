#!/bin/zsh
# Makes "Reachy Meetings.app": the app to double-click on the laptop next to Reachy.
#
#   laptop-app/build_app.sh --standalone   # dist/standalone/Reachy Meetings.app: brings its own Python and our code,
#                                          # for any Apple Silicon Mac. On its first start it fetches the backend's
#                                          # libraries (requirements-backend.txt, about 1.6 GB) into
#                                          # ~/Library/Application Support/Reachy Meetings (reachy_meetings_app/libraries.py).
#   laptop-app/build_app.sh                # dist/Reachy Meetings.app: a thin shell around this checkout and the
#                                          # backend's Python environment, for development: a `git pull` updates it.
#
# Both need the Xcode command line tools (the app's program is compiled, launcher.c) and the backend's environment
# (~/.venvs/reachy-backend, or $REACHY_PYTHON): the standalone app copies the Python that environment is made from.
# requirements-backend.txt is the list of that environment when it was last known to work; refresh it with
#   uv pip freeze --python ~/.venvs/reachy-backend/bin/python | grep -v "^-e " > laptop-app/requirements-backend.txt
# The app's version is laptop-app/VERSION. The standalone app then updates its code by itself from the project's
# GitHub releases (release.sh makes one, reachy_meetings_app/updates.py takes it).
#
# Opened from the Finder, the app itself is what macOS asks about for the microphone (room sound), the camera
# (faces) and the local network (headsets on the Wi-Fi), once each; the thin shell also for the folder this
# checkout is in, when that is Documents, Desktop or Downloads.

set -e
here=${0:A:h}
repo=${here:h}
python=${REACHY_PYTHON:-$HOME/.venvs/reachy-backend/bin/python}
mode=dev
[[ $1 == --standalone ]] && mode=standalone
app="$here/dist/Reachy Meetings.app"
[[ $mode == standalone ]] && app="$here/dist/standalone/Reachy Meetings.app"
[[ -n $REACHY_APP_OUT ]] && app="$REACHY_APP_OUT/Reachy Meetings.app"   # somewhere else, e.g. while the other one runs
res=$app/Contents/Resources

[[ -x $python ]] || { echo "No Python at $python: set up backend/ first (backend/README.md), or set REACHY_PYTHON"; exit 1 }
version=$($python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")

rm -rf $app
mkdir -p $app/Contents/MacOS $res
cp $here/AppIcon.icns $res/AppIcon.icns          # from the project's icon, see make_icon.py

cat > $app/Contents/Info.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Reachy Meetings</string>
  <key>CFBundleDisplayName</key><string>Reachy Meetings</string>
  <key>CFBundleIdentifier</key><string>ch.ethz.reachymeetings.laptop</string>
  <key>CFBundleExecutable</key><string>reachy-meetings</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$(<$here/VERSION)</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleVersion</key><string>$(git -C $repo rev-parse --short HEAD 2>/dev/null || echo 0)</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Reachy's microphones bring the meeting room to the headset and are transcribed for the captions.</string>
  <key>NSCameraUsageDescription</key><string>Reachy's camera is used to find the faces of the people who are speaking.</string>
  <key>NSLocalNetworkUsageDescription</key><string>Headsets on this Wi-Fi connect to Reachy through this laptop.</string>
  <key>NSDocumentsFolderUsageDescription</key><string>The Reachy Meetings project folder is in Documents; the app runs its programs from there.</string>
  <key>NSDesktopFolderUsageDescription</key><string>The Reachy Meetings project folder is on the Desktop; the app runs its programs from there.</string>
  <key>NSDownloadsFolderUsageDescription</key><string>The Reachy Meetings project folder is in Downloads; the app runs its programs from there.</string>
</dict>
</plist>
PLIST

if [[ $mode == standalone ]]; then
  # Python itself: the one the backend's environment is made from (a self-contained build that can be moved)
  base=$($python -c "import sys; print(sys.base_prefix)")
  [[ -f $base/lib/libpython$version.dylib ]] || { echo "This Python has no shared library ($base/lib/libpython$version.dylib): the app needs one"; exit 1 }
  cp -R $base $res/python
  rm -f $res/python/lib/python$version/EXTERNALLY-MANAGED          # the copy is the app's own: pip may install into it
  # its library names itself by the place it was installed in; inside the app it is found relative to the program
  install_name_tool -id @rpath/libpython$version.dylib $res/python/lib/libpython$version.dylib 2>/dev/null
  codesign --force --sign - $res/python/lib/libpython$version.dylib 2>/dev/null
  # for the window: pyobjc, in the versions of the backend's list
  cocoa=($(grep -iE "^pyobjc-(core|framework-cocoa)==" $here/requirements-backend.txt))
  $res/python/bin/python3 -m pip install --quiet --disable-pip-version-check --no-warn-script-location --only-binary :all: $cocoa
  # our code, laid out like the repository (supervisor.py finds its parts by that); an update brings the same
  $here/stage_code.sh $res/code
  mkdir -p $res/bin
  find $res -name __pycache__ -prune -exec rm -rf {} +
  # for headsets on other networks: the tunnel program, if this Mac has it
  tunnel=$(whence -p cloudflared) && cp -L $tunnel $res/bin/cloudflared \
    || echo "note: no cloudflared on this Mac: the app will not offer other networks unless the user installs it"
  cc -O2 -o $app/Contents/MacOS/reachy-meetings $here/launcher.c -DSTANDALONE -I$res/python/include/python$version \
     -L$res/python/lib -lpython$version -Wl,-rpath,@executable_path/../Resources/python/lib
else
  # A thin shell around this checkout, for development: see launcher.c for why it is a program and not a script
  read -r include libdir <<< $($python -c "import sysconfig as s; print(s.get_config_var('INCLUDEPY'), s.get_config_var('LIBDIR'))")
  [[ -f $libdir/libpython$version.dylib ]] || { echo "This Python has no shared library ($libdir/libpython$version.dylib): the app needs one"; exit 1 }
  cc -O2 -o $app/Contents/MacOS/reachy-meetings $here/launcher.c -I$include -L$libdir -lpython$version -Wl,-rpath,$libdir \
     -DREPO="\"$repo\"" -DPYTHON="\"$python\""
fi

# A local signature, so macOS knows the app again when it asks about microphone and camera
codesign --force --sign - $app 2>/dev/null || echo "note: not signed (codesign failed); macOS may ask for the permissions again"

echo "built $app ($(du -sh $app | cut -f1))"
