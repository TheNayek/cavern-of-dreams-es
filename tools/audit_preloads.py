"""Audit cloned assets' preload dependencies without changing any bundles.

Each clone must retain its source asset's dependency slice, replacing the source
asset's own PPtr with the clone's. UnityPy round-trip alone cannot test this.
Exit 1 means missing dependencies or an audit error.
"""

import argparse
import json
from pathlib import Path

import UnityPy

import unity_localization as ul
from ui_extension import original_object_preserved


ROOT = Path(__file__).resolve().parents[1]
BUNDLES = (
    "localization-string-tables-english(en)_assets_all.bundle",
    "localization-locales_assets_all.bundle",
)


def pointer(value):
    return value["m_FileID"], value["m_PathID"]


def preload_slice(bundle, info):
    start, size = info["preloadIndex"], info["preloadSize"]
    if start < 0 or size < 0 or start + size > len(bundle["m_PreloadTable"]):
        raise ValueError("invalid preload range")
    return [pointer(p) for p in bundle["m_PreloadTable"][start:start + size]]


def missing_dependencies(source_deps, source_asset, clone_asset, clone_deps):
    expected = {clone_asset if p == source_asset else p for p in source_deps}
    return sorted(expected - set(clone_deps))


def inspect_bundle(reference, candidate):
    def read(path):
        env = UnityPy.load(str(path))
        sf = next(f for f in env.file.files.values()
                  if f.__class__.__name__ == "SerializedFile")
        ab = next(o for o in sf.objects.values() if o.type.name == "AssetBundle")
        return sf, ab.read_typetree()

    source_sf, source_ab = read(reference)
    clone_sf, clone_ab = read(candidate)
    originals = dict(source_ab["m_Container"])
    clones = dict(clone_ab["m_Container"])
    findings = []
    for path, info in clones.items():
        if path in originals:
            continue
        if path.endswith("_es.asset"):
            source_path = path[:-len("_es.asset")] + "_en.asset"
        elif path == "Assets/Text/Spanish (es).asset":
            source_path = "Assets/Text/English (en).asset"
        else:
            raise ValueError("unknown clone: " + path)
        source_info = originals[source_path]
        src_ptr, dst_ptr = pointer(source_info["asset"]), pointer(info["asset"])
        if src_ptr[0] or dst_ptr[0]:
            raise ValueError("expected local container assets")
        source_obj = source_sf.objects[src_ptr[1]]
        clone_obj = clone_sf.objects[dst_ptr[1]]
        _, source_data, _ = ul.load_mono(source_obj)
        _, clone_data, _ = ul.load_mono(clone_obj)
        source_deps = preload_slice(source_ab, source_info)
        clone_deps = preload_slice(clone_ab, info)
        expected = [dst_ptr if p == src_ptr else p for p in source_deps]
        findings.append({
            "bundle": candidate.name,
            "asset": path,
            "source_preload_size": len(source_deps),
            "clone_preload_size": len(clone_deps),
            "missing": missing_dependencies(source_deps, src_ptr, dst_ptr, clone_deps),
            "preload_matches_source": clone_deps == expected,
            "script_pointer_preserved": source_data["m_Script"] == clone_data["m_Script"],
            "script_in_clone_preload": pointer(clone_data["m_Script"]) in clone_deps,
            "shared_data_in_clone_preload": (
                pointer(clone_data["m_SharedData"]) in clone_deps
                if "m_SharedData" in clone_data else None
            ),
        })
    unchanged = all(
        pid in clone_sf.objects
        and original_object_preserved(obj, clone_sf.objects[pid])
        for pid, obj in source_sf.objects.items() if obj.type.name != "AssetBundle"
    )
    return findings, unchanged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, default=ROOT / "build" / "out")
    parser.add_argument("--reference-dir", type=Path,
                        default=ROOT / "orig" / "aa" / "StandaloneWindows64")
    parser.add_argument("--json", type=Path, help="write diagnostic JSON only")
    args = parser.parse_args()
    findings, unchanged = [], {}
    for name in BUNDLES:
        rows, intact = inspect_bundle(args.reference_dir / name, args.bundle_dir / name)
        findings.extend(rows)
        unchanged[name] = intact
    if len(findings) != 36:
        raise ValueError("expected 35 cloned StringTables + 1 cloned Locale")
    failures = [row for row in findings if not row["preload_matches_source"]
                or not row["script_pointer_preserved"]
                or not row["script_in_clone_preload"]
                or row["shared_data_in_clone_preload"] is False]
    for row in failures:
        print("INVALID %s: source=%d clone=%d missing=%s" % (
            row["asset"], row["source_preload_size"], row["clone_preload_size"], row["missing"]))
    print("Clones with invalid preload dependencies: %d/%d" % (len(failures), len(findings)))
    print("Original objects preserved (UI_en allows declared additive rows):", unchanged)
    report = {"findings": findings, "original_objects_unchanged": unchanged}
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if failures or not all(unchanged.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
