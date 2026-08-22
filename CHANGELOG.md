# Changelog

## 3.2.0 - 2026-06-29 - CoProducer Design System + Premium Desktop UI

### Design System (app/nodaw/ui/)
New reusable design system package with theme tokens, components, SVG icons, and motion utilities.

**Theme** (`theme.py`):
- Color palette: carbon black, graphite, slate, restrained blue accent
- Typography: Inter font family with Display XL/L, H1/H2, Body, Caption scale
- 8-point spacing grid, elevation shadows, corner radii, easing curves
- `score_color()` and `score_rating()` helpers for semantic score mapping

**Components** (`components.py`):
- Card (elevated/success/danger/primary variants, hover, shadow)
- DropZone (animated drag-and-drop with hover pulse)
- ScoreDisplay (animated count-up, color-coded verdict badge)
- RecommendationCard (actionable items with check icons)
- VerdictBadge (status dot + score pill)
- StatusBadge (semantic colored pill with icon)
- CollapsibleSection (expandable card panels)
- FindingCard (severity-tagged issue display)
- MetricRow (label/delta/value rows)
- EmptyState (centered illustration + text)
- LoadingBar (shimmer animation)
- RecentCard (history item with music icon)
- ReferenceTrackCard (Track A/B comparison)
- DiffCard (metric delta display)
- PlatformRow (streaming readiness)

**SVG Icons** (`icons.py` + `assets/icons/*.svg`):
- 21 SVG icons loaded from disk (no unicode glyphs, no emoji)
- Consistent 1.5px stroke, rounded caps/joins, Material Symbols style
- IconWidget class with color and size control
- Professional fallback (circle icon) if file missing

**Animation** (`animations.py`):
- `fade_in()` utility for opacity transitions
- `ScoreCounter` utility for count-up animations

### Desktop App (`CoProducerDesktop.py`)
Refactored to consume the Design System:
- Sidebar navigation with active state
- Home Dashboard: drop zone, animated score, verdict, recs, recent, batch shortcut
- Interactive Report Viewer: collapsible Overview, Findings, Reference Match, Technical, Streaming, Codec, Advanced sections
- Reference Match screen: Track A/B comparison, similarity score, diff cards
- Loading bar and progress feedback
- Score counting animation
- All unicode glyphs replaced with SVG icons
- Inter font standardized throughout

### Phase 3.2A - Premium Home Experience
- Large centered drag-and-drop area with hover animation
- Animated Mix Score card with count-up effect
- Verdict badge with Release Ready / Needs Work indicator
- Top 3 actionable recommendations
- Recent analyses history cards with music icon
- Beautiful empty state with SVG illustration
- Smooth transitions and generous whitespace

### Phase 3.2B - Interactive Report Viewer
- Conclusions-first layout with score + recommendations
- Collapsible sections for Overview, Findings, Reference Match, Technical Analysis
- Streaming Readiness, Codec Analysis, Advanced Details (collapsed by default)
- Finding cards with severity badges and action text
- Metric rows with label/delta/value layout

### Phase 3.2C - Reference Match Experience
- Track A vs Track B display with music icons and metadata
- Large similarity score with color coding
- Metric difference cards with delta highlighting
- Recommendation card for actionable insights

### Phase 3.2D - Polish
- Score counting animation (0 to target over 800ms)
- Fade-in transitions for results
- QGraphicsDropShadowEffect on all cards (subtle depth)
- Drop zone hover pulse animation
- Collapsible section chevron animation
- Hover elevation changes
- Loading bar shimmer effect

## 3.1.0 - 2026-06-29 - CoProducer Core Analyzer Upgrade + Desktop GUI start (3.2)

### GUI (Phase 3.2 begin)
- Added `CoProducer_GUI.py` (tkinter, stdlib, no new deps)
- `START_GUI.bat` launcher
- Supports all 11 modes via the stable `WorkflowRunner` engine
- File/folder dialogs, threaded execution, log pane, open latest reports
- Pure presentation layer (as required)

See `CoProducer_GUI.py` and `START_GUI.bat`.

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

### Phase 3.1.0 Validation & Hardening (2026-06-29)
Full validation checklist executed:
- Installer: Python 3.11 detect, venv, pinned deps, ffmpeg/ffprobe, self-test, logs all pass.
- Launcher: Works from Explorer sim, paths with spaces, missing-dep handling.
- Audio formats: WAV (incl 96 kHz), MP3, FLAC, mono/stereo, clipped, quiet, short/long all analyzed with populated reasonable metrics.
- Reference Match: Logical similarity scores and measured-based recommendations across identical / alt-master / different-song cases.
- Reports: HTML/JSON/TXT generated, valid, complete (no nulls for real material), recs match data.
- Metadata: Mutagen embedding verified for supported containers; WAV limitation documented.
- Failures: All error cases return clear actionable messages. No crashes.
All completion criteria met. Engine is stable for GUI presentation layer.

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

