"""add-music: a kid-friendly page for adding music to a Jellyfin library."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def build_app():
    from . import downloader
    from .catalog import Catalog
    from .jellyfin import Jellyfin
    from .jobs import Store, Worker
    from .people import parse_people
    from .web import create_app

    env = os.environ.get
    state = Path(env("ADD_MUSIC_STATE", "/state"))
    people = parse_people(env("ADD_MUSIC_PEOPLE", ""))
    if not people:
        raise SystemExit("ADD_MUSIC_PEOPLE is empty: list Name=jellyfin.user pairs, comma-separated")
    people_dir = Path(env("ADD_MUSIC_PEOPLE_DIR", "/people"))
    jellyfin = Jellyfin(env("JELLYFIN_URL", "http://jellyfin:8096"), env("JELLYFIN_API_KEY", ""),
                        env("JELLYFIN_PEOPLE_PATH", "/media/music-people"))
    if not jellyfin.enabled:
        logging.warning("JELLYFIN_API_KEY is not set: no library view, no 'already have it' checks")
    store = Store(state / "jobs.db")
    worker = Worker(
        store=store,
        people_dir=people_dir,
        shared_dir=Path(env("ADD_MUSIC_SHARED", "/music")),
        download=downloader.download,
        fetch=downloader.fetch,
        refresh=jellyfin.refresh,
    )
    worker.start()
    catalog = Catalog(max_tracks=int(env("MAX_ALBUM_TRACKS", "60")))
    return create_app(catalog, store, worker, people, jellyfin, people_dir,
                      cap=int(env("ADD_MUSIC_CAP", "3")), cooldown=float(env("ADD_MUSIC_COOLDOWN_MIN", "60")) * 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if sys.argv[1:2] == ["setup-jellyfin"]:
        from .setup import main as setup_main

        return setup_main(sys.argv[2:])
    import uvicorn

    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("ADD_MUSIC_PORT", "8000")),
                proxy_headers=True, access_log=False)
