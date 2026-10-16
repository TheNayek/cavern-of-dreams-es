"""Lectura/escritura del catalog.json de Addressables de Cavern of Dreams.

Addressables 1.19.19. El catalog.json es un ContentCatalogData serializado a
JSON con 4 blobs binarios en base64:
  m_KeyDataString    : int32 n; por clave: byte tipo; 0/1 -> int32 len + utf8;
                       resto -> 4 u 8 bytes segun tipo
  m_BucketDataString : int32 n; por bucket: int32 keyIdx, int32 nEntries,
                       nEntries * int32 entryIdx
  m_EntryDataString  : int32 n; por entrada: 7 * int32
  m_ExtraDataString  : sin cabecera; por item: byte 0x07, 7bit-len assembly,
                       7bit-len type, int32 len(utf16 json), json utf-16le

Se valida con round-trip (serializar == original) antes de tocar nada.
"""

import base64
import json
import struct
import sys


def _read7(b, i):
    v = 0
    s = 0
    while True:
        c = b[i]
        i += 1
        v |= (c & 0x7F) << s
        if not (c & 0x80):
            break
        s += 7
    return v, i


def _write7(v):
    out = bytearray()
    while True:
        c = v & 0x7F
        v >>= 7
        if v:
            out.append(c | 0x80)
        else:
            out.append(c)
            break
    return bytes(out)


class Key:
    __slots__ = ("type", "value")

    def __init__(self, type_, value):
        self.type = type_
        self.value = value

    def to_bytes(self):
        if self.type in (0, 1):
            data = self.value.encode("utf-8")
            return bytes([self.type]) + struct.pack("<i", len(data)) + data
        return bytes([self.type]) + self.value


class Catalog:
    def __init__(self, path):
        self.path = path
        with open(path, "r", encoding="utf-8-sig") as f:
            self.data = json.load(f)
        self.keys = self._parse_keys()
        self.buckets, _ = self._parse_buckets()
        self.entries, _ = self._parse_entries()
        self.extra, _ = self._parse_extra()

    # ---- parse ----
    def _blob(self, name):
        return base64.b64decode(self.data[name])

    def _parse_keys(self):
        b = self._blob("m_KeyDataString")
        n = struct.unpack_from("<i", b, 0)[0]
        i = 4
        keys = []
        self.key_offsets = []  # offset de byte de cada clave (los buckets las referencian asi)
        for _ in range(n):
            self.key_offsets.append(i)
            t = b[i]
            i += 1
            if t in (0, 1):
                L = struct.unpack_from("<i", b, i)[0]
                i += 4
                keys.append(Key(t, b[i : i + L].decode("utf-8")))
                i += L
            elif t == 6:
                keys.append(Key(t, b[i : i + 8]))
                i += 8
            else:
                keys.append(Key(t, b[i : i + 4]))
                i += 4
        self.offset_to_keyidx = {off: idx for idx, off in enumerate(self.key_offsets)}
        return keys

    def _parse_buckets(self):
        b = self._blob("m_BucketDataString")
        n = struct.unpack_from("<i", b, 0)[0]
        i = 4
        buckets = []
        for _ in range(n):
            k = struct.unpack_from("<i", b, i)[0]
            c = struct.unpack_from("<i", b, i + 4)[0]
            i += 8
            ents = list(struct.unpack_from("<%di" % c, b, i))
            i += 4 * c
            buckets.append((k, ents))
        return buckets, i

    def _parse_entries(self):
        b = self._blob("m_EntryDataString")
        n = struct.unpack_from("<i", b, 0)[0]
        i = 4
        ents = [tuple(struct.unpack_from("<7i", b, i + 28 * k)) for k in range(n)]
        i += 28 * n
        return ents, i

    def _parse_extra(self):
        b = self._blob("m_ExtraDataString")
        i = 0
        items = []
        while i < len(b):
            start = i
            tag = b[i]
            i += 1
            L, i = _read7(b, i)
            asm = b[i : i + L]
            i += L
            L, i = _read7(b, i)
            typ = b[i : i + L]
            i += L
            jlen = struct.unpack_from("<i", b, i)[0]
            i += 4
            js = b[i : i + jlen]
            i += jlen
            items.append(
                {
                    "tag": tag,
                    "asm": asm.decode(),
                    "type": typ.decode(),
                    "json": js.decode("utf-16-le"),
                    "raw": b[start:i],
                    "offset": start,
                }
            )
        return items, i

    # ---- serialize ----
    def _ser_keys(self):
        out = bytearray(struct.pack("<i", len(self.keys)))
        for k in self.keys:
            out += k.to_bytes()
        return bytes(out)

    def _ser_buckets(self):
        out = bytearray(struct.pack("<i", len(self.buckets)))
        for k, ents in self.buckets:
            out += struct.pack("<ii", k, len(ents))
            out += struct.pack("<%di" % len(ents), *ents)
        return bytes(out)

    def _ser_entries(self):
        out = bytearray(struct.pack("<i", len(self.entries)))
        for e in self.entries:
            out += struct.pack("<7i", *e)
        return bytes(out)

    def _ser_extra(self):
        out = bytearray()
        self.extra_new_offsets = []
        for it in self.extra:
            self.extra_new_offsets.append(len(out))
            if "raw" in it:
                out += it["raw"]
            else:
                js = it["json"].encode("utf-16-le")
                out += bytes([it.get("tag", 7)])
                out += _write7(len(it["asm"].encode()))
                out += it["asm"].encode()
                out += _write7(len(it["type"].encode()))
                out += it["type"].encode()
                out += struct.pack("<i", len(js))
                out += js
        return bytes(out)

    def remap_extra_offsets(self):
        """Recalcula el campo `dataIndex` de las entradas tras re-serializar el blob extra.

        Los `dataIndex` son OFFSETS DE BYTE dentro de `m_ExtraDataString`, no indices de
        item: si un item cambia de longitud (p. ej. un `m_Crc` que pasa de 10 a 9 digitos),
        los siguientes se desplazan y las entradas que los referencian leen bytes a mitad
        del JSON -> `ReadObjectFromByteArray` no reconstruye el objeto ->
        `AssetBundleRequestOptions` = null -> AssetBundleProvider hace
        `loadType = LoadType.None` (ruta null) y falla con
        `Invalid path in AssetBundleProvider: ''`, arrastrando ademas todas las tablas
        que viven en ese bundle. Devuelve cuantas entradas se han corregido.
        """
        self._ser_extra()
        mapping = {}
        for it, new in zip(self.extra, self.extra_new_offsets):
            mapping[it["offset"]] = new
        fixed = 0
        for i, e in enumerate(self.entries):
            if e[4] < 0:
                continue
            if e[4] not in mapping:
                raise RuntimeError(
                    "entry[%d]: dataIndex=%d no es el inicio de ningun item extra" % (i, e[4])
                )
            if mapping[e[4]] != e[4]:
                e = list(e)
                e[4] = mapping[e[4]]
                self.entries[i] = tuple(e)
                fixed += 1
        return fixed

    def check_extra(self):
        """Cada `dataIndex` >= 0 debe apuntar al inicio de un item de `m_ExtraDataString`."""
        errs = []
        starts = set(getattr(self, "extra_new_offsets", [it["offset"] for it in self.extra]))
        for i, e in enumerate(self.entries):
            if e[4] >= 0 and e[4] not in starts:
                errs.append(
                    "entry[%d].dataIndex=%d no apunta al inicio de un item extra (starts=%s)"
                    % (i, e[4], sorted(starts))
                )
        return errs

    def check_roundtrip(self):
        checks = {
            "m_KeyDataString": self._ser_keys(),
            "m_BucketDataString": self._ser_buckets(),
            "m_EntryDataString": self._ser_entries(),
            "m_ExtraDataString": self._ser_extra(),
        }
        ok = True
        for name, ser in checks.items():
            orig = self._blob(name)
            if ser != orig:
                ok = False
                print(
                    "  MISMATCH %s: orig=%d ser=%d" % (name, len(orig), len(ser))
                )
                for j in range(min(len(orig), len(ser))):
                    if orig[j] != ser[j]:
                        print("    first diff at byte", j, orig[j], ser[j])
                        break
        return ok

    # ---- mutadores (solo anaden al final; preservan indices existentes) ----
    def add_string_key(self, s):
        off = len(self._ser_keys())
        self.keys.append(Key(0, s))
        self.key_offsets.append(off)
        self.offset_to_keyidx[off] = len(self.keys) - 1
        return len(self.keys) - 1, off

    def add_bucket(self, off, entries):
        self.buckets.append((off, list(entries)))

    def add_internal_id(self, s):
        self.data["m_InternalIds"].append(s)
        return len(self.data["m_InternalIds"]) - 1

    def add_entry(self, t):
        self.entries.append(tuple(t))
        return len(self.entries) - 1

    def set_extra_json(self, i, obj):
        item = self.extra[i]
        item.pop("raw", None)
        item["json"] = json.dumps(obj, separators=(",", ":"))

    def keys_for_entry(self, entry_idx):
        res = []
        for off, ents in self.buckets:
            if entry_idx in ents:
                res.append(self.offset_to_keyidx.get(off))
        return res

    def save(self, path):
        fixed = self.remap_extra_offsets()
        if fixed:
            print("catalogo: %d dataIndex de extra reajustados" % fixed)
        errs = self.check_layout() + self.check_extra()
        if errs:
            raise RuntimeError("catalogo inconsistente:\n  " + "\n  ".join(errs[:10]))
        out = json.loads(json.dumps(self.data))
        out["m_KeyDataString"] = base64.b64encode(self._ser_keys()).decode()
        out["m_BucketDataString"] = base64.b64encode(self._ser_buckets()).decode()
        out["m_EntryDataString"] = base64.b64encode(self._ser_entries()).decode()
        out["m_ExtraDataString"] = base64.b64encode(self._ser_extra()).decode()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    # ---- helpers ----
    def check_layout(self):
        """Invariante del catalogo de Addressables: `buckets` 1:1 con `keys` y en el
        MISMO orden (`buckets[i].keyOffset == keys[i].offset`).

        Addressables empareja bucket<->clave por posicion. Si se rompe, las direcciones
        nuevas resuelven a entradas equivocadas o a ninguna y la carga falla con
        `Invalid path in AssetBundleProvider: ''`. Devuelve la lista de errores.
        """
        errs = []
        if len(self.keys) != len(self.buckets):
            errs.append("keys=%d != buckets=%d" % (len(self.keys), len(self.buckets)))
        for i in range(min(len(self.keys), len(self.buckets))):
            if self.buckets[i][0] != self.key_offsets[i]:
                errs.append(
                    "bucket[%d].keyOffset=%d != keys[%d].offset=%d (%r)"
                    % (i, self.buckets[i][0], i, self.key_offsets[i], self.key_str(i))
                )
        return errs

    def key_str(self, idx):
        k = self.keys[idx]
        return k.value if isinstance(k.value, str) else "<%d:%s>" % (k.type, k.value.hex())

    def entries_for_key(self, idx):
        off = self.key_offsets[idx]
        for k, ents in self.buckets:
            if k == off:
                return ents
        return []

    def key_index(self, name):
        for i, k in enumerate(self.keys):
            if isinstance(k.value, str) and k.value == name:
                return i
        return None

    def key_at_offset(self, off):
        i = self.offset_to_keyidx.get(off)
        return None if i is None else self.key_str(i)

    def dump_key(self, name):
        idx = self.key_index(name)
        if idx is None:
            print("key %r not found" % name)
            return
        print("key %r idx=%d off=%d buckets(entries)=%s" % (name, idx, self.key_offsets[idx], self.entries_for_key(idx)))
        for ei in self.entries_for_key(idx):
            e = self.entries[ei]
            rts = self.data["m_resourceTypes"]
            rt = rts[e[6]].get("m_ClassName") if 0 <= e[6] < len(rts) else "?"
            print("  entry %d: rt=%s iid=%r" % (ei, rt, self.data["m_InternalIds"][e[0]]))
            print("     f1(prov)=%r f2=%r(idx)/%r(off) f3=%r f4=%r(idx)/%r(off) f5(primary=%r) f6=%r"
                  % (
                      self.data["m_ProviderIds"][e[1]],
                      e[2], self.key_at_offset(e[2]),
                      e[3],
                      e[4], self.key_at_offset(e[4]),
                      self.key_str(e[5]) if 0 <= e[5] < len(self.keys) else e[5],
                      e[6],
                  ))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    cat = Catalog(path)
    print("keys", len(cat.keys), "buckets", len(cat.buckets), "entries", len(cat.entries), "extra", len(cat.extra))
    print("roundtrip:", cat.check_roundtrip())
    print("resourceTypes:", [r.get("m_ClassName") for r in cat.data["m_resourceTypes"]])
    for name in sys.argv[2:]:
        cat.dump_key(name)


if __name__ == "__main__":
    main()
