# Architecture

NoDAW PRO separates customer interaction, orchestration, metrics, features,
reporting, configuration, and packaging.

    START_ANALYZER_PRO.bat
              |
              v
       app/nodaw/cli.py
              |
              v
       core/engine.py
        /      |       \
       v       v        v
    audio/  features/  reporting/
    ffmpeg  codecs     renderers
    metrics streaming  HTML/TXT/JSON/CSV/ZIP
            repairs
            history
              |
              v
       config/settings.json

The engine owns workflow composition and a per-run file-analysis cache. The
metrics layer owns all FFmpeg interaction. Feature modules own codec preview,
streaming preview, history, and repair behavior. Reporting receives plain
serializable report dictionaries and never invokes FFmpeg.

## Folder tree

    NoDAW_Audio_Quality_Analyzer_PRO/
    |-- START_ANALYZER_PRO.bat
    |-- app/
    |   |-- nodaw_cli.py
    |   +-- nodaw/
    |       |-- audio/
    |       |-- core/
    |       |-- features/
    |       |-- reporting/
    |       +-- utils/
    |-- assets/
    |-- config/
    |-- docs/
    |-- input/
    |   |-- song/
    |   |-- reference/
    |   |-- batch/
    |   +-- album/
    |-- reports/
    |-- exports/
    |-- logs/
    |-- packaging/
    +-- tests/

The customer portable package excludes development and tests. Runtime-created
audio, reports, exports, logs, Python caches, and test fixtures are excluded
from source packaging.

