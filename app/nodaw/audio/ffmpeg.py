from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ..core.models import CommandResult


class FFmpegError(RuntimeError):
    pass


class FFmpeg:
    def __init__(self, timeout_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def dependency_status() -> dict[str, bool]:
        return {
            "ffmpeg": shutil.which("ffmpeg") is not None,
            "ffprobe": shutil.which("ffprobe") is not None,
        }

    def require(self) -> None:
        missing = [name for name, found in self.dependency_status().items() if not found]
        if missing:
            raise FFmpegError(f"Missing required executable(s) on PATH: {', '.join(missing)}")

    def run(self, arguments: Sequence[str], timeout: int | None = None) -> CommandResult:
        try:
            process = subprocess.run(
                list(arguments),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout or self.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(
                f"Command timed out after {timeout or self.timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise FFmpegError(f"Unable to execute {arguments[0]}: {exc}") from exc
        return CommandResult(process.returncode, process.stdout, process.stderr)

    def run_checked(self, arguments: Sequence[str], timeout: int | None = None) -> CommandResult:
        result = self.run(arguments, timeout)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-1200:]
            raise FFmpegError(f"{arguments[0]} failed with exit code {result.returncode}: {detail}")
        return result

    def run_bytes(self, arguments: Sequence[str], timeout: int | None = None) -> bytes:
        try:
            process = subprocess.run(
                list(arguments),
                capture_output=True,
                timeout=timeout or self.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FFmpegError(
                f"Command timed out after {timeout or self.timeout_seconds} seconds."
            ) from exc
        except OSError as exc:
            raise FFmpegError(f"Unable to execute {arguments[0]}: {exc}") from exc
        if process.returncode:
            detail = process.stderr.decode("utf-8", errors="replace")[-1200:]
            raise FFmpegError(
                f"{arguments[0]} failed with exit code {process.returncode}: {detail}"
            )
        return process.stdout

    def probe(self, path: Path) -> dict:
        result = self.run_checked(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise FFmpegError(f"FFprobe returned invalid JSON for {path.name}.") from exc

    def encoder_available(self, encoder: str) -> bool:
        result = self.run(["ffmpeg", "-hide_banner", "-encoders"])
        return result.returncode == 0 and encoder in result.stdout
