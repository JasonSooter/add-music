"""yt-dlp: fetch one track's audio as MP3."""

from __future__ import annotations

from pathlib import Path


OPTIONS = {
    "format": "bestaudio",
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}],
}


def download(video_id: str, dest_dir: Path, stem: str) -> Path:
    """Download the audio to `dest_dir/<stem>.mp3` (LAME VBR V0, ~245 kbps).

    MP3 because it is the one format that played on every device we have.
    Jellify (the kids' player on their Boox readers) direct-plays only
    mp3/flac/wav/ogg, and on a Boox Go Color 7 neither Jellyfin's on-the-fly
    transcode of M4A nor direct-played Ogg/Opus made any sound, while the
    shared library's MP3s played (2026-11-08). The cost is one re-encode of
    YouTube's stream, inaudible at V0.

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
