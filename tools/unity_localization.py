"""Helpers para leer los assets de Unity.Localization de Cavern of Dreams.

El juego usa Unity 2019.4.17f1 (Mono). Los ScriptableObjects de localizacion
(StringTable, SharedTableData, Locale) son MonoBehaviours que contienen campos
[SerializeReference] (MetadataCollection / ManagedReferencesRegistry). UnityPy
no puede leerlos de serie ("Failed to get ref type node"), asi que generamos el
typetree desde las DLLs del juego y descartamos el nodo final
ManagedReferencesRegistry (que no necesitamos y sobran bytes al final).
"""

import os

import UnityPy
from UnityPy.helpers.TypeTreeGenerator import TypeTreeGenerator
from UnityPy.helpers.TypeTreeNode import TypeTreeNode
from UnityPy.helpers.TypeTreeHelper import read_typetree, write_typetree
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter

GAME_ROOT = os.environ.get("CAVERN_GAME_ROOT", r"E:\SteamLibrary\steamapps\common\Cavern of Dreams")
DATA_DIR = os.path.join(GAME_ROOT, "Cavern of Dreams_Data")
MANAGED_DIR = os.path.join(DATA_DIR, "Managed")
AA_DIR = os.path.join(DATA_DIR, "StreamingAssets", "aa")
BUNDLES_DIR = os.path.join(AA_DIR, "StandaloneWindows64")
UNITY_VERSION = "2019.4.17f1"

BUNDLE_EN = os.path.join(BUNDLES_DIR, "localization-string-tables-english(en)_assets_all.bundle")
BUNDLE_FR = os.path.join(BUNDLES_DIR, "localization-string-tables-french_assets_all.bundle")
BUNDLE_SHARED = os.path.join(BUNDLES_DIR, "localization-assets-shared_assets_all.bundle")
BUNDLE_LOCALES = os.path.join(BUNDLES_DIR, "localization-locales_assets_all.bundle")

_generator = None
_trees = {}


def generator():
    global _generator
    if _generator is None:
        _generator = TypeTreeGenerator(UNITY_VERSION)
        _generator.load_local_dll_folder(MANAGED_DIR)
    return _generator


def _strip_refs(nodes):
    """Descarta el nodo ManagedReferencesRegistry y sus descendientes."""
    out = []
    skip_level = None
    for n in nodes:
        lvl = n.m_Level
        if skip_level is not None:
            if lvl > skip_level:
                continue
            skip_level = None
        if n.m_Type == "ManagedReferencesRegistry":
            skip_level = lvl
            continue
        out.append(
            {
                "m_Level": lvl,
                "m_Type": n.m_Type,
                "m_Name": n.m_Name,
                "m_MetaFlag": getattr(n, "m_MetaFlag", 0),
            }
        )
    return out


def tree_for(assembly, fullname):
    key = (assembly, fullname)
    if key not in _trees:
        nodes = _strip_refs(generator().get_nodes(assembly, fullname))
        _trees[key] = TypeTreeNode.from_list(nodes)
    return _trees[key]


STRING_TABLE_TREE = lambda: tree_for(
    "Unity.Localization.dll", "UnityEngine.Localization.Tables.StringTable"
)
SHARED_TABLE_TREE = lambda: tree_for(
    "Unity.Localization.dll", "UnityEngine.Localization.Tables.SharedTableData"
)
LOCALE_TREE = lambda: tree_for("Unity.Localization.dll", "UnityEngine.Localization.Locale")


def load_env(path):
    env = UnityPy.load(path)
    env.typetree_generator = generator()
    return env


def read_mono(obj, tree):
    reader = EndianBinaryReader(obj.get_raw_data(), endian="<")
    return read_typetree(tree, reader, as_dict=True, byte_size=None, check_read=False)


# --- Serializacion FIEL usando el typetree EMBEBIDO en el bundle -----------------
# El typetree generado desde las DLLs NO es fiel: no aplica el padding tras m_Enabled
# (desplaza m_Script 3 bytes, corrompiendo el PPtr) y omite la seccion final
# ManagedReferencesRegistry. El bundle SI trae su propio typetree (obj.serialized_type
# .nodes). Lo usamos descartando solo ManagedReferencesRegistry (que UnityPy no sabe
# resolver: "Failed to get ref type node") y re-adjuntamos esa seccion tal cual estaba.
SKIP_TYPES = ("ManagedReferencesRegistry",)


def embedded_tree(obj):
    """Typetree del propio bundle, sin el nodo ManagedReferencesRegistry."""
    root = obj.serialized_type.nodes
    if root is None:
        return None
    flat = []

    def walk(n, lvl):
        if n.m_Type in SKIP_TYPES:
            return
        flat.append(
            {
                "m_Level": lvl,
                "m_Type": n.m_Type,
                "m_Name": n.m_Name,
                "m_MetaFlag": getattr(n, "m_MetaFlag", 0),
            }
        )
        for c in (n.m_Children or []):
            walk(c, lvl + 1)

    walk(root, 0)
    return TypeTreeNode.from_list(flat)


def load_mono(obj, tree=None):
    """Devuelve (tree, dict, registry_bytes) de un MonoBehaviour.

    `registry_bytes` es la seccion ManagedReferencesRegistry del objeto original,
    que hay que volver a adjuntar al reescribir (save_mono).
    """
    t = tree if tree is not None else embedded_tree(obj)
    raw = bytes(obj.get_raw_data())
    d = read_typetree(t, EndianBinaryReader(raw, endian="<"), as_dict=True, byte_size=None, check_read=False)
    w = EndianBinaryWriter(endian="<")
    write_typetree(d, t, w, obj.assets_file)
    registry = raw[len(w.bytes):]
    return t, d, registry


def save_mono(obj, tree, d, registry):
    """Reescribe el objeto: cuerpo segun el typetree embebido + registry original."""
    w = EndianBinaryWriter(endian="<")
    write_typetree(d, tree, w, obj.assets_file)
    return w.bytes + registry


def iter_monobehaviours(path):
    env = load_env(path)
    for obj in env.objects:
        if obj.type.name == "MonoBehaviour":
            yield obj
