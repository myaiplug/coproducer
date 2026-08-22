from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import RepairRecommendation, TrackAnalysis
from ..utils.files import safe_name


# ---------------------------------------------------------------------------
# Accurate auto-repair detection (conservative; only proven technical gates)
# ---------------------------------------------------------------------------

@dataclass
class RepairAction:
    """Single auto-detected repair step."""
    id: str
    label: str
    reason: str
    filter: str
    severity: str = "recommended"  # required | recommended
    confidence: float = 0.75       # 0..1


@dataclass
class RepairPlan:
    """Result of metric-driven repair detection for one mix."""
    actions: list[RepairAction] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)
    summary: str = ""
    target_lufs: float = -14.0
    tp_ceiling: float = -1.0

    @property
    def needed(self) -> bool:
        return bool(self.actions)

    @property
    def filter_chain(self) -> str:
        if not self.actions:
            return "anull"
        # highpass always first when present; loudnorm last among level tools
        order = {"highpass": 0, "true_peak_limit": 1, "loudnorm": 2}
        ordered = sorted(self.actions, key=lambda a: order.get(a.id, 9))
        return ",".join(a.filter for a in ordered if a.filter)

    def option_ids(self) -> set[str]:
        return {a.id for a in self.actions}


def _f(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _metrics_bundle(source: Any) -> dict[str, Any]:
    """Normalize TrackAnalysis | report dict | track dict into flat metric views."""
    track = source
    if isinstance(source, dict):
        if "track" in source and isinstance(source.get("track"), dict):
            track = source["track"]
        m = track.get("metrics") if isinstance(track, dict) else {}
        m = m if isinstance(m, dict) else {}
        lm = m.get("loudness") if isinstance(m.get("loudness"), dict) else {}
        extra = track.get("extra") if isinstance(track, dict) else {}
        extra = extra if isinstance(extra, dict) else {}
        faults = extra.get("technical_faults") if isinstance(extra.get("technical_faults"), dict) else {}
        spectral = m.get("spectral_balance_db") if isinstance(m.get("spectral_balance_db"), dict) else {}
        audio = track.get("audio") if isinstance(track, dict) else {}
        audio = audio if isinstance(audio, dict) else {}
        return {
            "lufs": _f(lm.get("integrated_lufs")),
            "lra": _f(lm.get("loudness_range_lu")),
            "tp": _f(lm.get("true_peak_dbtp")),
            "peak": _f(m.get("peak_dbfs")),
            "rms": _f(m.get("rms_dbfs")),
            "crest": _f(m.get("crest_factor")),
            "noise": _f(m.get("noise_floor_dbfs")),
            "phase": _f(m.get("phase_correlation") if m.get("phase_correlation") is not None else faults.get("phase_correlation")),
            "width": _f(m.get("stereo_width_percent")),
            "clip": m.get("clipped_samples_estimate") if m.get("clipped_samples_estimate") is not None else faults.get("clipped_samples"),
            "dc": _f(faults.get("dc_offset")),
            "silence": _f(faults.get("silence_ratio")),
            "spectral": spectral,
            "path": audio.get("path"),
            "file_name": audio.get("file_name"),
        }
    # TrackAnalysis dataclass path
    metrics = source.metrics
    loud = metrics.loudness
    extra = getattr(source, "extra", None) or {}
    faults = extra.get("technical_faults") if isinstance(extra, dict) else {}
    faults = faults if isinstance(faults, dict) else {}
    return {
        "lufs": _f(loud.integrated_lufs),
        "lra": _f(loud.loudness_range_lu),
        "tp": _f(loud.true_peak_dbtp),
        "peak": _f(metrics.peak_dbfs),
        "rms": _f(metrics.rms_dbfs),
        "crest": _f(metrics.crest_factor),
        "noise": _f(metrics.noise_floor_dbfs),
        "phase": _f(metrics.phase_correlation if metrics.phase_correlation is not None else faults.get("phase_correlation")),
        "width": _f(metrics.stereo_width_percent),
        "clip": metrics.clipped_samples_estimate if metrics.clipped_samples_estimate is not None else faults.get("clipped_samples"),
        "dc": _f(faults.get("dc_offset")),
        "silence": _f(faults.get("silence_ratio")),
        "spectral": metrics.spectral_balance_db or {},
        "path": source.audio.path,
        "file_name": source.audio.file_name,
    }


def detect_repair_plan(
    source: Any,
    *,
    target_lufs: float = -14.0,
    tp_ceiling: float = -1.0,
    settings: dict[str, Any] | None = None,
) -> RepairPlan:
    """
    Metric-driven repair detection - only safe, technical corrections.

    Design rules (accuracy-first, avoid making mixes worse):
    - Prefer ONE level tool: loudnorm when loudness is off-target, else TP limiter only.
    - High-pass only when rumble/DC/subsonic evidence is strong (not always-on).
    - Never auto soft-compress, EQ shelf boosts, mono wideners, silence trim, or resample.
    - Phase/width issues are cautions only (need mix-level work).
    - Clipping is a caution; we can limit peaks but cannot un-clip.
    """
    if settings:
        try:
            target_lufs = float(settings.get("analysis", {}).get("target_lufs", target_lufs))
        except (TypeError, ValueError):
            pass
        try:
            tp_ceiling = float(settings.get("analysis", {}).get("true_peak_ceiling_dbtp", tp_ceiling))
        except (TypeError, ValueError):
            pass

    b = _metrics_bundle(source)
    plan = RepairPlan(target_lufs=target_lufs, tp_ceiling=tp_ceiling)
    actions: list[RepairAction] = []

    lufs, tp, peak = b["lufs"], b["tp"], b["peak"]
    noise, dc = b["noise"], b["dc"]
    phase, clip = b["phase"], b["clip"]
    spectral = b["spectral"] or {}

    # --- True peak / sample peak evidence ---
    tp_hot = tp is not None and tp > tp_ceiling + 0.05
    tp_critical = tp is not None and tp > max(tp_ceiling, -0.5)
    sample_hot = peak is not None and peak > -0.3

    # --- Loudness evidence (streaming delivery) ---
    lufs_hot = lufs is not None and lufs > (target_lufs + 1.5)   # e.g. > -12.5 vs -14
    lufs_quiet = lufs is not None and lufs < (target_lufs - 3.5)  # e.g. < -17.5
    lufs_off = lufs is not None and abs(lufs - target_lufs) > 1.5

    # --- Subsonic / rumble / DC (high-pass only with strong evidence) ---
    # Ignore noise readings that look like "no silence in the track" (>-35 dBFS
    # is almost never a true noise floor on program material).
    need_hp = False
    hp_reasons: list[str] = []
    hp_conf = 0.0
    if noise is not None and -70.0 <= noise <= -42.0:
        # Plausible elevated floor (hiss/rumble window), not dense music RMS
        need_hp = True
        hp_reasons.append(f"elevated noise floor ({noise:.1f} dBFS)")
        hp_conf = max(hp_conf, 0.74)
        if noise > -48.0:
            hp_conf = max(hp_conf, 0.86)
    if dc is not None and abs(dc) > 0.012:
        need_hp = True
        hp_reasons.append(f"DC offset ({dc:.4f})")
        hp_conf = max(hp_conf, 0.92)
    # Relative sub energy much hotter than bass band (subsonic build-up)
    try:
        sub = _f(spectral.get("sub_bass") if "sub_bass" in spectral else spectral.get("SUB"))
        bass = _f(spectral.get("bass") if "bass" in spectral else spectral.get("BASS"))
        # spectral_balance_db is relative dB vs loudest band (0 = hottest)
        if sub is not None and bass is not None and sub >= -0.8 and (sub - bass) >= 4.0:
            need_hp = True
            hp_reasons.append(f"sub-bass dominates bass by {sub - bass:.1f} dB")
            hp_conf = max(hp_conf, 0.72)
    except Exception:
        pass

    if need_hp and hp_conf >= 0.72:
        actions.append(RepairAction(
            id="highpass",
            label="Subsonic high-pass (25 Hz)",
            reason="; ".join(hp_reasons) or "Subsonic / rumble evidence",
            filter="highpass=f=25",
            severity="recommended",
            confidence=round(min(0.95, hp_conf), 2),
        ))

    # --- Level path: loudnorm OR true-peak limiter (not redundant stack) ---
    # loudnorm when program loudness is meaningfully off target, or TP is critical
    # while loudness is also not already parked near target.
    if lufs is not None and (lufs_hot or lufs_quiet or (lufs_off and (tp_hot or sample_hot))):
        reasons = []
        conf = 0.8
        if lufs_hot:
            reasons.append(f"too loud for streaming ({lufs:.1f} LUFS, target {target_lufs:.0f})")
            conf = 0.92
        elif lufs_quiet:
            reasons.append(f"well below streaming target ({lufs:.1f} LUFS, target {target_lufs:.0f})")
            conf = 0.85
        else:
            reasons.append(f"loudness off target ({lufs:.1f} vs {target_lufs:.0f} LUFS)")
        if tp_hot and tp is not None:
            reasons.append(f"true peak {tp:.2f} dBTP over ceiling {tp_ceiling:.1f}")
            conf = max(conf, 0.9)
        # dual_mono + linear-friendly settings; hard limiter appended in finalize
        actions.append(RepairAction(
            id="loudnorm",
            label="Loudness normalize (streaming)",
            reason="; ".join(reasons),
            filter=(
                f"loudnorm=I={target_lufs}:TP={tp_ceiling}:LRA=11:"
                f"dual_mono=true:offset=0"
            ),
            severity="required" if lufs_hot or tp_critical else "recommended",
            confidence=round(conf, 2),
        ))
    elif tp_hot or (sample_hot and (lufs is None or abs(lufs - target_lufs) <= 1.5)):
        # Loudness already near target: only cap peaks (avoids re-coloring dynamics)
        why = []
        conf = 0.82
        if tp is not None and tp_hot:
            why.append(f"true peak {tp:.2f} dBTP exceeds {tp_ceiling:.1f} dBTP")
            conf = 0.93 if tp_critical else 0.86
        if sample_hot and peak is not None:
            why.append(f"sample peak {peak:.2f} dBFS near full scale")
            conf = max(conf, 0.8)
        actions.append(RepairAction(
            id="true_peak_limit",
            label="True-peak limiter",
            reason="; ".join(why) or "Peak over ceiling",
            filter=_hard_tp_limiter(tp_ceiling),
            severity="required" if tp_critical else "recommended",
            confidence=round(conf, 2),
        ))
    elif lufs is not None and lufs_off and not tp_hot:
        # Mild loudness drift without peak emergency - still recommend loudnorm
        actions.append(RepairAction(
            id="loudnorm",
            label="Loudness normalize (streaming)",
            reason=f"integrated loudness {lufs:.1f} LUFS is {abs(lufs - target_lufs):.1f} LU from {target_lufs:.0f}",
            filter=(
                f"loudnorm=I={target_lufs}:TP={tp_ceiling}:LRA=11:"
                f"dual_mono=true:offset=0"
            ),
            severity="recommended",
            confidence=0.78,
        ))

    # --- Cautions (never auto-filter) ---
    try:
        clip_n = int(clip) if clip is not None else 0
    except (TypeError, ValueError):
        clip_n = 0
    if clip_n > 0:
        plan.cautions.append(
            f"Clipping evidence ({clip_n} samples). Level control helps overs but cannot restore clipped transients."
        )
    if phase is not None and phase < 0.1:
        plan.cautions.append(
            f"Phase correlation is low ({phase:.2f}). Fix in the mix (not auto stereo repair)."
        )
    if phase is not None and phase < 0:
        plan.cautions.append(
            "Negative phase correlation - mono playback may cancel bass. Manual mid/side work required."
        )

    plan.actions = actions
    if not actions:
        plan.summary = "No automatic technical repair needed for current gates."
    else:
        bits = [f"{a.label} ({int(a.confidence * 100)}%)" for a in actions]
        plan.summary = "Auto-detected: " + "; ".join(bits)
    return plan


def _hard_tp_limiter(tp_ceiling: float = -1.0) -> str:
    """Always-on true-peak safety cap after loudness tools (prevents residual overs)."""
    limit_lin = 10 ** (float(tp_ceiling) / 20.0)
    # level=disabled keeps makeup off; alimiter catches intersample residuals
    return f"alimiter=limit={limit_lin:.6f}:level=disabled:attack=5:release=50"


def finalize_repair_chain(
    chain: str,
    *,
    tp_ceiling: float = -1.0,
    ensure_limiter: bool = True,
) -> str:
    """
    Normalize a filter chain so delivery always respects true-peak ceiling.

    Single-pass loudnorm often leaves TP slightly above target; appending a hard
    alimiter makes post-repair scores reflect the *intended* technical fix.
    """
    af = (chain or "anull").strip() or "anull"
    if not ensure_limiter:
        return af
    if "alimiter=" in af:
        return af
    # If only anull, no need to limit
    if af == "anull":
        return af
    return f"{af},{_hard_tp_limiter(tp_ceiling)}"


def build_auto_repair_command(
    input_path: Path | str,
    output_dir: Path | str,
    plan: RepairPlan,
) -> tuple[str, Path, str]:
    """Build FFmpeg command from an auto-detected RepairPlan (absolute paths)."""
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    src = Path(input_path).expanduser().resolve()
    out = (output_dir / f"{src.stem}_repaired.wav").resolve()
    chain = finalize_repair_chain(
        plan.filter_chain or "anull",
        tp_ceiling=float(plan.tp_ceiling),
        ensure_limiter=bool(plan.actions),
    )
    # Keep native sample rate; write 24-bit PCM for headroom (not forced 192k)
    cmd = (
        f'ffmpeg -y -i "{src}" -af "{chain}" '
        f'-c:a pcm_s24le "{out}"'
    )
    return cmd, out, chain


def run_auto_repair(
    input_path: Path | str,
    output_dir: Path | str,
    plan: RepairPlan,
    *,
    prefer_pedalboard: bool = True,
) -> dict[str, Any]:
    """
    Execute repair: Pedalboard first (high quality), FFmpeg fallback.

    Returns dict with ok, out_path, engine, command, applied, error, ...
    Never overwrites the original input path.
    """
    import subprocess

    from ..audio.pedalboard_repair import (
        apply_pedalboard_repair,
        pedalboard_available,
        plan_to_pedalboard_kwargs,
    )

    cmd, out, chain = build_auto_repair_command(input_path, output_dir, plan)
    src = Path(input_path).expanduser().resolve()
    out = Path(out).resolve()
    result: dict[str, Any] = {
        "ok": False,
        "out_path": str(out),
        "in_path": str(src),
        "command": cmd,
        "chain": chain,
        "engine": None,
        "applied": [],
        "error": None,
    }
    if not plan.actions:
        result["error"] = "no repair actions"
        return result
    if src.resolve() == out.resolve():
        result["error"] = "refusing to overwrite original"
        return result

    if prefer_pedalboard and pedalboard_available():
        kwargs = plan_to_pedalboard_kwargs(plan)
        pb_res = apply_pedalboard_repair(src, out, **kwargs)
        if pb_res.ok and out.is_file():
            result.update(
                {
                    "ok": True,
                    "engine": "pedalboard",
                    "applied": pb_res.filters_applied,
                    "input_lufs": pb_res.input_lufs,
                    "output_lufs": pb_res.output_lufs,
                    "input_tp_db": pb_res.input_tp_db,
                    "output_tp_db": pb_res.output_tp_db,
                    "command": (
                        f"# Pedalboard repair: {', '.join(pb_res.filters_applied)}\n"
                        f"# Equivalent FFmpeg reference:\n{cmd}"
                    ),
                }
            )
            return result
        result["pedalboard_error"] = pb_res.error

    # FFmpeg fallback
    try:
        pr = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=300
        )
        if pr.returncode == 0 and out.is_file():
            result.update(
                {
                    "ok": True,
                    "engine": "ffmpeg",
                    "applied": [chain],
                    "stderr_tail": (pr.stderr or "")[-300:],
                }
            )
        else:
            result["error"] = (pr.stderr or pr.stdout or f"exit {pr.returncode}")[-500:]
            result["engine"] = "ffmpeg"
    except Exception as exc:
        result["error"] = str(exc)
        result["engine"] = "ffmpeg"
    return result


def _ffmpeg_cmd(src: Path, out: Path, filter_chain: str) -> str:
    """Build a copy-pasteable FFmpeg command with absolute quoted paths."""
    src = Path(src).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    af = finalize_repair_chain((filter_chain or "anull").strip() or "anull")
    return f'ffmpeg -y -i "{src}" -af "{af}" -c:a pcm_s24le "{out}"'


def build_repairs(
    track: TrackAnalysis, settings: dict[str, Any], output_dir: Path
) -> list[RepairRecommendation]:
    """Engine recommendations - each item carries its own suggested FFmpeg command."""
    plan = detect_repair_plan(track, settings=settings)
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    src = Path(track.audio.path).expanduser().resolve()
    stem = safe_name(Path(track.audio.file_name or src.name).stem)
    chain = plan.filter_chain or "anull"

    if not plan.actions:
        out = output_dir / f"{stem}_repaired.wav"
        return [
            RepairRecommendation(
                title="No automatic repair needed",
                reason=plan.summary,
                ffmpeg_filter="anull",
                command=_ffmpeg_cmd(src, out, "anull"),
                caution=" ".join(plan.cautions)
                or "Technical gates pass. Remaining quality is listening judgment.",
            )
        ]

    items: list[RepairRecommendation] = []
    # Primary combined repair (full detected chain)
    out_all = output_dir / f"{stem}_repaired.wav"
    items.append(
        RepairRecommendation(
            title="Auto technical repair (all suggested)",
            reason=plan.summary + " " + " ".join(a.reason for a in plan.actions),
            ffmpeg_filter=chain,
            command=_ffmpeg_cmd(src, out_all, chain),
            caution=" ".join(plan.cautions)
            or "Review the rendered file by ear before replacing any master.",
        )
    )
    # Per-action rows: each has ITS OWN ffmpeg command (that filter only)
    for a in plan.actions:
        out_one = output_dir / f"{stem}_{safe_name(a.id)}_repaired.wav"
        items.append(
            RepairRecommendation(
                title=a.label,
                reason=a.reason,
                ffmpeg_filter=a.filter,
                command=_ffmpeg_cmd(src, out_one, a.filter),
                caution=f"Confidence {int(a.confidence * 100)}% · {a.severity}",
            )
        )
    return items


def write_repair_launcher(repairs: list[RepairRecommendation], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    commands = "\n".join(item.command for item in repairs)
    content = (
        "@echo off\n"
        "setlocal EnableExtensions\n"
        "title NoDAW Audio Quality Analyzer PRO - Apply Repair\n"
        "where ffmpeg >nul 2>nul || (echo [ERROR] FFmpeg is not on PATH.& exit /b 1)\n"
        f"{commands}\n"
        "if errorlevel 1 exit /b %errorlevel%\n"
        "echo Repair export completed. Review the output before release.\n"
    )
    destination.write_text(content, encoding="utf-8")
    return destination


def build_reference_repairs(
    user: TrackAnalysis,
    reference: TrackAnalysis,
    output_dir: Path,
) -> list[RepairRecommendation]:
    filters: list[str] = []
    reasons: list[str] = []
    centers = {
        "sub_bass": 45,
        "bass": 100,
        "low_mid": 250,
        "mid": 800,
        "presence": 2500,
        "high": 7000,
        "air": 13000,
    }
    for band, user_value in user.metrics.spectral_balance_db.items():
        reference_value = reference.metrics.spectral_balance_db.get(band)
        if user_value is None or reference_value is None:
            continue
        difference = user_value - reference_value
        if abs(difference) < 2:
            continue
        gain = max(-2.5, min(2.5, round(-difference * 0.35, 2)))
        if abs(gain) >= 0.5 and band in centers:
            filters.append(f"equalizer=f={centers[band]}:t=q:w=1:g={gain}")
            reasons.append(
                f"{band.replace('_', ' ')} differs from the reference by {difference:.1f} dB."
            )
    user_lufs = user.metrics.loudness.integrated_lufs
    reference_lufs = reference.metrics.loudness.integrated_lufs
    reference_peak = reference.metrics.loudness.true_peak_dbtp
    if user_lufs is not None and reference_lufs is not None and abs(user_lufs - reference_lufs) > 1:
        target = max(-18.0, min(-8.0, reference_lufs))
        ceiling = min(-1.0, reference_peak if reference_peak is not None else -1.0)
        filters.append(f"loudnorm=I={target}:TP={ceiling}:LRA=11")
        reasons.append(
            f"Program loudness differs from the reference by {user_lufs - reference_lufs:.1f} LU."
        )
    if not filters:
        filters.append("anull")
        reasons.append(
            "No conservative automatic correction is required for the measured differences."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{safe_name(Path(user.audio.file_name).stem)}_reference_matched.wav"
    chain = ",".join(filters)
    command = f'ffmpeg -y -i "{user.audio.path}" -af "{chain}" "{output}"'
    return [
        RepairRecommendation(
            title="Conservative reference-match repair",
            reason=" ".join(reasons),
            ffmpeg_filter=chain,
            command=command,
            caution="Reference matching is not mastering. Compare the result by ear and retain the original source.",
        )
    ]
