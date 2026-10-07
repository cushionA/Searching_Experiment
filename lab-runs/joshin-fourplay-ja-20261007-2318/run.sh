set -eu
cd /mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine
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
export BOT_DIAGNOSTICS_STATE=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-fourplay-ja-20261007-2318/state
export BOT_DIAGNOSTICS_X11_CAPTURE=/mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine/lab-runs/joshin-fourplay-ja-20261007-2318/capture-x11.py
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NODE_OPTIONS BOT_DIAGNOSTICS_CA CODEX_PROXY_CERT WAYLAND_DISPLAY
run_output=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-fourplay-ja-20261007-2318
saved_output=lab-runs/joshin-fourplay-ja-20261007-2318
mkdir -p "$run_output" "$saved_output/validation"
case "${1:-}" in
  fixture-fourplay-ja|fixture-fourplay-ja-os|live-fourplay-ja)
    task_mode="$1"
    driver_mode="$task_mode"
    if [ "$task_mode" != fixture-fourplay-ja ]; then
      if [ ! -d "$run_output/locale/ja_JP.UTF-8" ]; then
        mkdir -p "$run_output/locale"
        localedef --no-archive -i ja_JP -f UTF-8 "$run_output/locale/ja_JP.UTF-8"
      fi
      export LOCPATH="$run_output/locale"
      export LC_ALL=ja_JP.UTF-8
      export LANG=ja_JP.UTF-8
    fi
    if [ "$task_mode" = fixture-fourplay-ja-os ]; then driver_mode=fixture-fourplay-ja; fi
    node --check experiments/bot-diagnostics/joshin-input-diagnostics.mjs
    set +e
    xvfb-run -a -e "$run_output/xvfb-$task_mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' node experiments/bot-diagnostics/joshin-input-diagnostics.mjs "$run_output/$task_mode" "--$driver_mode" > "$saved_output/validation/$task_mode.log" 2>&1
    task_result=$?
    set -e
    printf '%s\n' "$task_result" > "$saved_output/validation/$task_mode.exit"
    test ! -e "$saved_output/$task_mode"
    cp -a "$run_output/$task_mode" "$saved_output/"
    cp "$run_output/xvfb-$task_mode.log" "$saved_output/validation/"
    tail -n 3 "$saved_output/validation/$task_mode.log"
    exit "$task_result"
    ;;
  finalize)
    python3 -B "$saved_output/finalize.py"
    ;;
  *) exit 2 ;;
esac
