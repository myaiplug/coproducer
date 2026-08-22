# CoProducer Release Plan — Three Phases → v1 Beta

**Product:** CoProducer (NoDAW Labs)  
**Target:** Invited-tester **v1 Beta** (Windows desktop first; mobile companion on LAN)  
**Version line:** 3.2.x → **1.0.0-beta**

---

## Phase 1 — Production beta core (THIS SPRINT) ✅ implementing

| Workstream | Deliverable |
|------------|-------------|
| **Engine accuracy** | Dual peak fields, polyphase TP, ebur128 cross-check hooks, two-pass loudnorm where applicable |
| **Repair quality** | **Pedalboard** high-quality limit/HP/level path + FFmpeg fallback; hard TP ceiling; non-decreasing score |
| **A/B listening** | Dual continuous decks, **volume-only switch**, shared playhead + shared lookahead (no stop/restart) |
| **Installer** | Bundled FFmpeg/FFprobe + frozen deps including **pedalboard**, scipy, pyloudnorm, PySide6 |
| **Beta access** | Email + confirmation code gate; local invite store; admin mint tool |
| **Telemetry** | Local SQLite: session duration, tracks analyzed, repairs run, A/B switches (exportable for owner) |
| **Docs** | Installer guide, beta tester guide, admin telemetry guide |
| **Mobile** | LAN **mobile companion** (touch UI) — phone uploads to machine running CoProducer Mobile server |

**Exit criteria:** Installer builds; analyze/repair/A-B work on real files; beta gate enforces code; stats file proves usage.

---

## Phase 2 — Mobile depth + delivery workflows

| Workstream | Deliverable |
|------------|-------------|
| Mobile | PWA install, offline queue, camera-roll pick, battery-efficient analysis queue |
| Workflows | Streaming Delivery Kit, Version Ladder, Mastering Gate |
| Repair | Named presets (Streaming / Club / Podcast / Safe), dry-run preview |
| Cloud (optional) | Invite codes from owner dashboard; email via real SMTP |

---

## Phase 3 — Calibration + pro polish

| Workstream | Deliverable |
|------------|-------------|
| Corpus | 30–50 commercial masters + intentional bad files; score retune |
| Platform scores | Spotify / Apple / YouTube / Club multi-target |
| Confidence | ±N on score when TP/stereo low-confidence |
| CI goldens | LUFS ±0.2, TP ±0.1, score never 100 auto |

---

## What “production ready” means for Phase 1

- No mock scores, no placeholder repair filters, no fake telemetry  
- Every path either works with real DSP or fails with a clear error  
- Pedalboard preferred; FFmpeg always available as fallback  
- Beta testers cannot skip gate without a valid invite code  
