# CoProducer v3.2 -- iOS / iPhone Support

## Current Status

CoProducer's full analysis pipeline (librosa, pyloudnorm, soundfile, NumPy/SciPy)
cannot run natively on stock iOS due to:

- Sandbox restrictions (no subprocess/ffmpeg access)
- Missing native wheel builds for ARM64 iOS
- No CPython runtime with full extension support on stock iOS

## Recommended Workflow

### Option 1: Desktop Analysis + Mobile Review (RECOMMENDED)

1. Analyze on Windows or macOS using the full desktop installer.
2. Open the generated HTML report on iPhone via:
   - iCloud Drive
   - AirDrop
   - Files app
3. The reports are fully self-contained HTML with embedded scores, findings,
   and recommendations. No app needed on iOS.

### Option 2: Pythonista / Carnets (Lightweight Inspection)

Third-party iOS Python runtimes (Pythonista, Carnets) can run a subset of
CoProducer's analysis for basic metadata inspection:

- Mutagen tag reading (format, bitrate, duration)
- Basic ffprobe-style metadata extraction
- Analysis tag display (if file was pre-analyzed on desktop)

They **cannot** run:
- pyloudnorm LUFS measurement
- librosa spectral/MIR analysis
- FFmpeg subprocess calls
- Full TrackAnalysis pipeline

### Option 3: Custom Swift + PythonKit App (Future)

A native SwiftUI app wrapping PythonKit could bridge CoProducer's core
analysis. This requires:

- Python 3.11 ARM64 framework embedded in the app bundle
- Pre-compiled wheels for NumPy, SciPy, soundfile for iOS
- FFmpeg as a static library or framework
- At minimum 500 MB app bundle size

Not currently planned for v3.x. Desktop remains the primary platform.

## Viewing Reports on iOS

After desktop analysis, transfer the report folder:

```
reports/
  html/   <-- Open these on iPhone
  json/
  txt/
```

The HTML reports are mobile-responsive and render well in Safari.

## File Tag Metadata

CoProducer writes analysis data (score, LUFS, tempo) into audio file tags.
These tags are readable on iPhone via:

- Files app > Get Info (limited)
- Third-party tag editors (Metadatics, MP3Tag iOS alternatives)
- The CoProducer JSON report for full detail

## Summary

| Capability          | Desktop (Win/Mac) | iOS Native | Pythonista |
|---------------------|-------------------|------------|------------|
| Full analysis       | YES               | No         | No         |
| Tag reading         | YES               | Partial    | YES        |
| HTML report viewing | YES               | YES        | YES        |
| GUI interface       | YES               | No         | No         |
| FFmpeg repairs      | YES               | No         | No         |
