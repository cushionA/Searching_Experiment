set -eu
cd /mnt/c/Users/tatuk/Desktop/SearchEngine
export PATH=/home/zabuton/.local/share/searchengine-bot-diagnostics/node-v22.15.0/bin:/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/nss-tools/usr/bin:/usr/local/bin:/usr/bin:/bin
export DISPLAY=:0
export MOZ_ENABLE_WAYLAND=0
export BOT_DIAGNOSTICS_DEPS=/home/zabuton/.local/share/searchengine-bot-diagnostics/deps
export BOT_DIAGNOSTICS_CAMOUFOX_DEPS=/mnt/c/Users/tatuk/Documents/Codex/2026-10-03/new-chat-2/work/camoufox-deps
export BOT_DIAGNOSTICS_FOURPLAY_DEPS=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay
export BOT_DIAGNOSTICS_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/upstream/ext
export BOT_DIAGNOSTICS_CAMOUFOX_FOURPLAY_EXTENSION=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay-camoufox/ext
export BOT_DIAGNOSTICS_FIREFOX=/mnt/c/Users/tatuk/Desktop/SearchEngine/.deps/fourplay/firefox/firefox
export BOT_DIAGNOSTICS_EVIDENCE_ROOT=/tmp/searchengine-trio-evidence-20261007-0810
set +e
env -u HTTPS_PROXY -u https_proxy -u HTTP_PROXY -u http_proxy -u ALL_PROXY -u all_proxy -u NODE_OPTIONS node lab-runs/browser-trio-direct-20261007-0810/matrix.mjs "$@"
task_result=$?
set -e
if [ "$1" = matrix-run ]; then
  test ! -e lab-runs/browser-trio-direct-20261007-0810/run
  cp -a /tmp/searchengine-trio-evidence-20261007-0810/run lab-runs/browser-trio-direct-20261007-0810/run
fi
exit "$task_result"
