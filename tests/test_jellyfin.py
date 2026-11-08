from add_music.jellyfin import artist_keys, base_title, norm
from conftest import jellyfin_with

ALBUMS = [
    {"Id": "a1", "Name": "Die Jahresuhr", "AlbumArtist": "Rolf Zuckowski", "Path": "/media/music-people/Ellie/Rolf Zuckowski/Die Jahresuhr"},
    {"Id": "a2", "Name": "Hamilton", "AlbumArtist": "Various Artists", "Path": "/media/music/Various Artists/Hamilton", "ImageTags": {"Primary": "x"}},
    {"Id": "a3", "Name": "Sommerkinder", "AlbumArtist": "Rolf Zuckowski", "Path": "/media/music-people/Mac/Rolf Zuckowski/Sommerkinder"},
]
TRACKS = [
    {"Name": "Die Jahresuhr", "Album": "Die Jahresuhr", "Path": "/media/music-people/Ellie/x.m4a"},
    {"Name": "My Shot", "Album": "Hamilton", "Path": "/media/music/x.mp3"},
    {"Name": "Besuch auf dem Bauernhof", "Album": "Sommerkinder", "Path": "/media/music-people/Mac/x.m4a"},
]


def test_norm_and_artist_keys():
    assert norm("Hamilton: An American Musical!") == "hamiltonanamericanmusical"
    assert artist_keys("Rolf Zuckowski & Freunde, Anuschka") == {"rolfzuckowski", "freunde", "anuschka"}


def test_index_sources_and_matching():
    idx = jellyfin_with(ALBUMS, TRACKS).index("ellie.sooter", "Ellie")
    assert idx.album_source("die jahresuhr", "Rolf Zuckowski") == "mine"
    assert idx.album_source("Die Jahresuhr", "Someone Else") is None          # same title, other artist
    assert idx.album_source("Hamilton", "Lin-Manuel Miranda") == "shared"      # Various Artists matches anyone
    assert idx.track_source("My Shot", "Hamilton") == "shared"
    assert idx.track_source("Die Jahresuhr", "Die Jahresuhr") == "mine"


def test_another_persons_library_blocks_nothing():
    # A parent who has been given access to look at Mac's library.
    idx = jellyfin_with(ALBUMS, TRACKS).index("ellie.sooter", "Ellie")
    assert idx.album_source("Sommerkinder", "Rolf Zuckowski") is None
    assert idx.track_source("Besuch auf dem Bauernhof", "Sommerkinder") is None


def test_index_is_cached():
    jf = jellyfin_with(ALBUMS, TRACKS)
    a = jf.index("ellie.sooter", "Ellie")
    assert jf.index("ellie.sooter", "Ellie") is a
    jf.forget()
    assert jf.index("ellie.sooter", "Ellie") is not a


def test_edition_labels_are_ignored():
    assert base_title("Hamilton (Original Broadway Cast Recording)") == "hamilton"
    assert base_title("Abbey Road (Remastered 2009)") == "abbeyroad"
    assert base_title("Bad Habits (feat. Someone)") == "badhabits"
    assert base_title("Hamilton (Instrumentals)") == "hamiltoninstrumentals"
    idx = jellyfin_with(ALBUMS, TRACKS).index("ellie.sooter", "Ellie")
    assert idx.album_source("Hamilton (Original Broadway Cast Recording)", "Lin-Manuel Miranda") == "shared"
    assert idx.album_source("Hamilton (Instrumentals)", "Lin-Manuel Miranda") is None
    assert idx.track_source("My Shot (feat. Anthony Ramos)", "Hamilton (Original Broadway Cast Recording)") == "shared"


def test_live_and_instrumental_versions_are_different_recordings():
    assert base_title("Hey Jude (Live Version)") == "heyjudeliveversion"
    assert base_title("My Shot (Instrumental Version)") == "myshotinstrumentalversion"
