import os
import sys
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unity_localization as ul
from catalog_tool import Catalog
from audit_preloads import inspect_bundle
import ui_extension

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "build", "out")

FALLOS = []


def check(cond, msg):
    if not cond:
        FALLOS.append(msg)
    return cond


def sf_of(env):
    for f in env.file.files.values():
        if f.__class__.__name__ == "SerializedFile":
            return f
    raise RuntimeError("sin SerializedFile")


def roundtrip(obj):
    t, d, reg = ul.load_mono(obj)
    return d, ul.save_mono(obj, t, d, reg) == bytes(obj.get_raw_data())


def main():
    en_path = os.path.join(OUT, [n for n in os.listdir(OUT) if "english" in n][0])
    loc_path = os.path.join(OUT, [n for n in os.listdir(OUT) if "locales" in n][0])

    # 0) invariante del catalogo (buckets 1:1 con las claves y en el mismo orden)
    cat = Catalog(os.path.join(OUT, "catalog.json"))
    errs = cat.check_layout()
    print("catalogo: keys=%d buckets=%d layout=%s" % (
        len(cat.keys), len(cat.buckets), "OK" if not errs else "FALLO"))
    check(not errs, "catalogo desordenado: %s" % (errs[:3],))
    check(cat.check_roundtrip(), "round-trip del catalogo roto")
    eerrs = cat.check_extra()
    check(not eerrs, "extra/tipos del catalogo: %s" % (eerrs[:3],))

    # 0b) simular ContentCatalogData.CreateLocator(): keys[i] se leen de buckets[i]
    sim = [cat.key_at_offset(off) for off, _ in cat.buckets]
    check(len(sim) == len(cat.buckets), "keys del runtime != buckets")
    iids = cat.data["m_InternalIds"]
    provs = cat.data["m_ProviderIds"]
    rts = cat.data["m_resourceTypes"]
    extra_starts = set(it["offset"] for it in cat.extra)
    problemas = []
    for i, e in enumerate(cat.entries):
        iid_i, prov_i, dep_i, _, data_i, pk_i, rt_i = e
        if not (0 <= iid_i < len(iids)) or not iids[iid_i]:
            problemas.append("entry[%d] internalId invalido (%s)" % (i, iid_i))
        if not (0 <= prov_i < len(provs)) or not (0 <= rt_i < len(rts)):
            problemas.append("entry[%d] provider/resourceType fuera de rango" % i)
        if not (0 <= pk_i < len(sim)) or sim[pk_i] is None:
            problemas.append("entry[%d] keys[primaryKey] nulo" % i)
        if dep_i >= 0 and (dep_i >= len(sim) or sim[dep_i] is None):
            problemas.append("entry[%d] keys[dependencyKey] nulo" % i)
        if provs[prov_i].endswith("AssetBundleProvider"):
            if data_i < 0 or data_i not in extra_starts:
                problemas.append("entry[%d] bundle sin AssetBundleRequestOptions" % i)
    check(not problemas, "locator roto: %s" % (problemas[:5],))
    print("locator simulado: %d entradas, %d problemas" % (len(cat.entries), len(problemas)))

    en_bundles = [k.value for k in cat.keys
                  if isinstance(k.value, str) and k.value.endswith(".bundle") and "english" in k.value]
    # resolver dependencias como el runtime: clave -> entradas de su bucket
    resueltos = {}
    for i, (off, ents) in enumerate(cat.buckets):
        resueltos.setdefault(str(sim[i]), []).extend(ents)
    sin_dep = [i for i, e in enumerate(cat.entries)
               if e[2] >= 0 and not resueltos.get(str(sim[e[2]]))]
    check(not sin_dep, "dependencias sin resolver: %s" % (sin_dep[:5],))
    bundle_por_entry = {}
    for i, e in enumerate(cat.entries):
        if provs[e[1]].endswith("AssetBundleProvider"):
            bundle_por_entry[i] = iids[e[0]].replace("\\", "/").rsplit("/", 1)[-1]
    en_file = [n for n in os.listdir(OUT) if "english" in n][0]
    es_tablas = [i for i, e in enumerate(cat.entries)
                 if cat.data["m_resourceTypes"][e[6]]["m_ClassName"].endswith("StringTable")
                 and str(cat.key_str(e[5])).endswith("_es")]
    sin_en = []
    for i in es_tablas:
        deps = resueltos.get(str(sim[cat.entries[i][2]]), [])
        if en_file not in [bundle_por_entry[d] for d in deps if d in bundle_por_entry]:
            sin_en.append(i)
    check(not sin_en, "tablas es que no dependen del bundle ingles: %s" % (sin_en[:5],))
    print("tablas es que dependen del bundle ingles: %d/%d" % (len(es_tablas) - len(sin_en), len(es_tablas)))

    env = ul.load_env(en_path)
    sf = sf_of(env)
    codes = {}
    tables = {}
    bad_rt = 0
    for obj in sf.objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        d, ok = roundtrip(obj)
        if not ok:
            bad_rt += 1
        code = d.get("m_LocaleId", {}).get("m_Code")
        codes[code] = codes.get(code, 0) + 1
        tables[d.get("m_Name")] = d

    print("tablas por code:", codes)
    check(codes.get("en") == 35, "se esperaban 35 tablas en")
    check(codes.get("es") == 35, "se esperaban 35 tablas es")
    check(bad_rt == 0, "round-trip byte-exacto roto en %d objetos" % bad_rt)
    print("round-trip byte-exacto: %s" % ("OK" if bad_rt == 0 else "FALLO en %d" % bad_rt))
    from build_es import load_translations, decompressed_crc
    translations = load_translations()
    for code in ("en", "es"):
        values = {entry["m_Id"]: entry["m_Localized"] for entry in tables["UI_" + code]["m_TableData"]}
        for row in ui_extension.additions():
            expected = row["en"] if code == "en" else translations[row["key"]]
            check(values.get(row["key_id"]) == expected,
                  "suplemento UI_%s incorrecto: %s" % (code, row["key"]))

    # 1) cada _es comparte m_SharedData con su _en y cambia textos
    iguales = 0
    for name, d in tables.items():
        if not name.endswith("_es"):
            continue
        en = tables.get(name[:-3] + "_en")
        check(en is not None, "falta la tabla %s" % (name[:-3] + "_en"))
        if en is None:
            continue
        check(d["m_SharedData"] == en["m_SharedData"], "%s no comparte m_SharedData" % name)
        enval = {str(e["m_Id"]): e["m_Localized"] for e in en["m_TableData"]}
        esval = {str(e["m_Id"]): e["m_Localized"] for e in d["m_TableData"]}
        check(set(enval) == set(esval), "%s: ids distintos" % name)
        iguales += sum(1 for k in esval if esval[k] == enval.get(k))
    print("cadenas es identicas al ingles (excluidas las vacias):", iguales)

    # 2) contenedor
    ab = next(o for o in sf.objects.values() if o.type.name == "AssetBundle")
    paths = [c[0] for c in ab.read_typetree()["m_Container"]]
    print("container:", len(paths), "es:", sum(1 for p in paths if p.endswith("_es.asset")))
    check(len(paths) == 70, "el contenedor deberia tener 70 entradas")
    check(len(es_tablas) == 35, "se esperaban 35 tablas es en el catalogo")
    for i in es_tablas:
        check(iids[cat.entries[i][0]] in paths,
              "asset del catalogo ausente en contenedor: %s" % iids[cat.entries[i][0]])
    label_es = cat.key_index("Locale-es")
    check(set(cat.entries_for_key(label_es)) == set(es_tablas),
          "Locale-es no contiene exactamente las 35 tablas es")

    # Precargas: preservar dependencias y orden, no solo contar el contenedor.
    root = Path(HERE).parent
    for path, count in [(en_path, 35), (loc_path, 1)]:
        candidate = Path(path)
        rows, unchanged = inspect_bundle(
            root / "orig" / "aa" / "StandaloneWindows64" / candidate.name, candidate)
        check(len(rows) == count, "numero de clones inesperado: %s" % candidate.name)
        check(unchanged, "objetos originales modificados: %s" % candidate.name)
        bad = [r["asset"] for r in rows if not r["preload_matches_source"]
               or not r["script_pointer_preserved"] or not r["script_in_clone_preload"]
               or r["shared_data_in_clone_preload"] is False]
        check(not bad, "precargas/scripts incompletos: %s" % bad)
        print("precargas %s: %d/%d OK, originales conservados=%s" % (
            candidate.name, len(rows) - len(bad), count, unchanged))

    # 3) locale
    env2 = ul.load_env(loc_path)
    sf2 = sf_of(env2)
    locs = []
    for obj in sf2.objects.values():
        if obj.type.name != "MonoBehaviour":
            continue
        d, ok = roundtrip(obj)
        check(ok, "round-trip del Locale %r roto" % d.get("m_Name"))
        locs.append((d.get("m_Name"), d.get("m_Identifier", {}).get("m_Code")))
    print("locales:", locs)
    codes_loc = [c for _, c in locs]
    check("en" in codes_loc and "es" in codes_loc, "faltan locales en/es")

    shared = ui_extension.SHARED_BUNDLE
    added = ui_extension.verify_shared(root / "orig" / "aa" / "StandaloneWindows64" / shared,
                                       Path(OUT) / shared)
    print("UI: %d claves nuevas en SharedData y tablas EN/ES" % added)
    for entry in cat.entries:
        if not provs[entry[1]].endswith("AssetBundleProvider"):
            continue
        filename = iids[entry[0]].replace("\\", "/").rsplit("/", 1)[-1]
        path = Path(OUT) / filename
        if not path.is_file():
            continue
        extra = next(item for item in cat.extra if item["offset"] == entry[4])
        options = json.loads(extra["json"])
        check(options["m_Crc"] == decompressed_crc(str(path)), "CRC incorrecto: " + filename)
        check(options["m_BundleSize"] == path.stat().st_size, "tamano incorrecto: " + filename)
    from scene_ui_patch import verify_scenes
    manifest = json.loads((Path(OUT) / "native_ui_manifest.json").read_text(encoding="utf-8"))
    check(manifest.get("layout_enabled") is True, "falta ajuste acotado de fuentes")
    result = verify_scenes(root / "orig" / "scenes", Path(OUT) / "scenes",
                           ui_extension.load_ui_spec(), manifest)
    check(result["verified"] and result["bindings"] == len(ui_extension.load_ui_spec()), "escenas UI no verificadas")
    print("escenas UI: %d enlaces; todos los bytes de objetos verificados" % result["bindings"])

    print()
    if FALLOS:
        print("FALLOS (%d):" % len(FALLOS))
        for f in FALLOS:
            print("  -", f)
        sys.exit(1)
    print("verify_build: TODO OK")


if __name__ == "__main__":
    main()
