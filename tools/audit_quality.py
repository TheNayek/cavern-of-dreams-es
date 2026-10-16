"""Read-only translation QA: formatting, coverage candidates and layout estimates.

Run audit_game_ui.py first to supply scene/font metadata. Glyph advance and word
wrap estimates are triage, not a TMP renderer: kerning, fallback fonts, runtime
layout and effects may change actual fit. No translation/build/game files change.
"""

import argparse
import collections
import json
import re
from pathlib import Path

from build_es import load_translations
from ui_extension import additions

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"<[^<>]*>")
NEWLINE = re.compile(r"\r\n|\r|\n")


def font_metrics(font):
    glyphs = {g["m_Index"]: g for g in font["m_GlyphTable"]}
    return {c["m_Unicode"]: glyphs[c["m_GlyphIndex"]]["m_Metrics"]["m_HorizontalAdvance"]
            * c["m_Scale"] * glyphs[c["m_GlyphIndex"]]["m_Scale"]
            for c in font["m_CharacterTable"]}


def estimate(text, font, width, size, line_spacing=0):
    advances = font_metrics(font)
    point = font["m_FaceInfo"]["m_PointSize"]
    heights = [size]
    x, largest_word, missing = 0, 0, set()
    sizes = [size]
    for token in re.findall(r"<[^<>]*>|\r\n|\r|\n|[^\s<>]+|[ \t]+", text):
        if token.startswith("<"):
            if token.lower().startswith("<size="):
                value = token[6:-1].strip('"')
                sizes.append(size * float(value[:-1]) / 100 if value.endswith("%") else float(value))
            elif token.lower() == "</size>" and len(sizes) > 1:
                sizes.pop()
            continue
        if token in ("\r", "\n", "\r\n"):
            heights.append(sizes[-1])
            x = 0
            continue
        for char in token:
            if ord(char) not in advances:
                missing.add(char)
        w = sum(advances.get(ord(c), advances.get(ord("?"), point / 2)) for c in token)
        w *= sizes[-1] / point
        if token.isspace():
            if x:
                x += w
            continue
        largest_word = max(largest_word, w)
        if x and x + w > width:
            heights.append(sizes[-1])
            x = 0
        x += w
        heights[-1] = max(heights[-1], sizes[-1])
    height = sum(font["m_FaceInfo"]["m_LineHeight"] * s / point for s in heights)
    height += (len(heights) - 1) * line_spacing * size / point
    return {"lines": len(heights), "height_estimate": round(height, 1),
            "largest_word": round(largest_word, 1), "missing_glyphs": sorted(missing)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=ROOT / "build" / "qa" / "ui-inventory.json")
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "qa" / "quality.json")
    args = parser.parse_args()
    tables = json.loads((ROOT / "extracted" / "strings_en.json").read_text(encoding="utf-8"))
    translations = load_translations()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    shared = json.loads((ROOT / "extracted" / "shared_tables.json").read_text(encoding="utf-8"))
    guids = {t["guid"].lower(): t for t in shared.values()}
    ui_shared = next(t for t in guids.values() if t["collection"] == "UI")
    ui_table = next(t for t in tables if t["collection"] == "UI")
    for row in additions():
        ui_shared["entries"][str(row["key_id"])] = row["name"]
        ui_table["entries"].append({"key": row["name"], "id": str(row["key_id"]), "localized": row["en"]})

    def localized_key(ref):
        if not isinstance(ref, dict) or "m_TableReference" not in ref:
            return None
        table_name = ref["m_TableReference"]["m_TableCollectionName"]
        table = guids.get(table_name.removeprefix("GUID:").lower())
        if not table:
            return None
        entry = ref["m_TableEntryReference"]
        key = entry["m_Key"] or table["entries"].get(str(entry["m_KeyId"]))
        return table["collection"] + "|" + key if key else None

    bindings = collections.defaultdict(list)
    dialog_keys = set()
    for row in inventory["components"]:
        data = row["data"]
        if row["class"] == "LocalizeStringEvent":
            key = localized_key(data["m_StringReference"])
            if key:
                calls = data["m_UpdateString"]["m_PersistentCalls"]["m_Calls"]
                for call in calls:
                    if call["m_Target"]["m_FileID"] == 0:
                        bindings[(row["file"], call["m_Target"]["m_PathID"])].append(key)
        elif row["class"].startswith("MO_"):
            refs = [k for value in data.values() if (k := localized_key(value))]
            target = data.get("text", {})
            if refs and target.get("m_FileID") == 0:
                bindings[(row["file"], target["m_PathID"])].extend(refs)
        if row["class"] == "Dialog":
            key = localized_key(data["text"])
            if key:
                dialog_keys.add(key)

    rows, formatting, unchanged, expanded = [], [], [], []
    for table in tables:
        for entry in table["entries"]:
            key = table["collection"] + "|" + entry["key"]
            en = entry["localized"]
            es = translations.get(key, en)
            en_plain, es_plain = TAG.sub("", en), TAG.sub("", es)
            row = {"key": key, "en": en, "es": es, "en_chars": len(en_plain), "es_chars": len(es_plain),
                   "ratio": round(len(es_plain) / len(en_plain), 3) if en_plain else None}
            rows.append(row)
            if en and en == es:
                unchanged.append(row)
            problems = []
            if TAG.findall(en) != TAG.findall(es):
                problems.append("tag sequence")
            if NEWLINE.findall(en) != NEWLINE.findall(es):
                problems.append("exact newline sequence")
            if (en[:1].isspace(), en[-1:].isspace()) != (es[:1].isspace(), es[-1:].isspace()):
                problems.append("boundary whitespace")
            if "\ufffd" in es:
                problems.append("replacement character")
            if problems:
                formatting.append({"key": key, "problems": problems})
            if en_plain and len(es_plain) >= len(en_plain) * 1.3 and len(es_plain) - len(en_plain) >= 12:
                expanded.append(row)

    candidates = []
    for row in inventory["texts"]:
        text = row["data"].get("m_text", "")
        path = row["hierarchy"]
        controls_header = "ControlsScreen/" in path and "/ControlExplanation" not in path and path.endswith("/Header Text")
        if not controls_header and not any(part in path for part in ("PauseMenu/", "OptionsMenu/", "ReturnTip/")):
            continue
        if (row["file"], row["id"]) in bindings or not re.search(r"[A-Za-z]{2}", text):
            continue
        if any(part in path for part in ("WorldName", "World Name", "Build Number")):
            continue
        candidates.append({"file": row["file"], "id": row["id"], "path": path, "text": text,
                           "status": "unbound scene text; runtime visibility still needs QA"})

    fonts = {(r["file"], r["id"]): r["data"] for r in inventory["components"] if r["class"] == "TMP_FontAsset"}
    dialog = next(r for r in inventory["texts"] if r["file"] == "level1" and r["id"] == 51045)
    font = fonts[("sharedassets1.assets", 4234)]
    layout = []
    for row in rows:
        if row["key"] not in dialog_keys:
            continue
        en = estimate(row["en"], font, 450, 16, dialog["data"]["m_lineSpacing"])
        es = estimate(row["es"], font, 450, 16, dialog["data"]["m_lineSpacing"])
        if es["height_estimate"] > 72 or es["largest_word"] > 450:
            layout.append({**row, "en_layout": en, "es_layout": es,
                           "new_risk": en["height_estimate"] <= 72 and en["largest_word"] <= 450,
                           "classification": "estimate requiring visual verification"})
    name_font = fonts[("sharedassets0.assets", 208)]
    advances = font_metrics(name_font)
    name_component = next(r for r in inventory["texts"] if r["file"] == "level1" and r["id"] == 51057)
    name_size = name_component["data"]["m_fontSize"]
    name_box_width = name_component["rect"]["m_SizeDelta"]["x"]
    name_minimum = (name_component["data"]["m_fontSizeMin"]
                    if name_component["data"]["m_enableAutoSizing"] else name_size)
    name_widths = []
    for row in rows:
        if not row["key"].startswith("CharacterNames|"):
            continue
        widths = {lang: round(sum(advances.get(ord(c), 0) for c in row[lang]) * name_size
                             / name_font["m_FaceInfo"]["m_PointSize"], 1)
                  for lang in ("en", "es")}
        if widths["es"] > name_box_width:
            name_widths.append({**row, "width_estimate": widths, "box_width": name_box_width,
                                "minimum_font_size": name_minimum,
                                "fits_at_minimum_estimate": widths["es"] * name_minimum / name_size <= name_box_width,
                                "new_risk": widths["en"] <= name_box_width,
                                "classification": "unwrapped glyph estimate; verify visually"})
    encyclopedia = []
    description = next(r for r in inventory["texts"] if r["file"] == "level1" and r["id"] == 51145)
    for row in rows:
        if not row["key"].startswith("Encyclopedia|"):
            continue
        en = estimate(row["en"], name_font, 250, 16, description["data"]["m_lineSpacing"])
        es = estimate(row["es"], name_font, 250, 16, description["data"]["m_lineSpacing"])
        if es["height_estimate"] > 50:
            encyclopedia.append({**row, "en_layout": en, "es_layout": es,
                                 "new_risk": en["height_estimate"] <= 50,
                                 "classification": "estimate; rect may differ from visible frame"})
    report = {"summary": {"source_entries": len(rows), "translated_nonempty": sum(bool(v) for v in translations.values()),
                          "formatting_errors": len(formatting), "unchanged_nonempty": len(unchanged),
                          "expansion_candidates": len(expanded), "unbound_ui_candidates": len(candidates),
                          "dialog_keys_mapped": len(dialog_keys), "layout_candidates": len(layout),
                          "name_width_candidates": len(name_widths),
                          "encyclopedia_candidates": len(encyclopedia)},
              "formatting_errors": formatting, "unchanged_nonempty": unchanged,
              "expansion_candidates": sorted(expanded, key=lambda r: r["ratio"], reverse=True),
              "unbound_ui_candidates": candidates, "dialog_layout_candidates": layout,
              "name_width_candidates": name_widths, "encyclopedia_candidates": encyclopedia,
              "entries": rows}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
