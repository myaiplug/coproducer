"""
Cross-drive VST + JSON effects discovery for CoProducer.

Scans every fixed drive for hostable plugins (VST3 / VST2 DLLs) and the
user's JSON effect libraries (NoDAW pedalboard catalogs, Airwindows
parameter metadata, engine-effect files) in typical install spots.

Results are cached in config/vst_library.json so the UI loads instantly.
"""

from __future__ import annotations

import json
import os
import re
import string
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

VST3_EXT = ".vst3"
DLL_EXT = ".dll"
JSON_EXT = ".json"
BAT_EXT = ".bat"

_SKIP_DIRS = {
    "windows", "programdata", "program files (x86)", "$recycle.bin",
    "system volume information", "node_modules", ".git", ".venv", "venv",
    "temp", "tmp", "cache", "npm-cache", "pip-cache", "uv-cache",
    "hf-cache", "conda-pkgs", "package cache", "winreagent",
    "$windows.~bt", "recovery", "documents and settings", "config.msi",
    "windowsapps", "xboxgames", "msys64", "vs2022", "python314", "python311",
    "rustdesk", "lmstudio-community", "ollama", "sd_webgui",
}

_PLUGIN_DIR_HINTS = (
    "vst", "vst3", "vst2", "plugin", "plugins", "preset", "presets",
    "effects", "effect", "engine_effect", "audio", "toolz", "daw", "studio",
    "music production", "samples_presets", "vstplugins", "steinberg",
)

_FILESYSTEM_PRESET_DIRS = {"metadata", "engine_effects", "presets", "preset json's", "effects", "preset json"}

_BAT_ROOTS = (
    Path(r"I:\Projects\NoDAW\ffmpeg_bats"),
    Path(r"I:\Projects\ffmpeg_bats"),
)

_BAT_SKIP_DIRS = {
    "broken", "test_logs", "test_outputs", "whisper_setup", "node_modules",
    ".git", "complete", "_work", "test", "tests", "whisper",
}

_BAT_SKIP_STEMS = (
    "__", "helper", "_run_all", "run_effect_tests", "createzip", "launch_",
    "start_", "_shuffle", "setup", "run_all", "test_",
)

_NON_PLUGIN_DLLS = {
    "d3dcompiler_47", "dxcompiler", "dxil", "ffmpeg", "libegl", "libglesv2",
    "vk_swiftshader", "vulkan-1", "managedbass", "onnxruntime", "avcodec-",
    "avformat-", "avutil-", "swresample-", "swscale-", "libavcodec",
    "libavformat", "libavutil", "msvcp140", "vcruntime140",
}


@dataclass
class VstPlugin:
    name: str
    path: str
    kind: str  # vst3 | vst2
    drive: str
    custom: bool
    size: int = 0


@dataclass
class JsonEffect:
    name: str
    path: str
    kind: str  # nodaw_catalog | airwindows_meta | engine_effect | generic | bat_effect
    category: str = ""
    detail: str = ""


@dataclass
class VstScanResult:
    plugins: list[VstPlugin] = None  # type: ignore
    json_effects: list[JsonEffect] = None  # type: ignore
    duration_s: float = 0.0
    error: str | None = None

    def __post_init__(self):
        if self.plugins is None:
            self.plugins = []
        if self.json_effects is None:
            self.json_effects = []


def _fixed_drives() -> list[str]:
    roots: list[str] = []
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if Path(root).exists():
            roots.append(root)
    return roots


def _typical_plugin_roots(drive: str) -> list[Path]:
    return [
        Path(drive) / "VST",
        Path(drive) / "vst",
        Path(drive) / "VST3",
        Path(drive) / "VSTPlugins",
        Path(drive) / "Plugins",
        Path(drive) / "Program Files" / "Common Files" / "VST3",
        Path(drive) / "Program Files" / "Common Files" / "VST2",
        Path(drive) / "Program Files" / "Common Files" / "Steinberg" / "VST3",
        Path(drive) / "Program Files" / "Steinberg" / "VstPlugins",
        Path(drive) / "Program Files" / "VSTPlugins",
        Path(drive) / "Program Files (x86)" / "Steinberg" / "VstPlugins",
    ]


def _enumerate_drives_for_hints() -> list[Path]:
    """Shallow keyword walk: catch custom plugin/preset folders anywhere."""
    found: list[Path] = []
    for drive in _fixed_drives():
        try:
            for entry in Path(drive).iterdir():
                if not entry.is_dir():
                    continue
                name = entry.name.lower()
                if name in _SKIP_DIRS:
                    continue
                if any(h in name for h in _PLUGIN_DIR_HINTS):
                    found.append(entry)
        except Exception:
            continue
    return found


def _classify_plugin_path(path: Path) -> VstPlugin | None:
    try:
        suffix = path.suffix.lower()
        if suffix == VST3_EXT and path.is_file():
            size = path.stat().st_size
            if size < 2000:
                return None
            return VstPlugin(
                name=path.stem,
                path=str(path),
                kind="vst3",
                drive=path.drive.rstrip(":\\"),
                custom=not str(path).lower().startswith(("c:\\program files", "d:\\program files", "e:\\program files")),
                size=size,
            )
        if suffix == DLL_EXT and path.is_file():
            size = path.stat().st_size
            if size < 30_000:
                return None
            stem = path.stem.lower()
            if any(stem.startswith(b) or stem == b for b in _NON_PLUGIN_DLLS):
                return None
            return VstPlugin(
                name=path.stem,
                path=str(path),
                kind="vst2",
                drive=path.drive.rstrip(":\\"),
                custom=not str(path).lower().startswith(("c:\\program files", "d:\\program files", "e:\\program files")),
                size=size,
            )
    except Exception:
        return None
    return None


def _vst3_bundle_plugin_path(path: Path) -> Path:
    """For a .vst3 directory bundle, locate the inner binary."""
    try:
        for sub in ("Contents", "x86_64-win", "x86_64-win64", "win64", "Win64"):
            cand = path / sub
            if cand.is_dir():
                for f in cand.iterdir():
                    if f.suffix.lower() == VST3_EXT and f.is_file():
                        return f
        for f in path.iterdir():
            if f.suffix.lower() == VST3_EXT and f.is_file():
                return f
    except Exception:
        pass
    return path


def _scan_directory(
    root: Path,
    plugins: list[VstPlugin],
    json_effects: list[JsonEffect],
    *,
    progress: Callable[[str], None] | None = None,
    budget_s: float = 25.0,
    max_plugins: int = 600,
    max_json: int = 1500,
    start: float | None = None,
) -> None:
    t0 = start if start is not None else time.time()
    plugin_seen: set[str] = set()
    json_seen: set[str] = set()
    queue = [root]
    depth = 0
    while queue and time.time() - t0 < budget_s:
        next_level: list[Path] = []
        for directory in queue:
            try:
                entries = list(directory.iterdir())
            except Exception:
                continue
            subdirs: list[Path] = []
            for entry in entries:
                if time.time() - t0 >= budget_s:
                    break
                try:
                    if entry.is_dir():
                        name = entry.name.lower()
                        if name not in _SKIP_DIRS:
                            subdirs.append(entry)
                        continue
                except Exception:
                    continue
                low = entry.name.lower()
                if low.endswith(VST3_EXT):
                    resolved = _vst3_bundle_plugin_path(entry)
                    plug = _classify_plugin_path(resolved)
                    if plug and str(resolved) not in plugin_seen:
                        plugin_seen.add(str(resolved))
                        plugins.append(plug)
                        if progress and len(plugins) % 25 == 0:
                            progress(f"found {len(plugins)} plugins…")
                        if len(plugins) >= max_plugins:
                            return
                elif low.endswith(DLL_EXT) and entry.is_file():
                    plug = _classify_plugin_path(entry)
                    if plug and str(entry) not in plugin_seen:
                        plugin_seen.add(str(entry))
                        plugins.append(plug)
                        if len(plugins) >= max_plugins:
                            return
                elif low.endswith(JSON_EXT):
                    eff = _classify_json(entry)
                    if eff and str(entry) not in json_seen:
                        json_seen.add(str(entry))
                        json_effects.append(eff)
                        if len(json_effects) >= max_json:
                            return
            for sd in subdirs:
                name = sd.name.lower()
                if any(h in name for h in _PLUGIN_DIR_HINTS) or name in _FILESYSTEM_PRESET_DIRS:
                    next_level.append(sd)
            if not next_level and depth < 3:
                next_level.extend(subdirs)
        queue = next_level
        depth += 1


def _classify_json(path: Path) -> JsonEffect | None:
    """Peek JSON head to classify: NoDAW catalog / Airwindows metadata / effect."""
    if path.stat().st_size > 4_000_000:
        return None
    try:
        raw = path.read_bytes()[:200_000]
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        return None
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and ("engine" in data[0] or "paramsSchema" in data[0]):
            return JsonEffect(
                name=path.stem,
                path=str(path),
                kind="nodaw_catalog",
                category="pedalboard",
                detail=f"{len(data)} effects",
            )
        return None
    if not isinstance(data, dict):
        return None
    params = data.get("parameters")
    has_meta = "plugin" in data or "scanned_files" in data or "header" in data
    has_name = "name" in data or "category" in data
    if (isinstance(params, dict) or isinstance(params, list)) and (has_meta or has_name):
        if isinstance(params, list):
            labels = [p.get("label", "?") if isinstance(p, dict) else "?" for p in params]
            detail = ", ".join(labels[:5])
            kind = "airwindows_meta"
            name = str(data.get("plugin") or path.stem)
            category = ""
            return JsonEffect(name=name, path=str(path), kind=kind, category=category, detail=detail)
        kind = "airwindows_meta" if "scanned_files" in data or "header" in data else "engine_effect"
        detail = ", ".join(f"{k}={v}" for k, v in list(params.items())[:4])
        return JsonEffect(
            name=str(data.get("name") or data.get("plugin") or path.stem),
            path=str(path),
            kind=kind,
            category=str(data.get("category", "")),
            detail=detail,
        )
    return None


def _clean_bat_name(stem: str) -> str:
    s = stem.replace("_", " ").strip()
    tokens = [t for t in s.split() if not t.lower().startswith("-")]
    s = " ".join(tokens).strip(" .-")
    return s[:48] or stem[:48]


def parse_bat_effect(path: Path) -> JsonEffect | None:
    """Extract the ffmpeg filter chain from a one-shot effect .bat.

    Standardized format declares EFFECT_NAME / CATEGORY / FILTER and calls
    __audio_process_helper.bat; legacy files are raw
    `ffmpeg -i %~1 -af "..." %~2...` one-liners.
    """
    try:
        if path.stat().st_size > 2_000_000:
            return None
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    stem_low = path.stem.lower()
    if any(stem_low.startswith(b) for b in _BAT_SKIP_STEMS):
        return None
    ff_filter: str | None = None
    category = ""
    eff_name = ""
    m = re.search(r'set\s+"FILTER=(.+)"\s*$', text, re.M)
    if m:
        ff_filter = m.group(1).rstrip('"').strip()
        if not ff_filter:
            return None
        mc = re.search(r'set\s+"CATEGORY=([^"\r\n]+)', text, re.M)
        if mc:
            category = mc.group(1).strip()
        mn = re.search(r'set\s+"EFFECT_NAME=([^"\r\n]+)', text, re.M)
        ml = re.search(r'set\s+"EFFECT_LABEL=([^"\r\n]+)', text, re.M)
        eff_name = (mn.group(1) if mn else "") or (ml.group(1) if ml else "")
    else:
        m2 = re.search(r'-af\s+"([^"]+)"', text)
        if not m2:
            return None
        ff_filter = m2.group(1).strip()
    if not eff_name:
        eff_name = _clean_bat_name(path.stem)
    return JsonEffect(
        name=eff_name[:64],
        path=str(path),
        kind="bat_effect",
        category=category[:32],
        detail=ff_filter[:400],
    )


def _scan_bat_effects(
    result: VstScanResult,
    *,
    progress: Callable[[str], None] | None = None,
    budget_s: float = 14.0,
    start: float | None = None,
) -> None:
    """Enumerate the ffmpeg one-shot effect .bat libraries (deduped)."""
    t0 = start if start is not None else time.time()
    seen: set[tuple[str, str, str]] = set()
    for root in _BAT_ROOTS:
        if not root.is_dir():
            continue
        if progress:
            progress(f"scanning {root}…")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames
                if d.lower() not in _BAT_SKIP_DIRS and not d.startswith("_")
            ]
            if time.time() - t0 >= budget_s:
                return
            for fname in filenames:
                if not fname.lower().endswith(BAT_EXT):
                    continue
                p = Path(dirpath) / fname
                eff = parse_bat_effect(p)
                if not eff:
                    continue
                key = (eff.kind, eff.name.lower(), eff.detail)
                if key in seen:
                    continue
                seen.add(key)
                result.json_effects.append(eff)


def scan_drives(
    *,
    progress: Callable[[str], None] | None = None,
    max_plugins: int = 600,
    max_json: int = 1500,
    budget_s: float = 25.0,
) -> VstScanResult:
    t0 = time.time()
    result = VstScanResult()
    try:
        roots: list[Path] = []
        for drive in _fixed_drives():
            for root in _typical_plugin_roots(drive):
                if root.is_dir():
                    roots.append(root)
        roots.extend(_enumerate_drives_for_hints())

        appdata = Path(os.environ.get("APPDATA", "")) / "VST3"
        if appdata.is_dir():
            roots.append(appdata)
        local = Path(os.environ.get("LOCALAPPDATA", "")) / "VST3"
        if local.is_dir():
            roots.append(local)

        seen_roots: set[str] = set()
        for root in sorted(roots, key=lambda p: str(p)):
            key = str(root).lower()
            if key in seen_roots:
                continue
            seen_roots.add(key)
            if progress:
                progress(f"scanning {root}…")
            _scan_directory(
                root,
                result.plugins,
                result.json_effects,
                progress=progress,
                budget_s=budget_s,
                max_plugins=max_plugins,
                max_json=max_json,
                start=t0,
            )

        _scan_bat_effects(
            result,
            progress=progress,
            budget_s=min(14.0, budget_s * 0.5),
            start=t0,
        )

        result.plugins = sorted(
            result.plugins,
            key=lambda p: (0 if p.custom else 1, p.name.lower()),
        )
        seen_json: dict[tuple[str, str, str], str] = {}
        deduped: list[JsonEffect] = []
        for e in sorted(result.json_effects, key=lambda e: (len(e.path), e.kind, e.name.lower())):
            key = (e.kind, e.name.lower(), e.detail)
            if key in seen_json:
                continue
            seen_json[key] = e.path
            deduped.append(e)
        result.json_effects = sorted(deduped, key=lambda e: (e.kind, e.name.lower()))
    except Exception as exc:
        result.error = str(exc)
    result.duration_s = round(time.time() - t0, 1)
    return result


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def library_path(project_root: Path) -> Path:
    return Path(project_root) / "config" / "vst_library.json"


def save_library(project_root: Path, result: VstScanResult) -> None:
    try:
        path = library_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "plugins": [asdict(p) for p in result.plugins],
                    "json_effects": [asdict(e) for e in result.json_effects],
                    "duration_s": result.duration_s,
                    "error": result.error,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def load_library(project_root: Path) -> VstScanResult:
    result = VstScanResult()
    try:
        data = json.loads(library_path(project_root).read_text(encoding="utf-8"))
        for item in data.get("plugins", []):
            result.plugins.append(VstPlugin(**item))
        for item in data.get("json_effects", []):
            result.json_effects.append(JsonEffect(**item))
        result.duration_s = float(data.get("duration_s", 0.0))
    except Exception:
        pass
    return result


def find_nodaw_catalogs(json_effects: list[JsonEffect]) -> list[JsonEffect]:
    return [e for e in json_effects if e.kind == "nodaw_catalog"]


# ---------------------------------------------------------------------------
# Blacklist — plugins that fail to load / open (user-curated)
# ---------------------------------------------------------------------------

def blacklist_path(project_root: Path) -> Path:
    return Path(project_root) / "config" / "vst_blacklist.json"


def load_blacklist(project_root: Path) -> dict[str, dict[str, Any]]:
    """Return {path: {name, error, when, kind}} for plugins marked broken."""
    try:
        data = json.loads(blacklist_path(project_root).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return dict(data["entries"])
        if isinstance(data, dict):
            # flat map path -> meta
            return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def save_blacklist(project_root: Path, entries: dict[str, dict[str, Any]]) -> None:
    try:
        path = blacklist_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"entries": entries, "updated": time.time()}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def add_to_blacklist(
    project_root: Path,
    plugin_path: str | Path,
    *,
    error: str = "",
    name: str = "",
    kind: str = "",
) -> dict[str, dict[str, Any]]:
    entries = load_blacklist(project_root)
    p = str(Path(plugin_path))
    entries[p] = {
        "path": p,
        "name": name or Path(p).stem,
        "error": (error or "failed to open")[:500],
        "kind": kind,
        "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    save_blacklist(project_root, entries)
    return entries


def remove_from_blacklist(project_root: Path, plugin_path: str | Path) -> dict[str, dict[str, Any]]:
    entries = load_blacklist(project_root)
    entries.pop(str(Path(plugin_path)), None)
    save_blacklist(project_root, entries)
    return entries


def filter_blacklisted(
    plugins: list[VstPlugin],
    project_root: Path,
) -> tuple[list[VstPlugin], list[VstPlugin]]:
    """Return (ok_plugins, blacklisted_plugins)."""
    bl = load_blacklist(project_root)
    if not bl:
        return list(plugins), []
    ok, bad = [], []
    for p in plugins:
        if str(Path(p.path)) in bl:
            bad.append(p)
        else:
            ok.append(p)
    return ok, bad


def probe_plugin(path: str | Path, timeout_s: float = 8.0) -> dict[str, Any]:
    """
    Try load_plugin in a worker thread with a hard timeout.

    Returns {ok, path, name, error, elapsed_s}. Does not open the native UI.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    p = Path(path)
    out: dict[str, Any] = {
        "ok": False,
        "path": str(p),
        "name": p.stem,
        "error": None,
        "elapsed_s": 0.0,
    }
    if not p.exists():
        out["error"] = "file missing"
        return out

    t0 = time.time()
    result: dict[str, Any] = {"plugin": None, "error": None}

    def _load():
        try:
            try:
                import ctypes

                ctypes.windll.ole32.CoInitializeEx(None, 2)
            except Exception:
                pass
            import pedalboard as pb

            plug = pb.load_plugin(str(p))
            if plug is None:
                result["error"] = "load_plugin returned None"
            else:
                result["plugin"] = plug
        except Exception as exc:
            result["error"] = repr(exc)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_load)
            fut.result(timeout=float(timeout_s))
    except FuturesTimeout:
        out["error"] = f"timeout after {timeout_s:.0f}s (hung / blocked UI load)"
        out["elapsed_s"] = round(time.time() - t0, 2)
        return out
    except Exception as exc:
        out["error"] = repr(exc)
        out["elapsed_s"] = round(time.time() - t0, 2)
        return out

    out["elapsed_s"] = round(time.time() - t0, 2)
    if result["error"]:
        out["error"] = result["error"]
        return out
    # Drop reference so we don't keep the plugin alive after probe
    result["plugin"] = None
    out["ok"] = True
    return out


def probe_plugins(
    paths: list[str | Path],
    *,
    timeout_s: float = 8.0,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Probe many plugins; progress(msg, i, n) optional."""
    results: list[dict[str, Any]] = []
    n = len(paths)
    for i, path in enumerate(paths):
        if progress:
            try:
                progress(f"Probing {Path(path).name}…", i + 1, n)
            except Exception:
                pass
        results.append(probe_plugin(path, timeout_s=timeout_s))
    return results
