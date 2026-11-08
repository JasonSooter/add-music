"""add-music: a kid-friendly page for adding music to a Jellyfin library."""

from __future__ import annotations

import logging
import os
from pathlib import Path


def build_app():
    from . import downloader, jellyfin
    from .catalog import Catalog
    from .jobs import Store, Worker
    from .web import create_app

    env = os.environ.get
    state = Path(env("ADD_MUSIC_STATE", "/state"))
    people = [p.strip() for p in env("ADD_MUSIC_PEOPLE", "").split(",") if p.strip()]
    if not people:
        raise SystemExit("ADD_MUSIC_PEOPLE is empty: list the names the page offers, comma-separated")
    store = Store(state / "jobs.db")
    library_dir = Path(env("ADD_MUSIC_LIBRARY", "/music"))
    worker = Worker(
        store=store,
        library_dir=library_dir,
        # Staged inside the library volume: a rename between two bind mounts
        # fails (EXDEV) even on one disk, and the rename is what makes a new
        # album appear all at once. Jellyfin ignores dot-folders.
        staging_dir=Path(env("ADD_MUSIC_STAGING", str(library_dir / ".add-music-staging"))),
        download=downloader.download,
        fetch=downloader.fetch,
        refresh=jellyfin.refresher(env("JELLYFIN_URL", "http://jellyfin:8096"), env("JELLYFIN_API_KEY", "")),
    )
    worker.start()
    catalog = Catalog(max_tracks=int(env("MAX_ALBUM_TRACKS", "60")))
    return create_app(catalog, store, worker, people)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("ADD_MUSIC_PORT", "8000")),
                proxy_headers=True, access_log=False)
