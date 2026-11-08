import copy
import json
import subprocess
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / f"{name}.json").read_text())


class FakeYT:
    """Answers like ytmusicapi.YTMusic, from responses recorded 2026-11-08."""

    def __init__(self):
        self.albums = {"MPREb_EGhFrDL8Sd9": load("album"), "MPREb_iNWH4dO6PQO": load("album_va")}
        self.watch = load("watch")
        self.searches = {"songs": load("songs"), "albums": load("albums"), "artists": load("artists")}

    def get_search_suggestions(self, q):
        return ["rolf zuckowski", "rolf zuckowski kinderlieder"]

    def search(self, q, filter=None):
        return copy.deepcopy(self.searches[filter])

    def get_album(self, browse_id):
        if browse_id not in self.albums:
            raise KeyError(browse_id)
        return copy.deepcopy(self.albums[browse_id])

    def get_artist(self, browse_id):
        return load("artist")

    def get_artist_albums(self, browse_id, params):
        raise RuntimeError("not recorded")

    def get_watch_playlist(self, videoId, limit=1):
        w = copy.deepcopy(self.watch)
        w["tracks"][0]["videoId"] = videoId
        return w


@pytest.fixture
def yt():
    return FakeYT()


def make_audio(path: Path, codec: str) -> Path:
    """A one-second real audio file, so mutagen tags a real container."""
    args = {"m4a": ["-c:a", "aac"], "opus": ["-c:a", "libopus"]}[codec]
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", *args, str(path)],
                   check=True)
    return path


def make_jpeg(w: int, h: int) -> bytes:
    import io
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 30)).save(out, "JPEG")
    return out.getvalue()
