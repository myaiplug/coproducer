# Analysis Engine — Accuracy Grade & Upgrade Plan

**Date:** 2026-07-29  
**Product:** CoProducer Core Analyzer v3.1.x  
**Scope:** Measurement honesty, score calibration, detection quality (not new product features).

---

## Executive grade

| Dimension | Grade | Score | Notes |
|-----------|-------|-------|--------|
| **Integrated loudness (LUFS)** | **A−** | 9/10 | pyloudnorm ITU-R BS.1770-style; solid for offline files |
| **True peak** | **C+** | 6.5/10 | 4× linear oversample estimate — better than sample peak, **not** full BS.1770-4 TP |
| **Sample peak / RMS / crest** | **A−** | 9/10 | Direct math on decoded PCM; depends on decode path |
| **Clipping detection** | **C** | 6/10 | Near-FS sample count; misses soft clips & intersample overs after limiters |
| **LRA / dynamics** | **B−** | 7/10 | pyloudnorm LRA when available; crest-based DR is a proxy |
| **Stereo / phase / width** | **D+** | 4.5/10 | Often mono or simplified path; weak for real M/S imaging |
| **Spectral / tempo / MIR** | **B−** | 7/10 | librosa defaults; useful, not mastering-lab grade |
| **Scoring model** | **C** | 6/10 | Transparent penalty stack; **not** AES listening-panel calibrated; easy “high 90s” if only technical gates pass |
| **Reference match** | **C+** | 6.5/10 | Weighted deltas; logical for same song, weak on genre/timbre |
| **Subjective / musical quality** | **F (by design)** | — | **Not measured** — arrangement, tone taste, vocal performance |

### Overall engine accuracy (technical delivery checks)

**Grade: B− / ~6.5–7.0 out of 10** for **streaming-oriented technical readiness** (loudness, peaks, gross faults).

**Not** a substitute for mastering, listening, or psychoacoustic “quality.”

### Why 100% felt wrong (and what we changed)

- Score was `100 − sum(penalties)`. If **no technical finding** fired → **100**.
- That means “within configured thresholds,” **not** “perfect mix.”
- **Change (v3.1.x honesty):** technical auto-score **ceiling = 97**. A plain 100 is not awarded by measurement alone; repair is disabled at a true 100 if ever set by future human cert.

Your skepticism is correct for a pure technical gate.

---

## What the engine actually does today

```
File → ffprobe metadata
     → soundfile / librosa decode (often mono path for analysis)
     → pyloudnorm integrated LUFS (+ LRA when available)
     → sample peak + 4× linear true-peak estimate
     → numpy faults (clip samples, DC, silence ratio)
     → librosa MIR (centroid, tempo, chroma, onset, energy bands)
     → evaluate_track() penalty scoring
```

**Strengths:** Fast, local, reproducible, good LUFS, clear findings.  
**Weaknesses:** True peak, stereo, clipping, score calibration, no listening model.

---

## Accuracy upgrade plan (todo — priority order)

### P0 — Measurement correctness (do first)

- [ ] **True peak (ITU-grade)**  
  - Replace linear 4× interp with `scipy.signal.resample_poly` (4× or 8×) or ffmpeg `ebur128` / `astats` true peak.  
  - Report **sample peak** and **true peak** as separate fields always.  
  - **Lib:** scipy (already available via stack) or ffmpeg filter JSON.

- [ ] **Stereo-preserving analysis path**  
  - Keep L/R through metrics when `channels >= 2`.  
  - Real mid/side energy, correlation, width, mono fold-down check.  
  - **Lib:** numpy; optional `soundfile` multi-channel.

- [ ] **Honest score policy (done partially)**  
  - [x] Technical ceiling &lt; 100.  
  - [ ] Separate **Technical score** vs optional **Delivery score** vs never auto **Artistic score**.  
  - [ ] UI label: “Technical readiness” not “perfect mix.”

### P1 — Detection quality

- [ ] **Intersample / soft clipping**  
  - Oversampled peak histogram; “near-clip” density per block.  
  - Optional `librosa.effects` / custom oversample peak hold.

- [ ] **LRA reliability**  
  - Ensure short-term loudness variance fallback if `loudness_range` fails.  
  - Compare to EBU R128 short-term series.

- [ ] **Noise floor / hiss**  
  - Percentile noise estimate in gated silence; optional high-band energy.  
  - **Lib:** numpy; optional `noisereduce` only for analysis (not as dependency for core if heavy).

- [ ] **DC / clicks / dropouts**  
  - Click detection via high residual of median filter; dropout via frame energy holes.

### P2 — Spectral & balance (mastering-relevant)

- [ ] **Proper multi-band spectrum**  
  - Fill `spectral_balance_db` every run (currently often empty → UI recomputes).  
  - A-weighted or K-weighted band energy option.

- [ ] **Harshness / mud proxies**  
  - 2–5 kHz and 200–500 Hz ratios with calibrated thresholds.  
  - **Lib:** librosa STFT already present.

- [ ] **Stereo image by band**  
  - Correlation and side energy per octave (low mono, highs wide).

### P3 — Scoring calibration

- [ ] **Internal corpus** of 30–50 commercial masters + intentional bad files.  
- [ ] Tune penalties so median good master lands **88–94 technical**, not 97.  
- [ ] Platform profiles: Spotify −14 / Apple −16 applied as multi-target, not single global.  
- [ ] Confidence interval on score (e.g. ±3) when true peak or stereo is low-confidence.

### P4 — Optional pro libraries / tools (add only if justified)

| Tool / lib | Use | Priority |
|------------|-----|----------|
| **scipy.signal** | Polyphase true-peak, filters | P0 |
| **ffmpeg ebur128 / loudnorm print_format=json** | Cross-check LUFS + TP | P0–P1 |
| **pyloudnorm** (current) | Keep as primary LUFS | keep |
| **essentia** (optional) | Richer MIR, key, danceability | P3 optional |
| **aubio** | Onset/tempo cross-check | P3 optional |
| **pedalboard** (Spotify) | High-quality offline DSP reference | P4 |
| **soxr** | High-quality resampling for TP | P1 |
| **EBU R128 test vectors** | Regression suite | P3 |

Avoid claiming AES/EBU certification without a formal test suite against published vectors.

### P5 — Validation & regression (must ship with accuracy work)

- [ ] Golden-file tests: known LUFS (±0.2 LU), known TP (±0.1 dB), mono file, clipped file.  
- [ ] `docs/VALIDATION_*.md` updated with measured deltas vs ffmpeg ebur128.  
- [ ] CI job: score never returns 100 from `evaluate_track` on synthetic pass-through.

---

## Suggested implementation order (sprints)

1. **Sprint A (1–2 days):** True peak polyphase + dual peak fields + stereo path for correlation/width.  
2. **Sprint B (1–2 days):** Spectral balance always filled + soft-clip detection + UI “Technical readiness” wording.  
3. **Sprint C (2–3 days):** Calibration corpus + penalty retune + confidence field on report.  
4. **Sprint D (optional):** essentia/aubio optional extras behind feature flags.

---

## UI / product rules tied to accuracy

| Rule | Status |
|------|--------|
| Repair disabled at score **100** | **Done** (greyed + blocked) |
| Technical score ceiling **97** | **Done** in `evaluate_track` |
| Same color scale for score + metrics | Done (prior) |
| Never market score as “artistic quality” | Documented; tighten copy in UI next |

---

## Bottom line

- **Accurate enough** for loudness-oriented delivery checks and gross technical faults.  
- **Not accurate enough** to call a mix “perfect” or to replace ears.  
- Path to **A-range technical delivery scoring** is clear: true peak + stereo + calibration, not more vanity metrics.

---

## Guaranteed metrics (no empty “—” when audio decodes)

Engine now **always** fills when a file loads:

| Field | Method |
|-------|--------|
| peak / RMS / crest / DR | numpy on mono PCM |
| integrated LUFS / LRA / TP | pyloudnorm + numeric fallbacks |
| noise floor | 10th-percentile frame RMS |
| phase + stereo width | stereo L/R (mono → 1.0 / 0.0 real values) |
| spectral_balance_db | 7-band STFT relative dB |
| waveform envelope | 180-point peak envelope |
| librosa MIR keys | always numeric (0.0 only if truly silent / failed feature) |

**0.0 is allowed only when the measurement is truly zero** (e.g. mono width, zero clips, silence).

---

## Open-source library upgrade plan (to raise accuracy further)

### Keep (already in tree)
| Library | Role |
|---------|------|
| **numpy** | DSP core |
| **soundfile** | Decode |
| **pyloudnorm** | BS.1770 LUFS |
| **librosa** | STFT, MIR |
| **mutagen** | Tags |
| **ffmpeg / ffprobe** | Probe, repair, convert |

### Add next (recommended)

| Priority | Library | Why | Install |
|----------|---------|-----|---------|
| **P0** | **scipy** | `resample_poly` true-peak; filters; stats | `pip install scipy` |
| **P0** | **soxr** (optional) | High-quality resampling for TP / SR convert | `pip install soxr` |
| **P1** | **ffmpeg-python** or stay CLI | Parse `ebur128` / `astats` JSON for cross-check LUFS+TP | already have ffmpeg CLI |
| **P2** | **essentia** (optional extra) | Key, danceability, richer MIR — already guarded | `pip install essentia` when wheels allow |
| **P2** | **aubio** | Onset/tempo second opinion | `pip install aubio` |
| **P3** | **pedalboard** | Reference-quality offline DSP for repair preview | `pip install pedalboard` |
| **P3** | **noisereduce** | Analysis-only noise estimate (not required for core) | optional |

### Implementation sequence
1. **scipy true-peak** + dual report fields (sample vs true).  
2. **ffmpeg ebur128 JSON** side-car validation in doctor + tests.  
3. **essentia/aubio** behind feature flags; never blank UI if missing.  
4. Calibration corpus + regression goldens.

### Policy
- Core path must run with **current** deps.  
- New libs improve accuracy; UI still never shows blank metrics when decode succeeds.

---

## Related files

- `app/nodaw/core/scoring.py` — penalties + ceiling  
- `app/nodaw/audio/analyzer.py` — decode, LUFS, TP, stereo, spectral, waveform  
- `app/nodaw/audio/metrics.py` — alternate metric path  
- `app/nodaw/config.py` — targets / platforms  
- `app/nodaw/ui/skins.py` — brand design tokens (color + type + layout)  
