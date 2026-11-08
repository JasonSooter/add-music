import time

import pytest
from fastapi.testclient import TestClient

from add_music.catalog import Catalog
from add_music.jobs import Store
from add_music.library import track_stem
from add_music.people import parse_people
from add_music.web import create_app
from conftest import jellyfin_with

ALBUM_ID = "MPREb_EGhFrDL8Sd9"   # Die schönsten Kita-Hits von Rolf Zuckowski
KITA = "Die schönsten Kita-Hits von Rolf Zuckowski"


@pytest.fixture
def env(tmp_path, yt):
    store = Store(tmp_path / "jobs.db")
    jf = jellyfin_with()
    people_dir = tmp_path / "people"
    app = create_app(Catalog(yt), store, None, parse_people("Ellie=ellie.sooter,Mac=mac.sooter"), jf, people_dir, cap=2)
    return TestClient(app), store, jf, people_dir


def add(client, who="Ellie", kind="album", id=ALBUM_ID, album_id=None):
    return client.post("/api/requests", json={"kind": kind, "id": id, "album_id": album_id, "who": who})


def test_page_and_health(env):
    client = env[0]
    assert "Add Music" in client.get("/").text
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/api/people").json() == ["Ellie", "Mac"]


def test_page_uses_only_relative_urls(env):
    html = env[0].get("/").text
    assert 'api("/' not in html and "fetch(\"/" not in html and "`/api" not in html


def test_search_and_suggest(env):
    client = env[0]
    r = client.get("/api/search", params={"q": "rolf", "who": "Ellie"}).json()
    assert r["songs"] and r["albums"] and r["artists"]
    assert all(s["state"] is None for s in r["songs"])
    assert client.get("/api/suggest", params={"q": "r"}).json() == []


def test_add_and_list(env):
    client = env[0]
    r = add(client)
    assert r.status_code == 201, r.text
    assert r.json()["title"] == KITA and "plan" not in r.json()
    assert [j["id"] for j in client.get("/api/requests", params={"who": "Ellie"}).json()] == [r.json()["id"]]
    assert client.get("/api/requests", params={"who": "Mac"}).json() == []


def test_unknown_person_rejected(env):
    assert add(env[0], who="Mallory").status_code == 400


def test_crafted_explicit_request_rejected(env, yt):
    yt.albums[ALBUM_ID]["isExplicit"] = True
    assert add(env[0]).status_code == 403


def test_same_thing_twice_is_on_its_way(env):
    client = env[0]
    assert add(client).status_code == 201
    r = add(client)
    assert r.status_code == 409 and "on its way" in r.json()["detail"]
    # ...and a song from that album too
    song = env[1].plan_of(1).tracks[3].video_id
    assert add(client, kind="song", id=song, album_id=ALBUM_ID).status_code == 409
    # Another kid can still ask for it: it goes into their own library.
    assert add(client, who="Mac").status_code == 201


def test_search_marks_queued(env, yt):
    client = env[0]
    add(client)
    a = client.get(f"/api/album/{ALBUM_ID}", params={"who": "Ellie"}).json()
    assert a["state"] == "queued" and all(t["state"] == "queued" for t in a["tracks"])
    assert client.get(f"/api/album/{ALBUM_ID}", params={"who": "Mac"}).json()["state"] is None


def test_cap_per_person(env, yt):
    client = env[0]
    songs = [t["videoId"] for t in yt.albums["MPREb_iNWH4dO6PQO"]["tracks"] if not t["isExplicit"]][:3]
    assert add(client, kind="song", id=songs[0], album_id="MPREb_iNWH4dO6PQO").status_code == 201
    assert add(client, kind="song", id=songs[1], album_id="MPREb_iNWH4dO6PQO").status_code == 201
    r = add(client, kind="song", id=songs[2], album_id="MPREb_iNWH4dO6PQO")
    assert r.status_code == 429 and "on the way" in r.json()["detail"]
    assert client.get("/api/status", params={"who": "Ellie"}).json()["waiting"] == 2
    assert add(client, who="Mac").status_code == 201        # the cap is per person


def test_failed_request_cools_down(env):
    client, store = env[0], env[1]
    job = add(client).json()
    store.set(job["id"], "failed", "Video unavailable")
    r = add(client)
    assert r.status_code == 429 and "minutes" in r.json()["detail"]
    assert client.get(f"/api/album/{ALBUM_ID}", params={"who": "Ellie"}).json()["state"] == "later"
    with store._db() as db:   # an hour later it may be tried again
        db.execute("UPDATE jobs SET updated=?", (time.time() - 3601,))
    assert add(client).status_code == 201


def test_already_on_disk_is_refused(env, yt):
    client, _, _, people_dir = env
    album = people_dir / "Ellie" / "Rolf Zuckowski" / KITA
    album.mkdir(parents=True)
    for i, t in enumerate(yt.albums[ALBUM_ID]["tracks"], 1):
        (album / f"{track_stem(i, t['title'])}.m4a").write_bytes(b"x")
    r = add(client)
    assert r.status_code == 409 and "your library" in r.json()["detail"]


def test_already_in_shared_library_is_refused(env):
    client, _, jf, _ = env
    fake = jellyfin_with(albums=[{"Id": "s1", "Name": KITA, "AlbumArtist": "Rolf Zuckowski", "Path": "/media/music/x"}])
    jf._get = fake._get
    jf.forget()
    r = add(client)
    assert r.status_code == 409 and "everyone" in r.json()["detail"]
    hit = [a for a in client.get("/api/search", params={"q": "rolf", "who": "Ellie"}).json()["albums"] if a["id"] == ALBUM_ID]
    assert all(a["state"] == "shared" for a in hit)


def test_library_view_and_in_jellyfin(env):
    client, store, jf, _ = env
    job = add(client).json()
    store.set(job["id"], "done", "Added 26 songs")
    assert client.get("/api/requests", params={"who": "Ellie"}).json()[0]["in_jellyfin"] is False
    fake = jellyfin_with(albums=[
        {"Id": "0123456789abcdef0123456789abcdef", "Name": KITA, "AlbumArtist": "Rolf Zuckowski",
         "Path": "/media/music-people/Ellie/Rolf Zuckowski/x", "ImageTags": {"Primary": "t"}},
        {"Id": "s1", "Name": "Hamilton", "AlbumArtist": "Various Artists", "Path": "/media/music/x"},
    ])
    jf._get = fake._get
    jf.forget()
    assert client.get("/api/requests", params={"who": "Ellie"}).json()[0]["in_jellyfin"] is True
    lib = client.get("/api/library", params={"who": "Ellie"}).json()
    assert [a["title"] for a in lib["mine"]] == [KITA] and [a["title"] for a in lib["shared"]] == ["Hamilton"]
    assert lib["mine"][0]["thumb"] == "api/cover/0123456789abcdef0123456789abcdef"
    assert client.get("/api/cover/0123456789abcdef0123456789abcdef").status_code == 200
    assert client.get("/api/cover/../../etc").status_code == 404


def test_queue_shows_everyone(env):
    client, store = env[0], env[1]
    add(client)
    add(client, who="Mac")
    assert [(j["who"], j["status"]) for j in client.get("/api/queue").json()] == [("Ellie", "queued"), ("Mac", "queued")]


def test_works_without_jellyfin(tmp_path, yt):
    app = create_app(Catalog(yt), Store(tmp_path / "j.db"), None, parse_people("Ellie"), None, tmp_path)
    client = TestClient(app)
    assert client.get("/api/library", params={"who": "Ellie"}).json()["connected"] is False
    assert add(client).status_code == 201


def test_simultaneous_requests_cannot_both_pass(env):
    """Two taps at once: the second must see the first job, not race past it."""
    import threading
    client, store = env[0], env[1]
    real_add = store.add

    def slow_add(*a, **k):
        time.sleep(0.3)          # widen the window between the check and the insert
        return real_add(*a, **k)

    store.add = slow_add
    codes = []
    threads = [threading.Thread(target=lambda: codes.append(add(client).status_code)) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert sorted(codes) == [201, 409]



def test_queued_song_needs_same_artist(env, yt):
    client = env[0]
    add(client)
    plan = env[1].plan_of(1)
    t = plan.tracks[0]
    album = {"name": plan.album, "id": "MPREb_somethingElse"}
    yt.searches["songs"] = [
        {"videoId": "same1", "title": t.title, "artists": [{"name": t.artist}], "album": album, "isExplicit": False},
        {"videoId": "diff1", "title": t.title, "artists": [{"name": "Somebody Else"}], "album": album, "isExplicit": False},
    ]
    songs = client.get("/api/search", params={"q": "x", "who": "Ellie"}).json()["songs"]
    assert [(s["id"], s["state"]) for s in songs] == [("same1", "queued"), ("diff1", None)]
