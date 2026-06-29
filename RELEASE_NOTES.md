# NoDAW Audio Quality Analyzer PRO v3.0.0

v3.0.0 is the first production-structured release of NoDAW PRO. It consolidates
the original analyzer and reference engine, removes unsupported menu claims,
and delivers a consistent Windows application with testable CLI workflows.

## Release highlights

- Eleven working interactive menu operations
- Backward-compatible analyze, reference, and both CLI modes
- Batch and album consistency reporting
- Real codec and platform-normalized preview exports
- Project history and report bundle exports
- One-click conservative FFmpeg repair scripts
- Dependency diagnostics for clean-machine validation
- No third-party Python package dependencies
- No bundled copyrighted sample music

## Upgrade note

The v3.0 directory structure is intentionally different from the MVP. Copy
personal audio into the new input folders. Do not copy legacy launchers,
generated reports, or nested utility packs into v3.0.

## Verification

Run:

    python -m unittest discover -s tests -v
    START_ANALYZER_PRO.bat --mode doctor

The portable artifact is produced by packaging/build_portable.ps1.

