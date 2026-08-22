#!/usr/bin/env python3
"""Public CoProducer analyze API — measure + project, no server-side repair."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from inspect import signature
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from nodaw.audio.ffmpeg import FFmpeg  # noqa: E402
from nodaw.core.engine import WorkflowRunner  # noqa: E402
from nodaw.features.web_projection import public_analyze_payload  # noqa: E402

HOST = os.environ.get("COPRODUCER_WEB_HOST", "127.0.0.1")
PORT = int(os.environ.get("COPRODUCER_WEB_PORT", "8788"))
MAX_BYTES = 40 * 1024 * 1024
EXTS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".aiff", ".aif"}

log = logging.getLogger("web")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
runner = WorkflowRunner(ROOT, log, generate_previews=False)
_busy = threading.Lock()


def _single(path: Path):
    fn = runner.single
    if "persist" in signature(fn).parameters:
        return fn(path, persist=False)
    return fn(path)


def _ffmpeg_ok() -> bool:
    try:
        if hasattr(FFmpeg, "available"):
            return bool(FFmpeg().available())
        FFmpeg().require()
        return True
    except Exception:
        return shutil.which("ffmpeg") is not None


def _parse_file(ctype: str, raw: bytes) -> tuple[str, bytes | None]:
    if "multipart/form-data" not in ctype:
        return "upload.bin", None
    boundary = ctype.split("boundary=")[-1].strip().strip('"').encode()
    parts = raw.split(b"--" + boundary)
    filename = "upload.bin"
    file_bytes = None
    for part in parts:
        if b"Content-Disposition" not in part:
            continue
        if b'name="file"' not in part and b"filename=" not in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        body = body.rstrip(b"\r\n--")
        for line in head.split(b"\r\n"):
            if b"filename=" in line:
                filename = line.decode("utf-8", "ignore").split("filename=")[-1].strip().strip('"')
        file_bytes = body
        break
    return filename, file_bytes


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, code: int, obj: dict, extra: dict[str, str] | None = None) -> None:
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._json(
                200,
                {
                    "ok": True,
                    "engine": "CoProducer Core Analyzer",
                    "ffmpeg": _ffmpeg_ok(),
                },
            )
        self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if u.path != "/api/analyze":
            return self._json(404, {"error": "not found"})
        if not _busy.acquire(blocking=False):
            return self._json(
                503,
                {"error": "Engine is analyzing another file"},
                extra={"Retry-After": "2"},
            )
        tmp_dir: Path | None = None
        dest: Path | None = None
        try:
            ctype = self.headers.get("Content-Type", "")
            filename, file_bytes = _parse_file(ctype, raw)
            if file_bytes is None:
                return self._json(400, {"error": "no file"})
            ext = Path(filename).suffix.casefold()
            if ext not in EXTS:
                return self._json(415, {"error": f"Unsupported audio extension: {ext or '(none)'}"})
            if len(file_bytes) > MAX_BYTES:
                return self._json(413, {"error": "File too large"})
            safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in Path(filename).name)[:80]
            if not safe:
                safe = f"upload{ext}"
            tmp_dir = Path(tempfile.mkdtemp(prefix="cp-web-"))
            dest = tmp_dir / safe
            dest.write_bytes(file_bytes)
            report = _single(dest)
            payload = public_analyze_payload(report, runner.settings)
            return self._json(200, payload)
        except Exception as exc:
            log.exception("analyze failed")
            return self._json(500, {"error": str(exc)})
        finally:
            if dest is not None:
                dest.unlink(missing_ok=True)
            if tmp_dir is not None:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            _busy.release()


if __name__ == "__main__":
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"CoProducer web analyze API  http://{HOST}:{PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
