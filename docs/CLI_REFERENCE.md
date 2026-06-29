# CLI Reference

The root launcher accepts every Python CLI option and returns the Python exit
code:

    START_ANALYZER_PRO.bat --mode MODE [options]

Modes:

- analyze: single-file quality report
- reference: user/reference comparison
- both: compatibility alias that runs analyze and reference
- batch or folder: recursive folder analysis
- album: album consistency analysis
- codecs: codec analysis and preview rendering
- streaming: platform readiness and normalized previews
- fixes or repairs: repair report and executable BAT command
- history: rebuild the history dashboard
- export: package current reports in a ZIP
- all or complete: run every applicable workflow
- doctor: dependency and installation diagnostics

Options:

- --root PATH: application root; normally supplied by the launcher
- --input FILE: primary audio override
- --reference FILE: reference audio override
- --folder PATH: batch or album folder override
- --no-previews: skip audio rendering while retaining analysis
- --verbose: show debug-level console logs
- --list-modes: print accepted mode names
- --version: print the application version

Exit codes:

- 0: completed successfully
- 1: input, configuration, dependency, FFmpeg, or workflow failure
- 2: dependency diagnostics completed with one or more failed checks

