"""Filing into the Jellyfin music library: names, tags, covers, and the move.

Library layout is the one the rest of the stack uses (home-media-server,
scripts/rip-postprocess/music_album.py): `<AlbumArtist>/<Album>/`, one folder
per album, because Jellyfin makes exactly one album per folder.
"""

from __future__ import annotations

import base64
import io
import os
import shutil
from pathlib import Path

AUDIO_EXTS = {".m4a", ".opus", ".ogg", ".mp3", ".flac"}


def folder_name(s: str) -> str:
    """Ported from music_album.folder_name: the abcde munging (":" -> " -",
    "/" -> "-", drop "?" and control characters), so a ripped CD and a
    downloaded album of the same name land in the same folder."""
    s = "".join(c for c in s.replace(":", " -").replace("/", "-") if c != "?" and c.isprintable()).strip()
    return "" if s in (".", "..") else s


def track_stem(number: int, title: str) -> str:
    return f"{number:02d} - {folder_name(title) or 'Track'}"


def album_path(library: Path, album_artist: str, album: str) -> Path:
    artist_dir, album_dir = folder_name(album_artist), folder_name(album)
    if not artist_dir or not album_dir:
        raise ValueError(f"cannot name a folder for {album_artist!r} / {album!r}")
    return library / artist_dir / album_dir


def has_track(album_dir: Path, stem: str) -> bool:
    """True when the album folder already holds this track in any format."""
    if not album_dir.is_dir():
        return False
    return any(p.stem == stem and p.suffix.lower() in AUDIO_EXTS for p in album_dir.iterdir())


def square_cover(data: bytes, max_size: int = 1200) -> bytes:
    """Centre-crop to a square and re-encode as JPEG.

    YT Music album art is already square; a song without an album falls back
    to its video thumbnail, which is 16:9 with the art in the middle -- the
    same bars that made the YouTube-Music zip imports look wrong in Jellyfin's
    track lists. (music_album.square_cover also trims a white margin, which is
    for photos of CD cases; real art can legitimately be white-edged, so that
    step is not ported.)"""
    from PIL import Image

    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    s = min(w, h)
    im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s))
    if s > max_size:
        im = im.resize((max_size, max_size), Image.LANCZOS)
    out = io.BytesIO()
    im.save(out, "JPEG", quality=92)
    return out.getvalue()


def tag(path: Path, *, title: str, artist: str, album_artist: str, album: str,
        number: int, total: int, year: str, cover: bytes | None) -> None:
    """Write the tags Jellyfin reads, with the cover embedded."""
    ext = path.suffix.lower()
    if ext == ".m4a":
        from mutagen.mp4 import MP4, MP4Cover

        m = MP4(path)
        m.delete()
        m = MP4(path)
        m["\xa9nam"], m["\xa9ART"], m["aART"], m["\xa9alb"] = [title], [artist], [album_artist], [album]
        m["trkn"] = [(number, total)]
        if year:
            m["\xa9day"] = [year]
        if cover:
            m["covr"] = [MP4Cover(cover, imageformat=MP4Cover.FORMAT_JPEG)]
        m.save()
    elif ext in (".opus", ".ogg"):
        from mutagen import File
        from mutagen.flac import Picture

        m = File(path)
        m.delete()
        m["title"], m["artist"], m["albumartist"], m["album"] = title, artist, album_artist, album
        m["tracknumber"], m["tracktotal"] = str(number), str(total)
        if year:
            m["date"] = year
        if cover:
            from PIL import Image

            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", cover
            pic.width, pic.height = Image.open(io.BytesIO(cover)).size
            pic.depth = 24
            m["metadata_block_picture"] = base64.b64encode(pic.write()).decode("ascii")
        m.save()
    else:
        raise ValueError(f"don't know how to tag {path.name}")


def library_ok(path: Path, *, need_write: bool = True, need_entries: bool = True) -> str | None:
    """None if the folder looks mounted (and writable, if asked), else why not.

    The music lives on a USB drive that has dropped before: either unmounted
    (the folder is then empty) or stale-mounted (listing it raises EIO).
    Writing into an unmounted folder would hide the files under the mount
    when the drive comes back, so refuse rather than write."""
    try:
        if not path.is_dir():
            return f"{path} is missing -- is the media drive mounted?"
        # Dot-entries don't count: staging lives there, and it would make an
        # unmounted, otherwise-empty folder look populated.
        if need_entries and not any(not p.name.startswith(".") for p in path.iterdir()):
            return f"{path} is empty -- is the media drive mounted?"
        if need_write and not os.access(path, os.W_OK):
            return f"{path} is not writable"
    except OSError as e:
        return f"{path} is unreadable ({e.strerror}) -- the media drive may have dropped"
    return None


def install(staged: Path, target: Path) -> tuple[int, int]:
    """Move a staged album folder into the library. Returns (added, skipped).

    A new album moves as one rename, so Jellyfin never sees half of it. An
    album that already exists only gains the tracks it is missing; nothing in
    the library is ever overwritten."""
    files = [p for p in staged.iterdir() if p.is_file()]
    tracks = [p for p in files if p.suffix.lower() in AUDIO_EXTS]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _move(staged, target)
        return len(tracks), 0
    added = skipped = 0
    for p in files:
        dest = target / p.name
        is_track = p.suffix.lower() in AUDIO_EXTS
        if dest.exists() or (is_track and has_track(target, p.stem)):
            skipped += is_track
            continue
        _move(p, dest)
        added += is_track
    return added, skipped


def _move(src: Path, dest: Path) -> None:
    try:
        os.rename(src, dest)
    except OSError:  # staging on another filesystem: not atomic, but correct
        shutil.move(str(src), str(dest))
