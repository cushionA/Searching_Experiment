"""Bounded authenticated verification; prints no authentication data."""
import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import zipfile

spec = importlib.util.spec_from_file_location('kaggle_ops', '/workspace/Searching_Experiment/.agents/skills/kaggle-ops/scripts/kaggle_ops.py')
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)

parser = argparse.ArgumentParser()
parser.add_argument('--ref', required=True)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--expected-bundle', type=Path)
parser.add_argument('--download-dir', type=Path)
args = parser.parse_args()
ops.kernel_ref(args.ref)
client = ops.api()
from kagglesdk.datasets.types.dataset_api_service import ApiGetDatasetMetadataRequest
request = ApiGetDatasetMetadataRequest()
request.owner_slug, request.dataset_slug = args.ref.split('/')
def metadata():
    with client.build_kaggle_client() as sdk:
        return sdk.datasets.dataset_api_client.get_dataset_metadata(request)
response = ops.read_retry(metadata)
if response.error_message:
    raise ValueError('dataset metadata response error')
info = response.info
status = ops.read_retry(lambda: client.dataset_status(args.ref))
datasets = ops.read_retry(lambda: client.dataset_list(mine=True, search=request.dataset_slug, page=1))
matches = [d for d in datasets if d.ref == args.ref]
if len(matches) != 1:
    raise ValueError('expected exactly one owned dataset')
version = matches[0].current_version_number
files = ops.read_retry(lambda: client.dataset_list_files(args.ref + '/' + str(version), page_size=20))
if files.error_message or files.next_page_token:
    raise ValueError('unexpected remote files response')
record = {'observed_at': datetime.now(timezone.utc).isoformat(), 'ref': args.ref,
          'status': status, 'private': info.is_private, 'version': version,
          'files': [{'name': f.name, 'bytes': f.total_bytes} for f in files.files],
          'quota_after': ops.quota(client)}
if args.expected_bundle is not None:
    expected = args.expected_bundle.stat().st_size
    if record['files'] != [{'name': args.expected_bundle.name, 'bytes': expected}]:
        raise ValueError('remote artifact name/byte mismatch')
    record['expected_remote_bundle_bytes_verified'] = True
if status != 'ready' or info.is_private is not True or version != 1:
    ops.save(args.output, record)
    raise ValueError('dataset not ready/private/version1')
if args.download_dir is not None:
    if args.expected_bundle is None:
        raise ValueError('download verification requires expected bundle')
    args.download_dir.mkdir(exist_ok=False)
    ops.read_retry(lambda: client.dataset_download_file(args.ref + '/' + str(version),
                                                        args.expected_bundle.name,
                                                        path=str(args.download_dir), quiet=True))
    downloads = [p for p in args.download_dir.iterdir() if p.is_file()]
    if len(downloads) != 1:
        raise ValueError('expected exactly one downloaded dataset file')
    digest = hashlib.sha256()
    count = 0
    downloaded = downloads[0]
    if downloaded.name == args.expected_bundle.name:
        with downloaded.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
                count += len(block)
    else:
        with zipfile.ZipFile(downloaded) as z:
            if z.namelist() != [args.expected_bundle.name]:
                raise ValueError('downloaded archive contains unexpected files')
            with z.open(args.expected_bundle.name) as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
                    count += len(block)
    local_sha = hashlib.sha256(args.expected_bundle.read_bytes()).hexdigest()
    if digest.hexdigest() != local_sha or count != args.expected_bundle.stat().st_size:
        raise ValueError('downloaded bundle SHA/bytes mismatch')
    record['downloaded_fixed_version'] = version
    record['downloaded_bundle_sha256'] = digest.hexdigest()
    record['downloaded_bundle_bytes'] = count
    record['downloaded_bundle_sha256_verified'] = True
ops.save(args.output, record)
print(json.dumps(record, ensure_ascii=False))
