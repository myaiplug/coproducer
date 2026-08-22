# -*- coding: utf-8 -*-
"""
Read/write musical metadata and embedded cover art (Mutagen).

Supports common containers: MP3 (ID3 APIC), FLAC, M4A/MP4, OGG, AIFF.
WAV has limited tag support - cover embed may be skipped with a clear result.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _text_from_frame(frame) -> str:
    try:
        t = getattr(frame, "text", None)
        if isinstance(t, list) and t:
            return str(t[0])
        if t is not None:
            return str(t)
    except Exception:
        pass
    return str(frame)


def read_tags(path: Path | str) -> dict[str, str]:
    """Easy-tag read for common fields (incl. WAV ID3 chunks)."""
    p = Path(path)
    if not p.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        from mutagen import File as MutagenFile

        # WAV: mutagen WAVE stores ID3 frames, not easy keys
        if p.suffix.lower() == ".wav":
            try:
                from mutagen.wave import WAVE

                w = WAVE(str(p))
                if w.tags:
                    id3_map = {
                        "TIT2": "title",
                        "TPE1": "artist",
                        "TALB": "album",
                        "TCON": "genre",
                        "TDRC": "date",
                        "TYER": "date",
                        "TBPM": "bpm",
                        "TPE2": "albumartist",
                        "TRCK": "tracknumber",
                        "TPOS": "discnumber",
                        "TKEY": "key",
                    }
                    for frame_id, easy_key in id3_map.items():
                        if frame_id in w.tags:
                            out[easy_key] = _text_from_frame(w.tags[frame_id])
                    for key in list(w.tags.keys()):
                        if str(key).startswith("COMM") and "comment" not in out:
                            out["comment"] = _text_from_frame(w.tags[key])
                if out:
                    return out
            except Exception:
                pass

        audio = MutagenFile(str(p), easy=True)
        if audio is None:
            return out
        for key in (
            "artist", "album", "title", "genre", "date", "bpm", "key", "comment",
            "albumartist", "tracknumber", "discnumber",
        ):
            if key in audio and audio[key]:
                val = audio[key]
                out[key] = val[0] if isinstance(val, list) else str(val)
        return out
    except Exception:
        return out


def save_audio_with_metadata(
    path: Path | str,
    tags: dict[str, str] | None = None,
    *,
    cover_bytes: bytes | None = None,
    cover_path: Path | str | None = None,
    cover_mime: str | None = None,
    dest: Path | str | None = None,
    overwrite: bool = True,
) -> tuple[bool, str]:
    """
    Write tags + cover into an audio file and optionally export to dest.

    - If dest is None: overwrite ``path`` in place.
    - If dest is set: copy audio to dest (overwrite when allowed), then write
      tags/cover onto dest. Same container/extension is required.
    """
    import shutil

    src = Path(path)
    if not src.is_file():
        return False, "Audio file not found"

    target = Path(dest) if dest else src
    notes: list[str] = []

    if target.resolve() != src.resolve():
        if target.suffix.lower() != src.suffix.lower():
            return (
                False,
                f"Keep the same format ({src.suffix}) when saving a copy. "
                f"Got {target.suffix or 'no extension'}.",
            )
        if target.exists() and not overwrite:
            return False, f"File already exists: {target.name}"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(target))
            notes.append(f"copied → {target.name}")
        except Exception as exc:
            return False, f"Could not write file: {exc}"
    else:
        notes.append(f"updated {src.name}")

    # Tags
    tag_dict = {str(k).strip().lower(): str(v).strip() for k, v in (tags or {}).items()}
    ok_t, msg_t = write_tags(target, tag_dict)
    notes.append(msg_t if ok_t else f"tags failed: {msg_t}")
    if not ok_t:
        return False, "; ".join(notes)

    # Cover (bytes, path, or keep whatever is already on the file)
    data: bytes | None = None
    mime = cover_mime or "image/jpeg"
    if cover_bytes:
        data = cover_bytes
    elif cover_path:
        cp = Path(cover_path)
        if not cp.is_file():
            return False, f"Cover image not found: {cp}"
        try:
            data = cp.read_bytes()
            mime = mime_from_path(cp)
        except Exception as exc:
            return False, f"Could not read cover: {exc}"
    else:
        # Re-embed existing cover so Save-as / re-save keeps art
        existing = extract_cover_bytes(src if target.resolve() != src.resolve() else target)
        if existing:
            data = existing
            if data[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"

    if data and len(data) >= 32:
        ok_c, msg_c = embed_cover_bytes(target, data, mime)
        notes.append(msg_c if ok_c else f"cover failed: {msg_c}")
        if not ok_c:
            return False, "; ".join(notes)
        try:
            _save_sidecar_cover(target, data, mime)
        except Exception:
            pass

    return True, "; ".join(notes)


def write_tags(path: Path | str, tags: dict[str, str]) -> tuple[bool, str]:
    """Write common tags. Returns (ok, message)."""
    p = Path(path)
    if not p.is_file():
        return False, "File not found"
    try:
        from mutagen import File as MutagenFile
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, ID3NoHeaderError

        ext = p.suffix.lower()
        # Ensure ID3 on MP3
        if ext == ".mp3":
            try:
                audio = EasyID3(str(p))
            except Exception:
                try:
                    ID3(str(p))
                except ID3NoHeaderError:
                    tags_id3 = ID3()
                    tags_id3.save(str(p))
                audio = EasyID3(str(p))
        elif ext == ".wav":
            # WAVE + ID3 chunk (mutagen)
            try:
                from mutagen.wave import WAVE

                audio = WAVE(str(p))
                if audio.tags is None:
                    audio.add_tags()
                # Prefer easy-style mapping via ID3 frames where possible
                from mutagen.id3 import TIT2, TPE1, TALB, TCON, TDRC, TBPM, COMM, TPE2, TRCK, TPOS

                frame_map = {
                    "title": lambda v: TIT2(encoding=3, text=v),
                    "artist": lambda v: TPE1(encoding=3, text=v),
                    "album": lambda v: TALB(encoding=3, text=v),
                    "genre": lambda v: TCON(encoding=3, text=v),
                    "date": lambda v: TDRC(encoding=3, text=v),
                    "bpm": lambda v: TBPM(encoding=3, text=v),
                    "comment": lambda v: COMM(encoding=3, lang="eng", desc="", text=v),
                    "albumartist": lambda v: TPE2(encoding=3, text=v),
                    "tracknumber": lambda v: TRCK(encoding=3, text=v),
                    "discnumber": lambda v: TPOS(encoding=3, text=v),
                }
                for key, value in tags.items():
                    key = str(key).strip().lower()
                    text = (value or "").strip()
                    if key not in frame_map:
                        continue
                    if not text:
                        continue
                    try:
                        audio.tags.add(frame_map[key](text))
                    except Exception:
                        pass
                audio.save()
                return True, "Metadata saved"
            except Exception as wav_exc:
                return False, f"WAV tags: {wav_exc}"
        else:
            audio = MutagenFile(str(p), easy=True)
            if audio is None:
                return False, f"Unsupported format for tags: {ext or 'unknown'}"
            if audio.tags is None:
                try:
                    audio.add_tags()
                except Exception:
                    pass

        for key, value in tags.items():
            key = str(key).strip().lower()
            if key not in (
                "artist", "album", "title", "genre", "date", "bpm", "key",
                "comment", "albumartist", "tracknumber", "discnumber",
            ):
                continue
            text = (value or "").strip()
            if not text:
                try:
                    if key in audio:
                        del audio[key]
                except Exception:
                    pass
                continue
            try:
                audio[key] = text
            except Exception:
                # Some keys not valid for container - skip
                continue
        audio.save()
        return True, "Metadata saved"
    except Exception as exc:
        return False, str(exc)


def copy_metadata_and_cover(
    source: Path | str,
    dest: Path | str,
    *,
    extra_tags: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """
    Copy tags + cover art from source audio to dest (e.g. after FFmpeg repair).

    FFmpeg re-encodes strip ID3/APIC; this restores them onto the repaired file
    (and writes a sidecar cover when the container cannot hold art).
    """
    src = Path(source)
    dst = Path(dest)
    if not src.is_file() or not dst.is_file():
        return False, "Source or destination missing"

    notes: list[str] = []
    # Merge file tags with any in-memory UI overrides
    tags = read_tags(src)
    if extra_tags:
        for k, v in extra_tags.items():
            if v is not None and str(v).strip():
                tags[str(k).strip().lower()] = str(v).strip()

    if tags:
        ok, msg = write_tags(dst, tags)
        notes.append(msg if ok else f"tags: {msg}")
    else:
        notes.append("no tags on source")

    # Cover: embedded or sidecar on source → dest embed + sidecar
    cover = extract_cover_bytes(src)
    if cover:
        # Detect mime roughly
        mime = "image/jpeg"
        if cover[:8] == b"\x89PNG\r\n\x1a\n":
            mime = "image/png"
        ok_c, msg_c = embed_cover_bytes(dst, cover, mime)
        notes.append(msg_c if ok_c else f"cover: {msg_c}")
        # Always keep a sidecar next to repaired file for UI reliability
        try:
            _save_sidecar_cover(dst, cover, mime)
        except Exception:
            pass
    else:
        # Copy existing sidecar if present
        side = _sidecar_cover_path(src)
        if side is not None:
            try:
                dest_side = dst.with_name(f"{dst.stem}.cover{side.suffix.lower()}")
                dest_side.write_bytes(side.read_bytes())
                notes.append(f"sidecar cover → {dest_side.name}")
                # try embed too
                embed_cover_bytes(dst, dest_side.read_bytes(), mime_from_path(dest_side))
            except Exception as exc:
                notes.append(f"sidecar copy failed: {exc}")
        else:
            notes.append("no cover on source")

    return True, "; ".join(notes)


def _sidecar_cover_path(p: Path) -> Path | None:
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        cand = p.with_name(f"{p.stem}.cover{ext}")
        if cand.is_file():
            return cand
    # also accept common folder.jpg next to file
    for name in ("cover.jpg", "cover.png", "folder.jpg", "Folder.jpg"):
        cand = p.parent / name
        if cand.is_file():
            return cand
    return None


def extract_cover_bytes(path: Path | str) -> bytes | None:
    """Return raw image bytes of the first embedded cover, or sidecar image."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(str(p))
        if audio is not None:
            # MP3 / ID3 / WAV-with-ID3
            try:
                from mutagen.id3 import ID3

                if hasattr(audio, "tags") and audio.tags is not None:
                    for key in list(getattr(audio.tags, "keys", lambda: [])()):
                        if str(key).startswith("APIC"):
                            apic = audio.tags[key]
                            data = getattr(apic, "data", None)
                            if data:
                                return bytes(data)
                try:
                    id3 = ID3(str(p))
                    for key in id3.keys():
                        if str(key).startswith("APIC"):
                            data = id3[key].data
                            if data:
                                return bytes(data)
                except Exception:
                    pass
            except Exception:
                pass

            # FLAC pictures
            pics = getattr(audio, "pictures", None)
            if pics:
                for pic in pics:
                    data = getattr(pic, "data", None)
                    if data:
                        return bytes(data)

            # MP4 / M4A
            tags = getattr(audio, "tags", None)
            if tags is not None and "covr" in tags:
                covr = tags["covr"]
                if covr:
                    item = covr[0]
                    data = bytes(item) if not isinstance(item, (bytes, bytearray)) else bytes(item)
                    if data:
                        return data
    except Exception:
        pass

    # Sidecar fallback (WAV repairs, OGG, or failed embeds)
    side = _sidecar_cover_path(p)
    if side is not None:
        try:
            return side.read_bytes()
        except Exception:
            pass
    return None


def save_cover_image(path: Path | str, image_path: Path | str) -> tuple[bool, str]:
    """Embed cover art from an image file into the audio file."""
    p = Path(path)
    img = Path(image_path)
    if not p.is_file():
        return False, "Audio file not found"
    if not img.is_file():
        return False, "Image file not found"
    try:
        data = img.read_bytes()
    except Exception as exc:
        return False, f"Could not read image: {exc}"
    if len(data) < 32:
        return False, "Image file is empty"
    return embed_cover_bytes(p, data, mime_from_path(img))


def mime_from_path(image_path: Path) -> str:
    ext = image_path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        return "image/jpeg"
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext == ".gif":
        return "image/gif"
    return "image/jpeg"


def _apic_frame(data: bytes, mime: str):
    from mutagen.id3 import APIC

    return APIC(encoding=3, mime=mime or "image/jpeg", type=3, desc="Cover", data=data)


def _save_sidecar_cover(p: Path, data: bytes, mime: str) -> Path:
    """Write stem.cover.jpg/png next to the audio file (fallback for any format)."""
    ext = ".png" if "png" in (mime or "") else ".jpg"
    side = p.with_name(f"{p.stem}.cover{ext}")
    side.write_bytes(data)
    return side


def embed_cover_bytes(
    path: Path | str,
    data: bytes,
    mime: str = "image/jpeg",
) -> tuple[bool, str]:
    """Embed cover image bytes into audio container (with sidecar fallback)."""
    p = Path(path)
    if not p.is_file():
        return False, "File not found"
    ext = p.suffix.lower()
    mime = mime or "image/jpeg"
    # Prefer JPEG for widest container support when mime is webp/gif
    if mime not in ("image/jpeg", "image/png"):
        mime = "image/jpeg"

    def _ok_msg(where: str) -> tuple[bool, str]:
        return True, f"Cover art saved ({where})"

    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                tags = ID3(str(p))
            except ID3NoHeaderError:
                tags = ID3()
            for key in list(tags.keys()):
                if str(key).startswith("APIC"):
                    del tags[key]
            tags.add(_apic_frame(data, mime))
            tags.save(str(p), v2_version=3)
            return _ok_msg("MP3")

        if ext == ".flac":
            from mutagen.flac import FLAC, Picture

            audio = FLAC(str(p))
            audio.clear_pictures()
            pic = Picture()
            pic.type = 3
            pic.mime = mime
            pic.desc = "Cover"
            pic.data = data
            audio.add_picture(pic)
            audio.save()
            return _ok_msg("FLAC")

        if ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(str(p))
            fmt = MP4Cover.FORMAT_JPEG
            if "png" in mime:
                fmt = MP4Cover.FORMAT_PNG
            if audio.tags is None:
                audio.add_tags()
            audio["covr"] = [MP4Cover(data, imageformat=fmt)]
            audio.save()
            return _ok_msg("M4A/MP4")

        if ext in (".aiff", ".aif"):
            from mutagen.aiff import AIFF

            audio = AIFF(str(p))
            if audio.tags is None:
                audio.add_tags()
            for key in list(audio.tags.keys()):
                if str(key).startswith("APIC"):
                    del audio.tags[key]
            audio.tags.add(_apic_frame(data, mime))
            audio.save()
            return _ok_msg("AIFF")

        # WAV: embed ID3 chunk when mutagen supports it
        if ext == ".wav":
            try:
                from mutagen.wave import WAVE

                audio = WAVE(str(p))
                if audio.tags is None:
                    audio.add_tags()
                for key in list(audio.tags.keys()):
                    if str(key).startswith("APIC"):
                        del audio.tags[key]
                audio.tags.add(_apic_frame(data, mime))
                audio.save()
                return _ok_msg("WAV")
            except Exception as wav_exc:
                # Sidecar so the UI still shows the image next to repaired WAVs
                side = _save_sidecar_cover(p, data, mime)
                return True, (
                    f"WAV container rejected embedded art ({wav_exc}). "
                    f"Saved cover beside file: {side.name}"
                )

        # OGG Vorbis - sidecar is most reliable
        if ext in (".ogg", ".oga", ".opus"):
            side = _save_sidecar_cover(p, data, mime)
            return True, f"Saved cover beside file: {side.name} (OGG uses sidecar covers)"

        # Unknown: always offer sidecar so + never dead-ends
        side = _save_sidecar_cover(p, data, mime)
        return True, (
            f"Embedded art not available for {ext or 'this format'}. "
            f"Saved cover beside file: {side.name}"
        )
    except Exception as exc:
        # Last resort: never fail hard if we can write a sidecar
        try:
            side = _save_sidecar_cover(p, data, mime)
            return True, f"Embed failed ({exc}). Saved cover beside file: {side.name}"
        except Exception as exc2:
            return False, f"{exc} / sidecar failed: {exc2}"


def remove_cover(path: Path | str) -> tuple[bool, str]:
    """Strip embedded cover art when possible."""
    p = Path(path)
    if not p.is_file():
        return False, "File not found"
    ext = p.suffix.lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError

            try:
                tags = ID3(str(p))
            except ID3NoHeaderError:
                return True, "No cover present"
            for key in list(tags.keys()):
                if str(key).startswith("APIC"):
                    del tags[key]
            tags.save(str(p))
            return True, "Cover removed"
        if ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(str(p))
            audio.clear_pictures()
            audio.save()
            return True, "Cover removed"
        if ext in (".m4a", ".mp4", ".aac"):
            from mutagen.mp4 import MP4

            audio = MP4(str(p))
            if audio.tags and "covr" in audio.tags:
                del audio.tags["covr"]
                audio.save()
            return True, "Cover removed"
        return False, "Remove cover not supported for this format"
    except Exception as exc:
        return False, str(exc)
