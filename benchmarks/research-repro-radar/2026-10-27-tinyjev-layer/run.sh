#!/bin/sh
set -eu
trial_dir=/tmp/tinyjev-oct27
evidence_dir=/tmp/radar-evening-oct27/benchmarks/research-repro-radar/2026-10-27-tinyjev-layer
docker run --rm --name tinyjev-oct27-layer-trial --user 1000:1000 \
 --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
 --memory 8g --cpus 2 --pids-limit 128 \
 --tmpfs /work:rw,size=3g,uid=1000,gid=1000,mode=0700 \
 --tmpfs /output:rw,size=16m,uid=1000,gid=1000,mode=0700 \
 --mount type=bind,src="$trial_dir/model",dst=/model,readonly \
 --mount type=bind,src="$trial_dir/source/tinyjev",dst=/src/tinyjev,readonly \
 --mount type=bind,src="$trial_dir/eval/inputs.json",dst=/eval/inputs.json,readonly \
 --mount type=bind,src="$trial_dir/eval/run_model.py",dst=/eval/run_model.py,readonly \
 --mount type=bind,src="$evidence_dir/bootstrap.py",dst=/eval/bootstrap.py,readonly \
 tinyjev-oct27-layer:local \
 python -B /eval/bootstrap.py > "$evidence_dir/execution.json" 2> "$evidence_dir/container-stderr.log"
