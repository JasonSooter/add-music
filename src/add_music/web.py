"""HTTP API and the page.

Served behind nginx at mediabox.local/add-music/, with the prefix stripped, so
every route here is relative and the page uses relative URLs only.

Every rule the page shows (already have it, on its way, too many waiting,
failed recently) is also enforced here: the page is a convenience, the API is
the gate.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from importlib.resources import files
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from . import library
from .catalog import Blocked, Catalog, Plan
from .jellyfin import Jellyfin, LibraryIndex, artist_keys, norm
from .jobs import Store, Worker
from .people import Person

log = logging.getLogger("add_music.web")


class AddRequest(BaseModel):
    kind: str = Field(pattern="^(album|song)$")
    id: str = Field(min_length=1, max_length=64)
    album_id: str | None = Field(default=None, max_length=64)
    who: str


def create_app(catalog: Catalog, store: Store, worker: Worker | None, people: dict[str, Person],
               jellyfin: Jellyfin | None, people_dir: Path, *, cap: int = 3, cooldown: float = 3600) -> FastAPI:
    app = FastAPI(title="add-music", docs_url=None, redoc_url=None, openapi_url=None)
    index_html = files("add_music").joinpath("static/index.html").read_text(encoding="utf-8")
    # Checking the rules and queueing the job happen as one step: FastAPI runs
    # these handlers on a thread pool, so two taps at once could otherwise both
    # pass the cap or duplicate check before either job exists. One process,
    # so a lock is enough.
    admit = threading.Lock()

    # ---- helpers ------------------------------------------------------------

    def upstream(fn, *args):
        try:
            return fn(*args)
        except Blocked as e:
            raise HTTPException(403, str(e))
        except Exception:
            log.exception("YouTube Music lookup failed")
            raise HTTPException(502, "YouTube Music did not answer. Try again in a moment.")

    def person(who: str | None) -> Person:
        if not who or who not in people:
            raise HTTPException(400, "Pick your name first.")
        return people[who]

    def index(p: Person) -> LibraryIndex:
        """What this person can already see in Jellyfin. Empty when Jellyfin
        can't be asked: the page then just shows no badges."""
        if jellyfin is None or not jellyfin.enabled:
            return LibraryIndex()
        try:
            return jellyfin.index(p.jellyfin_user, p.folder)
        except Exception as e:
            log.warning("Jellyfin library lookup for %s failed: %s", p.jellyfin_user, e)
            return LibraryIndex()

    def own_album(p: Person, plan: Plan) -> Path:
        return library.album_path(people_dir / p.folder, plan.album_artist, plan.album)

    def has_all(p: Person, plan: Plan) -> bool:
        target = own_album(p, plan)
        return all(library.has_track(target, library.track_stem(t.number, t.title)) for t in plan.tracks)

    def in_flight(p: Person) -> tuple[set[str], list[Plan]]:
        active = store.active(p.name)
        return {j["ref_id"] for j in active}, [Plan.from_dict(j["plan"]) for j in active]

    def cooling(p: Person, ref_id: str) -> dict | None:
        return store.failed_since(p.name, ref_id, time.time() - cooldown)

    def song_state(idx, refs, plans, s) -> str | None:
        album = (s.get("album") or {}).get("name")
        if src := idx.track_source(s["title"], album, s.get("artists") or ""):
            return src
        if s["id"] in refs or (s.get("album") or {}).get("id") in refs:
            return "queued"
        wanted = artist_keys(*(s.get("artists") or "").split(","))
        for pl in plans:
            if norm(pl.album) != norm(album):
                continue
            for t in pl.tracks:
                if norm(t.title) == norm(s["title"]) and (
                        not wanted or wanted & artist_keys(*t.artist.split(","), *pl.album_artist.split(","))):
                    return "queued"
        return None

    def album_state(idx, refs, a) -> str | None:
        if a["id"] in refs:
            return "queued"
        return idx.album_source(a["title"], a.get("artists") or "")

    def annotate(p: Person | None, data: dict) -> dict:
        if p is None:
            return data
        idx = index(p)
        refs, plans = in_flight(p)
        for s in data.get("songs", []):
            s["state"] = song_state(idx, refs, plans, s) or ("later" if cooling(p, s["id"]) else None)
        for key in ("albums", "singles"):
            for a in data.get(key, []):
                a["state"] = album_state(idx, refs, a) or ("later" if cooling(p, a["id"]) else None)
        if data.get("type") == "album":
            for t in data.get("tracks", []):
                song = {**t, "artists": f"{t.get('artists') or ''}, {data.get('artists') or ''}",
                        "album": {"id": data["id"], "name": data["title"]}}
                t["state"] = song_state(idx, refs, plans, song) or (
                    "later" if cooling(p, t["id"]) else None)
            state = album_state(idx, refs, data)
            # One added song makes Jellyfin list the album as "mine"; it is
            # only complete -- and "Add whole album" only pointless -- when
            # every song is there.
            if state == "mine" and any(t["state"] != "mine" for t in data.get("tracks", [])):
                state = None
            data["state"] = state or ("later" if cooling(p, data["id"]) else None)
        return data

    def gate(p: Person, req: AddRequest, plan: Plan) -> None:
        """Refuse what the page would have shown as unavailable."""
        refs, plans = in_flight(p)
        if req.id in refs or any(
            norm(pl.album) == norm(plan.album) and norm(pl.album_artist) == norm(plan.album_artist)
            and (pl.kind == "album" or plan.kind == "album" or {t.number for t in pl.tracks} & {t.number for t in plan.tracks})
            for pl in plans
        ):
            raise HTTPException(409, "That's already on its way!")
        if len(refs) >= cap:
            raise HTTPException(429, f"You already have {len(refs)} things on the way. Wait for one to finish, then add more.")
        if failed := cooling(p, req.id):
            wait = max(1, round((failed["updated"] + cooldown - time.time()) / 60))
            raise HTTPException(429, f"That didn't work a little while ago. Try again in {wait} minutes.")
        if has_all(p, plan):
            raise HTTPException(409, "That's already in your library!")
        idx = index(p)
        # Complete in Jellyfin even if not under this app's file names (an
        # edition with a different title, files filed by hand).
        if all(idx.track_source(t.title, plan.album, f"{t.artist}, {plan.album_artist}") == "mine" for t in plan.tracks):
            raise HTTPException(409, "That's already in your library!")
        if plan.kind == "album":
            src = idx.album_source(plan.album, plan.album_artist)
            if src == "shared":
                raise HTTPException(409, "That's already in Music for everyone!")
        else:
            src = idx.track_source(plan.tracks[0].title, plan.album, f"{plan.tracks[0].artist}, {plan.album_artist}")
            if src in ("mine", "shared"):
                raise HTTPException(409, "That's already in your library!" if src == "mine" else "That's already in Music for everyone!")

    # ---- routes -------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def page():
        return index_html

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/api/people")
    def get_people():
        return list(people)

    @app.get("/api/status")
    def status(who: str):
        p = person(who)
        return {"waiting": len(store.active(p.name)), "cap": cap, "jellyfin": bool(jellyfin and jellyfin.enabled)}

    @app.get("/api/suggest")
    def suggest(q: str = ""):
        return upstream(catalog.suggest, q.strip()) if len(q.strip()) >= 2 else []

    @app.get("/api/search")
    def search(q: str = "", who: str | None = None):
        if not q.strip():
            return {"songs": [], "albums": [], "artists": []}
        return annotate(people.get(who or ""), upstream(catalog.search, q.strip()))

    @app.get("/api/artist/{browse_id}")
    def artist(browse_id: str, who: str | None = None):
        return annotate(people.get(who or ""), upstream(catalog.artist, browse_id))

    @app.get("/api/album/{browse_id}")
    def album(browse_id: str, who: str | None = None):
        return annotate(people.get(who or ""), upstream(catalog.album, browse_id))

    @app.get("/api/library")
    def my_library(who: str):
        p = person(who)
        if jellyfin is None or not jellyfin.enabled:
            return {"connected": False, "mine": [], "shared": []}
        try:
            idx = jellyfin.index(p.jellyfin_user, p.folder)
        except Exception as e:
            log.warning("library for %s: %s", who, e)
            return {"connected": False, "mine": [], "shared": []}
        out = {"connected": True, "mine": [], "shared": []}
        for a in idx.albums:
            if a["source"] in out:
                out[a["source"]].append({**a, "thumb": f"api/cover/{a['id']}" if a["has_cover"] else ""})
        return out

    @app.get("/api/cover/{item_id}")
    def cover(item_id: str):
        if jellyfin is None or not jellyfin.enabled or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", item_id):
            raise HTTPException(404)
        try:
            data, ctype = jellyfin.cover(item_id)
        except Exception:
            raise HTTPException(404)
        return Response(data, media_type=ctype, headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/requests", status_code=201)
    def add(req: AddRequest):
        p = person(req.who)
        plan = upstream(catalog.resolve, req.kind, req.id, req.album_id)
        title = plan.album if req.kind == "album" else plan.tracks[0].title
        artist = plan.album_artist if req.kind == "album" else plan.tracks[0].artist
        cover_url = plan.cover_url.replace("=w1200-h1200", "=w226-h226")
        with admit:
            gate(p, req, plan)
            job = store.add(p.name, req.kind, req.id, title, artist, cover_url, plan)
        log.info("job %s: %s asked for %s %r by %r", job["id"], p.name, req.kind, title, artist)
        if worker:
            worker.poke()
        return job

    @app.get("/api/requests")
    def requests(who: str):
        """This person's requests. A finished one says 'in_jellyfin' only once
        Jellyfin itself lists it for them -- not merely because the files landed."""
        p = person(who)
        jobs = store.recent(p.name)
        idx = index(p) if any(j["status"] == "done" for j in jobs) else LibraryIndex()
        for j in jobs:
            j["in_jellyfin"] = False
            if j["status"] == "done":
                plan = store.plan_of(j["id"])
                j["in_jellyfin"] = (idx.album_source(plan.album, plan.album_artist) == "mine" if plan.kind == "album"
                                    else idx.track_source(plan.tracks[0].title, plan.album,
                                                          f"{plan.tracks[0].artist}, {plan.album_artist}") == "mine")
        return jobs

    @app.get("/api/queue")
    def queue():
        """The whole family's queue: who asked for what, and where it is."""
        return store.queue()

    return app
