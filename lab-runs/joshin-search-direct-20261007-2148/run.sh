set -eu
cd /mnt/c/Users/tatuk/.codex/worktrees/joshin-direct-search/SearchEngine
export PATH=/home/zabuton/.local/share/searchengine-bot-diagnostics/node-v22.15.0/bin:/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/nss-tools/usr/bin:/usr/local/bin:/usr/bin:/bin
export DISPLAY=:0
export MOZ_ENABLE_WAYLAND=0
export BOT_DIAGNOSTICS_DEPS=/home/zabuton/.local/share/searchengine-bot-diagnostics/deps
export BOT_DIAGNOSTICS_CAMOUFOX_DEPS=/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps
export BOT_DIAGNOSTICS_FOURPLAY_DEPS=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay
export BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/upstream/ext
export BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay-camoufox/ext
export BOT_DIAGNOSTICS_FIREFOX=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/firefox/firefox
export BOT_DIAGNOSTICS_SOURCE_COMMIT=308e049668e6e3f98ab21dc22f3675eeb79de998
export BOT_DIAGNOSTICS_STATE=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-direct-20261007-2148/state
unset HTTPS_PROXY https_proxy HTTP_PROXY http_proxy ALL_PROXY all_proxy NODE_OPTIONS BOT_DIAGNOSTICS_CA CODEX_PROXY_CERT
run_output=/home/zabuton/.local/share/searchengine-bot-diagnostics/joshin-direct-20261007-2148
saved_output=lab-runs/joshin-search-direct-20261007-2148
mkdir -p "$run_output" "$saved_output/validation"
case "${1:-}" in
  tests)
    node --test experiments/bot-diagnostics/fourplay-tab-navigation.test.mjs experiments/bot-diagnostics/fourplay-native-runtime.test.mjs experiments/bot-diagnostics/fourplay-bridge-gate.test.mjs experiments/bot-diagnostics/camoufox-fourplay-runtime.test.mjs experiments/bot-diagnostics/camoufox-runtime.test.mjs experiments/bot-diagnostics/joshin-product-extraction.test.mjs > "$saved_output/validation/node-tests.log" 2>&1
    python3 -B -m unittest discover -s tests -p 'test_lab*.py' > "$saved_output/validation/lab-tests.log" 2>&1
    tail -n 10 "$saved_output/validation/node-tests.log"
    tail -n 5 "$saved_output/validation/lab-tests.log"
    ;;
  fixtures)
    node experiments/bot-diagnostics/fourplay-navigation-fixture.mjs "$run_output/fixture-4play" --native > "$saved_output/validation/fixture-4play.log" 2>&1
    node experiments/bot-diagnostics/fourplay-navigation-fixture.mjs "$run_output/fixture-assembled" --hybrid > "$saved_output/validation/fixture-assembled.log" 2>&1
    cp -a "$run_output/fixture-4play" "$run_output/fixture-assembled" "$saved_output/"
    tail -n 2 "$saved_output/validation/fixture-4play.log"
    tail -n 2 "$saved_output/validation/fixture-assembled.log"
    ;;
  search)
    for browser_mode in camoufox standard assembled; do
      set +e
      node experiments/bot-diagnostics/joshin-product-search.mjs "$run_output/$browser_mode" "$browser_mode" 6000 > "$saved_output/validation/search-$browser_mode.log" 2>&1
      browser_result=$?
      set -e
      printf '%s\n' "$browser_result" > "$saved_output/validation/search-$browser_mode.exit"
      test ! -e "$saved_output/$browser_mode"
      cp -a "$run_output/$browser_mode" "$saved_output/"
      tail -n 1 "$saved_output/validation/search-$browser_mode.log"
    done
    ;;
  *) exit 2 ;;
esac
