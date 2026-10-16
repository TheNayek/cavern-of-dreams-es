"""Native UI supplement: deterministic keys, additive EN/ES rows and validation.

Only the UI table/shared-data object gains rows; existing rows and all other
objects remain byte-exact. Sources live under translation/, not extracted/.
"""

import copy
import hashlib
import json
from pathlib import Path

import unity_localization as ul

ROOT = Path(__file__).resolve().parents[1]
SHARED_BUNDLE = "localization-assets-shared_assets_all.bundle"


def additions(root=ROOT):
    root = Path(root)
    source = json.loads((root / "translation" / "_ui_extra_en.json").read_text(encoding="utf-8"))
    existing = json.loads((root / "extracted" / "strings_en.json").read_text(encoding="utf-8"))
    ui = next(t for t in existing if t["collection"] == "UI")
    keys = {"UI|" + e["key"] for e in ui["entries"]}
    ids = {int(e["id"]) for e in ui["entries"]}
    rows = []
    for key, text in sorted(source.items()):
        if not key.startswith("UI|") or key in keys or not isinstance(text, str) or not text:
            raise ValueError("invalid or colliding UI source key: " + key)
        digest = hashlib.sha256(("CavernES:" + key).encode("utf-8")).digest()
        key_id = (int.from_bytes(digest[:8], "little") & ((1 << 62) - 1)) | (1 << 62)
        if key_id in ids:
            raise ValueError("UI key ID collision: " + key)
        ids.add(key_id)
        rows.append({"key": key, "name": key.split("|", 1)[1], "key_id": key_id, "en": text})
    return rows


def load_ui_spec(root=ROOT):
    root = Path(root)
    tables = json.loads((root / "extracted" / "strings_en.json").read_text(encoding="utf-8"))
    ui = next(t for t in tables if t["collection"] == "UI")
    ids = {"UI|" + e["key"]: int(e["id"]) for e in ui["entries"]}
    ids.update({r["key"]: r["key_id"] for r in additions(root)})
    bindings = json.loads((root / "translation" / "_ui_bindings.json").read_text(encoding="utf-8"))
    seen = set()
    result = []
    for row in bindings:
        target = row["scene"], row["text_id"]
        if row["scene"] not in ("level0", "level1") or target in seen or row["key"] not in ids:
            raise ValueError("invalid UI binding: %r" % row)
        seen.add(target)
        result.append({**row, "key_id": ids[row["key"]]})
    return result


def append_table_entries(data, code, translations=None):
    if data["m_Name"] not in ("UI_en", "UI_es"):
        raise ValueError("UI supplement applied to another table")
    if code not in ("en", "es"):
        raise ValueError("unsupported UI supplement locale")
    ids = {row["m_Id"] for row in data["m_TableData"]}
    for row in additions():
        if row["key_id"] in ids:
            raise ValueError("UI key already appended: " + row["key"])
        text = row["en"] if code == "en" else translations[row["key"]]
        data["m_TableData"].append({"m_Id": row["key_id"], "m_Localized": text,
                                   "m_Metadata": {"m_Items": []}})


def append_shared_entries(data):
    if data["m_TableCollectionName"] != "UI":
        raise ValueError("expected UI SharedTableData")
    ids = {row["m_Id"] for row in data["m_Entries"]}
    for row in additions():
        if row["key_id"] in ids:
            raise ValueError("UI shared key already appended")
        data["m_Entries"].append({"m_Id": row["key_id"], "m_Key": row["name"],
                                 "m_Metadata": {"m_Items": []}})


def serialized_file(env):
    return next(f for f in env.file.files.values() if f.__class__.__name__ == "SerializedFile")


def extend_english(sf):
    for obj in sf.objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        tree, data, registry = ul.load_mono(obj)
        if data["m_Name"] == "UI_en":
            append_table_entries(data, "en")
            obj.set_raw_data(ul.save_mono(obj, tree, data, registry))
            return
    raise ValueError("UI_en table not found")


def build_shared(source, output):
    env = ul.load_env(str(source))
    sf = serialized_file(env)
    found = False
    for obj in sf.objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        tree, data, registry = ul.load_mono(obj)
        if data.get("m_TableCollectionName") == "UI":
            append_shared_entries(data)
            obj.set_raw_data(ul.save_mono(obj, tree, data, registry))
            found = True
    if not found:
        raise ValueError("UI shared data not found")
    Path(output).write_bytes(env.file.save(packer="lz4"))


def original_object_preserved(source, candidate):
    """Allow only the exact declared additive extension of UI_en/SharedData."""
    if bytes(source.get_raw_data()) == bytes(candidate.get_raw_data()):
        return True
    if source.type.name != "MonoBehaviour" or candidate.type_id != source.type_id:
        return False
    tree, data, registry = ul.load_mono(source)
    expected = copy.deepcopy(data)
    if data.get("m_Name") == "UI_en":
        append_table_entries(expected, "en")
    elif data.get("m_TableCollectionName") == "UI":
        append_shared_entries(expected)
    else:
        return False
    return bytes(candidate.get_raw_data()) == ul.save_mono(source, tree, expected, registry)


def verify_shared(source, candidate):
    src_sf = serialized_file(ul.load_env(str(source)))
    dst_sf = serialized_file(ul.load_env(str(candidate)))
    if set(src_sf.objects) != set(dst_sf.objects):
        raise ValueError("shared bundle object IDs changed")
    for pid, obj in src_sf.objects.items():
        if not original_object_preserved(obj, dst_sf.objects[pid]):
            raise ValueError("unexpected shared object modification: %s" % pid)
    data = next(ul.load_mono(obj)[1] for obj in dst_sf.objects.values()
                if obj.type.name == "MonoBehaviour"
                and ul.load_mono(obj)[1].get("m_TableCollectionName") == "UI")
    expected = {row["key_id"]: row["name"] for row in additions()}
    actual = {row["m_Id"]: row["m_Key"] for row in data["m_Entries"]}
    if any(actual.get(key) != value for key, value in expected.items()):
        raise ValueError("missing UI shared keys")
    return len(expected)
