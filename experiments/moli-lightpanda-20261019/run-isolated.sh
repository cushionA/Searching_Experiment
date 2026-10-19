#!/bin/sh
set -eu
# Only this new public-input staging directory is exposed, read-only.
exec docker run --rm --network none --user 1000:1000 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --memory 8g --memory-swap 8g --cpus 2 --pids-limit 128 \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m,mode=1777 \
  --mount type=bind,src=/tmp/moli-lightpanda-20261019-stage,dst=/work,readonly \
  --workdir /work --env HOME=/tmp --env LIGHTPANDA_DISABLE_TELEMETRY=true \
  --entrypoint python python@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 \
  -I -B "$@"
