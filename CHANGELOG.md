# Changelog

## 3.1.0 - 2026-06-29 - CoProducer Core Analyzer Upgrade

### Major Changes
- Locked runtime to Python 3.11 (Essentia safety).
- Full dependency stack: pyloudnorm (ITU-R BS.1770), librosa, mutagen, numpy/scipy/soundfile.
- Essentia remains fully optional (advanced MIR mode, guarded import).
- New `packaging/install.ps1` high-end Windows installer:
  - Requires + verifies Python 3.11 (clear error + download link if missing).
  - Creates `.venv`, installs pinned requirements.
  - Verifies ffmpeg/ffprobe on PATH.
  - Runs import self-test + basic engine smoke.
  - Writes detailed log to `logs/install.log`.
- Added Mutagen support: read musical metadata, write CoProducer analysis tags back into audio files (AI metadata as USP: score, LUFS, tempo, analysis date, version, etc.).
- Reference Match Engine now produces traceable similarity score + plain-English recommendations using measured deltas (LUFS, tempo, centroid, rolloff, dyn range, etc.).
- All scores/metrics traceable to real values from ffprobe + pyloudnorm + librosa. No fake AI scores.
- New analyzer module (`audio/analyzer.py`) providing unified measurements.
- Reports (HTML/JSON/TXT) enriched with new features, technical faults, energy balance, reference_match block.

### Installer support (in order of priority)
- Windows: `packaging/install.ps1` + updated START_ANALYZER_PRO.bat (prefers py -3.11 / .venv)
- macOS: see docs/INSTALLATION.md (shell + venv script)
- Android: Termux bootstrap script (see packaging/)
- iOS: Portable package notes + desktop analysis recommended (iOS Python constraints)

## 3.0.0 - 2026-06-29

- Replaced the monolithic analyzers with a modular Python package.
- Replaced obsolete and misleading launchers with one verified PRO launcher.
- Implemented single, reference, batch, album, codec, streaming, repair,
  history, export, diagnostics, and complete-analysis workflows.
- Added phase correlation, noise floor, waveform data, crest factor, and
  expanded codec and streaming measurements.
- Added real codec and streaming preview rendering.
- Added HTML, TXT, JSON, CSV, history, and ZIP output formats.
- Added professional scored dashboards with inline waveform and spectral charts.
- Added rotating application logs and centralized validated configuration.
- Added automated unit, integration, CLI, launcher, and packaging tests.
- Removed copyrighted samples, stale reports, duplicate utility packs,
  separate legacy engines, obsolete documentation, and unsupported menus.
- Standardized all product version references on 3.0.0.

## Legacy MVP

The earlier MVP established FFmpeg-based single-file analysis, reference
comparison, and basic HTML, TXT, and JSON output. It was never a v3.0 release.

