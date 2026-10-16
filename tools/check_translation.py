import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_es

TAG = re.compile(r"<[^<>]*>")
NEWLINE = re.compile(r"\r\n|\r|\n")
EMPTY_CONTROL = re.compile(r"^Controls\|.+_control$")


def _load_source(root):
    with open(os.path.join(root, "extracted", "strings_en.json"), encoding="utf-8") as f:
        en_tables = json.load(f)

    en = {}
    for table in en_tables:
        for entry in table["entries"]:
            en["%s|%s" % (table["collection"], entry["key"])] = entry["localized"]

    extra_path = os.path.join(root, "translation", "_ui_extra_en.json")
    if os.path.isfile(extra_path):
        with open(extra_path, encoding="utf-8") as f:
            extra = json.load(f)
        if not isinstance(extra, dict):
            raise ValueError("translation/_ui_extra_en.json debe ser un objeto JSON")
        for key, value in extra.items():
            if not isinstance(key, str) or not key.startswith("UI|"):
                raise ValueError("clave de fuente extra no UI: %s" % key)
            if key in en:
                raise ValueError("colision en fuente extra: %s" % key)
            if not isinstance(value, str):
                raise ValueError("texto de fuente extra no es cadena: %s" % key)
            en[key] = value
    return en


def _edge_whitespace(value):
    leading = re.match(r"\s*", value).group(0)
    trailing = re.search(r"\s*\Z", value).group(0)
    return leading, trailing


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    try:
        en = _load_source(root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("fuente EN invalida:", str(exc))
        return 1

    es = build_es.load_translations()
    missing = sorted(k for k in en if k not in es)
    extra = sorted(k for k in es if k not in en)
    empty_en = sorted(k for k, value in en.items() if value == "")
    invalid_empty_en = sorted(k for k in empty_en if not EMPTY_CONTROL.fullmatch(k))
    allowed_missing = set(empty_en) - set(invalid_empty_en)
    required_missing = sorted(k for k in missing if k not in allowed_missing)

    print("claves EN:", len(en), "| traducciones:", len(es))
    print("sin traducir (fallback EN):", len(missing))
    if required_missing:
        print("  ", required_missing[:40])
    print("claves sobrantes:", len(extra))
    if extra:
        print("  ", extra[:40])
    print("cadenas EN vacias:", len(empty_en), "->", empty_en if empty_en else "ninguna")

    bad_tags = []
    bad_ws = []
    bad_nl = []
    bad_empty = []
    bad_es_empty = []
    for key, source in en.items():
        if source == "":
            if key in es and es[key] != "":
                bad_empty.append(key)
            continue
        if key not in es:
            continue
        translated = es[key]
        if translated == "":
            bad_es_empty.append(key)
        if TAG.findall(source) != TAG.findall(translated):
            bad_tags.append((key, TAG.findall(source), TAG.findall(translated)))
        if _edge_whitespace(source) != _edge_whitespace(translated):
            bad_ws.append(key)
        if NEWLINE.findall(source) != NEWLINE.findall(translated):
            bad_nl.append((key, NEWLINE.findall(source), NEWLINE.findall(translated)))

    print("etiquetas distintas:", len(bad_tags))
    for key, source, translated in bad_tags[:30]:
        print("  ", key, source, "->", translated)
    print("espacios extremos distintos:", len(bad_ws), bad_ws[:20])
    print("saltos de linea distintos:", len(bad_nl))
    for key, source, translated in bad_nl[:30]:
        print("  ", key, source, "->", translated)
    print("controles EN vacios no permitidos:", len(invalid_empty_en), invalid_empty_en[:20])
    if len(allowed_missing) != 11:
        print("cantidad de controles EN vacios distinta de 11:", len(allowed_missing))
    print("controles vacios traducidos:", len(bad_empty), bad_empty[:20])
    print("traducciones ES vacias:", len(bad_es_empty), bad_es_empty[:20])

    failed = bool(required_missing or extra or invalid_empty_en or len(allowed_missing) != 11
                  or bad_tags or bad_ws or bad_nl or bad_empty or bad_es_empty)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
