"""Construye el parche de traduccion al espanol.

Enfoque (ver docs/addressables_catalog.md):
- Clona cada StringTable _en en una *_es (m_LocaleId 'es', textos traducidos o
  fallback a ingles) y las APPENDEA al bundle ingles (objetos nuevos + entradas en
  m_Container/m_PreloadTable). El bundle ingles queda ampliado, no reemplazado.
- Clona el Locale 'English (en)' en 'Spanish (es)' y lo APPENDEA al bundle de locales.
- Amplia UI SharedTableData y las tablas UI EN/ES con claves adicionales.
- Parchea catalog.json y actualiza hash/CRC/tamano de los tres bundles.
- Genera las escenas con enlaces nativos de localizacion y ajuste de texto.

Salida en build/out/: tres bundles, catalog.json, scenes/ y manifiesto verificable.
"""

import copy
import hashlib
import json
import os
import shutil
import sys
import uuid
import zlib
from pathlib import Path

import UnityPy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unity_localization as ul
import ui_extension
from catalog_tool import Catalog
from UnityPy.enums import ClassIDType
from UnityPy.files.ObjectReader import ObjectReader
from UnityPy.helpers.TypeTreeHelper import write_typetree
from UnityPy.streams import EndianBinaryWriter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ORIG = os.path.join(ROOT, "orig", "aa")
OUT = os.path.join(ROOT, "build", "out")
ES_JSON = os.path.join(ROOT, "translation", "es.json")
EXTRACTED = os.path.join(ROOT, "extracted")

LANG_CODE = "es"
LANG_NAME = "Spanish (es)"
LABEL = "Locale-es"

BUNDLE_EN_NAME = "localization-string-tables-english(en)_assets_all.bundle"
BUNDLE_LOCALES_NAME = "localization-locales_assets_all.bundle"
BUNDLE_PATH_PREFIX = r"{UnityEngine.AddressableAssets.Addressables.RuntimePath}\StandaloneWindows64" + "\\"

# FUENTE PRISTINA: siempre desde orig/aa, NUNCA desde la instalacion del juego
# (si no, cada build re-appende las tablas es sobre las ya instaladas).
SRC = os.path.join(ORIG, "StandaloneWindows64")
SRC_EN = os.path.join(SRC, BUNDLE_EN_NAME)
SRC_LOCALES = os.path.join(SRC, BUNDLE_LOCALES_NAME)


def serialized_file(env):
    for f in env.file.files.values():
        if f.__class__.__name__ == "SerializedFile":
            return f
    raise RuntimeError("sin SerializedFile")


def write_tree(d, tree, sf):
    w = EndianBinaryWriter(endian="<")
    write_typetree(d, tree, w, sf)
    return w.bytes


def add_object(sf, template, raw, new_path_id):
    nr = ObjectReader(
        assets_file=sf,
        reader=template.reader,
        path_id=new_path_id,
        type_id=template.type_id,
        serialized_type=template.serialized_type,
        class_id=template.class_id,
        type=ClassIDType.MonoBehaviour,
        byte_start=0,
        byte_size=len(raw),
        is_destroyed=None,
        is_stripped=None,
        data=raw,
    )
    sf.objects[new_path_id] = nr
    sf.mark_changed()
    return nr


def register_in_container(sf, assets):
    """Register (asset_path, new_path_id, source_path_id) with source preloads.

    A clone needs the same scripts/shared objects as its source. Merely listing
    the new object lets UnityPy read it but leaves Unity unable to load its type.
    """
    ab = None
    for obj in sf.objects.values():
        if obj.type.name == "AssetBundle":
            ab = obj
            break
    d = ab.read_typetree()
    sources = {
        info["asset"]["m_PathID"]: info
        for _, info in d["m_Container"] if info["asset"]["m_FileID"] == 0
    }
    for asset_path, path_id, source_path_id in assets:
        source = sources.get(source_path_id)
        if source is None:
            raise ValueError("source asset not found in container: %s" % source_path_id)
        start, size = source["preloadIndex"], source["preloadSize"]
        if start < 0 or size <= 0 or start + size > len(d["m_PreloadTable"]):
            raise ValueError("invalid source preload range: %s" % source_path_id)
        preload = copy.deepcopy(d["m_PreloadTable"][start:start + size])
        replaced = False
        for dependency in preload:
            if dependency == source["asset"]:
                dependency["m_PathID"] = path_id
                replaced = True
        if not replaced:
            raise ValueError("source asset missing from its preload: %s" % source_path_id)
        pre_idx = len(d["m_PreloadTable"])
        pp = {"m_FileID": 0, "m_PathID": path_id}
        d["m_PreloadTable"].extend(preload)
        d["m_Container"].append([asset_path, {
            "preloadIndex": pre_idx, "preloadSize": len(preload), "asset": pp,
        }])
    ab.save_typetree(d)


def next_path_id(sf):
    pid = -1
    while pid in sf.objects:
        pid -= 1
    return pid


def build_tables(es):
    """Devuelve lista de dicts con info de cada tabla es (sin objetos Unity todavia)."""
    with open(os.path.join(EXTRACTED, "strings_en.json"), encoding="utf-8") as f:
        en_tables = json.load(f)
    cat = Catalog(os.path.join(ORIG, "catalog.json"))
    env = ul.load_env(SRC_EN)
    sf = serialized_file(env)
    template = None
    objs_by_name = {}
    for obj in sf.objects.values():
        if obj.type.name == "MonoBehaviour":
            try:
                _, d, _ = ul.load_mono(obj)
            except Exception:
                continue
            objs_by_name[d.get("m_Name")] = obj
            if template is None:
                template = obj

    infos = []
    for t in en_tables:
        en_obj = objs_by_name.get(t["asset"])
        assert en_obj is not None, t["asset"]
        tree, d, registry = ul.load_mono(en_obj)
        d["m_LocaleId"]["m_Code"] = LANG_CODE
        stem = t["asset"][: -len("_en")]
        d["m_Name"] = stem + "_es"
        # rellenar traducciones
        by_id = {str(x["id"]): x for x in t["entries"]}
        for e in d["m_TableData"]:
            k = by_id[str(e["m_Id"])]["key"]
            e["m_Localized"] = es.get("%s|%s" % (t["collection"], k), by_id[str(e["m_Id"])]["localized"])
        if t["collection"] == "UI":
            ui_extension.append_table_entries(d, "es", es)
        raw = ul.save_mono(en_obj, tree, d, registry)
        addr_idx = cat.key_index(t["address"])
        entry_idx = cat.entries_for_key(addr_idx)[0]
        en_internal = cat.data["m_InternalIds"][cat.entries[entry_idx][0]]
        es_internal = en_internal[: -len("_en.asset")] + "_es.asset"
        infos.append(
            {
                "collection": t["collection"],
                "address": "%s_%s" % (t["collection"], LANG_CODE),
                "asset_name": d["m_Name"],
                "internal_id": es_internal,
                "source_path_id": en_obj.path_id,
                "raw": raw,
                "en_entry": entry_idx,
            }
        )
    return cat, infos, template, env, sf


def build_locale():
    env = ul.load_env(SRC_LOCALES)
    sf = serialized_file(env)
    template = None
    src_obj = None
    src = None
    tree = None
    registry = b""
    for obj in sf.objects.values():
        if obj.type.name == "MonoBehaviour":
            t, d, reg = ul.load_mono(obj)
            if d.get("m_Identifier", {}).get("m_Code") == "en":
                src = d
                src_obj = obj
                tree = t
                registry = reg
                template = obj
    assert src is not None
    d = json.loads(json.dumps(src))
    d["m_Name"] = LANG_NAME
    d["m_Identifier"]["m_Code"] = LANG_CODE
    d["m_LocaleName"] = LANG_NAME
    raw = ul.save_mono(src_obj, tree, d, registry)
    return raw, template, env, sf


def load_translations():
    """Fusiona todos los translation/*.json (orden alfabetico; los posteriores sobrescriben)."""
    tr_dir = os.path.join(ROOT, "translation")
    merged = {}
    for name in sorted(os.listdir(tr_dir)):
        if not name.endswith(".json") or name.startswith("_"):
            continue
        path = os.path.join(tr_dir, name)
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        for k, v in data.items():
            if not isinstance(v, str):
                raise TypeError("%s: valor no textual en %r" % (name, k))
        merged.update(data)
    return merged


def bundle_key_and_file(cat, needle):
    """Devuelve (clave_con_hash, nombre_de_fichero_en_disco) de un bundle.

    En el catalogo la CLAVE incluye el hash del contenido, pero el internalId (la
    ruta que Unity abre en disco) va SIN hash. El fichero real es el del internalId.
    """
    for i, k in enumerate(cat.keys):
        if isinstance(k.value, str) and k.value.endswith(".bundle") and needle in k.value:
            e = cat.entries_for_key(i)[0]
            iid = cat.data["m_InternalIds"][cat.entries[e][0]]
            fname = iid.replace("\\", "/").rsplit("/", 1)[-1]
            return k.value, fname
    raise RuntimeError("bundle no encontrado: " + needle)


def extra_for_bundle_key(cat, bundle_name):
    key_idx = cat.key_index(bundle_name)
    kstr = cat.keys[key_idx].value
    h = kstr.split("_")[-1].split(".bundle")[0]
    for i, it in enumerate(cat.extra):
        if json.loads(it["json"])["m_Hash"] == h:
            return i
    raise RuntimeError("extra no encontrado para %s" % bundle_name)


def decompressed_crc(path):
    """CRC que valida Unity al cargar un bundle.

    NO es el CRC del fichero comprimido en disco: es zlib.crc32 del CONTENIDO
    DESCOMPRIMIDO (el/los ficheros serializados del bundle). Verificado contra el
    valor que reporta el propio juego en su Player.log cuando el CRC no coincide.
    """
    env = UnityPy.load(path)
    parts = []
    for f in env.file.files.values():
        if f.__class__.__name__ == "SerializedFile":
            parts.append(bytes(f.save()))
    return zlib.crc32(b"".join(parts)) & 0xFFFFFFFF


def main():
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        if name.endswith(".bundle"):
            os.remove(os.path.join(OUT, name))
    es = load_translations()
    translated = sum(1 for v in es.values() if v)
    print("Traducciones cargadas:", translated, "/", len(es))

    # descubre el nombre real del bundle ingles (con hash) a partir del catalogo
    cat_full = Catalog(os.path.join(ORIG, "catalog.json"))
    en_bundle_name, en_bundle_file = bundle_key_and_file(cat_full, "english")
    locales_bundle_name, locales_bundle_file = bundle_key_and_file(cat_full, "locales")
    shared_bundle_name, shared_bundle_file = bundle_key_and_file(cat_full, "assets-shared")

    cat, infos, st_template, en_env, en_sf = build_tables(es)

    # --- bundle ingles: anadir tablas es ---
    new_en_path = os.path.join(OUT, en_bundle_file)
    shutil.copyfile(SRC_EN, new_en_path)
    en_env = ul.load_env(new_en_path)
    en_sf = serialized_file(en_env)
    ui_extension.extend_english(en_sf)
    added = {}
    container_entries = []
    for info in infos:
        pid = next_path_id(en_sf)
        add_object(en_sf, st_template, info["raw"], pid)
        container_entries.append((info["internal_id"], pid, info["source_path_id"]))
        added[info["address"]] = pid
    register_in_container(en_sf, container_entries)
    data = en_env.file.save(packer="lz4")
    with open(new_en_path, "wb") as f:
        f.write(data)
    print("bundle ingles:", len(data), "bytes,", len(infos), "tablas es")

    # --- bundle locales: anadir Locale-es ---
    loc_raw, loc_template, loc_env, loc_sf = build_locale()
    new_loc_path = os.path.join(OUT, locales_bundle_file)
    shutil.copyfile(SRC_LOCALES, new_loc_path)
    loc_env = ul.load_env(new_loc_path)
    loc_sf = serialized_file(loc_env)
    loc_pid = next_path_id(loc_sf)
    add_object(loc_sf, loc_template, loc_raw, loc_pid)
    register_in_container(loc_sf, [(
        "Assets/Text/%s.asset" % LANG_NAME, loc_pid, loc_template.path_id,
    )])
    data = loc_env.file.save(packer="lz4")
    with open(new_loc_path, "wb") as f:
        f.write(data)
    print("bundle locales:", len(data), "bytes")

    # Las nuevas claves UI comparten los IDs entre SharedTableData y EN/ES.
    new_shared_path = os.path.join(OUT, shared_bundle_file)
    ui_extension.build_shared(os.path.join(SRC, shared_bundle_file), new_shared_path)
    print("bundle compartido: %d claves UI nuevas" % len(ui_extension.additions()))

    # --- catalogo ---
    # IMPORTANTE: claves y buckets deben quedar 1:1 y en el MISMO orden
    # (buckets[i].keyOffset == keys[i].offset): Addressables empareja por posicion.
    for info in infos:
        addr_idx, _ = cat.add_string_key(info["address"])
        guid_idx, _ = cat.add_string_key(uuid.uuid4().hex)
        iid_idx = cat.add_internal_id(info["internal_id"])
        e_idx = cat.add_entry((iid_idx, 1, 179, -1116741280, -1, addr_idx, 2))
        cat.add_bucket(cat.key_offsets[addr_idx], [e_idx])
        cat.add_bucket(cat.key_offsets[guid_idx], [e_idx])
    # bucket del label Locale-es con todas las entradas (clave justo antes del bucket)
    es_entries = [cat.entries_for_key(cat.key_index(i["address"]))[0] for i in infos]
    label_idx, _ = cat.add_string_key(LABEL)
    cat.add_bucket(cat.key_offsets[label_idx], es_entries)

    # Locale-es
    loc_addr_idx, _ = cat.add_string_key(LANG_NAME)
    loc_guid_idx, _ = cat.add_string_key(uuid.uuid4().hex)
    loc_iid = cat.add_internal_id("Assets/Text/%s.asset" % LANG_NAME)
    loc_e = cat.add_entry((loc_iid, 1, 2, 357696813, -1, loc_addr_idx, 3))
    cat.add_bucket(cat.key_offsets[loc_addr_idx], [loc_e])
    cat.add_bucket(cat.key_offsets[loc_guid_idx], [loc_e])
    # anadir al label existente "Locale"
    loc_label_idx = cat.key_index("Locale")
    for j, (off, ents) in enumerate(cat.buckets):
        if off == cat.key_offsets[loc_label_idx]:
            cat.buckets[j] = (off, ents + [loc_e])
            break

    # actualizar extra de los dos bundles
    for bundle_name, path in [(en_bundle_name, new_en_path), (locales_bundle_name, new_loc_path),
                              (shared_bundle_name, new_shared_path)]:
        b = open(path, "rb").read()
        xi = extra_for_bundle_key(cat, bundle_name)
        obj = json.loads(cat.extra[xi]["json"])
        obj["m_Hash"] = hashlib.md5(b).hexdigest()
        obj["m_Crc"] = decompressed_crc(path)
        obj["m_BundleSize"] = len(b)
        cat.set_extra_json(xi, obj)
        print("extra x%d (%s): size=%d crc=%08x" % (xi, bundle_name[:30], len(b), obj["m_Crc"]))

    cat.save(os.path.join(OUT, "catalog.json"))
    from scene_ui_patch import build_scenes
    manifest = build_scenes(Path(ROOT) / "orig" / "scenes", Path(OUT) / "scenes",
                            ui_extension.load_ui_spec())
    Path(OUT, "native_ui_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("catalogo: keys=%d buckets=%d entries=%d" % (len(cat.keys), len(cat.buckets), len(cat.entries)))
    print("OUT:", OUT)


if __name__ == "__main__":
    main()

