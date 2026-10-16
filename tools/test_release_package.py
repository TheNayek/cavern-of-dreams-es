"""Integration checks for the actual portable ZIP; all writes stay in build/qa.

Run after package_release.py. Requires Windows PowerShell 5.1 and pristine
private orig/ inputs. Does not start the game or touch the installed copy.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import package_release as release

ROOT = Path(__file__).resolve().parents[1]
ZIP = ROOT / "patches/v0.1.0-beta/Cavern-of-Dreams-ES-v0.1.0-beta-windows.zip"
DELTA_ZIP = ZIP.with_name(ZIP.name.replace("-windows.zip", "-windows-delta.zip"))


@unittest.skipUnless(os.name == "nt", "Windows PowerShell integration")
class PortablePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DELTA_ZIP.is_file():
            raise unittest.SkipTest("Optional installer: generate with package_release.py --format both")
        cls.compatibility = json.loads((ROOT / "distribution/compatibility.json").read_text(encoding="utf-8"))
        cls.qa = ROOT / "build/qa"
        cls.qa.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        # Exercise both accented text and brackets that PowerShell may interpret.
        self.temporary = tempfile.TemporaryDirectory(prefix="parche-es-\u00e1 [prueba] ", dir=self.qa)
        self.addCleanup(self.temporary.cleanup)
        self.game = Path(self.temporary.name) / "Juego [1]"
        self.game.mkdir()
        with zipfile.ZipFile(DELTA_ZIP) as zipped:
            zipped.extractall(self.game)
        self.package = self.game / "CavernOfDreams-ES"
        self.script = self.package / "patch.ps1"
        self.manifest_path = self.package / "manifest.json"
        self.manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.originals = {}
        self.targets = {}
        for entry in self.compatibility["files"]:
            path = self.game / entry["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / entry["source"], path)
            self.originals[entry["path"]] = entry["sha256"]
        self.targets = {entry["path"]: entry["target_sha256"] for entry in self.manifest["files"]}
        self.sentinel = self.game / "Cavern of Dreams_Data/Managed/sentinel.txt"
        self.sentinel.parent.mkdir()
        self.sentinel.write_bytes(b"unrelated file must remain unchanged")

    def invoke(self, mode, success=True):
        args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                str(self.script), "-Mode", mode]
        environment = dict(os.environ, XDELTA="-this-option-must-never-be-inherited")
        # The CMD launchers are checked separately in a native Windows shell.
        # This suite exercises the real installer using PowerShell 5.1 directly.
        with tempfile.TemporaryFile() as capture:
            result = subprocess.run(args, cwd=self.game, env=environment, stdin=subprocess.DEVNULL,
                                    stdout=capture, stderr=subprocess.STDOUT, timeout=45)
            capture.seek(0)
            output = capture.read().decode("utf-8", errors="replace")
        self.assertEqual(result.returncode, 0 if success else 1, output)
        if not success:
            self.assertIn("ERROR:", output, "Failure must come from the installer, not its launcher")
        self.assertEqual(self.sentinel.read_bytes(), b"unrelated file must remain unchanged")
        return output

    def snapshot(self):
        return {path: release.sha(self.game / path) for path in self.originals}

    def receipt(self):
        return json.loads((self.package / "backup/receipt.json").read_text(encoding="utf-8"))

    def assert_unchanged_failure(self, mode, reason=None):
        before = self.snapshot()
        output = self.invoke(mode, success=False)
        if reason:
            self.assertIn(reason, output)
        self.assertEqual(self.snapshot(), before)

    def test_install_repeat_uninstall_reinstall(self):
        self.invoke("Install")
        self.assertEqual(self.snapshot(), self.targets)
        backups = {p: release.sha(self.package / "backup/files" / p) for p in self.originals}
        self.assertEqual(backups, self.originals)
        self.invoke("Install")
        self.assertEqual(self.snapshot(), self.targets)
        self.invoke("Uninstall")
        self.assertEqual(self.snapshot(), self.originals)
        self.invoke("Uninstall")
        self.invoke("Install")
        self.assertEqual(self.snapshot(), self.targets)
        self.invoke("Uninstall")
        self.assertEqual(self.snapshot(), self.originals)
        self.assertEqual(self.receipt()["state"], "uninstalled")

    def test_wrong_original_refused_before_backup(self):
        path = self.game / self.manifest["files"][-1]["path"]
        path.write_bytes(path.read_bytes() + b"other-mod")
        self.assert_unchanged_failure("Install", "incompatible")
        self.assertFalse((self.package / "backup").exists())

    def test_corrupt_tool_or_delta_refused(self):
        for relative in (self.manifest["tool"]["path"], self.manifest["files"][-1]["patch"]):
            with self.subTest(relative=relative):
                path = self.package / relative
                original = path.read_bytes()
                path.write_bytes(original + b"corrupt")
                self.assert_unchanged_failure("Install", "Integridad incorrecta")
                self.assertFalse((self.package / "backup").exists())
                path.write_bytes(original)

    def test_changed_game_not_overwritten_on_uninstall_or_install(self):
        self.invoke("Install")
        path = self.game / self.manifest["files"][-1]["path"]
        path.write_bytes(path.read_bytes() + b"external change")
        self.assert_unchanged_failure("Uninstall", "Cambio externo")
        self.assert_unchanged_failure("Install", "Cambio externo")

    def test_corrupt_or_missing_backup_refused(self):
        self.invoke("Install")
        path = self.package / "backup/files" / self.manifest["files"][-1]["path"]
        original = path.read_bytes()
        path.write_bytes(b"bad backup")
        self.assert_unchanged_failure("Uninstall", "Integridad incorrecta")
        self.assert_unchanged_failure("Install", "Integridad incorrecta")
        path.write_bytes(original)
        path.unlink()
        self.assert_unchanged_failure("Uninstall", "Falta archivo")

    def test_manifest_cannot_address_arbitrary_files(self):
        self.manifest["files"][0]["path"] = "../outside.txt"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.assert_unchanged_failure("Install", "no autorizado")
        self.assertFalse((self.package / "backup").exists())

    def test_unreceipted_development_install_refused(self):
        for entry in self.manifest["files"]:
            shutil.copyfile(ROOT / "build/out" / release.output_for(entry["path"]), self.game / entry["path"])
        self.assert_unchanged_failure("Install", "sin backup portatil")
        self.assert_unchanged_failure("Uninstall", "No hay recibo")
        self.assertFalse((self.package / "backup").exists())

    def test_last_file_locked_rolls_back_install_and_can_retry(self):
        catalog = self.game / (release.AA_PREFIX + "catalog.json")
        # Windows permits reads but refuses File.Replace while this handle exists.
        with catalog.open("rb"):
            output = self.invoke("Install", success=False)
        self.assertIn("recuperado el estado anterior", output)
        self.assertEqual(self.snapshot(), self.originals)
        self.assertEqual(self.receipt()["state"], "uninstalled")
        self.invoke("Install")
        self.assertEqual(self.snapshot(), self.targets)

    def test_last_file_locked_rolls_back_uninstall(self):
        self.invoke("Install")
        catalog = self.game / (release.AA_PREFIX + "catalog.json")
        with catalog.open("rb"):
            self.invoke("Uninstall", success=False)
        self.assertEqual(self.snapshot(), self.targets)
        self.assertEqual(self.receipt()["state"], "installed")
        self.invoke("Uninstall")
        self.assertEqual(self.snapshot(), self.originals)

    def test_upgrade_uses_pristine_backup(self):
        self.invoke("Install")
        receipt_path = self.package / "backup/receipt.json"
        receipt = self.receipt()
        receipt["version"] = "0.0.9-beta"
        record = receipt["files"][0]
        path = self.game / record["path"]
        path.write_bytes(path.read_bytes() + b"previous translation revision")
        record["installed_sha256"] = release.sha(path)
        record["installed_size"] = path.stat().st_size
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        self.invoke("Install")
        self.assertEqual(self.snapshot(), self.targets)
        self.assertEqual(self.receipt()["version"], "0.1.0-beta")
        self.invoke("Uninstall")
        self.assertEqual(self.snapshot(), self.originals)


class ManualArchiveTests(unittest.TestCase):
    def test_exact_data_files_and_no_executable_code(self):
        expected = set(release.TARGETS) | {
            "Traduccion-ES/LEEME.txt", "Traduccion-ES/RELEASE_NOTES.md",
            "Traduccion-ES/CREDITOS.md", "Traduccion-ES/manifest.json"}
        with zipfile.ZipFile(ZIP) as zipped:
            self.assertEqual(set(zipped.namelist()), expected)
            manifest = json.loads(zipped.read("Traduccion-ES/manifest.json"))
            self.assertEqual(manifest["format"], "copy-replace")
            self.assertEqual({f["path"] for f in manifest["files"]}, set(release.TARGETS))
            for entry in manifest["files"]:
                data = zipped.read(entry["path"])
                self.assertEqual(data, (ROOT / "build/out" / release.output_for(entry["path"])).read_bytes())
                self.assertEqual(len(data), entry["size"])
                self.assertEqual(release.hashlib.sha256(data).hexdigest(), entry["sha256"])

    def test_copy_over_originals_and_restore_backup(self):
        metadata = json.loads((ROOT / "distribution/compatibility.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory(prefix="copy-release-", dir=ROOT / "build/qa") as temporary:
            game = Path(temporary) / "Juego"
            backup = Path(temporary) / "Backup"
            for entry in metadata["files"]:
                path = game / entry["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / entry["source"], path)
            sentinel = game / "Cavern of Dreams_Data/Managed/sentinel.txt"
            sentinel.parent.mkdir()
            sentinel.write_bytes(b"preserve unrelated assets")
            shutil.copytree(game, backup)
            with zipfile.ZipFile(ZIP) as zipped:
                zipped.extractall(game)
            for relative in release.TARGETS:
                self.assertEqual(release.sha(game / relative), release.sha(ROOT / "build/out" / release.output_for(relative)))
            self.assertEqual(sentinel.read_bytes(), b"preserve unrelated assets")
            shutil.copytree(backup, game, dirs_exist_ok=True)
            for entry in metadata["files"]:
                self.assertEqual(release.sha(game / entry["path"]), entry["sha256"])


class PublicArchiveTests(unittest.TestCase):
    @unittest.skipUnless(DELTA_ZIP.is_file(), "Optional delta package not generated")
    def test_only_deltas_in_player_zip(self):
        with zipfile.ZipFile(DELTA_ZIP) as zipped:
            names = zipped.namelist()
            self.assertEqual(len([n for n in names if n.endswith(".xdelta")]), 6)
            self.assertFalse(any(n.endswith(".bundle") or Path(n).name in {"level0", "level1", "catalog.json"} for n in names))
            self.assertFalse(any("backup/" in n or "orig/" in n for n in names))
            self.assertTrue(any("XDELTA-LICENSE" in n for n in names))

    def test_source_zip_is_exact_allowlist(self):
        path = ZIP.with_name(ZIP.name.replace("-windows.zip", "-fuentes.zip"))
        with zipfile.ZipFile(path) as zipped:
            self.assertEqual(set(zipped.namelist()), {p.as_posix() for p in release.public_files(ROOT)})
            self.assertFalse(any(n.startswith(("orig/", "build/", "extracted/", "patches/")) for n in zipped.namelist()))
            for relative in release.public_files(ROOT):
                self.assertEqual(zipped.read(relative.as_posix()), (ROOT / relative).read_bytes(), str(relative))


if __name__ == "__main__":
    unittest.main(verbosity=2)
