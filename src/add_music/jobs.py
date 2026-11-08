"""The request queue: a SQLite table and one worker thread.

One worker, on purpose: downloads go one track at a time, which is gentle on
YouTube and on the home connection, and two jobs can never race to create the
same album folder.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import library
from .catalog import Plan

log = logging.getLogger("add_music.jobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  who TEXT NOT NULL,
  kind TEXT NOT NULL,
  ref_id TEXT NOT NULL,
  title TEXT NOT NULL,
  artist TEXT NOT NULL,
  thumb TEXT NOT NULL,
  plan TEXT NOT NULL,
  status TEXT NOT NULL,          -- queued | working | done | failed
  detail TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL,
  updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_who ON jobs (who, status);
"""

PUBLIC = ("id", "who", "kind", "ref_id", "title", "artist", "thumb", "status", "detail", "created", "updated")
ACTIVE = ("queued", "working")


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def add(self, who: str, kind: str, ref_id: str, title: str, artist: str, thumb: str, plan: Plan) -> dict:
        now = time.time()
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO jobs (who,kind,ref_id,title,artist,thumb,plan,status,created,updated)"
                " VALUES (?,?,?,?,?,?,?,'queued',?,?)",
                (who, kind, ref_id, title, artist, thumb, json.dumps(plan.to_dict()), now, now),
            )
            return _public(db.execute("SELECT * FROM jobs WHERE id=?", (cur.lastrowid,)).fetchone())

    def set(self, job_id: int, status: str, detail: str = "") -> None:
        with self._db() as db:
            db.execute("UPDATE jobs SET status=?, detail=?, updated=? WHERE id=?", (status, detail, time.time(), job_id))

    def recent(self, who: str | None = None, limit: int = 30) -> list[dict]:
        with self._db() as db:
            if who:
                rows = db.execute("SELECT * FROM jobs WHERE who=? ORDER BY id DESC LIMIT ?", (who, limit))
            else:
                rows = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
            return [_public(r) for r in rows]

    def queue(self, finished_within: float = 6 * 3600, limit: int = 40) -> list[dict]:
        """Everyone's waiting and running jobs in run order, then recently
        finished ones, newest first."""
        with self._db() as db:
            active = db.execute("SELECT * FROM jobs WHERE status IN ('queued','working') ORDER BY id").fetchall()
            done = db.execute(
                "SELECT * FROM jobs WHERE status IN ('done','failed') AND updated > ? ORDER BY updated DESC LIMIT ?",
                (time.time() - finished_within, limit),
            ).fetchall()
            return [_public(r) for r in active + done]

    def active(self, who: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM jobs WHERE who=? AND status IN ('queued','working') ORDER BY id", (who,))
            return [{**_public(r), "plan": json.loads(r["plan"])} for r in rows]

    def failed_since(self, who: str, ref_id: str, since: float) -> dict | None:
        with self._db() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE who=? AND ref_id=? AND status='failed' AND updated > ? ORDER BY id DESC LIMIT 1",
                (who, ref_id, since),
            ).fetchone()
            return _public(row) if row else None

    def plan_of(self, job_id: int) -> Plan:
        with self._db() as db:
            return Plan.from_dict(json.loads(db.execute("SELECT plan FROM jobs WHERE id=?", (job_id,)).fetchone()[0]))

    def next_queued(self) -> sqlite3.Row | None:
        with self._db() as db:
            return db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()

    def requeue_interrupted(self) -> int:
        """A job left 'working' was cut off by a restart; run it again."""
        with self._db() as db:
            return db.execute("UPDATE jobs SET status='queued', detail='' WHERE status='working'").rowcount


def _public(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in PUBLIC}


@dataclass
class Worker:
    store: Store
    people_dir: Path          # each person's music goes in people_dir/<Name>/
    shared_dir: Path          # the shared library: only checked, never written
    download: Callable[[str, Path, str], Path]
    fetch: Callable[[str], bytes]
    refresh: Callable[[], None]

    def __post_init__(self):
        self._wake = threading.Event()

    @property
    def staging_dir(self) -> Path:
        # Inside the people mount: a rename between two bind mounts fails
        # (EXDEV) even on one disk, and the rename is what makes a new album
        # appear all at once. The dot keeps it out of every library.
        return self.people_dir / ".add-music-staging"

    def poke(self) -> None:
        self._wake.set()

    def start(self) -> threading.Thread:
        self.store.requeue_interrupted()
        t = threading.Thread(target=self._loop, name="add-music-worker", daemon=True)
        t.start()
        return t

    def _loop(self) -> None:
        while True:
            if not self.run_once():
                self._wake.wait(timeout=30)
                self._wake.clear()

    def run_once(self) -> bool:
        """Run the oldest queued job. False when there was nothing to do."""
        row = self.store.next_queued()
        if row is None:
            return False
        job_id = row["id"]
        self.store.set(job_id, "working", "starting")
        try:
            detail = self.process(job_id, row["who"], Plan.from_dict(json.loads(row["plan"])))
            self.store.set(job_id, "done", detail)
        except Exception as e:  # one bad job must not stop the queue
            log.exception("job %s failed", job_id)
            self.store.set(job_id, "failed", str(e) or e.__class__.__name__)
        return True

    def check_storage(self) -> None:
        """The people folder sits on the same USB drive as the shared library,
        which has dropped before. The shared library is never empty when the
        drive is there, so it doubles as the 'is it mounted' check."""
        if (problem := library.library_ok(self.shared_dir, need_write=False)) is not None:
            raise RuntimeError(problem)
        if (problem := library.library_ok(self.people_dir, need_entries=False)) is not None:
            raise RuntimeError(problem)

    def process(self, job_id: int, who: str, plan: Plan) -> str:
        self.check_storage()
        target = library.album_path(self.people_dir / library.folder_name(who), plan.album_artist, plan.album)
        staged = self.staging_dir / f"job-{job_id}" / target.name
        if staged.parent.exists():
            shutil.rmtree(staged.parent)
        staged.mkdir(parents=True)
        try:
            cover = None
            if plan.cover_url:
                try:
                    cover = library.square_cover(self.fetch(plan.cover_url))
                except Exception:
                    log.warning("job %s: no cover from %s", job_id, plan.cover_url, exc_info=True)
            todo = [t for t in plan.tracks if not library.has_track(target, library.track_stem(t.number, t.title))]
            for i, t in enumerate(todo, 1):
                self.store.set(job_id, "working", f"downloading song {i} of {len(todo)}")
                path = self.download(t.video_id, staged, library.track_stem(t.number, t.title))
                library.tag(path, title=t.title, artist=t.artist or plan.album_artist,
                            album_artist=plan.album_artist, album=plan.album, number=t.number,
                            total=plan.total, year=plan.year, cover=cover)
            if cover:
                (staged / "cover.jpg").write_bytes(cover)
            added, skipped = library.install(staged, target)
        finally:
            shutil.rmtree(staged.parent, ignore_errors=True)
        try:
            self.refresh()
        except Exception as e:  # real-time monitoring will still find it
            log.warning("job %s: Jellyfin scan request failed: %s", job_id, e)
        already = len(plan.tracks) - len(todo) + skipped
        if added == 0:
            return "Already in your library"
        return f"Added {_songs(added)}" + (f", {already} already there" if already else "")


def _songs(n: int) -> str:
    return f"{n} song" + ("" if n == 1 else "s")
