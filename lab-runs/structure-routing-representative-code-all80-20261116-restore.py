import argparse
import hashlib
import json
from pathlib import Path
import zipfile


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("structure-routing-representative-code-all80-20261116-parts.json"))
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    directory = args.manifest.resolve().parent
    archive = directory / manifest["archive_file"]
    if archive.parent != directory:
        raise ValueError("archive path outside manifest directory")
    for part in manifest["parts"]:
        source = directory / part["file"]
        if source.parent != directory or source.stat().st_size != part["bytes"] or digest(source) != part["sha256"]:
            raise ValueError("invalid checkpoint part")
    if not archive.exists():
        with archive.open("xb") as output:
            for part in manifest["parts"]:
                with (directory / part["file"]).open("rb") as source:
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        output.write(block)
    if archive.stat().st_size != manifest["archive_bytes"] or digest(archive) != manifest["archive_sha256"]:
        raise ValueError("archive hash mismatch")
    if args.extract_to:
        destination = args.extract_to.resolve()
        with zipfile.ZipFile(archive) as saved:
            for name in saved.namelist():
                if not (destination / name).resolve().is_relative_to(destination):
                    raise ValueError("archive path outside extraction directory")
            saved.extractall(destination)
    print(json.dumps({"ok": True, "archive": str(archive), "sha256": manifest["archive_sha256"],
                      "extract_to": str(args.extract_to) if args.extract_to else None}))


if __name__ == "__main__":
    main()
