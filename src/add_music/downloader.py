"""yt-dlp: fetch one track's audio without re-encoding it."""

from __future__ import annotations

from pathlib import Path


OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio",
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "opus"}],
}


def download(video_id: str, dest_dir: Path, stem: str) -> Path:
    """Download the audio to `dest_dir/<stem>.opus`.

    Opus in an Ogg container, because that is what Jellify (the kids' player
    on their Boox readers) plays directly: its device profile lists only mp3,
    flac, wav and ogg. Anything else -- the M4A/AAC this used to keep -- makes
    Jellyfin transcode on the fly, and that path played silently on a Boox Go
    Color 7 (2026-11-08). YouTube's best audio stream is Opus anyway, so
    ExtractAudio only copies it out of its WebM container; a video without an
    Opus stream is the rare case that gets encoded.

    YouTube's player JS has to be solved to get stream URLs; that needs a JS
    runtime (deno, on PATH in the image) and the yt-dlp-ejs solver, which the
    `yt-dlp[default]` extra installs."""
    from yt_dlp import YoutubeDL

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": OPTIONS["format"],
        "outtmpl": str(dest_dir / f"{stem}.%(ext)s"),
        "postprocessors": OPTIONS["postprocessors"],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "overwrites": True,
        "retries": 5,
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://music.youtube.com/watch?v={video_id}", download=True)
    path = Path(info["requested_downloads"][0]["filepath"])
    if not path.exists():
        raise RuntimeError(f"yt-dlp reported {path} but it is not there")
    return path


def fetch(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.content
