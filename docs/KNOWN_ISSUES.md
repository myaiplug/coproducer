# Known Issues and Engineering Boundaries

- Clipping is an estimate based on full-scale peak behavior reported by
  FFmpeg. It is not a waveform restoration or forensic declipping system.
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

