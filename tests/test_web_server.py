from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT))

from tests.test_web_projection import SETTINGS, hot_report  # noqa: E402


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from web import server as web_server
        cls.ws = web_server
        cls.httpd = web_server.ThreadingHTTPServer(("127.0.0.1", 0), web_server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _conn(self):
        return HTTPConnection("127.0.0.1", self.port, timeout=5)

    def test_health(self):
        c = self._conn(); c.request("GET", "/health")
        r = c.getresponse(); body = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertTrue(body["ok"])

    def test_rejects_oversize(self):
        bound = "----x"
        payload = (
            f"--{bound}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\n" + ("a" * 16) + f"\r\n--{bound}--\r\n"
        ).encode()
        with patch.object(self.ws, "MAX_BYTES", 8):
            c = self._conn()
            c.request("POST", "/api/analyze", body=payload, headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
            r = c.getresponse()
            self.assertEqual(r.status, 413)

    def test_analyze_mocked(self):
        bound = "----x"
        payload = (
            f"--{bound}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"hot.wav\"\r\n"
            f"Content-Type: audio/wav\r\n\r\nRIFFDATA\r\n--{bound}--\r\n"
        ).encode()
        fake = hot_report()
        with patch.object(self.ws.runner, "single", return_value=fake), \
             patch.object(self.ws.runner, "settings", SETTINGS):
            c = self._conn()
            c.request("POST", "/api/analyze", body=payload, headers={"Content-Type": f"multipart/form-data; boundary={bound}"})
            r = c.getresponse(); body = json.loads(r.read())
        self.assertEqual(r.status, 200)
        self.assertNotIn("path", body["track"]["audio"])
        self.assertEqual(body["promo"]["code"], "WEB30")
        self.assertIn("vs_target", body)
        self.assertTrue(body["plan"]["needed"])


if __name__ == "__main__":
    unittest.main()
