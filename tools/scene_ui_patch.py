"""Build native scene UI bindings without serializing generated Mono typetrees.

Only native GameObjects are serialized, after a byte-exact round trip. Mono
objects retain their original bytes except explicitly checked fixed-size edits.
Generated Mono typetrees are used solely to inspect source and output metadata.
"""

import copy
import hashlib
import math
import struct
from pathlib import Path

import UnityPy
from UnityPy.enums import ClassIDType
from UnityPy.files import ObjectReader
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

import unity_localization as ul

UI_GUID = "GUID:a8cbcdd732998c74296158fd9a0b36a8"
TMP_CLASSES = {"TextMeshProUGUI", "TextMeshPro"}
FONT_FORMAT = "<ffiB3xff"
FONT_FIELDS = ("m_fontSize", "m_fontSizeBase", "m_fontWeight",
               "m_enableAutoSizing", "m_fontSizeMin", "m_fontSizeMax")
SPECIAL_LAYOUT = {51045: (14, 16), 51057: (18, 26),
                  51145: (14, 16), 51144: (20, 28)}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pp(path_id, file_id=0):
    return {"m_FileID": file_id, "m_PathID": path_id}


def _pack_pp(pointer):
    return struct.pack("<iq", pointer["m_FileID"], pointer["m_PathID"])


def _unique(raw, pattern, label):
    first = raw.find(pattern)
    if first < 0 or raw.find(pattern, first + 1) >= 0:
        raise ValueError(f"{label}: expected exactly one byte-pattern occurrence")
    return first


def _only_ranges(before, after, ranges):
    if len(before) != len(after):
        raise ValueError("fixed-size patch changed object length")
    allowed = bytearray(len(before))
    for offset, size in ranges:
        if offset < 0 or offset + size > len(before):
            raise ValueError("patch outside object")
        allowed[offset:offset + size] = b"\1" * size
    if any(a != b and not allowed[i] for i, (a, b) in enumerate(zip(before, after))):
        raise ValueError("patch changed bytes outside allowed fields")


def _calls(data):
    return data["m_UpdateString"]["m_PersistentCalls"]["m_Calls"]


def _check_template(data):
    ref = data["m_StringReference"]
    calls = _calls(data)
    if (ref["m_TableReference"]["m_TableCollectionName"] != UI_GUID
            or ref["m_TableEntryReference"]["m_Key"] != ""
            or not ref["m_TableEntryReference"]["m_KeyId"]
            or data["m_Enabled"] != 1
            or len(calls) != 1 or calls[0]["m_MethodName"] != "set_text"
            or calls[0]["m_Target"]["m_FileID"] != 0
            or calls[0]["m_Mode"] != 0 or calls[0]["m_CallState"] != 1):
        raise ValueError("unsupported UI LocalizeStringEvent template")


def _patch_binding(raw, data, go_id, text_id, key_id):
    """Clone bytes; preserve script pointer, registry, GUID and event arguments."""
    _check_template(data)
    if raw[:12] != _pack_pp(data["m_GameObject"]):
        raise ValueError("LocalizeStringEvent GameObject header mismatch")
    if raw[16:28] != _pack_pp(data["m_Script"]):
        raise ValueError("LocalizeStringEvent script header mismatch")
    key = data["m_StringReference"]["m_TableEntryReference"]["m_KeyId"]
    key_offset = _unique(raw, struct.pack("<q", key), "UI key ID")
    target_offset = _unique(raw, _pack_pp(_calls(data)[0]["m_Target"]), "event target")
    ranges = [(0, 12), (key_offset, 8), (target_offset, 12)]
    if key_offset < 28 or target_offset < 28:
        raise ValueError("binding field overlaps MonoBehaviour header")
    spans = [set(range(o, o + n)) for o, n in ranges]
    if any(spans[i] & spans[j] for i in range(len(spans)) for j in range(i)):
        raise ValueError("binding field byte patterns overlap")
    result = bytearray(raw)
    result[:12] = _pack_pp(_pp(go_id))
    result[key_offset:key_offset + 8] = struct.pack("<q", key_id)
    result[target_offset:target_offset + 12] = _pack_pp(_pp(text_id))
    result = bytes(result)
    _only_ranges(raw, result, ranges)
    return result, ranges


def _patch_layout(raw, data, minimum, maximum):
    if not all(math.isfinite(float(data[k])) for k in FONT_FIELDS):
        raise ValueError("non-finite TMP font metadata")
    if not 0 < minimum <= maximum:
        raise ValueError("invalid bounded TMP font range")
    pattern = struct.pack(FONT_FORMAT, *(data[k] for k in FONT_FIELDS))
    offset = _unique(raw, pattern, "TMP font layout")
    result = bytearray(raw)
    result[offset + 12] = 1
    struct.pack_into("<ff", result, offset + 16, minimum, maximum)
    result = bytes(result)
    ranges = [(offset + 12, 1), (offset + 16, 8)]
    _only_ranges(raw, result, ranges)
    return result, ranges


def _append_components(obj, original, added_ids):
    """Native GameObject write with proof of the exact component-array append."""
    tree = obj.read_typetree()
    if obj.save_typetree(copy.deepcopy(tree)) != original:
        raise ValueError(f"GameObject {obj.path_id}: native round trip is not byte-exact")
    components = tree["m_Component"]
    pointers = [row["component"] for row in components]
    if len(added_ids) != len(set(added_ids)) or any(_pp(i) in pointers for i in added_ids):
        raise ValueError("duplicate GameObject component append")
    count = len(components)
    prefix = struct.pack("<i", count) + b"".join(_pack_pp(p) for p in pointers)
    if not original.startswith(prefix):
        raise ValueError("unsupported native GameObject component-array layout")
    updated = copy.deepcopy(tree)
    updated["m_Component"].extend({"component": _pp(i)} for i in added_ids)
    expected = (struct.pack("<i", count + len(added_ids)) + original[4:len(prefix)]
                + b"".join(_pack_pp(_pp(i)) for i in added_ids) + original[len(prefix):])
    saved = obj.save_typetree(updated)
    if saved != expected:
        raise ValueError("native GameObject write changed more than the component append")
    return saved, updated


def _resize_name_rect(obj, original):
    """Use the existing 256x64 name background; enlarge only the text rect."""
    tree = obj.read_typetree()
    if (tree["m_GameObject"] != _pp(2001)
            or tree["m_SizeDelta"] != {"x": 144.0, "y": 24.0}):
        raise ValueError("unexpected original DialogNameText RectTransform")
    if obj.save_typetree(copy.deepcopy(tree)) != original:
        raise ValueError("name RectTransform native round trip is not byte-exact")
    offset = _unique(original, struct.pack("<ff", 144, 24), "native name rect size")
    updated = copy.deepcopy(tree)
    updated["m_SizeDelta"] = {"x": 232.0, "y": 32.0}
    saved = obj.save_typetree(updated)
    expected = bytearray(original)
    struct.pack_into("<ff", expected, offset, 232, 32)
    if saved != bytes(expected):
        raise ValueError("native name RectTransform changed more than m_SizeDelta")
    _only_ranges(original, saved, [(offset, 8)])
    return saved, updated, [(offset, 8)]


class _MonoReader:
    """Generated trees are read-only; always validate native header pointers."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.trees = {}

    def script(self, obj):
        sf = obj.assets_file
        index = obj.serialized_type.script_type_index
        if index < 0 or index >= len(sf.script_types):
            raise ValueError("invalid scene script type index")
        ref = sf.script_types[index]
        file_index = ref.local_serialized_file_index
        if (file_index <= 0 or file_index > len(sf.externals)
                or Path(sf.externals[file_index - 1].path).name != "globalgamemanagers.assets"):
            raise ValueError("unexpected scene script-file reference")
        script = self.scripts[ref.local_identifier_in_file]
        raw_script = struct.unpack_from("<iq", obj.get_raw_data(), 16)
        if raw_script != (file_index, ref.local_identifier_in_file):
            raise ValueError("scene script metadata differs from raw header")
        return script

    def read(self, obj):
        script = self.script(obj)
        key = script["m_AssemblyName"], script["m_Namespace"], script["m_ClassName"]
        if key not in self.trees:
            fullname = ".".join(x for x in key[1:] if x)
            nodes = ul._strip_refs(ul.generator().get_nodes(key[0], fullname))
            for node in nodes:
                if node["m_Name"] == "m_Enabled" and node["m_Level"] == 1:
                    node["m_MetaFlag"] |= 0x4000
            self.trees[key] = TypeTreeNode.from_list(nodes)
        data = ul.read_mono(obj, self.trees[key])
        raw = obj.get_raw_data()
        if (raw[:12] != _pack_pp(data["m_GameObject"])
                or raw[16:28] != _pack_pp(data["m_Script"])):
            raise ValueError("generated Mono read differs from native raw header")
        return data


def _add_object(sf, template, raw, path_id):
    obj = ObjectReader(assets_file=sf, reader=template.reader, path_id=path_id,
                       type_id=template.type_id, serialized_type=template.serialized_type,
                       class_id=template.class_id, type=ClassIDType.MonoBehaviour,
                       byte_start=0, byte_size=len(raw), is_destroyed=template.is_destroyed,
                       is_stripped=template.is_stripped, data=raw)
    sf.objects[path_id] = obj
    sf.mark_changed()
    return obj


def _hierarchy(sf, go_id):
    parts, seen = [], set()
    while go_id:
        if go_id in seen:
            raise ValueError("cyclic GameObject hierarchy")
        seen.add(go_id)
        data = sf.objects[go_id].read_typetree()
        parts.append(data["m_Name"])
        transforms = []
        for row in data["m_Component"]:
            p = row["component"]
            if p["m_FileID"] != 0:
                continue
            obj = sf.objects[p["m_PathID"]]
            if obj.type.name in ("Transform", "RectTransform"):
                transforms.append(obj.read_typetree())
        if len(transforms) != 1:
            raise ValueError("expected one native Transform per GameObject")
        parent = transforms[0]["m_Father"]
        if parent["m_FileID"] != 0:
            raise ValueError("external Transform parent")
        go_id = (sf.objects[parent["m_PathID"]].read_typetree()["m_GameObject"]["m_PathID"]
                 if parent["m_PathID"] else 0)
    return "/".join(reversed(parts))


def _script_signature(sf):
    return ([(r.local_serialized_file_index, r.local_identifier_in_file) for r in sf.script_types],
            [(t.class_id, t.script_type_index, t.script_id, t.old_type_hash) for t in sf.types])


def _build_scene(source, bindings, reader, layout):
    source_bytes = source.read_bytes()
    env = UnityPy.load(source_bytes)
    sf = env.file
    originals = {i: bytes(o.get_raw_data()) for i, o in sf.objects.items()}
    signatures = {i: (o.type_id, o.serialized_type.script_type_index)
                  for i, o in sf.objects.items()}
    script_signature = _script_signature(sf)
    monos, texts, templates = {}, {}, []
    for obj in list(sf.objects.values()):
        if obj.type.name != "MonoBehaviour":
            continue
        cls = reader.script(obj)["m_ClassName"]
        if cls not in TMP_CLASSES | {"LocalizeStringEvent"}:
            continue
        data = reader.read(obj)
        monos[obj.path_id] = data
        if cls in TMP_CLASSES:
            texts[obj.path_id] = data
        elif data["m_StringReference"]["m_TableReference"]["m_TableCollectionName"] == UI_GUID:
            _check_template(data)
            templates.append(obj)
    if bindings and not templates:
        raise ValueError(f"{source.name}: no existing UI localization template")
    template = min(templates, key=lambda o: o.path_id) if templates else None
    expected = dict(originals)
    changed, created, go_adds, native_trees = {}, [], {}, {}
    next_id = max(0, *originals) + 1
    for binding in bindings:
        text_id = binding["text_id"]
        if text_id not in texts:
            raise ValueError(f"{source.name}: {text_id} is not a TMP text")
        go = texts[text_id]["m_GameObject"]
        if go["m_FileID"] != 0 or sf.objects[go["m_PathID"]].type.name != "GameObject":
            raise ValueError("TMP has no local native GameObject")
        go_id = go["m_PathID"]
        if any(d["m_GameObject"] == go for i, d in monos.items() if i not in texts):
            raise ValueError(f"{source.name}: GameObject {go_id} already has a localization event")
        component_ids = [p["component"] for p in sf.objects[go_id].read_typetree()["m_Component"]]
        if component_ids.count(_pp(text_id)) != 1:
            raise ValueError("TMP must be listed exactly once on its GameObject")
        raw, ranges = _patch_binding(originals[template.path_id], monos[template.path_id],
                                     go_id, text_id, binding["key_id"])
        _add_object(sf, template, raw, next_id)
        expected[next_id] = raw
        go_adds.setdefault(go_id, []).append(next_id)
        created.append({**binding, "id": next_id, "go_id": go_id,
                        "template_id": template.path_id, "sha256": _sha(raw),
                        "template_sha256": _sha(originals[template.path_id]),
                        "type_id": template.type_id,
                        "script_type_index": template.serialized_type.script_type_index,
                        "script_reference": monos[template.path_id]["m_Script"],
                        "patched_byte_ranges": ranges,
                        "fields": ["m_GameObject", "m_StringReference.m_TableEntryReference.m_KeyId",
                                   "m_UpdateString.m_PersistentCalls.m_Calls[0].m_Target"]})
        next_id += 1
    for go_id, added in go_adds.items():
        raw, data = _append_components(sf.objects[go_id], originals[go_id], added)
        expected[go_id], native_trees[go_id] = raw, data
        changed[go_id] = {"id": go_id, "class": "GameObject", "fields": ["m_Component"],
                          "added_component_ids": added}
    layout_records = []
    if layout:
        targets = {b["text_id"]: (max(12, texts[b["text_id"]]["m_fontSize"] * .8),
                                    texts[b["text_id"]]["m_fontSize"]) for b in bindings}
        if source.name == "level1":
            targets.update(SPECIAL_LAYOUT)
            missing = set(SPECIAL_LAYOUT) - texts.keys()
            if missing:
                raise ValueError(f"missing special TMP layout objects: {sorted(missing)}")
        layout_exceptions = {}
        for text_id, data in texts.items():
            path = _hierarchy(sf, data["m_GameObject"]["m_PathID"])
            if path.startswith("Rendering/Canvas/ControlsScreen/"):
                original_size = data["m_fontSize"]
                targets[text_id] = (min(12, original_size), original_size)
                if original_size < 12:
                    layout_exceptions[text_id] = (
                        "Controls text originally below 12: retain original size as both bounds.")
        for text_id, (minimum, maximum) in sorted(targets.items()):
            data = texts[text_id]
            raw, ranges = _patch_layout(originals[text_id], data, minimum, maximum)
            sf.objects[text_id].set_raw_data(raw)
            expected[text_id] = raw
            record = {"id": text_id, "class": "TMP", "fields": ["m_enableAutoSizing",
                      "m_fontSizeMin", "m_fontSizeMax"], "minimum": minimum, "maximum": maximum,
                      "original": {k: data[k] for k in FONT_FIELDS}, "patched_byte_ranges": ranges}
            changed[text_id] = record
            if text_id in layout_exceptions:
                record["exception"] = layout_exceptions[text_id]
            layout_records.append(record)
        if source.name == "level1":
            rect_id = 50392
            rect = sf.objects[rect_id]
            if (texts[51057]["m_GameObject"] != _pp(2001)
                    or rect.type.name != "RectTransform"
                    or sf.objects[2001].read_typetree()["m_Component"].count(
                        {"component": _pp(rect_id)}) != 1):
                raise ValueError("name text does not use the expected native RectTransform")
            raw, tree, ranges = _resize_name_rect(rect, originals[rect_id])
            expected[rect_id], native_trees[rect_id] = raw, tree
            changed[rect_id] = {"id": rect_id, "class": "RectTransform",
                                "purpose": "name_text_rect", "text_id": 51057,
                                "go_id": 2001, "fields": ["m_SizeDelta"],
                                "original": {"x": 144.0, "y": 24.0},
                                "updated": {"x": 232.0, "y": 32.0},
                                "patched_byte_ranges": ranges,
                                "background_unchanged": {"rect_id": 50394,
                                                         "width": 256, "height": 64}}
    result = sf.save()
    # Re-open the actual serialized file; compare every original and cloned object.
    checked = UnityPy.load(result).file
    if set(checked.objects) != set(expected):
        raise ValueError("scene serialization removed, renumbered or added unexpected objects")
    if _script_signature(checked) != script_signature:
        raise ValueError("scene serialization changed script/type metadata")
    for path_id, raw in expected.items():
        obj = checked.objects[path_id]
        if bytes(obj.get_raw_data()) != raw:
            raise ValueError(f"serialized object {path_id} differs from expected bytes")
        signature = (obj.type_id, obj.serialized_type.script_type_index)
        expected_signature = signatures.get(path_id, signatures[template.path_id] if template else None)
        if signature != expected_signature:
            raise ValueError(f"serialized object {path_id} changed type/script reference")
    for native_id, tree in native_trees.items():
        if checked.objects[native_id].read_typetree() != tree:
            raise ValueError("serialized native fields/components differ")
    for row in created:
        expected_data = copy.deepcopy(monos[row["template_id"]])
        expected_data["m_GameObject"] = _pp(row["go_id"])
        expected_data["m_StringReference"]["m_TableEntryReference"]["m_KeyId"] = row["key_id"]
        _calls(expected_data)[0]["m_Target"] = _pp(row["text_id"])
        if reader.read(checked.objects[row["id"]]) != expected_data:
            raise ValueError("serialized localization clone differs from expected binding")
    for row in layout_records:
        actual = reader.read(checked.objects[row["id"]])
        expected_data = copy.deepcopy(texts[row["id"]])
        expected_data.update(m_enableAutoSizing=1, m_fontSizeMin=row["minimum"],
                             m_fontSizeMax=row["maximum"])
        # Unity's stored floats are float32; normalize the requested bounds.
        for field in ("m_fontSizeMin", "m_fontSizeMax"):
            expected_data[field] = struct.unpack("<f", struct.pack("<f", expected_data[field]))[0]
        if actual != expected_data:
            raise ValueError("serialized TMP metadata changed outside bounded font fields")
    for path_id, row in changed.items():
        row.update(original_sha256=_sha(originals[path_id]), sha256=_sha(expected[path_id]))
    manifest = {"file": source.name, "source_sha256": _sha(source_bytes),
                "sha256": _sha(result), "source_object_count": len(originals),
                "output_object_count": len(expected), "new_bindings": created,
                "touched_objects": list(changed.values()),
                "unchanged_original_objects": len(originals) - len(changed),
                "all_object_bytes_verified": True, "script_metadata_verified": True}
    return result, manifest


def _expected_scenes(source_dir, output_dir, bindings, layout):
    source_dir, output_dir = Path(source_dir).resolve(), Path(output_dir).resolve()
    if source_dir == output_dir or source_dir in output_dir.parents or output_dir in source_dir.parents:
        raise ValueError("source and output directories must be separate trees")
    grouped = {name: [] for name in ("level0", "level1")}
    seen = set()
    for binding in bindings:
        if set(binding) != {"scene", "text_id", "key_id", "key"} or binding["scene"] not in grouped:
            raise ValueError("invalid scene binding schema")
        if any(type(binding[k]) is not int or not 0 < binding[k] < 2**63
               for k in ("text_id", "key_id")) or not isinstance(binding["key"], str) or not binding["key"]:
            raise ValueError("invalid binding ID or key")
        identity = binding["scene"], binding["text_id"]
        if identity in seen:
            raise ValueError("duplicate TMP binding")
        seen.add(identity)
        grouped[binding["scene"]].append(dict(binding))
    scripts_path = source_dir / "globalgamemanagers.assets"
    if not scripts_path.exists():
        scripts_path = Path(ul.DATA_DIR) / "globalgamemanagers.assets"
    scripts_bytes = scripts_path.read_bytes()
    scripts_env = UnityPy.load(scripts_bytes)
    scripts = {o.path_id: o.read_typetree() for o in scripts_env.objects if o.type.name == "MonoScript"}
    reader = _MonoReader(scripts)
    outputs, scenes = {}, []
    for name in grouped:
        raw, manifest = _build_scene(source_dir / name, grouped[name], reader, layout)
        outputs[name] = raw
        scenes.append(manifest)
    manifest = {"format_version": 1, "ui_table_guid": UI_GUID, "layout_enabled": layout,
                "script_metadata_sha256": _sha(scripts_bytes), "scenes": scenes,
                "limitations": ["No visual or runtime QA; verify both English and Spanish in the game.",
                                "Bounded TMP auto sizing does not guarantee every translation fits."]}
    return outputs, manifest


def build_scenes(source_dir: Path, output_dir: Path, bindings: list[dict], *, layout: bool = True) -> dict:
    """Build level0/level1 from pristine copies, then return a verified manifest.

    The source directory may include globalgamemanagers.assets for read-only
    script metadata. Otherwise that metadata and the read-only DLL generator use
    the configured original game installation. Source scene bytes are never read
    from that installation or from a cached UI inventory.
    """
    outputs, manifest = _expected_scenes(source_dir, output_dir, bindings, layout)
    output_dir = Path(output_dir)
    # Both files must validate before publishing any generated scene output.
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, raw in outputs.items():
        target = output_dir / name
        target.write_bytes(raw)
        if _sha(target.read_bytes()) != _sha(raw):
            raise ValueError(f"written scene {name} failed hash verification")
    return manifest


def verify_scenes(source_dir: Path, output_dir: Path, bindings: list[dict], manifest: dict) -> dict:
    """Verify existing outputs against a pristine reconstruction, without writes.

    Reconstructing in memory also checks the raw edits, component lists and every
    untouched object; a manifest alone is never accepted as evidence of safety.
    """
    layout = manifest.get("layout_enabled")
    if type(layout) is not bool:
        raise ValueError("scene manifest has no boolean layout_enabled")
    outputs, expected_manifest = _expected_scenes(source_dir, output_dir, bindings, layout)
    # JSON converts byte-range tuples to lists; normalize that representation.
    import json
    if json.loads(json.dumps(expected_manifest)) != json.loads(json.dumps(manifest)):
        raise ValueError("scene manifest differs from pristine reconstruction")
    for name, expected in outputs.items():
        actual = (Path(output_dir) / name).read_bytes()
        if actual != expected:
            raise ValueError(f"output scene {name} differs from verified pristine reconstruction")
    return {"verified": True, "scenes": len(outputs),
            "bindings": sum(len(s["new_bindings"]) for s in expected_manifest["scenes"]),
            "all_object_bytes_verified": True, "visual_qa": False}
