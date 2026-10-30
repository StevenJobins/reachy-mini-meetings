#!/bin/zsh
# Caption backend for the Unity app on the same Wi-Fi: room sound, captions, faces.
# Run it in Terminal.app the first time, so macOS can ask for microphone and camera access.
# The secret keeps others on the Wi-Fi out; the app learns it once (xr-client/unity/README.md).
secret_file=~/.config/reachy-meetings/lan_secret
if [[ ! -f $secret_file ]]; then
  mkdir -p ${secret_file:h}
  (umask 077; openssl rand -hex 16 > $secret_file)
fi
REACHY_LAN_SECRET=$(<$secret_file) exec ~/.venvs/reachy-backend/bin/reachy-captions --host 0.0.0.0 "$@"
