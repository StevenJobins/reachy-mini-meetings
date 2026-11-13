#!/bin/zsh
# Makes an update for the standalone app: our code as a signed package, in laptop-app/dist/release.
#
#   laptop-app/release.sh 0.3.0
#
# then publish the three files it names as a GitHub release (it prints the command; it does not publish itself).
# The apps look at the project's latest release (reachy_meetings_app/updates.py), take the package if its version
# is newer than theirs and its signature is right, and use it from their next start.
#
# The key: ~/.config/reachy-meetings/release_key.pem, made on the first run. It is what makes a package ours: keep
# it (a copy somewhere safe) and keep it to yourself. Its public half, laptop-app/release_key.pub.pem, is in the
# repository and in every app. Without the key no installed app takes another update; with a new key, only apps
# built after the change do.
set -e
here=${0:A:h}
version=$1
key=~/.config/reachy-meetings/release_key.pem
out=$here/dist/release
[[ $version =~ '^[0-9]+(\.[0-9]+)*$' ]] || { echo "usage: release.sh <version>, numbers with dots, like 0.3.0"; exit 1 }

if [[ ! -f $key ]]; then
  mkdir -p ${key:h}
  (umask 077; openssl ecparam -name prime256v1 -genkey -noout -out $key)
  echo "made the release key $key: keep a copy somewhere safe"
fi
openssl ec -in $key -pubout 2>/dev/null > $here/release_key.pub.pem.new
if [[ -f $here/release_key.pub.pem ]] && ! cmp -s $here/release_key.pub.pem.new $here/release_key.pub.pem; then
  rm $here/release_key.pub.pem.new
  echo "The key on this Mac is not the one the apps trust (laptop-app/release_key.pub.pem): they would refuse this update."
  exit 1
fi
mv $here/release_key.pub.pem.new $here/release_key.pub.pem

stage=$(mktemp -d)
trap "rm -rf $stage" EXIT
$here/stage_code.sh $stage $version
rm -rf $out
mkdir -p $out
package=reachy-meetings-code-$version.tar.gz
COPYFILE_DISABLE=1 tar -czf $out/$package -C $stage laptop-app robot backend
openssl dgst -sha256 -sign $key -out $out/$package.sig $out/$package
print -r -- "{\"version\": \"$version\", \"file\": \"$package\"}" > $out/latest.json

echo "made $out/$package ($(du -h $out/$package | cut -f1))"
echo "To publish it (the apps then update themselves):"
echo "  gh release create v$version $out/latest.json $out/$package $out/$package.sig --title \"Reachy Meetings $version\" --notes \"…\""
