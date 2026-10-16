# YOLO26n CPU follow-up — STOPPED

2026-10-17 morning research. See the report for results. Inferences: 0.

`collect.py` downloads public metadata, official ONNX binaries and fixed-commit demo images to `/tmp/yolo17-context`. `requirements.txt` and `benchmarks/daily-tech-20261017/wheels.json` preserve all binary wheel versions/hashes. `probe.py` is our proposed CPU-only measurement code; it never ran successfully.

Historical sequence: collect; `pip download --only-binary=:all: -r requirements.txt --require-hashes --dest /tmp/yolo17-context/wheels`; copy Dockerfile/probe.py into that context; Docker build with network disabled and a fresh empty process-local BUILDX_CONFIG; `run.sh`.

**Do not rerun automatically.** Initial UID10001 file read failed. One repair changed COPY to chmod644; `/app` then lacked directory search permission. Both attempts exited2. Repair budget exhausted. Dockerfile intentionally preserves the failed state; it is not a working recipe. No further fix or candidate execution is authorized within this run. Read-only diagnostics are saved separately. No model/image binaries or private data are committed.

Planned method: 2 public images, fixed640 bilinear letterbox, warmup5/timed30 each, CPU/thread1, optional class-aware NMS0.7/confidence0.25, separate model processes. No annotated quality benchmark. An output with detections would not establish real-world accuracy.
