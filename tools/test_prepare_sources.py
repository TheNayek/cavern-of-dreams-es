"""Bootstrap tests use synthetic bytes only; no game files or DLLs are needed."""

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import prepare_sources as ps


class PrepareSourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.game = base / "repo", base / "game"
        (self.root / "distribution").mkdir(parents=True)
        self.game.mkdir()
        self.entries = []
        for index, name in enumerate(("catalog.json", "en.bundle", "locales.bundle", "shared.bundle", "level0", "level1")):
            source = ("orig/aa/" if index < 4 else "orig/scenes/") + name
            game_path = "Cavern of Dreams_Data/" + name
            data = f"synthetic pristine bytes {index}".encode()
            path = self.game / game_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.entries.append(dict(source=source, path=game_path, size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        self.write_manifest()

    def write_manifest(self):
        (self.root / "distribution/compatibility.json").write_text(
            json.dumps(dict(schema_version=1, steam_build=13947382, files=self.entries)), encoding="utf-8")

    def test_clean_copy_and_idempotence_with_patched_game(self):
        self.assertEqual(ps.prepare(self.game, self.root), 6)
        hashes = json.loads((self.root / "orig/scenes/hashes.json").read_text())
        self.assertEqual([row["Hash"] for row in hashes], [e["sha256"].upper() for e in self.entries[-2:]])
        before = {p: p.read_bytes() for p in (self.root / "orig").rglob("*") if p.is_file()}
        for entry in self.entries:
            (self.game / entry["path"]).write_bytes(b"installed patch")
        self.assertEqual(ps.prepare(self.game, self.root), 0)
        self.assertEqual(before, {p: p.read_bytes() for p in (self.root / "orig").rglob("*") if p.is_file()})

    def test_invalid_last_input_creates_nothing(self):
        (self.game / self.entries[-1]["path"]).write_bytes(b"patched")
        with self.assertRaisesRegex(ValueError, "Unsupported or modified"):
            ps.prepare(self.game, self.root)
        self.assertFalse((self.root / "orig").exists())

    def test_missing_input_creates_nothing(self):
        (self.game / self.entries[0]["path"]).unlink()
        with self.assertRaisesRegex(ValueError, "Unsupported or modified"):
            ps.prepare(self.game, self.root)
        self.assertFalse((self.root / "orig").exists())

    def test_partial_setup_checks_all_game_files(self):
        first = self.root / self.entries[0]["source"]
        first.parent.mkdir(parents=True)
        first.write_bytes((self.game / self.entries[0]["path"]).read_bytes())
        (self.game / self.entries[0]["path"]).write_bytes(b"patched")
        with self.assertRaisesRegex(ValueError, "Unsupported or modified"):
            ps.prepare(self.game, self.root)
        self.assertEqual([p for p in (self.root / "orig").rglob("*") if p.is_file()], [first])

    def test_mismatching_backup_never_overwritten(self):
        first = self.root / self.entries[0]["source"]
        first.parent.mkdir(parents=True)
        first.write_bytes(b"preserve me")
        with self.assertRaisesRegex(ValueError, "Existing pristine backup differs"):
            ps.prepare(self.game, self.root)
        self.assertEqual(first.read_bytes(), b"preserve me")
        self.assertFalse((self.root / self.entries[1]["source"]).exists())

    def test_path_traversal_rejected_before_copy(self):
        for key, value in (("source", "orig/../../escape"), ("path", "../escape"), ("source", "C:/escape")):
            with self.subTest(key=key, value=value):
                original = self.entries[0][key]
                self.entries[0][key] = value
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, "Unsafe relative path"):
                    ps.prepare(self.game, self.root)
                self.entries[0][key] = original
        self.assertFalse((self.root / "orig").exists())

    def test_missing_root_is_not_created(self):
        missing = self.root / "missing"
        with self.assertRaisesRegex(ValueError, "root does not exist"):
            ps.prepare(self.game, missing)
        self.assertFalse(missing.exists())

    def test_failed_copy_removes_only_new_destination(self):
        with patch.object(ps.shutil, "copyfileobj", side_effect=OSError("copy failed")):
            with self.assertRaisesRegex(OSError, "copy failed"):
                ps.prepare(self.game, self.root)
        self.assertFalse((self.root / self.entries[0]["source"]).exists())

    def test_extraction_reads_pristine_paths(self):
        fake_ul = types.ModuleType("unity_localization")
        for constant in ("BUNDLE_EN", "BUNDLE_SHARED", "BUNDLE_LOCALES"):
            setattr(fake_ul, constant, str(self.game / (constant + ".bundle")))
        fake_ul.SHARED_TABLE_TREE = fake_ul.STRING_TABLE_TREE = fake_ul.LOCALE_TREE = lambda: None
        seen = []
        fake_ul.iter_monobehaviours = lambda path: seen.append(path) or []
        with patch.dict("sys.modules", {"unity_localization": fake_ul}):
            spec = importlib.util.spec_from_file_location("pristine_extract_test", Path(__file__).with_name("extract.py"))
            extract = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(extract)
        extract.PRISTINE = str(self.root / "orig/aa")
        for name in ("BUNDLE_EN", "BUNDLE_SHARED", "BUNDLE_LOCALES"):
            path = Path(extract.PRISTINE) / (name + ".bundle")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"original")
        extract.extract_en()
        extract.extract_shared()
        extract.extract_locales()
        self.assertTrue(all(Path(path).is_relative_to(self.root / "orig/aa") for path in seen))
        self.assertEqual(len(seen), 3)
        (Path(extract.PRISTINE) / "BUNDLE_EN.bundle").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "prepare_sources.py"):
            extract.extract_en()


if __name__ == "__main__":
    unittest.main()
