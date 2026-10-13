#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .deps/sku-nextdata-cheerio
cp experiments/sku-matching/au-description-node-package.json .deps/sku-nextdata-cheerio/package.json
cp experiments/sku-matching/au-description-node-package-lock.json .deps/sku-nextdata-cheerio/package-lock.json
npm ci --prefix .deps/sku-nextdata-cheerio --cache /tmp/sku-nextdata-npm-cache --ignore-scripts --no-audit --no-fund
