"""yt-dlp: fetch one track's audio without re-encoding it."""

from __future__ import annotations

from pathlib import Path


def download(video_id: str, dest_dir: Path, stem: str) -> Path:
    """Download the best audio stream to `dest_dir/<stem>.<ext>`.

    AAC in M4A is preferred (every Jellyfin client plays it); otherwise the
    Opus stream is pulled out of its WebM container. ExtractAudio with codec
    "best" copies the stream rather than transcoding it.

    YouTube's player JS has to be solved to get stream URLs; that needs a JS
    runtime (deno, on PATH in the image) and the yt-dlp-ejs solver, which the
    `yt-dlp[default]` extra installs."""
    from yt_dlp import YoutubeDL

    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio",
        "outtmpl": str(dest_dir / f"{stem}.%(ext)s"),
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "best"}],
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
