# QA Matrix

| Area | Automated verification |
|---|---|
| Python compatibility | Compile all application and test modules |
| Configuration | Defaults, override merge, validation, and invalid-config failure |
| Scoring | Healthy and critical synthetic metric cases |
| Reporting | HTML chart content, JSON version/schema, TXT, CSV, and ZIP creation |
| Single analysis | Synthetic stereo WAV end-to-end |
| Reference comparison | Two independently generated synthetic WAV files |
| Batch analysis | Recursive two-file batch with CSV |
| Album analysis | Two-track median and consistency calculation |
| Codec analysis | Encoder detection and real preview rendering |
| Streaming readiness | Five platform calculations and real AAC previews |
| Repairs | Recommendation serialization and executable BAT generation |
| History | Append-only records and dashboard generation |
| Complete analysis | All applicable workflows and explicit operation statuses |
| CLI | Every accepted mode plus no-preview and root arguments |
| Windows launcher | Noninteractive doctor invocation and exit code |
| Dependencies | Python, FFmpeg, FFprobe, encoders, config, CSS, launcher, and write access |
| Branding | No unfinished phase branding or legacy version strings in customer files |
| Packaging | Whitelisted files, empty input placeholders, cache removal, and ZIP build |

The smoke suite uses temporary directories and generated sine-wave audio. It
does not require or distribute copyrighted fixtures.

