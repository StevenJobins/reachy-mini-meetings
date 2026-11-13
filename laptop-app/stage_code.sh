#!/bin/zsh
# Lays out our code the way the app carries it (Resources/code) and the way an update delivers it:
#   laptop-app/{reachy_meetings_app, requirements-backend.txt, release_key.pub.pem, VERSION}
#   robot/scripts/gateway.py      backend/src/reachy_meetings_backend
# Used by build_app.sh and release.sh, so the app and its updates cannot drift apart.
#   stage_code.sh <folder> [version]        version defaults to laptop-app/VERSION
set -e
here=${0:A:h}
repo=${here:h}
dest=$1
version=${2:-$(<$here/VERSION)}
[[ -n $dest ]] || { echo "usage: stage_code.sh <folder> [version]"; exit 1 }
[[ $version =~ '^[0-9]+(\.[0-9]+)*$' ]] || { echo "version must be numbers with dots, like 0.3.0 (got '$version')"; exit 1 }
mkdir -p $dest/laptop-app $dest/robot/scripts $dest/backend
cp -R $here/reachy_meetings_app $dest/laptop-app/
cp $here/requirements-backend.txt $here/release_key.pub.pem $dest/laptop-app/
print -r -- $version > $dest/laptop-app/VERSION
cp $repo/robot/scripts/gateway.py $dest/robot/scripts/
cp -R $repo/backend/src $dest/backend/src
find $dest -name __pycache__ -prune -exec rm -rf {} +
find $dest -name "*.egg-info" -prune -exec rm -rf {} +
find $dest -name ".DS_Store" -delete
