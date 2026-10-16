"""Focused regression tests for scene byte edits and native component appends."""

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch as mock_patch

import scene_ui_patch as patch


def template():
    return {"m_GameObject": patch._pp(31), "m_Enabled": 1,
            "m_Script": patch._pp(675, 1),
            "m_StringReference": {"m_TableReference": {"m_TableCollectionName": patch.UI_GUID},
                                  "m_TableEntryReference": {"m_KeyId": 73496326780858368,
                                                            "m_Key": ""}},
            "m_UpdateString": {"m_PersistentCalls": {"m_Calls": [
                {"m_Target": patch._pp(658), "m_MethodName": "set_text",
                 "m_Mode": 0, "m_CallState": 1}]}}}


def template_bytes(data):
    return (patch._pack_pp(data["m_GameObject"]) + b"\x01\0\0\0"
            + patch._pack_pp(data["m_Script"]) + b"unchanged fields: "
            + patch.UI_GUID.encode() + struct.pack("<q", 73496326780858368)
            + b"other unchanged fields:" + patch._pack_pp(patch._pp(658))
            + b"set_text\0registry and final padding\0\0")


class BytePatchTests(unittest.TestCase):
    def test_binding_preserves_guid_script_registry_and_other_bytes(self):
        data = template()
        before = template_bytes(data)
        after, ranges = patch._patch_binding(before, data, 42, 51236, 999999999)
        self.assertEqual(len(before), len(after))
        self.assertEqual(after[:12], patch._pack_pp(patch._pp(42)))
        self.assertEqual(after[16:28], before[16:28])
        self.assertIn(patch.UI_GUID.encode(), after)
        self.assertTrue(after.endswith(b"registry and final padding\0\0"))
        patch._only_ranges(before, after, ranges)
        self.assertEqual(struct.unpack_from("<q", after, ranges[1][0])[0], 999999999)
        self.assertEqual(after[ranges[2][0]:ranges[2][0] + 12], patch._pack_pp(patch._pp(51236)))

    def test_binding_rejects_missing_or_ambiguous_key_and_target(self):
        data = template()
        raw = template_bytes(data)
        for pattern in (struct.pack("<q", 73496326780858368), patch._pack_pp(patch._pp(658))):
            with self.subTest(pattern=pattern):
                for bad in (raw.replace(pattern, b"!" * len(pattern)), raw + pattern):
                    with self.assertRaisesRegex(ValueError, "exactly one"):
                        patch._patch_binding(bad, data, 42, 51236, 999)

    def test_binding_rejects_header_or_event_mismatch(self):
        data = template()
        raw = template_bytes(data)
        with self.assertRaisesRegex(ValueError, "header mismatch"):
            patch._patch_binding(b"!" + raw[1:], data, 42, 51236, 999)
        patch._calls(data)[0]["m_MethodName"] = "unrelated_method"
        with self.assertRaisesRegex(ValueError, "unsupported"):
            patch._patch_binding(raw, data, 42, 51236, 999)

    def test_font_patch_changes_only_bool_min_max_preserving_padding(self):
        data = dict(zip(patch.FONT_FIELDS, (26.0, 26.0, 400, 0, 18.0, 72.0)))
        pattern = struct.pack(patch.FONT_FORMAT, *(data[k] for k in patch.FONT_FIELDS))
        # Nonzero original padding must survive unchanged as well.
        pattern = pattern[:13] + b"\x12\x34\x56" + pattern[16:]
        # A strict packed-pattern match rejects unexpected padding rather than guessing.
        with self.assertRaisesRegex(ValueError, "exactly one"):
            patch._patch_layout(b"prefix" + pattern + b"tail", data, 18, 26)
        pattern = struct.pack(patch.FONT_FORMAT, *(data[k] for k in patch.FONT_FIELDS))
        raw = b"prefix" + pattern + b"registry"
        changed, ranges = patch._patch_layout(raw, data, 18, 26)
        patch._only_ranges(raw, changed, ranges)
        self.assertEqual(changed[:6], b"prefix")
        self.assertTrue(changed.endswith(b"registry"))
        self.assertEqual(struct.unpack_from(patch.FONT_FORMAT, changed, 6),
                         (26.0, 26.0, 400, 1, 18.0, 26.0))
        for bad in (b"no pattern", raw + pattern):
            with self.assertRaisesRegex(ValueError, "exactly one"):
                patch._patch_layout(bad, data, 18, 26)

    def test_out_of_range_changes_fail(self):
        with self.assertRaisesRegex(ValueError, "outside allowed"):
            patch._only_ranges(b"abc", b"axz", [(1, 1)])
        with self.assertRaisesRegex(ValueError, "length"):
            patch._only_ranges(b"abc", b"abcd", [(1, 1)])


class FakeGameObject:
    path_id = 31

    def __init__(self, corrupt_roundtrip=False, corrupt_append=False):
        self.tree = {"m_Component": [{"component": patch._pp(i, f)}
                                    for i, f in ((5, 0), (6, 0), (7, 2))],
                     "m_Layer": 8, "m_Name": "KEEP NAME", "m_Tag": 2,
                     "m_IsActive": True}
        self.corrupt_roundtrip = corrupt_roundtrip
        self.corrupt_append = corrupt_append

    def read_typetree(self):
        return copy.deepcopy(self.tree)

    def encode(self, tree):
        return (struct.pack("<i", len(tree["m_Component"]))
                + b"".join(patch._pack_pp(p["component"]) for p in tree["m_Component"])
                + b"native layer-name-tag-active-and-padding\0\0")

    def save_typetree(self, tree):
        raw = self.encode(tree)
        if self.corrupt_roundtrip or (self.corrupt_append and len(tree["m_Component"]) > 3):
            raw = raw[:-1] + b"!"
        return raw


class GameObjectTests(unittest.TestCase):
    def test_append_keeps_three_original_pptrs_and_all_other_fields(self):
        obj = FakeGameObject()
        original_tree = obj.read_typetree()
        original = obj.encode(original_tree)
        raw, updated = patch._append_components(obj, original, [100, 101])
        self.assertEqual(updated["m_Component"][:3], original_tree["m_Component"])
        self.assertEqual(updated["m_Component"][3:],
                         [{"component": patch._pp(100)}, {"component": patch._pp(101)}])
        without_components = lambda d: {k: v for k, v in d.items() if k != "m_Component"}
        self.assertEqual(without_components(updated), without_components(original_tree))
        self.assertEqual(raw[4:40], original[4:40])
        self.assertEqual(raw[64:], original[40:])
        self.assertEqual(struct.unpack_from("<i", raw)[0], 5)

    def test_rejects_roundtrip_or_append_corruption(self):
        for options in ({"corrupt_roundtrip": True}, {"corrupt_append": True}):
            obj = FakeGameObject(**options)
            with self.subTest(options=options), self.assertRaises(ValueError):
                patch._append_components(obj, obj.encode(obj.tree), [100])

    def test_rejects_duplicate_and_existing_component(self):
        obj = FakeGameObject()
        for ids in ([100, 100], [5]):
            with self.subTest(ids=ids), self.assertRaisesRegex(ValueError, "duplicate"):
                patch._append_components(obj, obj.encode(obj.tree), ids)

    def test_rejects_unknown_native_layout(self):
        obj = FakeGameObject()
        original = obj.encode(obj.tree)
        # Simulate a serializer whose native schema adds an unknown header.
        obj.encode = lambda tree: b"HEAD" + original
        with self.assertRaisesRegex(ValueError, "unsupported native"):
            patch._append_components(obj, b"HEAD" + original, [100])


class VerificationTests(unittest.TestCase):
    def test_verification_reads_outputs_and_accepts_json_manifest_without_writes(self):
        manifest = {"layout_enabled": True, "scenes": [{"new_bindings": [{"id": 7}],
                                                      "ranges": [(0, 12)]}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "level0").write_bytes(b"verified scene")
            before = (output / "level0").stat().st_mtime_ns
            with mock_patch.object(patch, "_expected_scenes", return_value=({"level0": b"verified scene"}, manifest)):
                result = patch.verify_scenes(Path("unused"), output, [], json.loads(json.dumps(manifest)))
            self.assertTrue(result["verified"])
            self.assertEqual(result["bindings"], 1)
            self.assertEqual((output / "level0").stat().st_mtime_ns, before)

    def test_verification_rejects_tampered_output_and_manifest(self):
        manifest = {"layout_enabled": True, "scenes": [{"new_bindings": []}]}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "level0").write_bytes(b"corrupt")
            with mock_patch.object(patch, "_expected_scenes", return_value=({"level0": b"expected"}, manifest)):
                with self.assertRaisesRegex(ValueError, "output scene"):
                    patch.verify_scenes(Path("unused"), output, [], manifest)
                tampered = copy.deepcopy(manifest)
                tampered["scenes"][0]["new_bindings"] = [{"id": 999}]
                with self.assertRaisesRegex(ValueError, "manifest differs"):
                    patch.verify_scenes(Path("unused"), output, [], tampered)

    def test_verification_requires_explicit_boolean_layout(self):
        for invalid in ({}, {"layout_enabled": 1}, {"layout_enabled": "true"}):
            with self.subTest(manifest=invalid), self.assertRaisesRegex(ValueError, "boolean"):
                patch.verify_scenes(Path("unused"), Path("unused"), [], invalid)


class FakeRectTransform:
    def __init__(self, corrupt=False):
        self.corrupt = corrupt
        self.tree = {"m_GameObject": patch._pp(2001),
                     "m_SizeDelta": {"x": 144.0, "y": 24.0},
                     "m_LocalPosition": {"x": 0, "y": 0, "z": 0},
                     "m_LocalScale": {"x": 1, "y": 1, "z": 1},
                     "m_AnchorMin": {"x": .5, "y": .5},
                     "m_AnchorMax": {"x": .5, "y": .5},
                     "m_Pivot": {"x": .5, "y": .5}}

    def read_typetree(self):
        return copy.deepcopy(self.tree)

    def encode(self, tree):
        fields = {k: v for k, v in tree.items() if k != "m_SizeDelta"}
        return (json.dumps(fields, sort_keys=True).encode()
                + struct.pack("<ff", tree["m_SizeDelta"]["x"], tree["m_SizeDelta"]["y"])
                + b"native padding\0\0")

    def save_typetree(self, tree):
        raw = self.encode(tree)
        return raw[:-1] + b"!" if self.corrupt else raw


class NameRectTests(unittest.TestCase):
    def test_resize_preserves_native_position_scale_anchors_and_other_fields(self):
        obj = FakeRectTransform()
        original = obj.encode(obj.tree)
        raw, updated, ranges = patch._resize_name_rect(obj, original)
        self.assertEqual(updated["m_SizeDelta"], {"x": 232.0, "y": 32.0})
        for name, value in obj.tree.items():
            if name != "m_SizeDelta":
                self.assertEqual(updated[name], value)
        self.assertEqual(len(raw), len(original))
        patch._only_ranges(original, raw, ranges)

    def test_resize_rejects_unknown_original_or_nonexact_native_roundtrip(self):
        obj = FakeRectTransform(corrupt=True)
        with self.assertRaisesRegex(ValueError, "round trip"):
            patch._resize_name_rect(obj, obj.encode(obj.tree))
        obj = FakeRectTransform()
        obj.tree["m_SizeDelta"]["x"] = 150
        with self.assertRaisesRegex(ValueError, "unexpected original"):
            patch._resize_name_rect(obj, obj.encode(obj.tree))


if __name__ == "__main__":
    unittest.main()
