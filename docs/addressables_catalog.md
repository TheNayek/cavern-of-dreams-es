# Estructura de datos de Addressables + Unity Localization

Juego: **Cavern of Dreams** — Unity **2019.4.17f1**, Mono x64, Steam AppID 2059660,
build 13947382. Sin cifrado. Datos en
`Cavern of Dreams_Data\StreamingAssets\aa\`.

## Bundles (StandaloneWindows64/)

| Fichero | Contenido |
|---|---|
| `defaultlocalgroup_assets_all_<hash>.bundle` | `LocalizationSettings` (`Assets/Text/Localization Settings.asset`) |
| `localization-assets-shared_assets_all.bundle` | 35 `SharedTableData` |
| `localization-locales_assets_all.bundle` | 1 `Locale` (`English (en)`) |
| `localization-string-tables-english(en)_assets_all.bundle` | 35 `StringTable` (`_en`) |
| `localization-string-tables-french_assets_all.bundle` | 13 `StringTable` (`_fr`, **stubs vacíos**) |

Las direcciones internas de assets dentro de un bundle son rutas tipo
`Assets/Text/Cave/Cave_en.asset`; el nombre del fichero del bundle aparece también
como subcarpeta en los internalIds de bundle.

## catalog.json (ContentCatalogData serializado a JSON)

4 blobs base64 (`tools/catalog_tool.py` los parsea y re-serializa con round-trip verificado):

- **`m_KeyDataString`**: `int32 n`; por clave `byte tipo`:
  - tipo 0/1 → `int32 len` + UTF-8
  - tipo 6 → 8 bytes
  - resto (p.ej. 4) → 4 bytes
  - **Las claves tipo 4 son hashes numéricos de bundle** (p.ej. `60e16fbd` para el
    bundle inglés, `a761df8d` para el francés). Son las *dependencias* de las tablas.
- **`m_BucketDataString`**: `int32 n`; por bucket: `int32 keyOffset`, `int32 nEntries`,
  `nEntries * int32 entryIdx`.
  - **`keyOffset` es el offset de byte** de la clave dentro de `m_KeyDataString`
    (NO un índice). Un bucket = una "address/label" → lista de entradas.
- **`m_EntryDataString`**: `int32 n`; por entrada **7 int32**:
  `(internalIdIdx, providerIdx, dependencyKeyIdx, dependencyHash, extraDataOffset|-1, primaryKeyIdx, resourceTypeIdx)`
  - `dependencyKeyIdx` / `primaryKeyIdx` → **índices** en la lista de claves.
  - `extraDataOffset` → **offset de byte** en `m_ExtraDataString` (solo bundles).
  - `resourceTypeIdx` → índice en `m_resourceTypes`.
- **`m_ExtraDataString`**: sin cabecera; por item: `byte tag 0x07`,
  `7bit-len assembly`, `7bit-len type`, `int32 len` + JSON **UTF-16LE**.
  Son `AssetBundleRequestOptions` con `m_Hash` (md5 hex), `m_Crc`, `m_BundleName`,
  `m_BundleSize`, etc. **Aquí se actualiza el hash/CRC/tamaño al repackear.**

### resourceTypes (índices)
0 `ResourceProviders.IAssetBundleResource` · 1 `Tables.SharedTableData` ·
2 `Tables.StringTable` · 3 `Locale` · 4 `Settings.LocalizationSettings`

### providers
0 `ResourceProviders.AssetBundleProvider` (bundles) ·
1 `ResourceProviders.BundledAssetProvider` (assets dentro de bundles)

### Claves por colección (ejemplo "Cave")
- `Assets/Text/Cave/Cave Shared Data.asset` → SharedTableData (dep: bundle shared)
- `56cacb14bafbbf945b82348691b31026` → GUID del SharedTableData (`m_TableCollectionNameGuidString`)
- `<colección>_en` (p.ej. `Cave_en`) → **address** de la StringTable (dep: hash del bundle)
- `<guid string>` → GUID del asset StringTable (address alternativa)
- Labels: `Locale` (assets `Locale`), `Locale-en` / `Locale-fr` (StringTables)
- `Preload` label

La **address** de una StringTable es `<m_TableCollectionName>_<code>`, que **no** siempre
coincide con el nombre del asset (p.ej. `PostFinale_en.asset` tiene address
`Epilogue_en`; `ItemHints_en.asset` → `SageAdvice_en`).

## ScriptableObjects (MonoBehaviour; typetree generado desde `Managed/`)

`tools/dump_assets.py` los vuelca. Campos relevantes:

- **Locale**: `m_Name`, `m_Identifier.m_Code` (p.ej. `en`), `m_LocaleName`,
  `m_SortOrder`. Script pathID `-1570598082612035584`.
- **SharedTableData**: `m_TableCollectionName`, `m_TableCollectionNameGuidString`,
  `m_Entries[{m_Id, m_Key, m_Metadata}]`. Script pathID `-6107132964447977472`.
- **StringTable**: `m_Name`, `m_LocaleId.m_Code`, `m_SharedData` (PPtr al
  SharedTableData, `m_FileID=1`), `m_TableData[{m_Id, m_Localized, m_Metadata}]`.
  Script pathID `-5446492885367128064`.
- **LocalizationSettings**: `m_StartupSelectors`, `m_AvailableLocales`,
  `m_AssetDatabase`, `m_StringDatabase`, `m_ProjectLocaleIdentifier.m_Code`.
  **No lista locales explícitamente**: se descubren por el label `Locale` y cada
  tabla por su address `<colección>_<code>` / label `Locale-<code>`. Por tanto,
  añadir español **no requiere editar LocalizationSettings**.

## Trampa UnityPy

`read_typetree()` falla con `Failed to get ref type node` por el nodo
`ManagedReferencesRegistry`. Workaround en `tools/unity_localization.py`:
generar el typetree desde `Managed/` (`TypeTreeGenerator`), **descartar el nodo
`ManagedReferencesRegistry`** y leer con `check_read=False`.

`ObjectReader.write` emite `self.data` si está fijado, así que se **pueden añadir
objetos nuevos** a un `SerializedFile` (crear `ObjectReader` con `data` = bytes del
MonoBehaviour, `type_id` de un tipo MonoBehaviour existente y un `path_id` nuevo),
reescribir el bundle y **registrar el asset en `m_Container` y `m_PreloadTable` del
objeto `AssetBundle`**.
