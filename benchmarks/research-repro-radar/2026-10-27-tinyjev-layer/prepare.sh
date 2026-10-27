#!/bin/sh
set -eu
base=python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
evidence_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p /tmp/tinyjev-layer-context
timeout 1200 docker run --rm --name tinyjev-layer-prepare --user 1000:1000 --network none --read-only --cap-drop ALL --security-opt no-new-privileges --memory 8g --cpus 2 --pids-limit 128 --ulimit cpu=1200:1200 --tmpfs /work:rw,size=3g,uid=1000,gid=1000,mode=0700 --mount type=bind,src=/tmp/tinyjev-oct27/wheels,dst=/wheels,readonly --mount type=bind,src="$evidence_dir/prepare.py",dst=/prepare.py,readonly "$base" python -B /prepare.py > /tmp/tinyjev-layer-context.tar 2> "$evidence_dir/prepare.log"
tar -xf /tmp/tinyjev-layer-context.tar -C /tmp/tinyjev-layer-context
cp "$evidence_dir/Dockerfile" /tmp/tinyjev-layer-context/Dockerfile
docker build --network none --pull=false --progress plain --tag tinyjev-oct27-layer:local --iidfile "$evidence_dir/image-id.txt" /tmp/tinyjev-layer-context > "$evidence_dir/build.log" 2>&1
