"""`add-music migrate-opus [--apply]`: replace M4A downloads with Opus.

Until 2026-11-08 add-music kept YouTube's AAC stream in M4A. Jellify on the
kids' Boox readers can't play M4A directly, and Jellyfin's on-the-fly
transcode to Opus played silently there. This re-downloads each M4A that
add-music made as Opus (a stream copy -- YouTube's own Opus), carries the
M4A's tags and cover over, and only then removes the M4A.

The YouTube id of each file comes from the job that downloaded it, so an M4A
add-music did not make (or whose job is gone) is reported and left alone.
Without --apply it only lists what it would do. Safe to re-run.
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
from pathlib import Path
from typing import Callable

from . import library
from .jobs import Store

log = logging.getLogger("add_music.migrate")


def plan(store: Store, people_dir: Path) -> tuple[list[tuple[Path, str]], list[Path]]:
    """(m4a files with their YouTube id, m4a files with no known id)."""
    ids: dict[Path, str] = {}
    for job in store.done_jobs():
        p = store.plan_of(job["id"])
        target = library.album_path(people_dir / library.folder_name(job["who"]), p.album_artist, p.album)
        for t in p.tracks:
            # The FIRST completed job naming a file is the one that downloaded
            # it; later jobs skip tracks that already exist (jobs.py), so their
            # ids prove nothing about the file on disk.
            ids.setdefault(target / f"{library.track_stem(t.number, t.title)}.m4a", t.video_id)
    known, unknown = [], []
    for f in sorted(people_dir.rglob("*.m4a")):
        if any(part.startswith(".") for part in f.relative_to(people_dir).parts):
            continue
        (known.append((f, ids[f])) if f in ids else unknown.append(f))
    return known, unknown


def _tags(m4a: Path) -> dict:
    from mutagen.mp4 import MP4

    m = MP4(m4a)
    first = lambda k: (m.get(k) or [""])[0]
    number, total = (m.get("trkn") or [(1, 1)])[0]
    return {"title": first("\xa9nam"), "artist": first("\xa9ART"), "album_artist": first("aART"),
            "album": first("\xa9alb"), "number": number or 1, "total": total or number or 1,
            "year": first("\xa9day"), "cover": bytes(m["covr"][0]) if m.get("covr") else None}


def convert(m4a: Path, video_id: str, staging: Path, download: Callable[[str, Path, str], Path]) -> Path:
    """Download the Opus version beside nothing, tag it like the M4A, move it
    into the M4A's folder, then remove the M4A."""
    # A run that stopped after moving the Opus file in but before removing
    # the M4A leaves both. Only this app writes the people folders, and it
    # never keeps two formats of one track, so the Opus twin is ours: finish
    # by removing the M4A instead of downloading again.
    done = m4a.with_suffix(".opus")
    if done.exists():
        m4a.unlink()
        return done
    tags = _tags(m4a)
    work = staging / f"migrate-{video_id}"
    shutil.rmtree(work, ignore_errors=True)
    try:
        new = download(video_id, work, m4a.stem)
        library.tag(new, **tags)
        dest = m4a.with_suffix(new.suffix)
        if dest.exists():
            raise FileExistsError(dest)
        os.rename(new, dest)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    m4a.unlink()
    return dest


def main(argv: list[str]) -> None:
    from . import downloader
    from .jellyfin import Jellyfin

    ap = argparse.ArgumentParser(prog="add-music migrate-opus", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="convert (default: only list)")
    args = ap.parse_args(argv)
    env = os.environ.get
    people_dir = Path(env("ADD_MUSIC_PEOPLE_DIR", "/people"))
    store = Store(Path(env("ADD_MUSIC_STATE", "/state")) / "jobs.db")
    known, unknown = plan(store, people_dir)
    for f, vid in known:
        print(f"{'CONVERT' if args.apply else 'would convert'} {f.relative_to(people_dir)}  (youtube {vid})")
    for f in unknown:
        print(f"SKIP (no job knows its YouTube id) {f.relative_to(people_dir)}")
    if not args.apply:
        print(f"\nDry run: {len(known)} to convert, {len(unknown)} skipped. Re-run with --apply.")
        return
    staging = people_dir / ".add-music-staging"
    done = failed = 0
    for f, vid in known:
        try:
            convert(f, vid, staging, downloader.download)
            done += 1
        except Exception as e:  # keep going; the M4A is still there
            failed += 1
            print(f"FAILED {f.relative_to(people_dir)}: {e}")
    try:
        Jellyfin(env("JELLYFIN_URL", "http://jellyfin:8096"), env("JELLYFIN_API_KEY", "")).refresh()
    except Exception as e:
        print(f"Jellyfin scan request failed ({e}); real-time monitoring will still pick it up")
    print(f"Done: {done} converted, {failed} failed, {len(unknown)} skipped.")
