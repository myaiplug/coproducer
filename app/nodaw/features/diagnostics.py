from __future__ import annotations

import os
import platform
import sys
from typing import Any

from ..audio.ffmpeg import FFmpeg
from ..config import ProjectPaths


def check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"operation": name, "status": "pass" if passed else "fail", "detail": detail}


def diagnostic_checks(paths: ProjectPaths, settings: dict[str, Any], ffmpeg: FFmpeg) -> list[dict[str, Any]]:
    dependencies = ffmpeg.dependency_status()
    checks = [
        check("Python 3.10+", sys.version_info >= (3, 10), platform.python_version()),
        check("FFmpeg on PATH", dependencies["ffmpeg"], "required"),
        check("FFprobe on PATH", dependencies["ffprobe"], "required"),
        check("Configuration", paths.config_file.is_file(), str(paths.config_file)),
        check("Report CSS", (paths.root / "assets" / "report.css").is_file(), "required"),
        check("Windows launcher", (paths.root / "START_ANALYZER_PRO.bat").is_file(), "required"),
        check("Project root writable", os.access(paths.root, os.W_OK), str(paths.root)),
    ]
    if dependencies["ffmpeg"]:
        for profile in settings["previews"]["codec_profiles"]:
            encoder = str(profile["codec"])
            checks.append(check(f"Encoder {encoder}", ffmpeg.encoder_available(encoder), "preview feature"))
    return checks

