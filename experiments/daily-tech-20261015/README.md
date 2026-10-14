# 2026-10-15 morning research

- `collect.py`: primary-source snapshots (GitHub reads use existing gh authorization; nothing is passed to containers).
- `supplement.py`: fixed tag/model and prior repository handoff evidence. Run once after collect.
- `probe.py`: AST-selects the exact Crawl4AI v0.9.5 helper and three regex constants. Twelve explicit synthetic expectations and three bounded timing inputs. No full-package import or model inference.
- `run.sh`: official Python Docker digest, offline nonroot read-only container; input-only tmpfs and stdin tar, no host mounts. `docker pull python:3.12-slim` is the preparation step; recorded digest is in benchmarks.
- `validate.py`: evidence hashes, probe result consistency, syntax, and local report links; does not rerun candidate code.

From repository root: `python3 -B experiments/daily-tech-20261015/collect.py`, then `python3 -B experiments/daily-tech-20261015/supplement.py`, then `bash experiments/daily-tech-20261015/run.sh` (Docker required). Historical reruns should use recorded source snapshots and image digest rather than refresh mutable discovery metadata. For a different machine, pull the recorded digest and run with `IMAGE=python@sha256:... bash experiments/daily-tech-20261015/run.sh`.

Upstream source snapshots are unmodified. Crawl4AI source/derived AST execution is under the saved Apache-2.0 LICENSE (`benchmarks/daily-tech-20261015/crawl4ai-license.txt`); original project: https://github.com/unclecode/crawl4ai. New harness is separately authored here. The extraction is a limited function test, not a claim of complete scraper integration.

Initial Docker copy failed with `Error response from daemon: container rootfs is marked read-only` (exit 1); successful reproduction uses tmpfs/stdin without relaxing root read-only. No build, extra permissions, API inference, or GPU.
