# Daily technology review — 2026-10-12

Scope: public primary-source review and read-only repository/PR review. Public GitHub and Hugging Face source metadata plus official Crawl4AI docs were fetched; no target-site crawl, hosted API call, model inference, OCR, GPU, Kaggle, or new account/credential was used. The Docling CPU PoC used the official v2.15 release binary on a self-generated text-layer PDF; see `benchmarks/daily-tech-20261012/docling-probe-results.json` and `docling-first-byte-results.json`.

Collectors:

```sh
python3 -B experiments/daily-tech-20261012/collect_sources.py
python3 -B experiments/daily-tech-20261012/collect_model_sources.py
```

The saved snapshots are time-specific. `source-evidence.json` pins Docling source tag, code snapshots, SHA-256 and claim limits; `model-sources.json` and `model-card-evidence.json` store selected exact IDs/revisions and model-family query evidence. Run scripts make new API requests and therefore produce new timestamps/results.

## Reproduce the saved Docling CPU PoC

The binary and fixture were already used to generate the committed results. These commands reproduce that experiment if you intentionally want a new run (which will overwrite the result JSON):

```sh
mkdir -p /tmp/docling215
curl -L 'https://github.com/docling-project/docling.rs/releases/download/v2.15.0/docling-rs-v2.15.0-x86_64-unknown-linux-gnu.tar.gz' -o /tmp/docling-rs-v2.15.0-x86_64-unknown-linux-gnu.tar.gz
echo '9e309b913cd6a1e904eab22bf938c29ed9adea9656fa076db773ff95cb9e5185  /tmp/docling-rs-v2.15.0-x86_64-unknown-linux-gnu.tar.gz' | sha256sum -c -
tar -xzf /tmp/docling-rs-v2.15.0-x86_64-unknown-linux-gnu.tar.gz -C /tmp/docling215
python3 -B experiments/daily-tech-20261012/make_pdf_fixture.py
python3 -B experiments/daily-tech-20261012/run_docling_stream_probe.py
python3 -B experiments/daily-tech-20261012/run_docling_first_byte.py
```

The saved runs have already been independently checked without rerunning the conversion scripts with:

```sh
python3 -B experiments/daily-tech-20261012/verify_saved_probe.py
```
