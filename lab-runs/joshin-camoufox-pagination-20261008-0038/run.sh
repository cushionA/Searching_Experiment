set -eu
cd /mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine
export PATH=/home/zabuton/.local/share/searchengine-bot-diagnostics/node-v22.15.0/bin:/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/nss-tools/usr/bin:/usr/local/bin:/usr/bin:/bin
export MOZ_ENABLE_WAYLAND=0
export LIBGL_ALWAYS_SOFTWARE=1
export BOT_DIAGNOSTICS_DEPS=/home/zabuton/.local/share/searchengine-bot-diagnostics/deps
export BOT_DIAGNOSTICS_CAMOUFOX_DEPS=/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps
export BOT_DIAGNOSTICS_FOURPLAY_DEPS=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay
export BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay-camoufox/ext
export BOT_DIAGNOSTICS_SOURCE_COMMIT=308e049668e6e3f98ab21dc22f3675eeb79de998
export BOT_DIAGNOSTICS_INPUT_FINGERPRINT_FROM=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-input-20261007-2215/live
export TZ=Asia/Tokyo
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy JSE_PROXY_URL NO_PROXY no_proxy NODE_OPTIONS BOT_DIAGNOSTICS_CA CODEX_PROXY_CERT WAYLAND_DISPLAY
run_output=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-camoufox-pagination-20261008-0038
saved_output=lab-runs/joshin-camoufox-pagination-20261008-0038
export BOT_DIAGNOSTICS_STATE=$run_output/state
mkdir -p "$run_output" "$saved_output/validation"
case "${1:-}" in
  fixture-complete|fixture-denied|fixture-no-progress|fixture-duplicate|fixture-complete-recheck|fixture-denied-recheck|fixture-no-progress-recheck|fixture-duplicate-recheck)
    task_mode="$1"
    scenario="${task_mode#fixture-}"
    fixture_script=fixture.mjs
    if [[ "$task_mode" == *-recheck ]]; then scenario="${scenario%-recheck}"; fixture_script=fixture-recheck.mjs; fi
    test ! -e "$run_output/$task_mode"
    test ! -e "$saved_output/$task_mode"
    test ! -e "$saved_output/$task_mode-server"
    node "$saved_output/$fixture_script" "$saved_output/$task_mode-server" "$scenario" > "$saved_output/validation/$task_mode-server.log" 2>&1 &
    task_server_pid=$!
    trap 'kill "$task_server_pid" 2>/dev/null || true' EXIT
    for attempt in $(seq 1 100); do
      if [ -f "$saved_output/$task_mode-server/ready.json" ]; then break; fi
      if ! kill -0 "$task_server_pid" 2>/dev/null; then exit 1; fi
      sleep .05
    done
    export BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL=$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")).homepage_url)' "$saved_output/$task_mode-server/ready.json")
    node --check experiments/bot-diagnostics/joshin-product-search.mjs
    set +e
    xvfb-run -a -e "$run_output/xvfb-$task_mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' node experiments/bot-diagnostics/joshin-product-search.mjs "$run_output/$task_mode" camoufox-ja-fixture > "$saved_output/validation/$task_mode.log" 2>&1
    task_result=$?
    set -e
    kill "$task_server_pid"
    wait "$task_server_pid" || true
    trap - EXIT
    printf '%s\n' "$task_result" > "$saved_output/validation/$task_mode.exit"
    cp -a "$run_output/$task_mode" "$saved_output/"
    cp "$run_output/xvfb-$task_mode.log" "$saved_output/validation/"
    tail -n 5 "$saved_output/validation/$task_mode.log"
    exit "$task_result"
    ;;
  live|live-recheck)
    task_mode="$1"
    test ! -e "$run_output/$task_mode"
    test ! -e "$saved_output/$task_mode"
    unset BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL
    node --check experiments/bot-diagnostics/joshin-product-search.mjs
    set +e
    xvfb-run -a -e "$run_output/xvfb-$task_mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' node experiments/bot-diagnostics/joshin-product-search.mjs "$run_output/$task_mode" camoufox-ja > "$saved_output/validation/$task_mode.log" 2>&1
    task_result=$?
    set -e
    printf '%s\n' "$task_result" > "$saved_output/validation/$task_mode.exit"
    cp -a "$run_output/$task_mode" "$saved_output/"
    cp "$run_output/xvfb-$task_mode.log" "$saved_output/validation/"
    tail -n 5 "$saved_output/validation/$task_mode.log"
    exit "$task_result"
    ;;
  tests)
    node --test experiments/bot-diagnostics/joshin-product-extraction.test.mjs > "$saved_output/validation/extraction-tests.log" 2>&1
    python3 -B -m unittest discover -s tests -p 'test_lab*.py' > "$saved_output/validation/lab-tests.log" 2>&1
    tail -n 7 "$saved_output/validation/extraction-tests.log"
    tail -n 4 "$saved_output/validation/lab-tests.log"
    ;;
  fixtures)
    for fixture_case in fixture-complete fixture-denied fixture-no-progress fixture-duplicate; do
      bash "$saved_output/run.sh" "$fixture_case"
    done
    ;;
  fixtures-recheck)
    for fixture_case in fixture-complete-recheck fixture-denied-recheck fixture-no-progress-recheck fixture-duplicate-recheck; do
      bash "$saved_output/run.sh" "$fixture_case"
    done
    ;;
  finalize) python3 -B "$saved_output/finalize.py" ;;
  *) exit 2 ;;
esac
