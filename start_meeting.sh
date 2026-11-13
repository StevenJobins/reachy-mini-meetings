#!/bin/zsh
# Everything the laptop next to Reachy has to run for a meeting, with one command:
#   the Reachy daemon (Reachy Mini Control), the caption backend, and the gateway: the one door (port 8790) through
#   which a headset reaches both, open only with that headset's token (robot/scripts/gateway.py).
# Shows whether each part is up, and stops gateway and backend again with Ctrl+C. Extra arguments go to the backend.
#
#   ./start_meeting.sh            # headsets on the same Wi-Fi
#   ./start_meeting.sh --remote   # also headsets on other networks (at home): a Cloudflare quick tunnel gives this
#                                 # laptop a public address and posts it for the paired headsets (gateway.py)
#
# The headset only has to open the app. A headset that has never been used with this laptop asks for the six-digit
# code shown below, once (it finds the laptop on the Wi-Fi by itself and gets its token for the code).

cd ${0:A:h}
py=~/.venvs/reachy-backend/bin/python
logs=~/Library/Logs/reachy-meetings
mkdir -p $logs

daemon() { curl -s -m 2 localhost:8000/api/daemon/status }
field() { python3 -c "import sys, json; print(json.load(sys.stdin).get('$1') or '')" 2>/dev/null }

if [[ -z "$(daemon)" ]]; then
  echo "Starting Reachy Mini Control ..."
  open -a "Reachy Mini Control"
  for i in {1..40}; do [[ -n "$(daemon)" ]] && break; sleep 2; done
fi

remote=()
if (( ${@[(I)--remote]} )); then remote=(--remote); set -- ${@:#--remote}; fi
python3 -u robot/scripts/gateway.py $remote > $logs/gateway.log 2>&1 &
gateway=$!
# Models already on this laptop: no questions to Hugging Face at the start (an expired login there stops them loading)
[[ -d ~/.cache/huggingface/hub/models--microsoft--wavlm-base-plus-sv ]] && export HF_HUB_OFFLINE=1
# The backend stays on 127.0.0.1 (its default): the headset reaches it through the gateway
(cd backend && PYTHONPATH=src PYTHONUNBUFFERED=1 exec $py -m reachy_meetings_backend.captions "$@") > $logs/backend.log 2>&1 &
backend=$!
trap 'kill $gateway $backend 2>/dev/null; echo; echo "Stopped. Reachy Mini Control keeps running."; exit' INT TERM

mark() { [[ $1 == 1 ]] && print -n "  ok    " || print -n "  --    " }
while true; do
  ip=$(ipconfig getifaddr en0)
  daemon_status=$(daemon)
  state=$(print -r -- $daemon_status | field state)
  error=$(print -r -- $daemon_status | field error)
  video=0; nc -z localhost 8443 2>/dev/null && video=1
  back=0; nc -z localhost 8766 2>/dev/null && back=1
  door=0; kill -0 $gateway 2>/dev/null && nc -z localhost 8790 2>/dev/null && door=1
  heads=$(( $(grep -c ": connected" $logs/gateway.log 2>/dev/null) - $(grep -c ": closed" $logs/gateway.log 2>/dev/null) ))
  known=$(python3 robot/scripts/gateway.py list 2>/dev/null | grep -vc "^no headsets yet")
  code=$(python3 robot/scripts/gateway.py code 2>/dev/null | grep -E '^[0-9]{3} [0-9]{3}$')
  far=$(python3 robot/scripts/gateway.py remote 2>/dev/null)
  refused=$(grep -c ": refused" $logs/gateway.log 2>/dev/null)
  faces=$(grep -a "faces per frame" $logs/backend.log 2>/dev/null | tail -1 | sed -E 's/.*s, ([0-9.]+) faces per frame, brightness ([0-9]+).*/\1 faces in view, brightness \2 of 255/')
  clear
  echo "Reachy Mini Meetings     $(date +%H:%M:%S)     Ctrl+C stops"
  echo
  mark $(( ${+state} && ${#state} && 1 )) ; [[ $state == running ]] && echo "Robot: running" || echo "Robot: ${state:-Reachy Mini Control does not answer}  $error"
  mark $video; echo "Camera stream for the headset (port 8443)"
  mark $back;  echo "Captions, translation, faces (port 8766)${faces:+: $faces}"
  mark $door;  echo "Door for headsets (port 8790${ip:+, this laptop: $ip}): $known headset(s) known"
  if (( $#remote )); then
    [[ $far == off || -z $far ]] && { mark 0; echo "From other networks: not yet ($(grep -a '^remote:' $logs/gateway.log | tail -1 | cut -c9-90))" } \
                                 || { mark 1; echo "From other networks: open ($far)" }
  fi
  mark $(( heads > 0 )); echo "Headset connected"
  kill -0 $backend 2>/dev/null || { echo; echo "  The backend stopped. Last lines of $logs/backend.log:"; tail -5 $logs/backend.log | cut -c1-160 }
  kill -0 $gateway 2>/dev/null || { echo; echo "  The gateway stopped. Last lines of $logs/gateway.log:"; tail -5 $logs/gateway.log | cut -c1-160 }
  if (( heads <= 0 )); then
    echo
    (( refused > 0 )) && echo "  $refused request(s) without a known token were turned away (see $logs/gateway.log)."
  fi
  if [[ -n $code ]]; then
    echo
    echo "  New headset? Open Reachy Meetings in it and enter this code:   $code"
    (( $#remote )) && echo "  (on this Wi-Fi, once; after that it also connects from other networks)"
  fi
  sleep 3
done
