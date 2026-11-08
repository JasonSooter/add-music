"""HTTP API and the page.

Served behind nginx at mediabox.local/add-music/, with the prefix stripped, so
every route here is relative and the page uses relative URLs only.
"""

from __future__ import annotations

import logging
from importlib.resources import files

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .catalog import Blocked, Catalog
from .jobs import Store, Worker

log = logging.getLogger("add_music.web")


class AddRequest(BaseModel):
    kind: str = Field(pattern="^(album|song)$")
    id: str = Field(min_length=1, max_length=64)
    album_id: str | None = Field(default=None, max_length=64)
    who: str


def create_app(catalog: Catalog, store: Store, worker: Worker | None, people: list[str]) -> FastAPI:
    app = FastAPI(title="add-music", docs_url=None, redoc_url=None, openapi_url=None)
    index = files("add_music").joinpath("static/index.html").read_text(encoding="utf-8")

    def upstream(fn, *args):
        try:
            return fn(*args)
        except Blocked as e:
            raise HTTPException(403, str(e))
        except Exception:
            log.exception("YouTube Music lookup failed")
            raise HTTPException(502, "YouTube Music did not answer. Try again in a moment.")

    @app.get("/", response_class=HTMLResponse)
    def page():
        return index

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/people")
    def get_people():
        return people

    @app.get("/api/suggest")
    def suggest(q: str = ""):
        return upstream(catalog.suggest, q.strip()) if len(q.strip()) >= 2 else []

    @app.get("/api/search")
    def search(q: str = ""):
        if not q.strip():
            return {"songs": [], "albums": [], "artists": []}
        return upstream(catalog.search, q.strip())

    @app.get("/api/artist/{browse_id}")
    def artist(browse_id: str):
        return upstream(catalog.artist, browse_id)

    @app.get("/api/album/{browse_id}")
    def album(browse_id: str):
        return upstream(catalog.album, browse_id)

    @app.post("/api/requests", status_code=201)
    def add(req: AddRequest):
        if req.who not in people:
            raise HTTPException(400, "Pick your name first.")
        plan = upstream(catalog.resolve, req.kind, req.id, req.album_id)
        title = plan.album if req.kind == "album" else plan.tracks[0].title
        artist = plan.album_artist if req.kind == "album" else plan.tracks[0].artist
        cover = plan.cover_url.replace("=w1200-h1200", "=w226-h226")
        job = store.add(req.who, req.kind, req.id, title, artist, cover, plan)
        log.info("job %s: %s asked for %s %r by %r", job["id"], req.who, req.kind, title, artist)
        if worker:
            worker.poke()
        return job

    @app.get("/api/requests")
    def requests(who: str | None = None):
        return store.recent(who)

    return app
