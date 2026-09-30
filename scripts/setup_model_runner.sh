#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Optional, independently authenticated subprocess. Never copy the parent task's auth cache.
command -v npm >/dev/null || { echo 'Node.js/npm is required for the Codex CLI runner' >&2; exit 2; }
npm install --prefix .deps/codex --no-audit --no-fund --save-exact @openai/codex@0.156.1
.deps/codex/node_modules/.bin/codex --version
echo 'Installed only. Configure independent CLI authentication and agent-phase network access before use.'
