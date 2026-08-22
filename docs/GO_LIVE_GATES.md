# CoProducer — What Must Be Proven Before Paid Live

**Last proof run:** 2026-08-02  
**Machine evidence:** `exports/proof_golive/GO_LIVE_PROOF.json`  
**Runner:** `exports/proof_golive/run_proof.py`

**Verdict: NOT LIVE-READY** — commerce delivery (#9) is unproven; installer/screenshots need product-page publish confirmation.

---

## Gate matrix

| # | Gate | Status | Evidence |
|---|------|--------|----------|
| 1 | Clean Windows installer exists | **PASS** (stale risk) | `packaging/output/CoProducer-Setup-3.1.0.exe` (~103 MB, built 2026-07-30). GUI still edited 2026-07-31 → **rebuild before shipping**. |
| 2 | WAV, MP3, FLAC reliable | **PASS** | E2E analyze + repair on real WAV + real MP3 + FLAC fixture from real material. |
| 3 | Repair creates new file; never overwrites original | **PASS** | SHA-256 of source unchanged after repair; output always `{stem}_repaired*.wav` under exports, never the input path. |
| 4 | Generated FFmpeg command matches executed repair | **PASS** | Same `build_auto_repair_command()` string supplies `-i`, `-af`, and out path used by subprocess / Run in Terminal. |
| 5 | Reports identify what was changed | **PASS** (thin) | Report `repairs[]` includes title + reason + full FFmpeg command. **Gap:** no dedicated post-repair “applied filters + score before/after” block in HTML. |
| 6 | Failed repairs show useful error | **PASS** (code) | `CoProducerDesktop._on_repair_done` → critical dialog with exit code + stderr (first 400 chars); timeout and missing-output paths also dialog. |
| 7 | Score deterministic on re-analysis | **PASS** | Cold re-run identical: WAV **63/63**, MP3 **91/91**, FLAC **68/68**. |
| 8 | ≥3 real songs end-to-end | **PASS** | WILDKARDZ WAV, BAM cKASSEL MP3, FLAC from real WAV; analyze → repair → re-score. |
| 9 | Buyer receives installer automatically | **FAIL** | No verified Shopify (or other) digital product that delivers `CoProducer-Setup-*.exe` after payment. |
| 10 | Product page has real UI/report screenshots | **ASSETS PASS / PAGE UNVERIFIED** | Local: `packaging/output/coproducer-screenshot-{dashboard,live,reference}.png`. Live store listing not checked. |

---

## E2E score snapshot (proof run)

| Format | Source | Score ×2 | After repair | Original intact |
|--------|--------|----------|--------------|-----------------|
| WAV | WILDKARDZ-BOOM FT.DNEEZY | 63 = 63 | **86** | yes |
| MP3 | BAM cKASSEL … BAKIN SODA | 91 = 91 | 86 | yes |
| FLAC | 45s fixture from real WAV | 68 = 68 | **86** | yes |

Note: loudnorm-to-streaming can **lower** an already high score (MP3 91→86). That is expected trade-off, not a silent failure — still worth calling out on the product page (“streaming delivery target, not always higher score”).

---

## Code paths that back the gates

| Gate | Implementation |
|------|----------------|
| New file only | `app/nodaw/features/repairs.py` → `build_auto_repair_command` / `_ffmpeg_cmd` write to `exports/repairs/{stem}_repaired.wav` |
| Cmd = execution | `CoProducerDesktop._run_custom_repair` builds cmd once, passes same string to `_run_repair` |
| Errors | `CoProducerDesktop._on_repair_done` |
| Formats | Engine + desktop filters: `.wav .mp3 .flac` (+ more) |
| Installer | `packaging/build_installer.ps1` + `CoProducer.iss` → Setup.exe |

---

## Remaining work before “active” paid product

### Blockers

1. **#9 Digital delivery**  
   - Shopify digital product (or Gumroad/etc.)  
   - Upload **fresh** `CoProducer-Setup-*.exe` as the downloadable file  
   - Test purchase → buyer email/download works without manual send  

2. **#1 Rebuild installer after latest GUI**  
   ```powershell
   cd "D:\nodaw\CoProducer Audio Analysis"
   powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
   ```  
   Smoke-install on a clean Windows user/profile.

3. **#10 Publish real screenshots on the product page**  
   Use the three PNGs under `packaging/output/` (or recapture after rebuild). Include Home + Report.

### Should-fix (honesty / polish)

4. **#5 Post-repair report section** — embed “Applied: …” + score before/after + filter chain in HTML/JSON after auto-repair.  
5. **Re-run** `python exports/proof_golive/run_proof.py` after any scoring or repair change.  
6. Optional: unique timestamped repair filenames so re-running repair does not clobber the previous repaired file (original is already safe).

---

## How to re-prove

```powershell
cd "D:\nodaw\CoProducer Audio Analysis"
.\.venv\Scripts\python.exe exports\proof_golive\run_proof.py
# → exports\proof_golive\GO_LIVE_PROOF.json
```

**Live-ready rule:** every row in the gate matrix is **PASS** with no “UNVERIFIED” on commerce or storefront.
