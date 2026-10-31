# Supplied-HTML extraction preflight (2026-11-01 JST)

Purpose: compare newly found `trafilaturacore==0.8.11` with
`trafilatura==2.3.1`, fast mode, for text retention and preprocessing time.
This is not a crawling or search ranking trial. The baseline has previously
been release-audited; this pair has not previously been benchmarked here.

`fixtures.json` was fixed before execution: 2 languages × 6 layouts, 36
required paragraphs and 24 navigation/footer markers. Both arms exclude comments,
retain tables, and produce HTML. Text is normalized for substring checks.
No web fetches, models, private data, browser, or SKU inputs are used.
One first call and 20 warm calls per case are planned, with all outputs saved.
The core arm includes metadata/sanitization work, so this is end-to-end API cost,
not equivalent internal-kernel timing. Small synthetic pages are a preflight,
not evidence of web coverage or production Japanese extraction quality.

## Status: not executed

The dependency wheels were downloaded binary-only and statically unpacked.
No `.pth` or escaping member paths were found. Entry point, extraction pipeline,
metadata calls and process/network/dynamic-code references were inspected, not
formally security-audited. No candidate imports ran on the host. Public wheel
hashes/versions are recorded in `benchmarks/daily-tech-20261101/wheels.json`.

BuildKit could not write `/home/agent/.docker/buildx`. One allowed repair used
`DOCKER_BUILDKIT=0` with the same managed socket/configuration. The image contains
only wheels and these two input files; Dockerfile has no RUN instruction.
Its base digest and image ID are in `execution.json`.

Nonroot execution then failed because the copied `/app/bench.py` is root-owned
mode 600. Read-only diagnostics confirmed this. The repair budget is exhausted;
no chmod, rebuild, root execution, alternate environment or baseline trial followed.
The dependencies contain files created under the restrictive host umask too;
their readability must be checked before any future authorized repair.

Actual run command, using the managed local socket and preserving Docker config:

```sh
timeout 180 docker --host=unix:///var/run/docker.sock run --rm \
  --name morning-core-preflight --network none --read-only --user 65534:65534 \
  --cap-drop ALL --security-opt no-new-privileges --memory 8g --cpus 2 \
  --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m \
  morning-core:20261101 core
```

Docker endpoint/context/TLS selectors were cleared for that command. Do not treat
this recipe as a passing run. Additional repair needs authorization under this run's
limit. A future run must preserve the source/input hashes and explicit nonroot,
read-only, network-none restrictions; changing the environment must not reset the limit.

Offline evidence check (no candidate imports):

```sh
python3 -B experiments/trafilaturacore-cpu/verify.py
```
