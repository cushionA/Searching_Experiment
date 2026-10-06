#!/bin/sh
set -eu
profile=/tmp/fourplay-profile
mkdir -p "$profile"
cp -R /run/profile-seed/. "$profile/"
if [ ! -f "$profile/cert9.db" ]; then
    certutil -N -f /dev/null -d "sql:$profile" --empty-password
fi
certutil -A -f /dev/null -d "sql:$profile" -n managed-cloud-proxy -t 'C,,' -i /run/proxy-ca.pem
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/tmp/fourplay-display.log 2>&1 &
display_pid=$!
firefox_pid=''
server_pid=''
trap 'kill "$display_pid" ${firefox_pid:+"$firefox_pid"} ${server_pid:+"$server_pid"} 2>/dev/null || true' EXIT INT TERM
sleep 1
firefox-esr --no-remote --profile "$profile" --width 1280 --height 720 about:blank >/tmp/fourplay-firefox.log 2>&1 &
firefox_pid=$!
node /app/server.cjs &
server_pid=$!
wait "$server_pid"
