# CoProducer - Proposed Feature List

**Status:** Proposal (not implemented)  
**Audience:** Product + engineering  
**Goal:** Save producer time, raise technical delivery quality, and deepen mastering / mix-check workflows without leaving the desktop app.

---

## A. Improve what already ships

| Area | Improvement | Why it saves time |
|------|-------------|-------------------|
| Analyze | One-click re-analyze last path + batch folder drop | Skip re-browsing files |
| Repair | Named repair presets (Streaming / Club / Podcast / Safe) | Stop re-checking boxes every session |
| Repair | Dry-run preview (metrics only, no write) | Decide before rendering a WAV |
| Reference Match | Save reference library + last 5 refs | Instant recall of house refs |
| A/B | Loop region + level-match toggle | Fairer listening, fewer second-guesses |
| Reports | Client PDF one-pager + stem of fixes | Hand-off without rewriting notes |
| Dashboard | Session history with score timeline | Spot regressions across versions |
| Convert | Multi-format export pack (WAV+MP3+M4A) | Delivery package in one click |
| Studio | Markers + clip notes synced to report | Engineering comments at timestamps |
| Skins / UI | Collapse memory per sidebar section | Cleaner layout every launch |

---

## B. Five super-helpful time-saving workflows (new)

Each workflow is a **guided path**: drop files → automatic analysis → recommended actions → optional render → auto A/B + report. Designed for real studio pressure, not demo theater.

---

### 1. Mastering Gate (pre-master technical clearance)

**Problem:** Engineers burn 20-40 minutes checking LUFS, true peak, stereo, DC, silence, and sample rate before a mastering session.

**Workflow**
1. Drop mix (or stem bus bounce).
2. Engine runs **Mastering Gate** profile: integrated LUFS, short-term max, true peak, sample peak, LRA, crest, phase correlation, stereo width, DC, leading/trailing silence, sample rate / bit depth, clipped-sample estimate, mono fold check.
3. Pass / Warn / Fail chips on a single card with **one-click fix set** (high-pass, true-peak limit, loudnorm target selectable: -14 / -16 / -9 club).
4. Optional: export **Pre-Master Pack** (24-bit WAV + JSON gate report + client HTML).

**Implementation sketch**
- Profile flag on analyzer: `workflow=mastering_gate`
- Threshold table in config (streaming vs club vs podcast)
- Repair preset auto-maps fails → filter chain
- Report section: `Mastering Gate` with pass/fail matrix

**Time saved:** ~15-30 min per song on technical prep.

---

### 2. Version Ladder (v1 → v2 → vFinal mix comparison)

**Problem:** Producers iterate mixes all night and lose track of what actually got better.

**Workflow**
1. Drop 2-6 versions of the same track (or pick a folder named `MySong_v*`).
2. Auto-align by duration / loudness-normalize for fair A/B (analysis still uses raw files).
3. Side-by-side score ladder + metric deltas (LUFS, TP, width, phase, crest, brightness).
4. Instant dual-player A/B with version switcher (volume-only, same playhead).
5. Highlight **best technical score** and **largest regressions** (e.g. v4 got louder but phase collapsed).
6. One-click export: `version_ladder.html` + CSV for the client or yourself.

**Implementation sketch**
- Batch analyze → relative comparison matrix
- UI page or Reference Match mode: `Version Ladder`
- Reuse AB dual-player with N-way source list
- Store ladder session in history

**Time saved:** 30-60 min of manual spreadsheet / DAW bouncing comparison.

---

### 3. Streaming Delivery Kit (platform-ready masters)

**Problem:** Spotify, Apple, YouTube, Tidal, and Beatport want different loudness / peak / format habits; re-exporting is tedious and error-prone.

**Workflow**
1. Drop final master.
2. Choose platforms (multi-select chips).
3. Engine computes **per-platform compliance** (target LUFS, TP ceiling, recommended sample rate, codec).
4. Batch-render platform files into dated folder:
   - `spotify/` loudnorm -14 LUFS, TP -1, 44.1k WAV + 320 MP3
   - `apple/` -16 LUFS path
   - `youtube/` -14 with TP guard
   - optional `beatport/` hotter club profile
5. Auto report: table of measured vs target for every render.
6. Optional: zip pack + open folder.

**Implementation sketch**
- Platform profiles already partially in engine config → expose as workflow
- Parallel FFmpeg jobs with progress bar
- Post-render analyze each output (fast path) to verify compliance
- Prefs remember last platform set

**Time saved:** 20-45 min of manual loudnorm + convert + re-measure loops.

---

### 4. Reference Clone Assist (tonal / loudness match guide)

**Problem:** "Make it more like this reference" is slow without a structured plan.

**Workflow**
1. Drop **Your Mix** + **Reference** (Reference Match).
2. Run **Clone Assist** (not a black-box AI master): engine diffs loudness, spectral balance (7-band + centroid/rolloff), width/phase, dynamics.
3. Output a **priority fix list** ordered by impact:
   - e.g. "Lower integrated loudness 1.8 LU"
   - "Trim 80 Hz shelf ~1.5 dB"
   - "Widen less / raise mid correlation"
4. Generate a **suggested EQ/dynamics chain** (FFmpeg or Pedalboard recipe) as optional auto-repair.
5. After repair, auto open A/B with meters + spectrogram and score delta.
6. Save as preset: "House Ref Match - Artist X".

**Implementation sketch**
- Extend `compare_reference` with actionable deltas (already partial)
- Map deltas → repair catalog filters with gain amounts
- UI: checklist with Apply Selected
- Store reference + recipe in library

**Time saved:** 25-50 min of guess-and-check EQ while referencing.

---

### 5. Session Closer (end-of-night archive + QA)

**Problem:** At 2 a.m. files scatter: repairs, converts, reports, half-checked peaks. Next day is archaeology.

**Workflow**
1. Click **Close Session** (or auto-prompt after N analyses).
2. Collects from this session: all analyzed paths, repaired outputs, converts, scores, notes.
3. Runs quick **final QA** on the latest "winning" file (gate + clip + TP).
4. Builds folder:
   ```
   Session_2026-07-29_TrackName/
     01_original/
     02_repaired/
     03_delivery/   (platform pack if chosen)
     04_reports/    (HTML, JSON, one-page summary)
     SESSION_NOTES.txt
   ```
5. Optional: write `NEXT_STEPS.md` from open findings (what still fails).
6. Optional: open Explorer + copy summary to clipboard for Discord/client.

**Implementation sketch**
- Session object in UI prefs / temp JSON (run_id list, paths)
- Archive job = copy/move + report render
- Hook from secret menu + Dashboard button
- Doctor-style status: archived N files, remaining fails

**Time saved:** 15-30 min of end-of-session file sorting; huge next-day clarity.

---

## C. Supporting engineering / mastering capabilities (backlog)

These power the five workflows and raise the product ceiling:

1. **True peak (BS.1770-style)** - polyphase oversample, dual TP fields on every report  
2. **Momentary / short-term LUFS timeline** - graph under waveform for pumping / overs  
3. **K-weighted spectrum + tonal balance target curves** - genre presets (hip-hop, EDM, pop, podcast)  
4. **Stereo image tools** - mid/side energy, correlation over time, mono-compatibility scrub  
5. **Clip / soft-clip detection** - histogram of near-0 dBFS samples  
6. **Silence / count-in / outro detect** - auto markers for trim  
7. **Codec preview** - AAC/MP3 round-trip peak prediction before upload  
8. **Stem-aware mode** (optional) - drop drum bus + vocal + mix for conflict notes  
9. **Calibration corpus** - commercial masters set for honest scoring (see ENGINE_ACCURACY_PLAN)  
10. **Offline batch CLI parity** - same five workflows from headless scripts for houses with folders of songs  

---

## D. Suggested build order

| Sprint | Deliverable | Unlocks |
|--------|-------------|---------|
| 1 | Repair presets + dry-run + sidebar collapse prefs | Faster daily repair |
| 2 | **Mastering Gate** workflow | Workflow #1 |
| 3 | **Streaming Delivery Kit** | Workflow #3 |
| 4 | Version Ladder + multi A/B sources | Workflow #2 |
| 5 | Reference Clone Assist recipes | Workflow #4 |
| 6 | Session Closer archive | Workflow #5 |
| 7 | TP / short-term LUFS accuracy upgrades | Trust in all workflows |

---

## E. Success metrics

- Median time from drop → platform-ready folder under **3 minutes** for a single master  
- Version Ladder used on ≥2 versions in 40% of multi-export sessions  
- Repair preset usage > ad-hoc checkbox combos within 2 weeks of ship  
- Zero silent score regressions after repair (dashboard always shows repaired stats - already policy)

---

## F. Notes

- Keep CoProducer **offline-first**; workflows use local FFmpeg + current analyzer stack.  
- No em dashes in product UI copy.  
- Every workflow ends with **audible A/B** and **written report** so producers trust the machine.
