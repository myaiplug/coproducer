# -*- coding: utf-8 -*-
"""Engine/backends/auto_backend.py — Automatic backend selection based on analysis + available binaries."""

from __future__ import annotations

from . import ffmpeg_backend, sox_backend, pedalboard_backend


def select(analysis: dict, preferred: str = "auto") -> str:
    """Select best backend: auto, ffmpeg, sox, pedalboard, or hybrid."""
    if preferred != "auto":
        return preferred

    # Pedalboard preferred for enhancement (musical quality)
    if pedalboard_backend.available():
        return "pedalboard"

    # FFmpeg fallback for everything
    return "ffmpeg"


def build_chain(backend: str, chain_type: str, strength: float = 0.5):
    """Return the filter args or plugin config for the chosen backend."""
    if backend == "pedalboard":
        from .pedalboard_backend import artifact_chain, noise_chain, enhancer_chain
        return {
            "artifact": artifact_chain(strength),
            "noise": noise_chain(strength),
            "enhance": enhancer_chain(strength),
        }.get(chain_type, artifact_chain(strength))

    if backend == "sox":
        return {"type": "sox", "chain": chain_type, "strength": strength}

    # Default: FFmpeg
    from .ffmpeg_backend import build_artifact_filter, build_noise_filter, build_enhancer_filter
    return {
        "artifact": build_artifact_filter(strength),
        "noise": build_noise_filter(strength),
        "enhance": build_enhancer_filter(strength),
    }.get(chain_type, build_artifact_filter(strength))
