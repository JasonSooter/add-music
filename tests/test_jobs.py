import shutil

from mutagen import File

from add_music.catalog import Catalog
from add_music.jobs import Store, Worker
from conftest import make_audio, make_jpeg


def make_worker(tmp_path, refresh=lambda: None):
    shared = tmp_path / "music"
    (shared / "Existing Artist").mkdir(parents=True)
    people = tmp_path / "people"
    people.mkdir()
    calls = []
    source = make_audio(tmp_path / "src.m4a", "m4a")

    def download(video_id, dest, stem):
        calls.append(video_id)
        dest.mkdir(parents=True, exist_ok=True)
        return shutil.copy(source, dest / f"{stem}.m4a") and (dest / f"{stem}.m4a")

    store = Store(tmp_path / "state" / "jobs.db")
    w = Worker(store, people, shared, download, lambda url: make_jpeg(1280, 720), refresh)
    return w, store, people / "Ellie", calls


def test_album_job_end_to_end(tmp_path, yt):
    refreshed = []
    w, store, lib, calls = make_worker(tmp_path, lambda: refreshed.append(1))
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    plan.tracks = plan.tracks[:3]
    job = store.add("Ellie", "album", "MPREb_EGhFrDL8Sd9", plan.album, plan.album_artist, "", plan)
    assert w.run_once() is True
    [j] = store.recent("Ellie")
    assert j["status"] == "done", j["detail"]
    assert j["detail"] == "Added 3 songs"
    album = lib / "Rolf Zuckowski" / "Die schönsten Kita-Hits von Rolf Zuckowski"
    files = sorted(p.name for p in album.iterdir())
    assert files[0].startswith("01 - ") and files[-1] == "cover.jpg" and len(files) == 4
    m = File(album / files[0])
    assert m["aART"] == ["Rolf Zuckowski"] and m["trkn"] == [(1, 26)]
    assert refreshed == [1]
    assert list((lib.parent / ".add-music-staging").iterdir()) == []
    assert w.run_once() is False


def test_second_request_skips_existing_tracks(tmp_path, yt):
    w, store, lib, calls = make_worker(tmp_path)
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    plan.tracks = plan.tracks[:2]
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    w.run_once()
    plan.tracks = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9").tracks[:3]
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    w.run_once()
    assert len(calls) == 3
    assert store.recent("Ellie")[0]["detail"] == "Added 1 song, 2 already there"


def test_each_person_gets_their_own_copy(tmp_path, yt):
    w, store, lib, calls = make_worker(tmp_path)
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    plan.tracks = plan.tracks[:1]
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    store.add("Mac", "album", "x", "t", "a", "", plan)
    w.run_once(); w.run_once()
    for who in ("Ellie", "Mac"):
        assert (lib.parent / who / "Rolf Zuckowski" / "Die schönsten Kita-Hits von Rolf Zuckowski").is_dir()
    assert len(calls) == 2


def test_unmounted_library_fails_the_job_without_writing(tmp_path, yt):
    w, store, lib, calls = make_worker(tmp_path)
    shutil.rmtree(tmp_path / "music" / "Existing Artist")
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    w.run_once()
    [j] = store.recent()
    assert j["status"] == "failed" and "mounted" in j["detail"]
    assert calls == []


def test_download_failure_fails_job_and_cleans_staging(tmp_path, yt):
    w, store, lib, _ = make_worker(tmp_path)
    def boom(*a):
        raise RuntimeError("Video unavailable")
    w.download = boom
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    w.run_once()
    assert store.recent()[0]["detail"] == "Video unavailable"
    assert not (lib / "Rolf Zuckowski").exists()
    assert list((lib.parent / ".add-music-staging").iterdir()) == []


def test_scan_failure_still_completes(tmp_path, yt):
    def refresh():
        raise RuntimeError("401")
    w, store, lib, _ = make_worker(tmp_path, refresh)
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    plan.tracks = plan.tracks[:1]
    store.add("Ellie", "album", "x", "t", "a", "", plan)
    w.run_once()
    j = store.recent()[0]
    assert j["status"] == "done" and j["detail"] == "Added 1 song"


def test_interrupted_job_is_requeued(tmp_path, yt):
    _, store, _, _ = make_worker(tmp_path)
    plan = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    j = store.add("Ellie", "album", "x", "t", "a", "", plan)
    store.set(j["id"], "working", "downloading 2 of 26")
    assert store.requeue_interrupted() == 1
    assert store.recent()[0]["status"] == "queued"
