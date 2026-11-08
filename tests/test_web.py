import pytest
from fastapi.testclient import TestClient

from add_music.catalog import Catalog
from add_music.jobs import Store
from add_music.web import create_app


@pytest.fixture
def client(tmp_path, yt):
    app = create_app(Catalog(yt), Store(tmp_path / "jobs.db"), None, ["Ellie", "Mac"])
    return TestClient(app)


def test_page_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "Add Music" in r.text
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/api/people").json() == ["Ellie", "Mac"]


def test_page_uses_only_relative_urls(client):
    # Served under /add-music/: an absolute "/api" would hit the landing page.
    html = client.get("/").text
    assert 'api("/' not in html and "fetch(\"/" not in html and "`/api" not in html


def test_search_and_suggest(client):
    r = client.get("/api/search", params={"q": "rolf"}).json()
    assert r["songs"] and r["albums"] and r["artists"]
    assert client.get("/api/suggest", params={"q": "r"}).json() == []
    assert client.get("/api/suggest", params={"q": "rolf"}).json()[0] == "rolf zuckowski"


def test_add_and_list(client):
    r = client.post("/api/requests", json={"kind": "album", "id": "MPREb_EGhFrDL8Sd9", "who": "Ellie"})
    assert r.status_code == 201, r.text
    j = r.json()
    assert j["status"] == "queued" and j["title"] == "Die schönsten Kita-Hits von Rolf Zuckowski"
    assert "plan" not in j
    assert [x["id"] for x in client.get("/api/requests", params={"who": "Ellie"}).json()] == [j["id"]]
    assert client.get("/api/requests", params={"who": "Mac"}).json() == []


def test_unknown_person_rejected(client):
    r = client.post("/api/requests", json={"kind": "album", "id": "MPREb_EGhFrDL8Sd9", "who": "Mallory"})
    assert r.status_code == 400


def test_crafted_explicit_request_rejected(client, yt):
    yt.albums["MPREb_EGhFrDL8Sd9"]["isExplicit"] = True
    r = client.post("/api/requests", json={"kind": "album", "id": "MPREb_EGhFrDL8Sd9", "who": "Ellie"})
    assert r.status_code == 403
    assert client.get("/api/album/MPREb_EGhFrDL8Sd9").status_code == 403


def test_upstream_failure_is_502(client):
    assert client.get("/api/album/nope").status_code == 502


def test_bad_kind_rejected(client):
    r = client.post("/api/requests", json={"kind": "playlist", "id": "x", "who": "Ellie"})
    assert r.status_code == 422
