import shutil

from mutagen import File

from add_music import library
from add_music.catalog import Catalog
from add_music.jobs import Store
from add_music.migrate import convert, plan
from conftest import make_audio, make_jpeg


def test_downloads_opus():
    from add_music.downloader import OPTIONS
    assert OPTIONS["format"].startswith("bestaudio[acodec=opus]")
    assert OPTIONS["postprocessors"][0]["preferredcodec"] == "opus"


def test_plan_and_convert(tmp_path, yt):
    people = tmp_path / "people"
    store = Store(tmp_path / "jobs.db")
    p = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    p.tracks = p.tracks[:1]
    job = store.add("Mac", "album", "x", "t", "a", "", p)
    store.set(job["id"], "done", "Added 1 song")
    album = library.album_path(people / "Mac", p.album_artist, p.album)
    album.mkdir(parents=True)
    m4a = make_audio(album / f"{library.track_stem(1, p.tracks[0].title)}.m4a", "m4a")
    cover = library.square_cover(make_jpeg(400, 400))
    library.tag(m4a, title=p.tracks[0].title, artist="Rolf Zuckowski und seine Freunde", album_artist=p.album_artist,
                album=p.album, number=1, total=26, year="2025", cover=cover)
    stray = make_audio(people / "Mac" / "hand-filed.m4a", "m4a")

    known, unknown = plan(store, people)
    assert known == [(m4a, p.tracks[0].video_id)] and unknown == [stray]

    src = make_audio(tmp_path / "src.opus", "opus")

    def download(vid, dest, stem):
        dest.mkdir(parents=True, exist_ok=True)
        return shutil.copy(src, dest / f"{stem}.opus") and dest / f"{stem}.opus"

    new = convert(m4a, p.tracks[0].video_id, people / ".add-music-staging", download)
    assert new.suffix == ".opus" and new.exists() and not m4a.exists()
    m = File(new)
    assert m["albumartist"] == [p.album_artist] and m["tracknumber"] == ["1"] and m["tracktotal"] == ["26"]
    assert "metadata_block_picture" in m
    assert plan(store, people) == ([], [stray])          # re-running finds nothing more to do


def test_first_job_owns_the_id(tmp_path, yt):
    people = tmp_path / "people"
    store = Store(tmp_path / "jobs.db")
    p = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    p.tracks = p.tracks[:1]
    first = store.add("Mac", "album", "x", "t", "a", "", p)
    store.set(first["id"], "done", "Added 1 song")
    later = Catalog(yt).resolve("album", "MPREb_EGhFrDL8Sd9")
    later.tracks = later.tracks[:1]
    later.tracks[0].video_id = "someOtherId"     # e.g. the same song asked for via search
    j2 = store.add("Mac", "song", "y", "t", "a", "", later)
    store.set(j2["id"], "done", "Already in your library")
    album = library.album_path(people / "Mac", p.album_artist, p.album)
    album.mkdir(parents=True)
    make_audio(album / f"{library.track_stem(1, p.tracks[0].title)}.m4a", "m4a")
    [(_, vid)] = plan(store, people)[0]
    assert vid == p.tracks[0].video_id


def test_interrupted_run_finishes_without_downloading(tmp_path):
    album = tmp_path / "people" / "Mac" / "A" / "B"
    album.mkdir(parents=True)
    m4a = make_audio(album / "01 - Song.m4a", "m4a")
    opus = make_audio(album / "01 - Song.opus", "opus")

    def download(*a):
        raise AssertionError("must not download again")

    assert convert(m4a, "vid", tmp_path / "people" / ".add-music-staging", download) == opus
    assert not m4a.exists() and opus.exists()
