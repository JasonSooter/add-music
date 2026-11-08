from mutagen import File

from add_music import library
from add_music.migrate import convert, targets
from conftest import make_audio, make_jpeg


def test_downloads_mp3():
    from add_music.downloader import OPTIONS
    pp = OPTIONS["postprocessors"][0]
    assert pp["preferredcodec"] == "mp3" and pp["preferredquality"] == "0"


def _tagged(path, codec):
    f = make_audio(path, codec)
    cover = library.square_cover(make_jpeg(300, 300))
    library.tag(f, title="Brutal", artist="Olivia Rodrigo", album_artist="Olivia Rodrigo",
                album="SOUR", number=1, total=11, year="2021", cover=cover)
    return f, cover


def test_mp3_tag_round_trip(tmp_path):
    f = make_audio(tmp_path / "x.mp3", "mp3")
    cover = library.square_cover(make_jpeg(300, 300))
    library.tag(f, title="T", artist="A, B", album_artist="A", album="Al", number=3, total=9, year="2020", cover=cover)
    m = File(f)
    assert str(m["TIT2"]) == "T" and str(m["TPE2"]) == "A" and str(m["TRCK"]) == "3/9" and str(m["TDRC"]) == "2020"
    assert m.tags.version[:2] == (2, 3)
    assert m["APIC:Cover"].data == cover


def test_converts_opus_and_m4a_in_place(tmp_path):
    people = tmp_path / "people"
    album = people / "Ellie" / "Olivia Rodrigo" / "SOUR"
    album.mkdir(parents=True)
    opus, cover = _tagged(album / "01 - brutal.opus", "opus")
    m4a, _ = _tagged(album / "02 - traitor.m4a", "m4a")
    (people / ".add-music-staging").mkdir()
    make_audio(people / ".add-music-staging" / "ignore.opus", "opus")
    assert targets(people) == [opus, m4a]
    for src in (opus, m4a):
        out = convert(src, people / ".add-music-staging")
        assert out.suffix == ".mp3" and out.exists() and not src.exists()
        m = File(out)
        assert str(m["TALB"]) == "SOUR" and str(m["TRCK"]) == "1/11" and m["APIC:Cover"].data == cover
    assert targets(people) == []


def test_interrupted_run_finishes(tmp_path):
    album = tmp_path / "people" / "Mac" / "A" / "B"
    album.mkdir(parents=True)
    src = make_audio(album / "01 - Song.opus", "opus")
    mp3 = make_audio(album / "01 - Song.mp3", "mp3")

    def encode(*a):
        raise AssertionError("must not encode again")

    assert convert(src, tmp_path / "people" / ".add-music-staging", encode) == mp3
    assert not src.exists()
