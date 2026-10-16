#!/bin/sh
set -eu
out=benchmarks/daily-tech-20261017
for model in yolo26n yolo11n; do
  timeout 180 docker run --rm --network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges --user 10001:10001 --cpus=1 --memory=1g --memory-swap=1g --pids-limit=32 --tmpfs /tmp:rw,noexec,nosuid,size=16m research-yolo17 "$model" > "$out/$model.json" 2> "$out/$model.stderr"
done
