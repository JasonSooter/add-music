import pytest

from add_music.catalog import VARIOUS, Blocked, Catalog, thumb


def test_thumb_picks_largest_and_resizes():
    t = [{"url": "https://x/a=w60-h60-l90-rj", "width": 60}, {"url": "https://x/b=w544-h544-l90-rj", "width": 544}]
    assert thumb(t, 1200) == "https://x/b=w1200-h1200-l90-rj"
    assert thumb(None) == ""


def test_search_normalises_and_hides_explicit(yt):
    yt.searches["songs"][0]["isExplicit"] = True
    yt.searches["albums"][0]["isExplicit"] = True
    r = Catalog(yt).search("rolf")
    song_ids = [s["id"] for s in r["songs"]]
    assert yt.searches["songs"][0]["videoId"] not in song_ids
    assert yt.searches["songs"][1]["videoId"] in song_ids
    assert yt.searches["albums"][0]["browseId"] not in [a["id"] for a in r["albums"]]
    s = r["songs"][0]
    assert set(s) == {"type", "id", "title", "artists", "album", "duration", "thumb"}
    assert s["album"]["id"].startswith("MPREb_")
    assert r["artists"][0]["id"].startswith("UC")


def test_album_view_drops_explicit_tracks(yt):
    yt.albums["MPREb_EGhFrDL8Sd9"]["tracks"][0]["isExplicit"] = True
    a = Catalog(yt).album("MPREb_EGhFrDL8Sd9")
    assert len(a["tracks"]) == 25
    assert a["artists"] == "Rolf Zuckowski"


def test_explicit_album_is_blocked(yt):
    yt.albums["MPREb_EGhFrDL8Sd9"]["isExplicit"] = True
    with pytest.raises(Blocked):
        Catalog(yt).album("MPREb_EGhFrDL8Sd9")
    with pytest.raises(Blocked):
        Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")


def test_album_plan(yt):
    p = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    assert (p.album, p.album_artist, p.year, p.total) == ("Die schönsten Kita-Hits von Rolf Zuckowski", "Rolf Zuckowski", "2025", 26)
    assert [t.number for t in p.tracks] == list(range(1, 27))
    assert p.cover_url.endswith("=w1200-h1200-l90-rj")


def test_album_over_the_limit_is_blocked(yt):
    with pytest.raises(Blocked, match="limit"):
        Catalog(yt, max_tracks=30).resolve("album", "MPREb_iNWH4dO6PQO")


def test_album_without_artists_is_various(yt):
    yt.albums["MPREb_EGhFrDL8Sd9"]["artists"] = []
    assert Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9").album_artist == VARIOUS


def test_song_plan_files_under_its_album(yt):
    track = yt.albums["MPREb_EGhFrDL8Sd9"]["tracks"][9]
    p = Catalog(yt).resolve("song", track["videoId"], "MPREb_EGhFrDL8Sd9")
    assert p.album == "Die schönsten Kita-Hits von Rolf Zuckowski"
    assert [(t.video_id, t.number) for t in p.tracks] == [(track["videoId"], 10)]


def test_song_plan_matches_by_title_when_ids_differ(yt):
    title = yt.albums["MPREb_EGhFrDL8Sd9"]["tracks"][4]["title"]
    yt.watch["tracks"][0]["title"] = title.upper()
    p = Catalog(yt).resolve("song", "someOtherId", "MPREb_EGhFrDL8Sd9")
    assert p.tracks[0].number == 5 and p.tracks[0].video_id == "someOtherId"


def test_explicit_song_is_blocked(yt):
    yt.watch["tracks"][0]["isExplicit"] = True
    with pytest.raises(Blocked):
        Catalog(yt).resolve("song", "abc", None)


def test_explicit_album_track_is_blocked(yt):
    track = yt.albums["MPREb_EGhFrDL8Sd9"]["tracks"][2]
    track["isExplicit"] = True
    with pytest.raises(Blocked):
        Catalog(yt).resolve("song", track["videoId"], "MPREb_EGhFrDL8Sd9")


def test_song_without_album_is_its_own_album(yt):
    yt.watch["tracks"][0]["album"] = None
    p = Catalog(yt).resolve("song", "abc", None)
    assert p.album == yt.watch["tracks"][0]["title"] and p.total == 1 and p.tracks[0].number == 1
