"""Extrae las StringTables (ingles), SharedTableData y Locales a JSON/CSV.

Empareja cada StringTable con su SharedTableData por el PathID del PPtr
m_SharedData (el nombre del asset no coincide con m_TableCollectionName en
algunos casos: PostFinale->Epilogue, ItemHints->SageAdvice).

Salida en extracted/:
  shared_tables.json  { pathid: { collection, asset, guid, entries:{id:key} } }
  strings_en.json     lista de { asset, address, collection, code, shared_pathid,
                                 entries:[{id,key,localized}] }
  strings.csv         address, table_asset, collection, shared_pathid, id, key, english, chars
  locales.json        [{ asset, name, code, custom_culture, order }]
"""

import csv
import json
import os

import unity_localization as ul

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "extracted")
PRISTINE = os.path.join(os.path.dirname(HERE), "orig", "aa", "StandaloneWindows64")


def pristine_bundle(installed_path):
    path = os.path.join(PRISTINE, os.path.basename(installed_path))
    if not os.path.isfile(path):
        raise FileNotFoundError(
            "Missing pristine source: %s. Run python tools/prepare_sources.py --game <game-folder> first."
            % path)
    return path


def extract_shared():
    tree = ul.SHARED_TABLE_TREE()
    tables = {}
    for obj in ul.iter_monobehaviours(pristine_bundle(ul.BUNDLE_SHARED)):
        v = ul.read_mono(obj, tree)
        if not v.get("m_Name", "").endswith(" Shared Data"):
            continue
        entries = {str(e["m_Id"]): e["m_Key"] for e in v.get("m_Entries", [])}
        tables[str(obj.path_id)] = {
            "asset": v.get("m_Name", ""),
            "collection": v.get("m_TableCollectionName", ""),
            "guid": v.get("m_TableCollectionNameGuidString", ""),
            "entries": entries,
        }
    return tables


def extract_en():
    tree = ul.STRING_TABLE_TREE()
    out = []
    for obj in ul.iter_monobehaviours(pristine_bundle(ul.BUNDLE_EN)):
        v = ul.read_mono(obj, tree)
        code = v.get("m_LocaleId", {}).get("m_Code", "")
        name = v.get("m_Name", "")
        sp = v.get("m_SharedData", {})
        shared_pathid = str(sp.get("m_PathID", ""))
        entries = [
            {
                "id": str(e["m_Id"]),
                "localized": e.get("m_Localized", ""),
            }
            for e in v.get("m_TableData", [])
        ]
        out.append(
            {
                "asset": name,
                "code": code,
                "shared_pathid": shared_pathid,
                "entries": entries,
                "_pathid": str(obj.path_id),
            }
        )
    return out


def extract_locales():
    tree = ul.LOCALE_TREE()
    out = []
    for obj in ul.iter_monobehaviours(pristine_bundle(ul.BUNDLE_LOCALES)):
        v = ul.read_mono(obj, tree)
        out.append(
            {
                "asset": v.get("m_Name", ""),
                "name": v.get("m_LocaleName", ""),
                "code": v.get("m_Identifier", {}).get("m_Code", ""),
                "custom_culture": v.get("m_CustomFormatCultureCode", ""),
                "order": v.get("m_SortOrder", 0),
            }
        )
    return out


def main():
    for path in (ul.BUNDLE_SHARED, ul.BUNDLE_EN, ul.BUNDLE_LOCALES):
        pristine_bundle(path)
    shared = extract_shared()
    en = extract_en()
    locales = extract_locales()

    # resolver coleccion/direccion via shared_pathid
    unmatched_shared = []
    rows = []
    missing_key = 0
    for t in en:
        sdata = shared.get(t["shared_pathid"])
        collection = sdata["collection"] if sdata else t["asset"]
        if sdata is None:
            unmatched_shared.append(t["asset"])
        address = "%s_%s" % (collection, t["code"])
        t["collection"] = collection
        t["address"] = address
        t["shared_asset"] = sdata["asset"] if sdata else ""
        keys = sdata["entries"] if sdata else {}
        for e in t["entries"]:
            e["key"] = keys.get(e["id"], "")
            if not e["key"]:
                missing_key += 1
            rows.append(
                [
                    address,
                    t["asset"],
                    collection,
                    t["shared_pathid"],
                    e["id"],
                    e["key"],
                    e["localized"],
                    len(e["localized"]),
                ]
            )

    for t in en:
        t.pop("_pathid", None)

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "shared_tables.json"), "w", encoding="utf-8") as f:
        json.dump(shared, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "strings_en.json"), "w", encoding="utf-8") as f:
        json.dump(en, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "locales.json"), "w", encoding="utf-8") as f:
        json.dump(locales, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "strings.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "address",
                "table_asset",
                "collection",
                "shared_pathid",
                "id",
                "key",
                "english",
                "chars",
            ]
        )
        w.writerows(rows)

    total_chars = sum(r[7] for r in rows)
    empty = sum(1 for r in rows if r[6] == "")
    print("Colecciones (SharedTableData):", len(shared))
    print("StringTables EN:", len(en))
    print("Locales:", locales)
    print("Filas totales:", len(rows))
    print("Claves sin emparejar:", missing_key)
    print("Cadenas vacias:", empty)
    print("Caracteres EN:", total_chars)
    print("Tablas EN sin SharedTableData:", unmatched_shared)
    print("Direcciones:", sorted(t["address"] for t in en))


if __name__ == "__main__":
    main()
