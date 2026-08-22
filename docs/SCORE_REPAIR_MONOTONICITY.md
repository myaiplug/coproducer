# Why scores used to drop after repair — and the fix (v3.2)

## Root cause (proven on real files)

| Source | Pre | Post (old) | Why |
|--------|-----|------------|-----|
| Quiet MP3 | 91 | **86** | Pre: soft “quiet” (−5) + “lossy” (−4). Post: single-pass `loudnorm` left true peak at **−0.65 dBTP** vs ceiling **−1.0**, triggering a **hard −14 cliff**. Net drop. |
| Hot WAV | 63 | 86 | Clip + TP fixed → score rose (OK). |
| Edge | — | — | **`−0.99 > −1.00`** was still “Unsafe true peak” with full −14 — measurement noise, not a real failure. |

Also: post-repair WAV was sometimes re-measured with a harsh absolute model that did not credit “we fixed what the user asked to fix.”

## Fixes in 3.2.0

1. **Repair chain** (`features/repairs.py`)  
   - Append hard `alimiter` after loudnorm so delivery **actually** respects TP ceiling.  
   - Write **pcm_s24le** (no accidental rate thrash).  
   - `loudnorm` with `dual_mono=true`.

2. **Scoring** (`core/scoring.py`)  
   - Graduated penalties (smooth ramps), not cliffs.  
   - **TP epsilon 0.15 dB** — no −14 for −0.99 vs −1.00.  
   - Softer quiet/lossy notices.  
   - **`floor_score_after_repair`**: displayed score never below pre-repair after a CoProducer repair.

3. **Measurement** (`audio/analyzer.py`)  
   - True peak via **`scipy.signal.resample_poly`** (block-wise on long files).  
   - Intersample clip probe around peak region.

4. **Product path** (`engine.single(floor_score=…)`, desktop `_commit_repaired_analysis`)  
   - Always applies the floor + annotates findings when raw re-score is lower.

## Proof (2026-08-02)

```
src_wav.wav:  70 → raw 97 / floored 97  (+27)
src_mp3.mp3:  93 → raw 96 / floored 96  (+3)   # no longer drops
src_flac.flac: 73 → raw 97 / floored 97  (+24)
```

Evidence: `exports/proof_golive/SCORE_NEVER_DROPS.json`  
Re-run: `python exports/proof_golive/prove_score_never_drops.py`
