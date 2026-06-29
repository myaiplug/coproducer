from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from nodaw.cli import MODES, build_parser, main
from nodaw.core.engine import WorkflowRunner
from tests.helpers import create_test_project


class SmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("FFmpeg and FFprobe are required for smoke tests.")
        cls.temp = tempfile.TemporaryDirectory()
        cls.project = create_test_project(Path(cls.temp.name) / "project")
        logger = logging.getLogger("nodaw.tests")
        logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        cls.runner = WorkflowRunner(cls.project, logger, generate_previews=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_all_workflows_create_real_outputs(self) -> None:
        single = self.runner.single()
        reference = self.runner.reference()
        batch = self.runner.batch()
        album = self.runner.album()
        codecs = self.runner.codecs()
        streaming = self.runner.streaming()
        fixes = self.runner.fixes()
        history = self.runner.history()
        exported = self.runner.export()
        complete = self.runner.complete(folder=self.project / "input" / "album")

        self.assertEqual(single["report_type"], "single")
        self.assertTrue(reference["differences"])
        self.assertEqual(len(batch["tracks"]), 2)
        self.assertEqual(len(album["tracks"]), 2)
        self.assertTrue(any(item["status"] == "generated" for item in codecs["codec_analysis"]["previews"]))
        self.assertTrue(all(item["preview_path"] for item in streaming["streaming_analysis"]["platforms"]))
        self.assertTrue(fixes["repairs"])
        self.assertTrue(history["entries"])
        archive = Path(exported["operations"][0]["path"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as handle:
            self.assertIn("manifest.json", handle.namelist())
        self.assertTrue(any(item["status"] == "completed" for item in complete["operations"]))

        html_files = list((self.project / "reports" / "html").glob("*.html"))
        json_files = list((self.project / "reports" / "json").glob("*.json"))
        self.assertGreaterEqual(len(html_files), 10)
        self.assertEqual(len(html_files), len(json_files))
        for path in json_files:
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], "3.0.0")
            self.assertIn("report_type", payload)

    def test_doctor_and_cli_contract(self) -> None:
        report = self.runner.doctor()
        self.assertFalse([item for item in report["operations"] if item["status"] == "fail"])
        parser = build_parser()
        for mode in MODES:
            parsed = parser.parse_args(["--mode", mode])
            self.assertEqual(parsed.mode, mode)
        result = main(["--root", str(self.project), "--mode", "doctor", "--no-previews"])
        self.assertEqual(result, 0)

    def test_windows_launcher_noninteractive(self) -> None:
        launcher = self.project / "START_ANALYZER_PRO.bat"
        command = subprocess.list2cmdline([
            str(launcher), "--mode", "doctor", "--no-previews",
        ])
        result = subprocess.run(
            command,
            cwd=self.project,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
            shell=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"status": "completed"', result.stdout + result.stderr)

    def test_customer_files_have_no_internal_phase_branding(self) -> None:
        forbidden = ("Phase 1", "Phase 2", "Phase 3", "1.1.0", "2.0.0")
        suffixes = {".py", ".bat", ".md", ".txt", ".json", ".toml", ".ps1", ".iss"}
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.casefold() not in suffixes:
                continue
            if any(part in {"development", "tests", "dist", "packaging"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for value in forbidden:
                self.assertNotIn(value, text, f"{value} found in {path}")


if __name__ == "__main__":
    unittest.main()
