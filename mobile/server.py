#!/usr/bin/env python3
"""
CoProducer Mobile Companion — LAN server for phone-only producers.

Run on a PC/laptop on the same Wi‑Fi as the phone:
  py -3.11 mobile/server.py
  → open the shown URL on your phone browser

Features (real engine, no mocks):
- Upload mix → analyze
- Auto repair (Pedalboard preferred)
- Download repaired WAV + JSON report
- Session stats

Touch-first HTML UI served from / 
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from nodaw.core.engine import WorkflowRunner  # noqa: E402
from nodaw.features.repairs import detect_repair_plan, run_auto_repair  # noqa: E402
from nodaw.beta import BetaGate, TelemetryStore  # noqa: E402

HOST = os.environ.get("COPRODUCER_MOBILE_HOST", "0.0.0.0")
PORT = int(os.environ.get("COPRODUCER_MOBILE_PORT", "8787"))
UPLOAD = ROOT / "exports" / "mobile_uploads"
REPAIR = ROOT / "exports" / "mobile_repairs"
UPLOAD.mkdir(parents=True, exist_ok=True)
REPAIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("mobile")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
runner = WorkflowRunner(ROOT, log, generate_previews=False)
gate = BetaGate(ROOT)
_sessions: dict[str, dict] = {}
_lock = threading.Lock()


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0a0a0c"/>
<title>CoProducer Mobile</title>
<style>
:root { --bg:#0a0a0c; --card:#14141a; --text:#f4f4f5; --muted:#a1a1aa; --acc:#a855f7; --ok:#22c55e; --bad:#ef4444; }
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:var(--bg);color:var(--text);min-height:100dvh}
header{padding:16px 18px 8px;position:sticky;top:0;background:linear-gradient(var(--bg),transparent);z-index:2}
h1{font-size:1.25rem;margin:0;letter-spacing:.02em}
.sub{color:var(--muted);font-size:.85rem;margin-top:4px}
main{padding:8px 16px 96px;max-width:520px;margin:0 auto}
.card{background:var(--card);border:1px solid #27272a;border-radius:16px;padding:16px;margin:12px 0}
label{display:block;font-size:.75rem;color:var(--muted);margin-bottom:6px;text-transform:uppercase;letter-spacing:.06em}
input,button,select{font:inherit;width:100%;border-radius:12px;border:1px solid #3f3f46;background:#0c0c10;color:var(--text);padding:14px 12px}
button{background:var(--acc);border:none;font-weight:600;margin-top:10px}
button.secondary{background:#27272a}
button:disabled{opacity:.45}
.score{font-size:3rem;font-weight:700;color:var(--acc);line-height:1}
.muted{color:var(--muted);font-size:.9rem}
.row{display:flex;gap:8px}
.row>*{flex:1}
.err{color:var(--bad);font-size:.9rem}
.ok{color:var(--ok)}
.list{font-size:.9rem;line-height:1.5;color:var(--muted);white-space:pre-wrap}
a{color:var(--acc)}
#gate,#app{display:none}
</style>
</head>
<body>
<header>
  <h1>CoProducer</h1>
  <div class="sub">Mobile companion · same engine as desktop</div>
</header>
<main>
<section id="gate" class="card">
  <label>Beta email</label>
  <input id="email" type="email" autocomplete="email" placeholder="you@studio.com"/>
  <label style="margin-top:12px">Invite code</label>
  <input id="code" inputmode="numeric" maxlength="6" placeholder="123456"/>
  <button id="btnAct">Activate</button>
  <p id="gateMsg" class="muted"></p>
</section>
<section id="app">
  <div class="card">
    <label>Upload mix (WAV / MP3 / FLAC)</label>
    <input id="file" type="file" accept="audio/*,.wav,.mp3,.flac,.m4a,.aiff"/>
    <button id="btnAn">Analyze</button>
    <button id="btnRep" class="secondary" disabled>Auto Repair</button>
    <p id="status" class="muted">Ready.</p>
  </div>
  <div class="card" id="result" style="display:none">
    <div class="score" id="score">—</div>
    <div class="muted" id="rating"></div>
    <div class="list" id="findings"></div>
    <div class="row" style="margin-top:12px">
      <a id="dlRep" href="#" style="display:none">Download repaired</a>
      <a id="dlJson" href="#" style="display:none">JSON report</a>
    </div>
  </div>
</section>
</main>
<script>
const S = {token: localStorage.getItem('cp_token')||'', email: localStorage.getItem('cp_email')||''};
const $ = id => document.getElementById(id);
async function api(path, opts={}) {
  const h = opts.headers||{};
  if (S.token) h['X-Session'] = S.token;
  opts.headers = h;
  const r = await fetch(path, opts);
  const j = await r.json().catch(()=>({}));
  if (!r.ok) throw new Error(j.error||r.statusText);
  return j;
}
function showApp(on){ $('gate').style.display=on?'none':'block'; $('app').style.display=on?'block':'none'; }
async function boot(){
  try {
    const st = await api('/api/status');
    if (st.activated && S.token) { showApp(true); return; }
  } catch(e) {}
  showApp(false); $('gate').style.display='block';
  if (S.email) $('email').value = S.email;
}
$('btnAct').onclick = async () => {
  try {
    const j = await api('/api/activate', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({email:$('email').value, code:$('code').value})});
    S.token = j.token; S.email = j.email;
    localStorage.setItem('cp_token', S.token); localStorage.setItem('cp_email', S.email);
    showApp(true);
  } catch(e) { $('gateMsg').textContent = e.message; $('gateMsg').className='err'; }
};
let lastPath = null;
$('btnAn').onclick = async () => {
  const f = $('file').files[0];
  if (!f) { $('status').textContent='Pick a file first.'; return; }
  $('status').textContent='Uploading & analyzing…';
  $('btnAn').disabled = true;
  try {
    const fd = new FormData(); fd.append('file', f);
    const j = await api('/api/analyze', {method:'POST', body: fd});
    lastPath = j.path;
    $('result').style.display='block';
    $('score').textContent = j.score;
    $('rating').textContent = j.rating||'';
    $('findings').textContent = (j.findings||[]).map(x=>`• ${x.title}: ${x.message}`).join('\n');
    $('btnRep').disabled = !j.can_repair;
    $('dlJson').href = j.report_url; $('dlJson').style.display='inline';
    $('dlRep').style.display='none';
    $('status').textContent = 'Analysis complete.';
  } catch(e) { $('status').textContent = e.message; $('status').className='err'; }
  $('btnAn').disabled = false;
};
$('btnRep').onclick = async () => {
  if (!lastPath) return;
  $('status').textContent='Repairing (Pedalboard/FFmpeg)…';
  $('btnRep').disabled = true;
  try {
    const j = await api('/api/repair', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({path: lastPath})});
    lastPath = j.path;
    $('score').textContent = j.score;
    $('rating').textContent = (j.rating||'') + (j.engine?` · ${j.engine}`:'');
    $('findings').textContent = (j.findings||[]).map(x=>`• ${x.title}`).join('\n');
    if (j.download_url) { $('dlRep').href=j.download_url; $('dlRep').style.display='inline'; }
    $('status').textContent = 'Repair complete.';
  } catch(e) { $('status').textContent = e.message; }
  $('btnRep').disabled = false;
};
boot();
</script>
</body>
</html>
"""


def _auth(handler: BaseHTTPRequestHandler) -> dict | None:
    tok = handler.headers.get("X-Session") or ""
    with _lock:
        return _sessions.get(tok)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        body = HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Session")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            return self._html()
        if u.path == "/api/status":
            st = gate.status()
            return self._json(200, {"activated": st.activated, "email": st.email})
        if u.path.startswith("/files/"):
            # serve downloads under exports
            rel = u.path[len("/files/") :]
            path = (ROOT / "exports" / rel).resolve()
            if not str(path).startswith(str((ROOT / "exports").resolve())) or not path.is_file():
                return self._json(404, {"error": "not found"})
            data = path.read_bytes()
            self.send_response(200)
            ctype = "application/octet-stream"
            if path.suffix.lower() == ".json":
                ctype = "application/json"
            elif path.suffix.lower() == ".wav":
                ctype = "audio/wav"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
            self.end_headers()
            self.wfile.write(data)
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""

        if u.path == "/api/activate":
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "bad json"})
            st = gate.activate(body.get("email", ""), body.get("code", ""))
            if not st.activated:
                return self._json(403, {"error": st.message})
            tok = secrets.token_urlsafe(24)
            with _lock:
                _sessions[tok] = {
                    "email": st.email,
                    "telemetry": TelemetryStore(ROOT, st.email),
                    "t0": time.time(),
                }
            return self._json(200, {"token": tok, "email": st.email})

        sess = _auth(self)
        if not sess and gate.status().activated:
            # allow if desktop already activated on this machine — auto session
            st = gate.status()
            tok = secrets.token_urlsafe(16)
            sess = {
                "email": st.email,
                "telemetry": TelemetryStore(ROOT, st.email or "mobile"),
                "t0": time.time(),
            }
            with _lock:
                _sessions[tok] = sess
            # client should store token; still accept this request
        if not sess:
            return self._json(401, {"error": "activate first"})

        tel: TelemetryStore = sess["telemetry"]

        if u.path == "/api/analyze":
            # multipart: naive parse for single file field "file"
            ctype = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in ctype:
                return self._json(400, {"error": "multipart required"})
            boundary = ctype.split("boundary=")[-1].encode()
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
            if not file_bytes:
                return self._json(400, {"error": "no file"})
            safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in Path(filename).name)[:80]
            dest = UPLOAD / f"{int(time.time())}_{safe}"
            dest.write_bytes(file_bytes)
            try:
                runner._cache.clear()
                report = runner.single(dest)
            except Exception as exc:
                return self._json(500, {"error": str(exc)})
            score = report.get("score")
            tel.track_analyzed(str(dest), int(score) if score is not None else None)
            plan = detect_repair_plan(report)
            # write json report path relative for download
            jname = f"mobile_{dest.stem}.json"
            jpath = ROOT / "exports" / jname
            jpath.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            findings = report.get("findings") or []
            return self._json(
                200,
                {
                    "path": str(dest),
                    "score": score,
                    "rating": report.get("rating"),
                    "can_repair": bool(plan.actions),
                    "findings": [
                        {"title": f.get("title"), "message": f.get("message")}
                        for f in findings[:12]
                        if isinstance(f, dict)
                    ],
                    "report_url": f"/files/{jname}",
                },
            )

        if u.path == "/api/repair":
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except Exception:
                return self._json(400, {"error": "bad json"})
            src = Path(body.get("path") or "")
            if not src.is_file():
                return self._json(400, {"error": "path not found"})
            try:
                runner._cache.clear()
                pre = runner.single(src)
                plan = detect_repair_plan(pre)
                if not plan.actions:
                    return self._json(400, {"error": "no repair needed"})
                res = run_auto_repair(src, REPAIR, plan, prefer_pedalboard=True)
                if not res.get("ok"):
                    return self._json(500, {"error": res.get("error") or "repair failed"})
                out = Path(res["out_path"])
                runner._cache.clear()
                post = runner.single(
                    out,
                    floor_score=int(pre.get("score") or 0),
                    applied_repair_filters=res.get("chain"),
                )
                tel.track_repaired(
                    str(out),
                    int(pre.get("score") or 0),
                    int(post.get("score") or 0),
                )
                rel = str(out.relative_to(ROOT / "exports")).replace("\\", "/")
                return self._json(
                    200,
                    {
                        "path": str(out),
                        "score": post.get("score"),
                        "rating": post.get("rating"),
                        "engine": res.get("engine"),
                        "findings": post.get("findings") or [],
                        "download_url": f"/files/{rel}",
                    },
                )
            except Exception as exc:
                return self._json(500, {"error": str(exc)})

        self._json(404, {"error": "not found"})


def main():
    # Ensure PATH has ffmpeg when frozen/dev
    ff = ROOT / "packaging" / "dist" / "CoProducer" / "runtime" / "ffmpeg" / "bin"
    if ff.is_dir():
        os.environ["PATH"] = str(ff) + os.pathsep + os.environ.get("PATH", "")
    ip = _lan_ip()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 56)
    print("  CoProducer Mobile Companion")
    print(f"  Local:  http://127.0.0.1:{PORT}/")
    print(f"  Phone:  http://{ip}:{PORT}/")
    print("  Same Wi‑Fi required. Ctrl+C to stop.")
    print("=" * 56)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
