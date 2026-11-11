#!/bin/zsh
# Everything the laptop next to Reachy has to run for a meeting, with one command:
#   the Reachy daemon (Reachy Mini Control), the relay that lets the headset reach it, and the caption backend.
# Shows whether each part is up, and stops relay and backend again with Ctrl+C. Extra arguments go to the backend.
#
#   ./start_meeting.sh
#
# The headset only has to open the app. A headset that has never been used with this laptop needs its address and
# the secret once; the command for that is printed below.

cd ${0:A:h}
py=~/.venvs/reachy-backend/bin/python
logs=~/Library/Logs/reachy-meetings
secret_file=~/.config/reachy-meetings/lan_secret
mkdir -p $logs ${secret_file:h}
[[ -f $secret_file ]] || (umask 077; openssl rand -hex 16 > $secret_file)

daemon() { curl -s -m 2 localhost:8000/api/daemon/status }
field() { python3 -c "import sys, json; print(json.load(sys.stdin).get('$1') or '')" 2>/dev/null }

if [[ -z "$(daemon)" ]]; then
  echo "Starting Reachy Mini Control ..."
  open -a "Reachy Mini Control"
  for i in {1..40}; do [[ -n "$(daemon)" ]] && break; sleep 2; done
fi

python3 robot/scripts/daemon_lan_relay.py > $logs/relay.log 2>&1 &
relay=$!
# Models already on this laptop: no questions to Hugging Face at the start (an expired login there stops them loading)
[[ -d ~/.cache/huggingface/hub/models--microsoft--wavlm-base-plus-sv ]] && export HF_HUB_OFFLINE=1
(cd backend && REACHY_LAN_SECRET=$(<$secret_file) PYTHONPATH=src PYTHONUNBUFFERED=1 exec $py -m reachy_meetings_backend.captions --host 0.0.0.0 "$@") > $logs/backend.log 2>&1 &
backend=$!
trap 'kill $relay $backend 2>/dev/null; echo; echo "Stopped. Reachy Mini Control keeps running."; exit' INT TERM

mark() { [[ $1 == 1 ]] && print -n "  ok    " || print -n "  --    " }
while true; do
  ip=$(ipconfig getifaddr en0)
  status=$(daemon)
  state=$(print -r -- $status | field state)
  error=$(print -r -- $status | field error)
  video=0; nc -z localhost 8443 2>/dev/null && video=1
  back=0; nc -z localhost 8766 2>/dev/null && back=1
  heads=$(( $(grep -c ": connected" $logs/relay.log 2>/dev/null) - $(grep -c ": closed" $logs/relay.log 2>/dev/null) ))
  faces=$(grep -a "faces per frame" $logs/backend.log 2>/dev/null | tail -1 | sed -E 's/.*s, ([0-9.]+) faces per frame, brightness ([0-9]+).*/\1 faces in view, brightness \2 of 255/')
  clear
  echo "Reachy Mini Meetings     $(date +%H:%M:%S)     Ctrl+C stops"
  echo
  mark $(( ${+state} && ${#state} && 1 )) ; [[ $state == running ]] && echo "Robot: running" || echo "Robot: ${state:-Reachy Mini Control does not answer}  $error"
  mark $video; echo "Camera stream for the headset (port 8443)"
  mark $back;  echo "Captions, translation, faces (port 8766)${faces:+: $faces}"
  mark $(( heads > 0 )); echo "Headset connected to the robot${ip:+ (this laptop: $ip)}"
  kill -0 $backend 2>/dev/null || { echo; echo "  The backend stopped. Last lines of $logs/backend.log:"; tail -5 $logs/backend.log | cut -c1-160 }
  if (( heads <= 0 )); then
    echo
    echo "  New headset? Once, with the headset on adb:"
    echo "  adb shell am start -n ch.ethz.reachymeetings/com.unity3d.player.UnityPlayerGameActivity \\"
    echo "      -e signalling ws://$ip:8443 -e secret $(<$secret_file)"
  fi
  sleep 3
done
