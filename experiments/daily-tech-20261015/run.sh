#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
# No host mounts, credentials, network, model weights, or writable root filesystem.
image="${IMAGE:-$(docker image inspect python:3.12-slim --format '{{index .RepoDigests 0}}')}"
printf '%s\n' "$image" > benchmarks/daily-tech-20261015/image.txt
tmp="$(mktemp -d)"
trap 'rm -r "$tmp"' EXIT
cp experiments/daily-tech-20261015/probe.py "$tmp/"
cp benchmarks/daily-tech-20261015/crawl4ai-scraping.py.txt "$tmp/"
set +e
tar -C "$tmp" -cf - probe.py crawl4ai-scraping.py.txt | timeout 30 docker run --rm -i --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges --cpus 1 --memory 256m --memory-swap 256m --pids-limit 32 --tmpfs /input:rw,noexec,nosuid,size=16m,mode=1777 "$image" sh -c 'tar -xf - -C /input && python -B /input/probe.py' > benchmarks/daily-tech-20261015/probe.json 2> benchmarks/daily-tech-20261015/probe.stderr
exit_code="$?"
set -e
printf '%s\n' "$exit_code" > benchmarks/daily-tech-20261015/probe.exit
[[ "$exit_code" == 0 ]]
