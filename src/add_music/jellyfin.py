"""Jellyfin: what a person can already see, scans, covers, and library setup.

Each person gets their own music library ("Ellie's Music") on top of the
shared Music library. Jellyfin hides a whole library from a user reliably,
which is what keeps one kid's additions out of another's view; per-item
hiding (tags) leaks through artist pages and is wiped by a metadata refresh.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

import httpx

VARIOUS = "variousartists"


def norm(s: str | None) -> str:
    """Comparison form of a title or name: case, spacing and punctuation dropped."""
    return re.sub(r"[\W_]+", "", (s or "").casefold())


# Bracketed labels that name an edition of the same recording, not a
# different one: "Hamilton (Original Broadway Cast Recording)" is the
# "Hamilton" already in the library; "Hamilton (Instrumentals)" is not.
_EDITION = re.compile(
    r"\s*[(\[][^)\]]*\b(original|cast|recording|soundtrack|deluxe|edition|expanded|remaster(ed)?|"
    r"anniversary|bonus|feat\.?|featuring|with)\b[^)\]]*[)\]]",
    re.I,
)


def base_title(s: str | None) -> str:
    """Comparison form with edition labels dropped."""
    return norm(_EDITION.sub("", s or ""))


def artist_keys(*names: str | None) -> set[str]:
    out = set()
    for n in names:
        for part in re.split(r",|&| and | x | feat\.? ", n or "", flags=re.I):
            if k := norm(part):
                out.add(k)
    return out


@dataclass
class LibraryIndex:
    """One person's view of Jellyfin music: their own library plus the shared one."""

    albums: list[dict] = field(default_factory=list)
    _by_title: dict[str, list[tuple[set[str], dict]]] = field(default_factory=dict)
    _tracks: dict[tuple[str, str], list[tuple[set[str], str]]] = field(default_factory=dict)

    def add_album(self, entry: dict, artists: set[str]) -> None:
        self.albums.append(entry)
        for key in {norm(entry["title"]), base_title(entry["title"])}:
            self._by_title.setdefault(key, []).append((artists, entry))

    def add_track(self, album: str, title: str, source: str, artists: set[str] | None = None) -> None:
        if source == "other":
            return
        for key in {(norm(album), norm(title)), (base_title(album), base_title(title))}:
            self._tracks.setdefault(key, []).append((artists or set(), source))

    def album_source(self, title: str, artists: str) -> str | None:
        """'mine' or 'shared' when an album with this title by one of these
        artists (or a Various Artists album) is already visible."""
        wanted = artist_keys(artists)
        found = None
        candidates = self._by_title.get(norm(title), []) + self._by_title.get(base_title(title), [])
        for keys, entry in candidates:
            if entry["source"] == "other":
                continue
            if not wanted or keys & wanted or VARIOUS in keys:
                if entry["source"] == "mine":
                    return "mine"
                found = entry["source"]
        return found

    def track_source(self, title: str, album: str | None, artists: str = "") -> str | None:
        """'mine' or 'shared' when this song from this album, by one of these
        artists, is already visible. Titles alone are not enough: "Intro" on
        two different artists' "Live" albums are different recordings."""
        if not album:
            return None
        wanted = artist_keys(*artists.split(",")) if artists else set()
        found = None
        for key in ((norm(album), norm(title)), (base_title(album), base_title(title))):
            for keys, source in self._tracks.get(key, []):
                if not wanted or not keys or keys & wanted or VARIOUS in keys:
                    if source == "mine":
                        return "mine"
                    found = source
        return found


class Jellyfin:
    def __init__(self, url: str, api_key: str, people_path: str = "/media/music-people", ttl: float = 20):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.people_path = people_path.rstrip("/") + "/"
        self.ttl = ttl
        self._cache: dict[str, tuple[float, LibraryIndex]] = {}
        self._users: dict[str, str] | None = None
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def _get(self, path: str, **params):
        r = httpx.get(f"{self.url}{path}", params=params, headers={"X-Emby-Token": self.api_key}, timeout=20)
        r.raise_for_status()
        return r

    def _post(self, path: str, json=None, **params):
        r = httpx.post(f"{self.url}{path}", params=params, json=json, headers={"X-Emby-Token": self.api_key}, timeout=30)
        r.raise_for_status()
        return r

    # ---- scans --------------------------------------------------------------

    def refresh(self) -> None:
        if not self.enabled:
            raise RuntimeError("JELLYFIN_API_KEY is not set")
        self._post("/Library/Refresh")
        self.forget()

    def forget(self) -> None:
        with self._lock:
            self._cache.clear()

    # ---- what a person can see ----------------------------------------------

    def user_id(self, username: str) -> str | None:
        if self._users is None or username.casefold() not in self._users:
            self._users = {u["Name"].casefold(): u["Id"] for u in self._get("/Users").json()}
        return self._users.get(username.casefold())

    def index(self, username: str, folder: str) -> LibraryIndex:
        """Albums and tracks this Jellyfin user can see, cached briefly. `folder`
        is the person's own folder under the people path: albums there are
        "mine"; the shared library is "shared"; another person's library (a
        parent who has been given access to look) is "other" and blocks nothing."""
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(username)
            if hit and now - hit[0] < self.ttl:
                return hit[1]
        uid = self.user_id(username)
        if uid is None:
            raise RuntimeError(f"no Jellyfin user named {username!r}")
        idx = LibraryIndex()
        albums = self._get("/Items", userId=uid, IncludeItemTypes="MusicAlbum", Recursive="true",
                           Fields="Path", SortBy="SortName").json()["Items"]
        for a in albums:
            source = self._source(a.get("Path"), folder)
            idx.add_album(
                {"id": a["Id"], "title": a.get("Name", ""), "artist": a.get("AlbumArtist") or ", ".join(a.get("Artists") or []),
                 "year": a.get("ProductionYear") or "", "source": source, "has_cover": "Primary" in (a.get("ImageTags") or {})},
                artist_keys(a.get("AlbumArtist"), *(a.get("Artists") or []),
                            *((x.get("Name") for x in a.get("AlbumArtists") or []))),
            )
        tracks = self._get("/Items", userId=uid, IncludeItemTypes="Audio", Recursive="true", Fields="Path").json()["Items"]
        for t in tracks:
            idx.add_track(t.get("Album") or "", t.get("Name") or "", self._source(t.get("Path"), folder),
                          artist_keys(t.get("AlbumArtist"), *(t.get("Artists") or [])))
        with self._lock:
            self._cache[username] = (now, idx)
        return idx

    def _source(self, path: str | None, folder: str) -> str:
        path = path or ""
        if path.startswith(f"{self.people_path}{folder}/"):
            return "mine"
        return "other" if path.startswith(self.people_path) else "shared"

    def cover(self, item_id: str) -> tuple[bytes, str]:
        r = self._get(f"/Items/{item_id}/Images/Primary", fillWidth=300, fillHeight=300, quality=85)
        return r.content, r.headers.get("content-type", "image/jpeg")
