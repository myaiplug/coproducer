# Known Issues and Engineering Boundaries

- Clipping detection uses sample peak threshold. Very loud but non-hard-clipped masters may trigger or miss depending on dither. Use the count as a guide only.
- LUFS on extremely short clips (< ~0.5s) or pure test tones may return None. Real program material produces reliable values via pyloudnorm.
- Metadata embedding (CoProducer* tags) works reliably on MP3/FLAC/M4A. WAV has limited tag support (ID3 not native); analysis data is still in the JSON/HTML reports.
- Dynamic range is a crest-based estimate; loudness range is reported
  separately from FFmpeg loudnorm.
- Spectral bands require multiple FFmpeg filter passes. Large batch and album
  jobs are CPU-intensive even though band passes are parallelized conservatively.
- Streaming previews model target loudness and true-peak limits. Platforms can
  change proprietary encoding and normalization behavior.
- Repair scripts are intentionally conservative and never replace the source.
  Mix-level polarity, clipping, and arrangement problems require human work.
- The thin portable ZIP requires system Python and FFmpeg. A distributor can
  supply approved runtime folders to build_portable.ps1 for a standalone ZIP
  and must include the corresponding upstream licenses.
- The Inno Setup definition is installer-ready; compiling the installer
  requires Inno Setup 6, which is not a runtime dependency of NoDAW PRO.

These boundaries are disclosed product behavior, not incomplete menu features.

