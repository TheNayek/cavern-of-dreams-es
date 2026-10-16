"""Prepare private pristine inputs from an owned, supported game installation.

No game files are modified. Existing backups are verified and never overwritten.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]


def contained(root, relative):
    """Reject absolute paths, traversal and symlinks leading outside the root."""
    if not isinstance(relative, str) or "\\" in relative or ":" in relative:
        raise ValueError(f"Unsafe relative path: {relative!r}")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or not parts.parts or any(p in (".", "..") for p in relative.split("/")):
        raise ValueError(f"Unsafe relative path: {relative!r}")
    path = root.joinpath(*parts.parts)
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes root: {relative}")
    return path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matches(path, entry):
    return path.is_file() and path.stat().st_size == entry["size"] and digest(path) == entry["sha256"]


def load_manifest(root):
    manifest = json.loads((root / "distribution" / "compatibility.json").read_text(encoding="utf-8"))
    entries = manifest.get("files", [])
    if manifest.get("schema_version") != 1 or len(entries) != 6:
        raise ValueError("Unsupported compatibility metadata (expected schema 1 and six files)")
    sources, paths = set(), set()
    for entry in entries:
        source, path = entry["source"], entry["path"]
        contained(root, source)
        if not source.startswith("orig/") or source in sources or path in paths:
            raise ValueError("Invalid or duplicate compatibility source/path")
        if not isinstance(entry["size"], int) or isinstance(entry["size"], bool) or entry["size"] < 0:
            raise ValueError("Invalid file size in compatibility metadata")
        if not isinstance(entry["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Invalid SHA-256 in compatibility metadata")
        sources.add(source)
        paths.add(path)
    return entries


def prepare(game, root=ROOT):
    root = Path(root).resolve()
    game = Path(game).resolve()
    if not root.is_dir():
        raise ValueError(f"Repository root does not exist: {root}")
    entries = load_manifest(root)
    destinations = [contained(root, entry["source"]) for entry in entries]
    inputs = [contained(game, entry["path"]) for entry in entries]
    hashes_path = contained(root, "orig/scenes/hashes.json")
    for entry, destination in zip(entries, destinations):
        if destination.exists() and not matches(destination, entry):
            raise ValueError(f"Existing pristine backup differs: {destination}. Preserve it and use a separate clean source checkout.")
    complete = all(destination.exists() for destination in destinations)
    if not complete:
        # Validate the whole installation before creating even one backup.
        for entry, source in zip(entries, inputs):
            if not matches(source, entry):
                raise ValueError(f"Unsupported or modified game file: {source}. Restore Steam build 13947382 originals before preparing a clean checkout.")
    copied = 0
    for entry, source, destination in zip(entries, inputs, destinations):
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also protects originals created by another process.
        with destination.open("xb") as output:
            try:
                with source.open("rb") as original:
                    shutil.copyfileobj(original, output)
            except BaseException:
                output.close()
                destination.unlink()
                raise
        if not matches(destination, entry):
            destination.unlink()
            raise ValueError(f"Source changed during copy: {source}; retry with unmodified originals.")
        copied += 1
    if not hashes_path.exists():
        scenes = [{"Path": str(destination), "Hash": entry["sha256"].upper()}
                  for entry, destination in zip(entries, destinations)
                  if entry["source"].startswith("orig/scenes/")]
        hashes_path.parent.mkdir(parents=True, exist_ok=True)
        with hashes_path.open("x", encoding="utf-8") as output:
            json.dump(scenes, output, indent=2)
            output.write("\n")
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", required=True, type=Path, help="Owned Cavern of Dreams installation folder")
    parser.add_argument("--root", type=Path, default=ROOT, help="Source checkout (default: script parent)")
    args = parser.parse_args()
    try:
        copied = prepare(args.game, args.root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Cannot prepare sources: {exc}", file=sys.stderr)
        return 1
    print(f"Verified six pristine sources; copied {copied}. Existing backups preserved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
