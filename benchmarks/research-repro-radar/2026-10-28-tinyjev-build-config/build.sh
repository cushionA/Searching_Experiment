#!/bin/sh
set -eu
# Existing artifacts only; do not download or reinstall.
context=/tmp/tinyjev-layer-context
[ -d "$context/deps" ]
[ -f "$context/Dockerfile" ]
[ -f /tmp/tinyjev-oct27/model/model.safetensors ]
[ -d /tmp/tinyjev-oct27/source/tinyjev ]
BUILDX_CONFIG=$(mktemp -d /tmp/tinyjev-buildx.XXXXXX)
export BUILDX_CONFIG
docker build --network none --pull=false --progress plain --tag tinyjev-oct27-layer:local "$context"
