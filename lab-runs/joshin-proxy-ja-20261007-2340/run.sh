#!/usr/bin/env bash
set -eu
repo=/mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine
run_id=joshin-proxy-ja-20261007-2340
run_output=/home/zabuton/.local/share/searchengine-bot-diagnostics/$run_id
saved_output=lab-runs/$run_id
fingerprint_source=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-input-20261007-2215/live
proxy_expected=http://127.0.0.1:3148
cd "$repo"
export PATH=/home/zabuton/.local/share/searchengine-bot-diagnostics/node-v22.15.0/bin:/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/nss-tools/usr/bin:/usr/local/bin:/usr/bin:/bin
export MOZ_ENABLE_WAYLAND=0
export LIBGL_ALWAYS_SOFTWARE=1
export BOT_DIAGNOSTICS_DEPS=/home/zabuton/.local/share/searchengine-bot-diagnostics/deps
export BOT_DIAGNOSTICS_CAMOUFOX_DEPS=/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps
export BOT_DIAGNOSTICS_FOURPLAY_DEPS=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay
export BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/upstream/ext
export BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay-camoufox/ext
export BOT_DIAGNOSTICS_FIREFOX=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/firefox/firefox
export BOT_DIAGNOSTICS_XDOTOOL=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/input-tools/extracted/usr/bin/xdotool
export LD_LIBRARY_PATH=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/input-tools/extracted/usr/lib/x86_64-linux-gnu
export BOT_DIAGNOSTICS_SOURCE_COMMIT=308e049668e6e3f98ab21dc22f3675eeb79de998
export BOT_DIAGNOSTICS_STATE=$run_output/state
export BOT_DIAGNOSTICS_X11_CAPTURE=$repo/$saved_output/capture-x11.py
export BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM=$fingerprint_source
export TZ=Asia/Tokyo
unset NODE_OPTIONS WAYLAND_DISPLAY
mkdir -p "$run_output" "$saved_output/validation" "$run_output/locale"
if [ ! -d "$run_output/locale/ja_JP.UTF-8" ]; then
  localedef --no-archive -i ja_JP -f UTF-8 "$run_output/locale/ja_JP.UTF-8"
fi
export LOCPATH=$run_output/locale
export LC_ALL=ja_JP.UTF-8
export LANG=ja_JP.UTF-8

check_proxy() {
  local found=0 candidate
  for candidate in "${JSE_PROXY_URL:-}" "${HTTPS_PROXY:-}" "${https_proxy:-}" "${HTTP_PROXY:-}" "${http_proxy:-}"; do
    if [ -n "$candidate" ]; then
      if [ "$candidate" != "$proxy_expected" ]; then
        printf '%s\n' 'proxy endpoint mismatch; no run started' >&2
        return 1
      fi
      found=1
    fi
  done
  if [ "$found" -ne 1 ]; then
    printf '%s\n' 'approved proxy endpoint is not configured; no live run started' >&2
    return 1
  fi
}

run_one() {
  local mode=$1 driver_mode=$1 result=0 log="$saved_output/validation/$1.log"
  if [ -e "$run_output/$mode" ] || [ -e "$saved_output/$mode" ] || [ -e "$log" ] || \
    [ -e "$run_output/xvfb-$mode.log" ] || [ -e "$saved_output/validation/xvfb-$mode.log" ]; then
    printf '%s\n' "existing output preserved; refusing to overwrite $mode" >&2
    return 2
  fi
  node --check experiments/bot-diagnostics/joshin-input-diagnostics.mjs
  if [[ "$mode" == fixture-* ]]; then
    if env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy -u ALL_PROXY -u all_proxy -u JSE_PROXY_URL \
      xvfb-run -a -e "$run_output/xvfb-$mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' \
      node experiments/bot-diagnostics/joshin-input-diagnostics.mjs "$run_output/$mode" "--$driver_mode" >"$log" 2>&1; then
      result=0
    else
      result=$?
    fi
  else
    if HTTPS_PROXY="$proxy_expected" https_proxy="$proxy_expected" HTTP_PROXY="$proxy_expected" http_proxy="$proxy_expected" \
      xvfb-run -a -e "$run_output/xvfb-$mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' \
      node experiments/bot-diagnostics/joshin-input-diagnostics.mjs "$run_output/$mode" "--$driver_mode" >"$log" 2>&1; then
      result=0
    else
      result=$?
    fi
  fi
  printf '%s\n' "$result" >"$saved_output/validation/$mode.exit"
  if [ -d "$run_output/$mode" ]; then
    cp -a "$run_output/$mode" "$saved_output/"
  fi
  if [ -f "$run_output/xvfb-$mode.log" ]; then
    cp "$run_output/xvfb-$mode.log" "$saved_output/validation/"
  fi
  tail -n 3 "$log" || true
  return "$result"
}

run_group() {
  local group=$1 result=0 mode
  if [ "$group" = live ]; then check_proxy || return 2; fi
  if [ "$group" = fixture ]; then
    for mode in fixture-camoufox-ja fixture-fourplay-ja fixture-ja; do
      if ! run_one "$mode"; then result=1; fi
    done
  else
    for mode in live-camoufox-ja live-fourplay-ja live-dom-ja; do
      if ! run_one "$mode"; then result=1; fi
    done
  fi
  return "$result"
}

case "${1:-}" in
  fixture-camoufox-ja|fixture-fourplay-ja|fixture-ja|live-camoufox-ja|live-fourplay-ja|live-dom-ja)
    if [[ "$1" == live-* ]]; then check_proxy; fi
    run_one "$1"
    ;;
  fixtures) run_group fixture ;;
  live) run_group live ;;
  all)
    fixture_result=0
    run_group fixture || fixture_result=$?
    live_result=0
    run_group live || live_result=$?
    if [ "$fixture_result" -ne 0 ] || [ "$live_result" -ne 0 ]; then exit 1; fi
    ;;
  finalize) python3 -B "$saved_output/finalize.py" ;;
  *) exit 2 ;;
esac
