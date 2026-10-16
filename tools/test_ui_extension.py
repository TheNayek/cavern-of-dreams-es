"""Regressions for additive UI rows and preservation of the English source."""
import copy
import unittest
from pathlib import Path

import unity_localization as ul
import ui_extension as ui


class UIExtensionTests(unittest.TestCase):
    def test_key_ids_and_bindings_are_unique_and_resolve(self):
        rows = ui.additions()
        self.assertEqual(len(rows), 18)
        self.assertEqual(len({row["key_id"] for row in rows}), 18)
        self.assertTrue(all(0 < row["key_id"] < 2**63 for row in rows))
        bindings = ui.load_ui_spec()
        self.assertEqual(len(bindings), 35)
        self.assertEqual(len({(row["scene"], row["text_id"]) for row in bindings}), 35)
        self.assertTrue(all(row["key_id"] > 0 for row in bindings))

    def test_english_append_preserves_old_rows_but_rejects_old_text_mutation(self):
        path = ui.ROOT / "orig/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle"
        source = ui.serialized_file(ul.load_env(str(path)))
        candidate_env = ul.load_env(str(path))
        candidate = ui.serialized_file(candidate_env)
        source_obj = next(obj for obj in source.objects.values()
                          if obj.type.name == "MonoBehaviour" and ul.load_mono(obj)[1]["m_Name"] == "UI_en")
        before = ul.load_mono(source_obj)[1]
        ui.extend_english(candidate)
        # UnityPy get_raw_data() still reads the original reader after
        # set_raw_data(). Inspect the serialized result, as verify_build does.
        candidate_env = ul.load_env(candidate_env.file.save(packer="lz4"))
        candidate = ui.serialized_file(candidate_env)
        changed = candidate.objects[source_obj.path_id]
        tree, data, registry = ul.load_mono(changed)
        self.assertEqual(data["m_TableData"][:len(before["m_TableData"])], before["m_TableData"])
        self.assertEqual(len(data["m_TableData"]), len(before["m_TableData"]) + 18)
        self.assertTrue(ui.original_object_preserved(source_obj, changed))
        data["m_TableData"][0]["m_Localized"] = "UNAUTHORIZED CHANGE"
        changed.set_raw_data(ul.save_mono(changed, tree, data, registry))
        changed = ui.serialized_file(ul.load_env(candidate_env.file.save(packer="lz4"))).objects[source_obj.path_id]
        self.assertFalse(ui.original_object_preserved(source_obj, changed))

    def test_shared_append_keeps_original_keys_and_rejects_repeat(self):
        path = ui.ROOT / "orig/aa/StandaloneWindows64" / ui.SHARED_BUNDLE
        source = ui.serialized_file(ul.load_env(str(path)))
        data = next(ul.load_mono(obj)[1] for obj in source.objects.values()
                    if obj.type.name == "MonoBehaviour"
                    and ul.load_mono(obj)[1].get("m_TableCollectionName") == "UI")
        original = copy.deepcopy(data["m_Entries"])
        ui.append_shared_entries(data)
        self.assertEqual(data["m_Entries"][:len(original)], original)
        self.assertEqual({row["m_Id"] for row in data["m_Entries"][len(original):]},
                         {row["key_id"] for row in ui.additions()})
        with self.assertRaises(ValueError):
            ui.append_shared_entries(data)


if __name__ == "__main__":
    unittest.main()
