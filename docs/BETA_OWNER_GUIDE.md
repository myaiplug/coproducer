# CoProducer v1 Beta — Owner Guide

## Mint invites

```powershell
cd "D:\nodaw\CoProducer Audio Analysis"
.\.venv\Scripts\python.exe tools\mint_beta_invite.py tester@example.com --note "wave1"
```

Output shows the **6-digit code**. Send email + code to the tester.

Invites are stored in `config/beta_invites.json` (hashes only; codes not re-readable).

## Export usage stats

```powershell
.\.venv\Scripts\python.exe tools\export_beta_stats.py
```

Writes `logs/beta_usage_report.json` with per-email:

- sessions, total session seconds  
- tracks analyzed, unique track names  
- repairs, A/B switches  
- first/last seen  

## Build installer

Prerequisites: Python 3.11, Inno Setup 6, FFmpeg on PATH (recommended).

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

Output: `packaging\output\CoProducer-Setup-1.0.0-beta.exe`

The freeze step installs `requirements.txt` (includes **pedalboard**) and bundles FFmpeg into `runtime\ffmpeg\bin`.

## Optional SMTP confirmation codes

```
set COPRODUCER_SMTP_HOST=smtp.example.com
set COPRODUCER_SMTP_PORT=587
set COPRODUCER_SMTP_USER=...
set COPRODUCER_SMTP_PASS=...
set COPRODUCER_SMTP_FROM=beta@nodaw.com
```

Then `BetaGate.request_email_code(email)` can email codes. Default beta path is **owner-minted invites** (offline-safe).

## Mobile companion

```powershell
.\START_MOBILE.bat
```

Phone on same Wi‑Fi → `http://<pc-lan-ip>:8787/`  
Uses the same analyze/repair engine + beta gate.

## Master test code (local only)

```
set COPRODUCER_BETA_MASTER_CODE=000000
```

Any valid email + that code activates (do not ship to public testers).
