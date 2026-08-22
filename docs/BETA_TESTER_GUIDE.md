# CoProducer v1 Beta — Tester Guide

**Build:** 1.0.0-beta  
**Channel:** Invite-only  

---

## Install (Windows)

1. Run `CoProducer-Setup-1.0.0-beta.exe` from the owner.
2. Complete the wizard (admin may be required).
3. Launch **CoProducer** from Desktop / Start Menu.

The installer includes:

- CoProducer desktop app (frozen Python + **Pedalboard**, scipy, pyloudnorm, PySide6, …)
- **FFmpeg / FFprobe** under `runtime\ffmpeg\bin` (when bundled at build time)
- Writable folders: `reports`, `exports`, `config`, `logs`

---

## Activate (required)

On first launch you must enter:

1. The **email** the owner invited  
2. The **6-digit invite code** they sent you  

Without a valid pair the app will not open.

Developer bypass (owner machines only):

```
set COPRODUCER_BETA_BYPASS=1
```

---

## What to test

| Flow | How |
|------|-----|
| Analyze | Drop WAV / MP3 / FLAC on Home |
| Auto Repair | Run Auto Repair after analysis (Pedalboard first, FFmpeg fallback) |
| A/B | After repair: **Play**, then switch A/B — playback must **not** restart; same playhead |
| Report | Export HTML / JSON / TXT |
| Mobile | On the PC run `START_MOBILE.bat`, open the shown URL on your phone (same Wi‑Fi) |

---

## What we log (local)

On this machine only (`logs/beta_telemetry.sqlite`):

- Session start/end + duration  
- Tracks analyzed (filename + score)  
- Repairs run (scores before/after)  
- A/B switches  

Owner exports with `tools/export_beta_stats.py`.  
No cloud upload unless you later enable it.

---

## Known limits (beta)

- Artistic “sounds good” is **not** scored — technical readiness only  
- Auto-repair is conservative (level / TP / high-pass) — not mastering  
- Mobile companion requires the PC server running on LAN  

---

## Feedback

Send: OS build, track type, scores before/after repair, whether A/B stayed locked, any crash logs from `logs/gui_crash.log`.
