"""Build a copy-and-replace Windows ZIP and a public source export.

The default package contains the six verified modified data files, with the
game's folder layout, and documentation. No installer is needed. An optional
xdelta package remains available with --format delta or --format both.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0-beta"
PUBLIC_ROOT_FILES = ("README.md", "CHANGELOG.md", "CREDITOS.md", "LICENSE",
                     ".gitignore", "requirements.txt")
PUBLIC_TOOLS = (
    "unity_localization.py", "catalog_tool.py", "extract.py", "check_translation.py",
    "build_es.py", "verify_build.py", "ui_extension.py", "scene_ui_patch.py",
    "audit_preloads.py", "audit_game_ui.py", "audit_quality.py",
    "install.ps1", "restore.ps1", "prepare_sources.py", "package_release.py",
    "test_preload_registration.py", "test_scene_ui_patch.py", "test_ui_extension.py",
    "test_prepare_sources.py", "test_release_package.py",
)
PUBLIC_DOCS = ("DISTRIBUCION.md", "addressables_catalog.md")
BUNDLES = (
    "localization-string-tables-english(en)_assets_all.bundle",
    "localization-locales_assets_all.bundle",
    "localization-assets-shared_assets_all.bundle",
)
AA_PREFIX = "Cavern of Dreams_Data/StreamingAssets/aa/"
TARGETS = tuple(AA_PREFIX + "StandaloneWindows64/" + n for n in BUNDLES) + (
    "Cavern of Dreams_Data/level0", "Cavern of Dreams_Data/level1", AA_PREFIX + "catalog.json")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(args, **kwargs):
    env = {k: v for k, v in os.environ.items() if k.upper() != "XDELTA"}
    result = subprocess.run([str(a) for a in args], env=env, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}\n"
                           + result.stdout.decode("utf-8", errors="replace")
                           + result.stderr.decode("utf-8", errors="replace"))
    return result


def fetch_pinned(item, cache):
    """Download to a temporary name and accept only the pinned bytes."""
    target = cache / item["cache_name"]
    if target.exists():
        if sha(target) != item["sha256"]:
            raise ValueError(f"Cached upstream file changed: {target}")
        return target
    request = urllib.request.Request(item["url"], headers={"User-Agent": "CavernOfDreams-ES-release"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        raise ValueError(f"Upstream checksum mismatch: {item['url']}")
    target.write_bytes(content)
    return target


def prepare_vendor(root, package, cache):
    lock_path = root / "distribution/vendor.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    archive = fetch_pinned(lock["archive"], cache)
    tool = package / "bin/xdelta3.exe"
    tool.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive) as zipped:
        tool.write_bytes(zipped.read(lock["exe_member"]))
    if sha(tool) != lock["exe_sha256"]:
        raise ValueError("Unexpected xdelta executable in verified archive")
    notices = package / "licencias"
    notices.mkdir()
    for item in lock["notices"]:
        shutil.copyfile(fetch_pinned(item, cache), notices / item["output_name"])
    shutil.copyfile(lock_path, notices / "vendor.lock.json")
    (notices / "LEEME.txt").write_text(
        "Herramienta de terceros sin modificar: xdelta3 3.2.2, Joshua MacDonald.\n"
        "https://github.com/jmacd/xdelta/releases/tag/v3.2.2\n"
        "Licencia Apache-2.0. El binario oficial incluye liblzma (XZ Utils 5.8.3,\n"
        "0BSD) y BLAKE3 1.8.5 (Apache-2.0 o CC0). Se adjuntan los avisos\n"
        "y textos de licencia correspondientes. vendor.lock.json registra las\n"
        "fuentes exactas y sus SHA-256. El juego no forma parte del paquete.\n",
        encoding="utf-8-sig")
    shutil.copyfile(root / "LICENSE", notices / "PROYECTO-MIT.txt")
    shutil.copyfile(root / "CREDITOS.md", package / "CREDITOS.md")
    return tool, lock


def public_files(root):
    files = [Path(p) for p in PUBLIC_ROOT_FILES]
    files += [Path("tools") / p for p in PUBLIC_TOOLS]
    files += [Path("docs") / p for p in PUBLIC_DOCS]
    files += [p.relative_to(root) for p in sorted((root / "translation").glob("*.json"))]
    files += [Path("distribution") / p for p in (
        "compatibility.json", "vendor.lock.json", "LEEME.txt", "LEEME-delta.txt", "RELEASE_NOTES.md")]
    files += [Path("distribution/runtime") / p for p in (
        "patch.ps1", "Instalar traduccion.cmd", "Desinstalar traduccion.cmd")]
    files += [Path(".github/ISSUE_TEMPLATE/traduccion.yml")]
    for relative in files:
        if not (root / relative).is_file():
            raise ValueError(f"Missing public source: {relative}")
    return files


def zip_tree(directory, destination):
    """Stable ZIP metadata; no symlinks, system paths, backups or extra files."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Symlink in release: {path}")
            if path.is_file():
                info = zipfile.ZipInfo(path.relative_to(directory).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                zipped.writestr(info, path.read_bytes(), compresslevel=9)


def output_for(game_relative):
    if game_relative.endswith("/level0") or game_relative.endswith("/level1"):
        return Path("scenes") / Path(game_relative).name
    return Path(game_relative).name


def make_manual_package(root, stage, version, sources):
    """Stage only the fixed six destinations and informational documents."""
    manifest = {"schema_version": 1, "version": version, "format": "copy-replace",
                "steam_build": str(sources["steam_build"]), "files": []}
    for relative in TARGETS:
        source = root / "build/out" / output_for(relative)
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        expected = sha(source)
        if sha(destination) != expected:
            raise ValueError(f"Manual package copy differs: {relative}")
        manifest["files"].append({"path": relative, "sha256": expected,
                                  "size": source.stat().st_size})
    info = stage / "Traduccion-ES"
    info.mkdir()
    for source, name in (("distribution/LEEME.txt", "LEEME.txt"),
                         ("distribution/RELEASE_NOTES.md", "RELEASE_NOTES.md"),
                         ("CREDITOS.md", "CREDITOS.md")):
        shutil.copyfile(root / source, info / name)
    write_json(info / "manifest.json", manifest)


def build(root=ROOT, version=VERSION, verify=True, package_format="manual"):
    root = Path(root).resolve()
    if os.name != "nt":
        raise ValueError("Package generation currently requires Windows x64")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9.-]+)?", version):
        raise ValueError("Invalid release version")
    if package_format not in {"manual", "delta", "both"}:
        raise ValueError("Invalid package format")
    sources = json.loads((root / "distribution/compatibility.json").read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in sources["files"]}
    if len(rows) != 6 or set(rows) != set(TARGETS):
        raise ValueError("Expected exactly the six supported game files")
    for row in rows.values():
        source = (root / row["source"]).resolve()
        if not source.is_relative_to(root / "orig"):
            raise ValueError("Original source outside orig/")
        if sha(source) != row["sha256"] or source.stat().st_size != row["size"]:
            raise ValueError(f"Original source is incompatible: {row['source']}")
    if verify:
        for script in ("check_translation.py", "verify_build.py"):
            result = run([sys.executable, root / "tools" / script], cwd=root)
            print(result.stdout.decode("utf-8", errors="replace").rstrip())
    allowlist = public_files(root)
    cache = root / "build/vendor"
    cache.mkdir(parents=True, exist_ok=True)
    release = root / "patches" / f"v{version}"
    release.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="release-", dir=root / "build") as temporary:
        work = Path(temporary)
        artifacts = []
        if package_format in {"manual", "both"}:
            manual = work / "windows-manual"
            make_manual_package(root, manual, version, sources)
            windows_zip = release / f"Cavern-of-Dreams-ES-v{version}-windows.zip"
            zip_tree(manual, windows_zip)
            artifacts.append(windows_zip)
        if package_format in {"delta", "both"}:
            stage = work / "windows-delta"
            package = stage / "CavernOfDreams-ES"
            package.mkdir(parents=True)
            for name in ("Instalar traduccion.cmd", "Desinstalar traduccion.cmd"):
                shutil.copyfile(root / "distribution/runtime" / name, stage / name)
            shutil.copyfile(root / "distribution/LEEME-delta.txt", stage / "LEEME.txt")
            shutil.copyfile(root / "distribution/RELEASE_NOTES.md", stage / "RELEASE_NOTES.md")
            shutil.copyfile(root / "distribution/runtime/patch.ps1", package / "patch.ps1")
            tool, lock = prepare_vendor(root, package, cache)
            manifest = {"schema_version": 1, "version": version,
                        "steam_build": str(sources["steam_build"]),
                        "tool": {"path": "bin/xdelta3.exe", "sha256": sha(tool)}, "files": []}
            (package / "deltas").mkdir()
            for index, path in enumerate(TARGETS):
                row = rows[path]
                source = root / row["source"]
                target = root / "build/out" / output_for(path)
                delta_relative = f"deltas/{index:02d}.xdelta"
                delta = package / delta_relative
                # No filenames/application headers; never auto-decompress source inputs.
                run([tool, "-e", "-a", "-A", "-D", "-R", "-s", source, target, delta])
                decoded = work / f"decoded-{index}"
                run([tool, "-d", "-D", "-R", "-s", source, delta, decoded])
                if sha(decoded) != sha(target):
                    raise ValueError(f"Delta reconstruction differs: {path}")
                manifest["files"].append({
                    "path": path, "patch": delta_relative,
                    "source_sha256": row["sha256"], "target_sha256": sha(target),
                    "patch_sha256": sha(delta), "source_size": row["size"],
                    "target_size": target.stat().st_size,
                })
                print(f"delta {Path(path).name}: {delta.stat().st_size:,} bytes")
            write_json(package / "manifest.json", manifest)
            windows_zip = release / f"Cavern-of-Dreams-ES-v{version}-windows-delta.zip"
            zip_tree(stage, windows_zip)
            artifacts.append(windows_zip)
        public = root / "build/public-repo"
        # An explicit allowlist avoids leaking generated assets or local history.
        # Refuse unrelated extras instead of removing somebody else's files.
        allowed = {p.as_posix() for p in allowlist}
        if public.exists():
            extra = [p for p in public.rglob("*") if p.is_file()
                     and p.relative_to(public).as_posix() not in allowed]
            if extra:
                raise ValueError(f"Unexpected files in public export; inspect: {extra[0]}")
        for relative in allowlist:
            destination = public / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, destination)
        source_zip = release / f"Cavern-of-Dreams-ES-v{version}-fuentes.zip"
        zip_tree(public, source_zip)
        shutil.copyfile(root / "distribution/RELEASE_NOTES.md", release / "RELEASE_NOTES.md")
        (release / "SHA256SUMS.txt").write_text(
            "".join(f"{sha(path)}  {path.name}\n" for path in artifacts + [source_zip]), encoding="ascii")
        print(f"Release: {release}\nPublic source export: {public}")
        return artifacts[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--format", choices=("manual", "delta", "both"), default="manual")
    args = parser.parse_args()
    try:
        build(version=args.version, package_format=args.format)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, f"Release failed: {exc}\n")


if __name__ == "__main__":
    main()
