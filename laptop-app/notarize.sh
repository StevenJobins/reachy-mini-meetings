#!/bin/zsh
# Makes the download for everyone: "Reachy Meetings.dmg", signed with a Developer ID and notarized by Apple, so
# macOS opens it without the warning about an unknown developer.
#
#   laptop-app/notarize.sh            # builds the standalone app (build_app.sh --standalone) and does the rest
#   laptop-app/notarize.sh --keep     # takes dist/standalone/Reachy Meetings.app as it is
#
# Needs on this Mac, once:
#   - the certificate "Developer ID Application: …" with its private key in the login keychain
#     (REACHY_SIGN_ID names it, default: the only Developer ID Application identity there);
#   - Apple's notary service signed in under the name reachy-notary (REACHY_NOTARY_PROFILE):
#       xcrun notarytool store-credentials reachy-notary --apple-id <Apple ID> --team-id <team>
#
# What it does: every program and library inside the app gets the Developer ID signature with the hardened runtime
# and Apple's timestamp (Apple refuses anything less), the app goes to the notary service and gets its ticket
# stapled on, then the same for the disk image. Result: dist/Reachy Meetings <version>.dmg

set -e
here=${0:A:h}
app="$here/dist/standalone/Reachy Meetings.app"
profile=${REACHY_NOTARY_PROFILE:-reachy-notary}
identity=${REACHY_SIGN_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' | head -1)}
[[ -n $identity ]] || { echo "No Developer ID Application certificate in the keychain"; exit 1 }

[[ $1 == --keep ]] || $here/build_app.sh --standalone
[[ -d $app ]] || { echo "No app at $app"; exit 1 }
version=$(<$here/VERSION)
work=$(mktemp -d)
trap "rm -rf $work" EXIT

# The app runs Python, which loads the backend's libraries from ~/Library/Application Support (fetched on the first
# start, signed by their makers or not at all) and makes code at run time (libffi, used by the window)
cat > $work/entitlements.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>com.apple.security.cs.disable-library-validation</key><true/>
  <key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>
  <key>com.apple.security.cs.allow-jit</key><true/>
  <key>com.apple.security.device.audio-input</key><true/>
  <key>com.apple.security.device.camera</key><true/>
</dict>
</plist>
PLIST

sign() { codesign --force --options runtime --timestamp --sign "$identity" "$@" }

echo "signing as $identity"
# inside first: libraries plain, programs with the entitlements; the app itself last
n=0
find $app -type f ! -path "$app/Contents/MacOS/*" -print0 | while IFS= read -r -d '' f; do
  kind=$(file -b "$f")
  [[ $kind == Mach-O* ]] || continue
  if [[ $kind == *executable* ]]; then sign --entitlements $work/entitlements.plist "$f" 2>/dev/null
  else sign "$f" 2>/dev/null; fi
  n=$((n + 1))
done
for f in $app/Contents/MacOS/*; do
  [[ ${f:t} == reachy-meetings ]] && continue
  sign --identifier ch.ethz.reachymeetings.laptop.${f:t} --entitlements $work/entitlements.plist "$f"
done
sign --entitlements $work/entitlements.plist "$app"
codesign --verify --deep --strict "$app"

notarize() {   # a file Apple can take (zip or dmg)
  xcrun notarytool submit "$1" --keychain-profile $profile --wait --output-format json > $work/result.json || true
  cat $work/result.json; echo
  grep -q '"status":"Accepted"' $work/result.json && return 0
  id=$(sed -n 's/.*"id":"\([^"]*\)".*/\1/p' $work/result.json)
  [[ -n $id ]] && xcrun notarytool log $id --keychain-profile $profile
  return 1
}

echo "notarizing the app (a few minutes)"
ditto -c -k --keepParent "$app" $work/app.zip
notarize $work/app.zip
xcrun stapler staple "$app"

dmg="$here/dist/Reachy Meetings $version.dmg"
mkdir $work/image
cp -R "$app" $work/image/
ln -s /Applications $work/image/Applications
rm -f "$dmg"
hdiutil create -quiet -volname "Reachy Meetings" -srcfolder $work/image -format UDZO "$dmg"
codesign --force --timestamp --sign "$identity" "$dmg"
echo "notarizing the disk image"
notarize "$dmg"
xcrun stapler staple "$dmg"
spctl --assess --type open --context context:primary-signature -v "$dmg"
echo "done: $dmg ($(du -h "$dmg" | cut -f1))"
