set -eu
cd /mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine
export PATH=/home/zabuton/.local/share/searchengine-bot-diagnostics/node-v22.15.0/bin:/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/nss-tools/usr/bin:/usr/local/bin:/usr/bin:/bin
export LIBGL_ALWAYS_SOFTWARE=1
export BOT_DIAGNOSTICS_DEPS=/home/zabuton/.local/share/searchengine-bot-diagnostics/deps
export BOT_DIAGNOSTICS_CHROMIUM=$BOT_DIAGNOSTICS_DEPS/chromium
export BOT_DIAGNOSTICS_CAMOUFOX_DEPS=/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps
export BOT_DIAGNOSTICS_SOURCE_COMMIT=308e049668e6e3f98ab21dc22f3675eeb79de998
export TZ=Asia/Tokyo
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy JSE_PROXY_URL NO_PROXY no_proxy NODE_OPTIONS BOT_DIAGNOSTICS_CA CODEX_PROXY_CERT WAYLAND_DISPLAY
run_output=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-patchright-smoke-20261008-0116
saved_output=lab-runs/joshin-patchright-smoke-20261008-0116
export BOT_DIAGNOSTICS_STATE=$run_output/state
mkdir -p "$run_output" "$saved_output/validation"
case "${1:-}" in
  fixture-ja-complete|fixture-ja-denied|fixture-en-complete)
    task_mode="$1"
    scenario=complete
    browser_mode=patchright-ja-fixture
    if [ "$task_mode" = fixture-ja-denied ]; then scenario=denied; fi
    if [ "$task_mode" = fixture-en-complete ]; then browser_mode=patchright-en-fixture; fi
    test ! -e "$run_output/$task_mode"
    test ! -e "$saved_output/$task_mode"
    test ! -e "$saved_output/$task_mode-server"
    node "$saved_output/fixture.mjs" "$saved_output/$task_mode-server" "$scenario" > "$saved_output/validation/$task_mode-server.log" 2>&1 &
    task_server_pid=$!
    trap 'kill "$task_server_pid" 2>/dev/null || true' EXIT
    for attempt in $(seq 1 100); do
      if [ -f "$saved_output/$task_mode-server/ready.json" ]; then break; fi
      if ! kill -0 "$task_server_pid" 2>/dev/null; then exit 1; fi
      sleep .05
    done
    export BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL=$(node -e 'process.stdout.write(JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")).homepage_url)' "$saved_output/$task_mode-server/ready.json")
    ;;
  live-ja|live-en)
    task_mode="$1"
    browser_mode=patchright-ja
    if [ "$task_mode" = live-en ]; then browser_mode=patchright-en; fi
    test ! -e "$run_output/$task_mode"
    test ! -e "$saved_output/$task_mode"
    unset BOT_DIAGNOSTICS_JOSHIN_FIXTURE_URL
    ;;
  fixtures)
    for task_case in fixture-ja-complete fixture-ja-denied fixture-en-complete; do bash "$saved_output/run.sh" "$task_case"; done
    exit 0
    ;;
  live)
    for task_case in live-ja live-en; do bash "$saved_output/run.sh" "$task_case"; done
    exit 0
    ;;
  tests)
    node --test experiments/bot-diagnostics/joshin-product-extraction.test.mjs > "$saved_output/validation/extraction-tests.log" 2>&1
    python3 -B -m unittest discover -s tests -p 'test_lab*.py' > "$saved_output/validation/lab-tests.log" 2>&1
    tail -n 7 "$saved_output/validation/extraction-tests.log"
    tail -n 4 "$saved_output/validation/lab-tests.log"
    exit 0
    ;;
  finalize) python3 -B "$saved_output/finalize.py"; exit 0 ;;
  *) exit 2 ;;
esac
node --check experiments/bot-diagnostics/joshin-product-search.mjs
set +e
xvfb-run -a -e "$run_output/xvfb-$task_mode.log" --server-args='-screen 0 1440x1000x24 -nolisten tcp' node experiments/bot-diagnostics/joshin-product-search.mjs "$run_output/$task_mode" "$browser_mode" > "$saved_output/validation/$task_mode.log" 2>&1
task_result=$?
set -e
if [ -n "${task_server_pid:-}" ]; then
  kill "$task_server_pid"
  wait "$task_server_pid" || true
  trap - EXIT
fi
printf '%s\n' "$task_result" > "$saved_output/validation/$task_mode.exit"
cp -a "$run_output/$task_mode" "$saved_output/"
cp "$run_output/xvfb-$task_mode.log" "$saved_output/validation/"
tail -n 5 "$saved_output/validation/$task_mode.log"
exit "$task_result"
