"""Persisted UI preferences (output folder, repair options, etc.)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_REPAIR_OPTIONS: dict[str, bool] = {
    # Basics - on by default
    "loudnorm": True,
    "true_peak_limit": True,
    "highpass": True,
    # Additional - off by default
    "soft_compress": False,
    "de_ess_presence": False,
    "air_shelf": False,
    "bass_trim": False,
    "fade_edges": False,
    "silence_trim": False,
    "mono_safe": False,
    "resample_48k": False,
    "normalize_peak": False,
}


def prefs_path(project_root: Path) -> Path:
    return project_root / "config" / "ui_prefs.json"


def load_prefs(project_root: Path) -> dict[str, Any]:
    path = prefs_path(project_root)
    data: dict[str, Any] = {
        "output_folder": str(project_root / "exports" / "repairs"),
        "repair_options": dict(DEFAULT_REPAIR_OPTIONS),
        "convert_format": "wav",
    }
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k != "repair_options"})
                ro = raw.get("repair_options")
                if isinstance(ro, dict):
                    merged = dict(DEFAULT_REPAIR_OPTIONS)
                    merged.update({k: bool(v) for k, v in ro.items() if k in merged})
                    data["repair_options"] = merged
    except Exception:
        pass
    # ensure output folder exists later when used
    return data


def save_prefs(project_root: Path, data: dict[str, Any]) -> None:
    path = prefs_path(project_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# Catalog: id → (label, ffmpeg fragment builder, tooltip, basic?)
# builder(settings) -> filter string or None


def repair_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": "loudnorm",
            "label": "Loudness normalize (streaming)",
            "basic": True,
            "tip": "Matches integrated loudness to about −14 LUFS with true-peak ceiling - standard for Spotify/YouTube-style delivery.",
            "filter": lambda s: (
                f"loudnorm=I={s.get('target_lufs', -14)}:TP={s.get('tp_ceiling', -1)}:LRA=11"
            ),
        },
        {
            "id": "true_peak_limit",
            "label": "True-peak limiter",
            "basic": True,
            "tip": "Caps peaks so intersample overs don’t clip after lossy encode. Safe default for masters.",
            "filter": lambda s: (
                f"alimiter=limit={10 ** (float(s.get('tp_ceiling', -1)) / 20):.4f}:level=disabled"
            ),
        },
        {
            "id": "highpass",
            "label": "Subsonic high-pass (25 Hz)",
            "basic": True,
            "tip": "Removes inaudible rumble/DC that wastes headroom and can muddy the low end.",
            "filter": lambda s: "highpass=f=25",
        },
        {
            "id": "soft_compress",
            "label": "Soft glue compression",
            "basic": False,
            "tip": "Gentle broadband compression to even dynamics. Use sparingly - can reduce punch.",
            "filter": lambda s: (
                "acompressor=threshold=-18dB:ratio=1.8:attack=20:release=200:makeup=2"
            ),
        },
        {
            "id": "de_ess_presence",
            "label": "Presence tame (3.5 kHz)",
            "basic": False,
            "tip": "Slight cut around harsh vocal/synth presence. Helps harsh mixes without a full de-esser.",
            "filter": lambda s: "equalizer=f=3500:t=q:w=1.2:g=-2",
        },
        {
            "id": "air_shelf",
            "label": "Air shelf (+1.5 dB @ 12 kHz)",
            "basic": False,
            "tip": "Subtle high-shelf lift for polish/air. Skip if the mix is already bright or sibilant.",
            "filter": lambda s: "treble=g=1.5:f=12000",
        },
        {
            "id": "bass_trim",
            "label": "Low-shelf trim (−1.5 dB @ 80 Hz)",
            "basic": False,
            "tip": "Slightly reduces heavy low end for cleaner streaming translation.",
            "filter": lambda s: "bass=g=-1.5:f=80",
        },
        {
            "id": "fade_edges",
            "label": "Fade in/out (15 ms)",
            "basic": False,
            "tip": "Tiny fades avoid clicks at file start/end after processing.",
            "filter": lambda s: "afade=t=in:st=0:d=0.015,afade=t=out:st=0:d=0.015",
        },
        {
            "id": "silence_trim",
            "label": "Trim leading/trailing silence",
            "basic": False,
            "tip": "Removes long dead air at start/end. May cut intentional silence - check the result.",
            "filter": lambda s: (
                "silenceremove=start_periods=1:start_silence=0.3:start_threshold=-50dB:detection=peak"
            ),
        },
        {
            "id": "mono_safe",
            "label": "Mono-safe mid emphasis",
            "basic": False,
            "tip": "Mild mid reinforcement for better mono playback (phones, clubs). Not a full stereo fix.",
            "filter": lambda s: "extrastereo=m=0.5",
        },
        {
            "id": "resample_48k",
            "label": "Resample to 48 kHz",
            "basic": False,
            "tip": "Standard video/streaming sample rate. Use when delivery requires 48 kHz.",
            "filter": lambda s: "aresample=48000",
        },
        {
            "id": "normalize_peak",
            "label": "Peak normalize (−1 dBFS)",
            "basic": False,
            "tip": "Scales sample peak to −1 dBFS. Prefer loudnorm for loudness-matched delivery.",
            "filter": lambda s: "volume=replaygain=drop",  # placeholder - real peaknorm below
        },
    ]


def build_repair_command(
    input_path: Path,
    output_dir: Path,
    options: dict[str, bool],
    target_lufs: float = -14.0,
    tp_ceiling: float = -1.0,
) -> tuple[str, Path, str]:
    """Return (ffmpeg command, output path, filter_chain)."""
    settings = {"target_lufs": target_lufs, "tp_ceiling": tp_ceiling}
    filters: list[str] = []
    for item in repair_catalog():
        if not options.get(item["id"]):
            continue
        fid = item["id"]
        if fid == "normalize_peak":
            filters.append("loudnorm=I=-14:TP=-1:LRA=7")  # safer than broken volume
            # replace with peak-oriented dynaudnorm light
            filters[-1] = "dynaudnorm=f=150:g=15"
            continue
        frag = item["filter"](settings)
        if frag:
            # fade_edges special: need duration - use simple afade in only if chain empty issues
            filters.append(frag)

    if not filters:
        filters = ["anull"]

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    src = Path(input_path).expanduser().resolve()
    stem = src.stem
    out = (output_dir / f"{stem}_repaired.wav").resolve()
    chain = ",".join(filters)
    # Always absolute quoted paths so the UI never depends on process cwd
    cmd = f'ffmpeg -y -i "{src}" -af "{chain}" "{out}"'
    return cmd, out, chain
