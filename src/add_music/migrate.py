"""`add-music migrate-mp3 [--apply]`: convert earlier downloads to MP3.

add-music saved M4A until 2026-11-08 and then, briefly, Opus. On a Boox Go
Color 7, Jellify played neither (the M4A through Jellyfin's transcode, the
Opus directly) -- only MP3, which is what add-music saves now. This converts
every .m4a/.opus/.ogg in the people folders to MP3 (LAME VBR V0) in place,
carrying tags and cover over, and removes the original only once the MP3 is
in its folder. It converts the file on disk; nothing is downloaded again.

Without --apply it only lists what it would do. Safe to re-run: an MP3 twin
already in place means a previous run got that far, so it just finishes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from . import library

SOURCES = {".m4a", ".opus", ".ogg"}


def targets(people_dir: Path) -> list[Path]:
    return sorted(
        f for f in people_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in SOURCES
        and not any(part.startswith(".") for part in f.relative_to(people_dir).parts)
    )


def encode_mp3(src: Path, dest: Path) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-map", "0:a:0",
                    "-c:a", "libmp3lame", "-q:a", "0", str(dest)], check=True)


def convert(src: Path, staging: Path, encode: Callable[[Path, Path], None] = encode_mp3) -> Path:
    done = src.with_suffix(".mp3")
    # Only add-music writes the people folders and it never keeps two formats
    # of one track, so an MP3 twin is this migration's own: finish the job.
    if done.exists():
        src.unlink()
        return done
    tags = library.read_tags(src)
    work = staging / f"mp3-{hashlib.sha1(str(src).encode()).hexdigest()[:12]}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        out = work / f"{src.stem}.mp3"
        encode(src, out)
        library.tag(out, **tags)
        os.rename(out, done)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    src.unlink()
    return done


def main(argv: list[str]) -> None:
    from .jellyfin import Jellyfin

    ap = argparse.ArgumentParser(prog="add-music migrate-mp3", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="convert (default: only list)")
    args = ap.parse_args(argv)
    env = os.environ.get
    people_dir = Path(env("ADD_MUSIC_PEOPLE_DIR", "/people"))
    files = targets(people_dir)
    if not args.apply:
        for f in files:
            print(f"would convert {f.relative_to(people_dir)}")
        print(f"\nDry run: {len(files)} to convert. Re-run with --apply.")
        return
    staging = people_dir / ".add-music-staging"
    ok = failed = 0
    for f in files:
        try:
            convert(f, staging)
            ok += 1
        except Exception as e:  # keep going; the original is still there
            failed += 1
            print(f"FAILED {f.relative_to(people_dir)}: {e}", flush=True)
    try:
        Jellyfin(env("JELLYFIN_URL", "http://jellyfin:8096"), env("JELLYFIN_API_KEY", "")).refresh()
    except Exception as e:
        print(f"Jellyfin scan request failed ({e}); real-time monitoring will still pick it up")
    print(f"Done: {ok} converted, {failed} failed.")
