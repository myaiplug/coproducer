# CoProducer Core Analyzer v3.1

**CoProducer** brings the proven NoDAW measurement engine into a modern, dependency-rich professional analyzer.

Python 3.11 locked runtime. Real measurements using pyloudnorm (LUFS), librosa (MIR), mutagen (metadata + analysis embedding), + ffprobe.

NoDAW PRO is a Windows-first audio engineering analyzer powered by Python and
FFmpeg. It produces measurable, inspectable reports without requiring a DAW or
third-party Python packages.

## Capabilities

- Single-file technical analysis and health scoring
- Reference-track comparison and conservative match recommendations
- Recursive folder and batch analysis with CSV summaries
- Album loudness, peak, and dynamic consistency analysis
- Codec suitability analysis with real MP3, AAC, and Opus previews
- Streaming readiness for Spotify, Apple Music, YouTube, Amazon Music, and TIDAL
- Repair recommendations with executable FFmpeg command files
- HTML, TXT, JSON, CSV, history, and ZIP report exports
- Dependency diagnostics and a complete-analysis workflow

Reports include integrated LUFS, true peak, peak and RMS levels, crest-based
dynamic range, stereo width, phase correlation, frequency balance, clipping
estimates, noise floor, codec guidance, streaming compatibility, repair
commands, color-coded scores, and SVG charts.

## Quick start

1. Install Python 3.10 or newer.
2. Install FFmpeg and ensure ffmpeg and ffprobe are on PATH.
3. Place a song in input/song.
4. Optionally add a reference under input/reference, batch material under
   input/batch, or album tracks under input/album.
5. Double-click START_ANALYZER_PRO.bat and choose an operation.

For scripted use:

    START_ANALYZER_PRO.bat --mode analyze
    START_ANALYZER_PRO.bat --mode reference
    START_ANALYZER_PRO.bat --mode all
    START_ANALYZER_PRO.bat --mode doctor

Generated material is written to reports, exports, and logs. Input audio and
generated outputs are intentionally excluded from source-control packaging.

## Requirements

- Windows 10 or Windows 11
- Python 3.10+
- FFmpeg and FFprobe on PATH
- Write access to the application directory

No pip packages are required. See docs/INSTALLATION.md for clean-machine setup,
docs/USER_GUIDE.md for workflows, and docs/CLI_REFERENCE.md for automation.

## Important engineering limitation

Automated measurements cannot replace monitoring, mix context, human hearing,
or mastering judgment. Clipping is reported as an estimate based on full-scale
sample peaks. Repair commands are conservative starting points and never
overwrite the source.

