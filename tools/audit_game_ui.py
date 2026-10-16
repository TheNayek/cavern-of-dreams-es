"""Read-only inventory of scene UI, localization bindings and TMP font metrics.

Player assets have no embedded typetrees. Generated trees are for inspection
only: fix the native MonoBehaviour header alignment and cross-check m_Script
against the raw bytes before using the data. Never serialize with these trees.
"""

import argparse
import collections
import json
import struct
from pathlib import Path

import UnityPy
from UnityPy.helpers.TypeTreeNode import TypeTreeNode

import unity_localization as ul

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "TextMeshProUGUI", "TextMeshPro", "Text", "TMP_FontAsset", "Dialog",
    "DialogSetting", "DialogGraphic", "Textbox", "InteractText", "TeleType",
    "ContentSizeFitter", "HorizontalLayoutGroup", "VerticalLayoutGroup",
    "LayoutElement", "UIController", "ControlExplanationShow",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "qa" / "ui-inventory.json")
    args = parser.parse_args()
    base = Path(ul.DATA_DIR)
    env = UnityPy.load(str(base / "globalgamemanagers.assets"))
    scripts = {o.path_id: o.read_typetree() for o in env.objects if o.type.name == "MonoScript"}
    trees = {}

    def read_mono(obj, script):
        key = script["m_AssemblyName"], script["m_Namespace"], script["m_ClassName"]
        if key not in trees:
            full = ".".join(p for p in key[1:] if p)
            nodes = ul._strip_refs(ul.generator().get_nodes(key[0], full))
            for node in nodes:
                if node["m_Name"] == "m_Enabled" and node["m_Level"] == 1:
                    node["m_MetaFlag"] |= 0x4000
            trees[key] = TypeTreeNode.from_list(nodes)
        data = ul.read_mono(obj, trees[key])
        expected = struct.unpack_from("<iq", obj.get_raw_data(), 16)
        if (data["m_Script"]["m_FileID"], data["m_Script"]["m_PathID"]) != expected:
            raise ValueError("generated tree header does not match raw script pointer")
        return data

    texts, components, errors, scanned = [], [], [], {}
    for name in ["level0", "level1", "resources.assets", "sharedassets0.assets", "sharedassets1.assets"]:
        env = ul.load_env(str(base / name))
        sf = env.file
        names, gameobjects, transforms, go_transform = {}, {}, {}, {}
        for obj in env.objects:
            if obj.type.name == "GameObject":
                data = obj.read_typetree()
                names[obj.path_id] = data["m_Name"]
                gameobjects[obj.path_id] = data
            elif obj.type.name in ("Transform", "RectTransform"):
                data = obj.read_typetree()
                transforms[obj.path_id] = data
                go_transform[data["m_GameObject"]["m_PathID"]] = obj.path_id

        def hierarchy(go):
            parts, seen = [], set()
            while go and go not in seen:
                seen.add(go)
                parts.append(names.get(go, str(go)))
                transform = transforms.get(go_transform.get(go), {})
                parent = transform.get("m_Father", {}).get("m_PathID", 0)
                go = transforms.get(parent, {}).get("m_GameObject", {}).get("m_PathID", 0)
            return "/".join(reversed(parts))

        counts = collections.Counter()
        for obj in env.objects:
            if obj.type.name != "MonoBehaviour":
                continue
            try:
                ref = sf.script_types[obj.serialized_type.script_type_index]
                # All inspected scene scripts must resolve to the known script file.
                index = ref.local_serialized_file_index
                if index != 1 or sf.externals[index - 1].path != "globalgamemanagers.assets":
                    raise ValueError("unexpected script file reference")
                script = scripts[ref.local_identifier_in_file]
                cls = script["m_ClassName"]
                counts[cls] += 1
                if cls not in TARGETS and not cls.startswith("MO_") and not any(
                    part in cls for part in ("Menu", "Pause", "Option", "Localiz")
                ):
                    continue
                data = read_mono(obj, script)
                go = data.get("m_GameObject", {}).get("m_PathID", 0)
                row = {"file": name, "id": obj.path_id, "class": cls,
                       "hierarchy": hierarchy(go), "go": go, "data": data}
                if cls in ("TextMeshProUGUI", "TextMeshPro", "Text"):
                    row["rect"] = transforms.get(go_transform.get(go), {})
                    row["go_components"] = gameobjects.get(go, {}).get("m_Component", [])
                    texts.append(row)
                else:
                    components.append(row)
            except Exception as error:
                errors.append({"file": name, "id": obj.path_id, "error": repr(error)})
        scanned[name] = dict(counts)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {"texts": texts, "components": components, "errors": errors, "scanned_classes": scanned}
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print("UI inventory: %d texts, %d components, %d read errors" % (len(texts), len(components), len(errors)))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
