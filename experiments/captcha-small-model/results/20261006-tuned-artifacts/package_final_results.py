"""Allowlisted private result bundle; keeps original evidence unchanged."""
import hashlib
import json
import os
from pathlib import Path
import zipfile

base = Path('/workspace/work/captcha-tuning-20261006')
repo = Path('/workspace/Searching_Experiment')
exp = repo / 'experiments/captcha-small-model'
stage = base / 'final-results-upload'
stage.mkdir(exist_ok=False)
entries = {}

def add_tree(source, prefix):
    source = Path(source)
    if not source.is_dir():
        raise ValueError('required result directory missing: ' + str(source))
    for path in sorted(source.rglob('*')):
        if path.is_symlink():
            raise ValueError('symlink is not an allowed artifact')
        if path.is_file():
            add_file(path, str(Path(prefix) / path.relative_to(source)))

def add_file(path, arcname):
    path = Path(path)
    if arcname in entries or Path(arcname).is_absolute() or '..' in Path(arcname).parts:
        raise ValueError('duplicate/unsafe archive name')
    if any(p.startswith('.') for p in Path(arcname).parts):
        raise ValueError('hidden file is not an allowed artifact')
    entries[arcname] = path

records = ['20261006-paddle-ocr', '20261006-ocr-alternatives', '20261006-aster-ocr',
           '20261006-image-candidates', '20261006-image-restoration',
           '20261006-crnn-finetune', '20261006-image-eval-finetune']
for name in records:
    add_tree(exp / 'results' / name, 'records/' + name)
for name in ('exported-crnn', 'exported-image-head'):
    add_tree(base / name, 'runtime/' + name)
add_tree(base / 'ocr-job-003/artifacts/crnn-finetune-result', 'models/crnn-epoch37-and-resume')
for model in ('s3', 's4'):
    add_tree(base / ('image-selected-head-mobileclip2_' + model), 'models/mobileclip2-' + model + '-head')
for path in sorted(exp.iterdir()):
    if path.is_file() and (path.suffix == '.py' or path.name.startswith('requirements') or path.name == 'README.md'):
        add_file(path, 'code/captcha-small-model/' + path.name)
for path in sorted((repo / 'tests').glob('test_lab*.py')):
    add_file(path, 'code/tests/' + path.name)
for name in ('evidence.md', 'round2-source-verification.md'):
    add_file(Path('/workspace/work/model-candidates-round2') / name, 'research/' + name)
for name in ('MobileCLIP2-S3.features.safetensors', 'MobileCLIP2-S3.features.index.json'):
    add_file(base / 'image-job-001/artifacts/public-eval-result-mobileclip2_s3' / name,
             'features/mobileclip2-s3/' + name)
receipts = ['ocr-cpu-cross-runtime-audit.json', 'ocr-audit-job003-receipt.json',
            'image-final-audit-001.json', 'image-strong-selection.json',
            'image-dataset-ready.json', 'ocr-dataset-ready.json',
            'image-input-receipt.json', 'ocr-input-receipt.json',
            'validation-tests-final.log']
for name in receipts:
    add_file(base / name, 'verification/' + name)

readme = '''公開CAPTCHAのモデル比較・上位候補の学習結果（2026-10-06）

学習したモデルはCRNNとMobileCLIP2-S3/S4だけです。
英数字OCR: 選択CRNN epoch37 201/214=93.93%、common_old178/214=83.18%。
数字OCR: 未調整PP-OCRv6 Small372/400=93.00%、Medium380/400=95.00%。
           学習CRNN366/400=91.50%なので数字はPP-OCRv6を維持します。
画像分類: S3 raw/headとも811/815=99.51%。
画像ボード: S3 raw79/203=38.92% → head135/203=66.50%。
S4 head130/203=64.04%。選択はvalidation同率と小さいcheckpointに基づくS3です。
画像エンコーダーは固定し、線形ヘッドのみ学習しました。
両ヘッドはmax1000epochで終わっており完全収束の確認はしていません。

これらは以前のモデル選びにも使った公開コーパスの内部保留データです。
新規外部テストでも実サービスの認証通過率でもありません。
橋のtestボードは0件なので精度は未測定です。

runtime/exported-crnn/: 約14.3MBのONNX、Torch不要の画像読取CLI、
                       batch1/32の全614件回答一致検証と依存一覧。
runtime/exported-image-head/: S3画像エンコーダーと保存ヘッドを組み合わせる
                             オフライン読取CLIとfeatureからの再計算確認。
models/: 選択重み、S4比較ヘッド、学習設定、CTC学習を再開する状態。
features/mobileclip2-s3/: 固定S3特徴と対応画像SHA。再学習・監査に利用可能。
records/: 全モデルの生予測、固定分割、出典、コードSHA、失敗を含む試行記録。
code/: 評価・学習・エクスポートコードの現在のスナップショット。

画像S3は元encoder約997MBが別途必要です。小さいheadだけで画像を読めません。
入力画像・初期CRNN9ファイルは以下のprivate Dataset version1に保存しています。
  superbigzabuton/captcha-ocr-ft-input-20261006
  superbigzabuton/captcha-image-ft-input-20261006
全5エンコーダーのGPU予測・特徴はNotebook version1の出力にも保存しています。
  superbigzabuton/captcha-image-candidates-20261006
元GPUジョブは古いheadコードでerrorですが、全encoderは成功し、44出力を回収・
SHA監査済みです。修正後のhead学習は上位S3/S4だけ成功しました。
OCR選択checkpointの元Notebookはcaptcha-ocr-ft-extended-20261006 version2です。

EfficientFormer/MobileOne/YOLO/EfficientDet/RRTrN等は未実行・未測定の候補です。
ASTERは実測が低かったため学習していません。詳細は各records/*/REPORT.md。
出典ライセンス記録を継承しています。各モデル・入力の条件を確認して使用してください。

このcaptcha-tuned-results.binは拡張子を.binにした通常のZIPです。
保存元のraw evidenceは変更していません。展開例:
  python -m zipfile -e captcha-tuned-results.bin restored-results
MANIFEST.sha256は自身以外の全ファイルのSHA-256です。
認証情報は含めていません。Kaggle所有者のアカウントで取得できます。
'''
generated = {'README.txt': readme.encode('utf-8')}
manifest = []
secret_values = [os.environ.get(k, '').encode() for k in
                 ('KAGGLE_API_TOKEN', 'KAGGLE_KEY', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN')]
secret_values = [v for v in secret_values if len(v) >= 8]
for name, path in sorted(entries.items()):
    raw = path.read_bytes()
    if any(v in raw for v in secret_values):
        raise ValueError('credential bytes detected in allowlisted artifact')
    if path.name.lower() in ('kaggle.json', '.env'):
        raise ValueError('authentication file is not an allowed artifact')
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
for name, raw in generated.items():
    manifest.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
generated['MANIFEST.sha256'] = ''.join(row['sha256'] + '  ' + row['path'] + '\n'
                                     for row in sorted(manifest, key=lambda x: x['path'])).encode()
bundle = stage / 'captcha-tuned-results.bin'
with zipfile.ZipFile(bundle, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as z:
    for name, path in sorted(entries.items()):
        z.write(path, name)
    for name, raw in sorted(generated.items()):
        z.writestr(name, raw)
with zipfile.ZipFile(bundle) as z:
    for row in manifest:
        raw = z.read(row['path'])
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('archive verification failed')
    if z.testzip() is not None:
        raise ValueError('archive CRC failure')
metadata = {'title': 'Captcha Tuned Results 20261006',
            'id': 'superbigzabuton/captcha-tuned-results-20261006',
            'licenses': [{'name': 'other'}]}
(stage / 'dataset-metadata.json').write_text(json.dumps(metadata, indent=2) + '\n')
receipt = {'dataset_ref': metadata['id'], 'private_requested': True, 'bundle': bundle.name,
           'bundle_bytes': bundle.stat().st_size,
           'bundle_sha256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
           'files': len(manifest) + 1, 'uncompressed_bytes': sum(r['bytes'] for r in manifest),
           'sha256_manifest_sha256': hashlib.sha256(generated['MANIFEST.sha256']).hexdigest(),
           'all_archive_members_reverified': True, 'auth_value_scan_passed': True}
(base / 'final-results-input-receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps(receipt))
