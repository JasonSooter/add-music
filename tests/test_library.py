import io
import os

import pytest
from mutagen import File
from PIL import Image

from add_music import library
from conftest import make_audio, make_jpeg


@pytest.mark.parametrize("raw,want", [
    ("AC/DC", "AC-DC"), ("Hamilton: An American Musical", "Hamilton - An American Musical"),
    ("Why?", "Why"), ("..", ""), ("  x\x07 ", "x"),
])
def test_folder_name_matches_abcde(raw, want):
    assert library.folder_name(raw) == want


def test_album_path_refuses_empty_names(tmp_path):
    assert library.album_path(tmp_path, "A/B", "C") == tmp_path / "A-B" / "C"
    with pytest.raises(ValueError):
        library.album_path(tmp_path, "..", "C")


def test_square_cover_crops_16_9():
    out = Image.open(io.BytesIO(library.square_cover(make_jpeg(1280, 720))))
    assert out.size == (720, 720) and out.format == "JPEG"
    assert Image.open(io.BytesIO(library.square_cover(make_jpeg(2000, 2000)))).size == (1200, 1200)


@pytest.mark.parametrize("codec", ["m4a", "opus"])
def test_tag_round_trip(tmp_path, codec):
    p = make_audio(tmp_path / f"t.{codec}", codec)
    cover = library.square_cover(make_jpeg(600, 600))
    library.tag(p, title="Die Jahresuhr", artist="Rolf Zuckowski und seine Freunde", album_artist="Rolf Zuckowski",
                album="Rolfs Liederkalender", number=3, total=12, year="1981", cover=cover)
    m = File(p)
    if codec == "m4a":
        assert m["\xa9nam"] == ["Die Jahresuhr"] and m["aART"] == ["Rolf Zuckowski"]
        assert m["\xa9alb"] == ["Rolfs Liederkalender"] and m["trkn"] == [(3, 12)] and m["\xa9day"] == ["1981"]
        assert bytes(m["covr"][0]) == cover
    else:
        assert m["title"] == ["Die Jahresuhr"] and m["albumartist"] == ["Rolf Zuckowski"]
        assert m["tracknumber"] == ["3"] and m["tracktotal"] == ["12"] and m["date"] == ["1981"]
        assert "metadata_block_picture" in m


def _staged(tmp_path, *names):
    d = tmp_path / "stage" / "Album"
    d.mkdir(parents=True)
    for n in names:
        (d / n).write_bytes(b"new")
    return d


def test_install_new_album_moves_whole_folder(tmp_path):
    staged = _staged(tmp_path, "01 - A.m4a", "02 - B.m4a", "cover.jpg")
    target = tmp_path / "lib" / "Artist" / "Album"
    assert library.install(staged, target) == (2, 0)
    assert sorted(os.listdir(target)) == ["01 - A.m4a", "02 - B.m4a", "cover.jpg"]
    assert not staged.exists()


def test_install_merges_and_never_overwrites(tmp_path):
    target = tmp_path / "lib" / "Artist" / "Album"
    target.mkdir(parents=True)
    (target / "01 - A.opus").write_bytes(b"old")   # same track, other format
    (target / "cover.jpg").write_bytes(b"old")
    staged = _staged(tmp_path, "01 - A.m4a", "02 - B.m4a", "cover.jpg")
    assert library.install(staged, target) == (1, 1)
    assert sorted(os.listdir(target)) == ["01 - A.opus", "02 - B.m4a", "cover.jpg"]
    assert (target / "cover.jpg").read_bytes() == b"old"


def test_library_ok(tmp_path):
    lib = tmp_path / "music"
    assert "missing" in library.library_ok(lib)
    lib.mkdir()
    (lib / ".add-music-staging").mkdir()
    assert "mounted" in library.library_ok(lib)   # only dot-entries: looks unmounted
    (lib / "Some Artist").mkdir()
    assert library.library_ok(lib) is None
