"""YouTube Music catalog: search, browse, and resolve a request into a download plan.

Everything that leaves this module has already been filtered: explicit items
are dropped here, on the server, so the page never shows them and a crafted
request for one is refused by `resolve`.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field


class Blocked(Exception):
    """The request names something the page must not add (explicit, too big, gone)."""


VARIOUS = "Various Artists"


def thumb(thumbs, size: int = 544) -> str:
    """Largest thumbnail, re-sized. YT Music art URLs carry their size as
    `=w226-h226-...`; asking for a bigger square is just a different suffix."""
    if not thumbs:
        return ""
    url = max(thumbs, key=lambda t: t.get("width") or 0).get("url", "")
    return re.sub(r"=w\d+-h\d+", f"=w{size}-h{size}", url)


def names(artists) -> str:
    return ", ".join(a["name"] for a in artists or [] if a.get("name"))


@dataclass
class Track:
    video_id: str
    title: str
    artist: str
    number: int


@dataclass
class Plan:
    """Everything the worker needs, resolved once when the request is made."""

    kind: str  # "album" or "song"
    album: str
    album_artist: str
    year: str
    cover_url: str
    total: int
    tracks: list[Track] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(**{**d, "tracks": [Track(**t) for t in d["tracks"]]})


class Catalog:
    def __init__(self, yt=None, max_tracks: int = 60):
        if yt is None:
            from ytmusicapi import YTMusic

            yt = YTMusic(language="en")
        self.yt = yt
        self.max_tracks = max_tracks

    # ---- browsing ---------------------------------------------------------

    def suggest(self, q: str) -> list[str]:
        out = self.yt.get_search_suggestions(q) or []
        return [s if isinstance(s, str) else s.get("text", "") for s in out][:8]

    def search(self, q: str) -> dict:
        with ThreadPoolExecutor(3) as pool:
            songs, albums, artists = pool.map(
                lambda f: self.yt.search(q, filter=f) or [], ["songs", "albums", "artists"]
            )
        songs = self._cleaned(songs, self._clean_song)
        albums = self._cleaned(albums, self._clean_album)
        return {
            "songs": [s for s in map(self._song, songs) if s][:20],
            "albums": [a for a in map(self._album, albums) if a][:20],
            "artists": [a for a in map(self._artist, artists) if a][:10],
        }

    # ---- clean versions ---------------------------------------------------
    # YouTube Music ranks the explicit version of a song first and often
    # offers a clean one further down (Olivia Rodrigo's "pretty isn't pretty",
    # 2026-11-08: the explicit one was hidden and the clean one never shown).
    # Explicit items are swapped for their clean version where one exists,
    # and dropped otherwise -- never shown.

    def _cleaned(self, items: list, find, limit: int = 4) -> list:
        explicit = [i for i in items if i.get("isExplicit")][:limit]
        if not explicit:
            return items
        with ThreadPoolExecutor(len(explicit)) as pool:
            found = dict(zip(map(id, explicit), pool.map(self._safe(find), explicit)))
        out, seen = [], set()
        for i in items:
            i = found.get(id(i), i) if i.get("isExplicit") else i
            key = i.get("videoId") or i.get("browseId") if i else None
            if i is not None and key not in seen:
                seen.add(key)
                out.append(i)
        return out

    @staticmethod
    def _safe(fn):
        def run(x):
            try:
                return fn(x)
            except Exception:
                return None
        return run

    def _clean_song(self, s: dict) -> dict | None:
        from .jellyfin import artist_keys, norm

        artist = names(s.get("artists"))
        want = artist_keys(*artist.split(","))
        for r in self.yt.search(f"{s.get('title', '')} {artist} clean", filter="songs") or []:
            if (not r.get("isExplicit") and r.get("videoId") and norm(r.get("title")) == norm(s.get("title"))
                    and (not want or want & artist_keys(*names(r.get("artists")).split(",")))):
                return {**r, "clean": True}
        return None

    def _clean_album(self, a: dict) -> dict | None:
        if not a.get("browseId"):
            return None
        full = self.yt.get_album(a["browseId"])
        for v in full.get("other_versions") or []:
            if not v.get("isExplicit") and v.get("browseId"):
                return {**v, "year": v.get("year") or a.get("year"), "clean": True,
                        "artists": v.get("artists") or a.get("artists")}
        return None

    def artist(self, browse_id: str) -> dict:
        a = self.yt.get_artist(browse_id)
        out = {"id": browse_id, "title": a.get("name", ""), "thumb": thumb(a.get("thumbnails"))}
        for key in ("albums", "singles"):
            section = a.get(key) or {}
            results = section.get("results") or []
            if section.get("browseId") and section.get("params"):
                try:  # the artist page shows ~10; this lists them all
                    results = self.yt.get_artist_albums(section["browseId"], section["params"]) or results
                except Exception:
                    pass
            results = self._cleaned([{**r, "artists": r.get("artists") or [{"name": out["title"]}]} for r in results],
                                    self._clean_album, limit=8)
            items = []
            for r in results:
                r = {**r, "artists": r.get("artists") or [{"name": out["title"]}]}
                if (item := self._album(r)) is not None:
                    items.append(item)
            out[key] = items
        return out

    def album(self, browse_id: str) -> dict:
        a = self.yt.get_album(browse_id)
        # The album flag alone isn't enough: the explicit GUTS reports
        # isExplicit False on its own page while 7 of its 12 songs are
        # explicit. Any explicit song + a clean edition -> show the edition.
        if a.get("isExplicit") or any(t.get("isExplicit") for t in a.get("tracks") or []):
            clean = next((v for v in a.get("other_versions") or [] if not v.get("isExplicit") and v.get("browseId")), None)
            if clean is not None:
                browse_id, a = clean["browseId"], self.yt.get_album(clean["browseId"])
        if a.get("isExplicit"):
            raise Blocked("This album is marked explicit.")
        tracks = [
            {
                "id": t["videoId"],
                "title": t.get("title", ""),
                "artists": names(t.get("artists")),
                "number": t.get("trackNumber") or i + 1,
                "duration": t.get("duration") or "",
            }
            for i, t in enumerate(a.get("tracks") or [])
            if self._playable(t)
        ]
        return {
            "type": "album",
            "id": browse_id,
            "title": a.get("title", ""),
            "artists": names(a.get("artists")) or VARIOUS,
            "year": a.get("year") or "",
            "thumb": thumb(a.get("thumbnails")),
            "tracks": tracks,
        }

    # ---- resolving a request ---------------------------------------------

    def resolve(self, kind: str, ref_id: str, album_id: str | None = None) -> Plan:
        if kind == "album":
            return self._album_plan(ref_id)
        if kind == "song":
            return self._song_plan(ref_id, album_id)
        raise Blocked(f"Unknown kind {kind!r}.")

    def _album_plan(self, browse_id: str) -> Plan:
        a = self.yt.get_album(browse_id)
        if a.get("isExplicit"):
            raise Blocked("This album is marked explicit.")
        tracks = [
            Track(t["videoId"], t.get("title", ""), names(t.get("artists")), t.get("trackNumber") or i + 1)
            for i, t in enumerate(a.get("tracks") or [])
            if self._playable(t)
        ]
        if not tracks:
            raise Blocked("None of this album's songs can be added.")
        if len(tracks) > self.max_tracks:
            raise Blocked(f"This album has {len(tracks)} songs; the limit is {self.max_tracks}.")
        return Plan(
            kind="album",
            album=a.get("title", ""),
            album_artist=names(a.get("artists")) or VARIOUS,
            year=str(a.get("year") or ""),
            cover_url=thumb(a.get("thumbnails"), 1200),
            total=a.get("trackCount") or len(a.get("tracks") or []),
            tracks=tracks,
        )

    def _song_plan(self, video_id: str, album_id: str | None) -> Plan:
        watch = (self.yt.get_watch_playlist(videoId=video_id, limit=1) or {}).get("tracks") or []
        w = next((t for t in watch if t.get("videoId") == video_id), None)
        if w is None:
            raise Blocked("That song could not be found.")
        if w.get("isExplicit"):
            raise Blocked("This song is marked explicit.")
        album_id = album_id or ((w.get("album") or {}).get("id"))
        if album_id:
            a = self.yt.get_album(album_id)
            # A song search can return a different videoId for the same
            # recording than the album lists, so fall back to the title.
            match = next((t for t in a.get("tracks") or [] if t.get("videoId") == video_id), None) or next(
                (t for t in a.get("tracks") or [] if (t.get("title") or "").casefold() == w["title"].casefold()),
                None,
            )
            if match is not None:
                if a.get("isExplicit") and match.get("isExplicit") is not False:
                    raise Blocked("This song is marked explicit.")
                if match.get("isExplicit"):
                    raise Blocked("This song is marked explicit.")
                number = match.get("trackNumber") or (a["tracks"].index(match) + 1)
                return Plan(
                    kind="song",
                    album=a.get("title", ""),
                    album_artist=names(a.get("artists")) or VARIOUS,
                    year=str(a.get("year") or ""),
                    cover_url=thumb(a.get("thumbnails"), 1200),
                    total=a.get("trackCount") or len(a.get("tracks") or []),
                    tracks=[Track(video_id, match.get("title") or w["title"], names(match.get("artists")) or names(w.get("artists")), number)],
                )
        # No album to file it under: it becomes a one-song album of its own.
        artist = names(w.get("artists")) or "Unknown Artist"
        return Plan(
            kind="song",
            album=w["title"],
            album_artist=artist,
            year=str(w.get("year") or ""),
            cover_url=thumb(w.get("thumbnail") or w.get("thumbnails"), 1200),
            total=1,
            tracks=[Track(video_id, w["title"], artist, 1)],
        )

    # ---- normalising ------------------------------------------------------

    @staticmethod
    def _playable(t) -> bool:
        return bool(t.get("videoId")) and t.get("isAvailable", True) and not t.get("isExplicit")

    @staticmethod
    def _song(s) -> dict | None:
        if s.get("isExplicit") or not s.get("videoId"):
            return None
        album = s.get("album") or {}
        return {
            "type": "song",
            "id": s["videoId"],
            "title": s.get("title", ""),
            "artists": names(s.get("artists")),
            "album": {"id": album.get("id"), "name": album.get("name")} if album else None,
            "duration": s.get("duration") or "",
            "thumb": thumb(s.get("thumbnails"), 226),
            "clean": bool(s.get("clean")),
        }

    @staticmethod
    def _album(a) -> dict | None:
        if a.get("isExplicit") or not a.get("browseId"):
            return None
        return {
            "type": "album",
            "id": a["browseId"],
            "title": a.get("title", ""),
            "artists": names(a.get("artists")),
            "year": a.get("year") or "",
            "kind": a.get("type") or "Album",
            "thumb": thumb(a.get("thumbnails"), 226),
            "clean": bool(a.get("clean")),
        }

    @staticmethod
    def _artist(a) -> dict | None:
        if not a.get("browseId"):
            return None
        return {
            "type": "artist",
            "id": a["browseId"],
            "title": a.get("artist") or names(a.get("artists")),
            "thumb": thumb(a.get("thumbnails"), 226),
        }
