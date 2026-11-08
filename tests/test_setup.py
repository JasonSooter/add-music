from add_music.people import parse_people
from add_music.setup import plan
from conftest import jellyfin_with

FOLDERS = [
    {"Name": "Movies", "ItemId": "m0", "CollectionType": "movies"},
    {"Name": "Music", "ItemId": "mu", "CollectionType": "music", "LibraryOptions": {"EnableRealtimeMonitor": True}},
]


def users(ellie_policy):
    return [{"Name": "ellie.sooter", "Id": "e1", "Policy": ellie_policy}]


def test_plans_library_and_access():
    jf = jellyfin_with(folders=FOLDERS, users=users({"EnableAllFolders": True, "EnabledFolders": [], "MaxParentalRating": 12}))
    creates, policies = plan(jf, parse_people("Ellie=ellie.sooter"), "/media/music-people")
    assert creates == [{"name": "Ellie's Music", "path": "/media/music-people/Ellie", "options": {"EnableRealtimeMonitor": True}}]
    [p] = policies
    assert p["before"] == "ALL libraries"
    assert p["policy"]["EnableAllFolders"] is False
    assert p["policy"]["EnabledFolders"] == ["m0", "mu"]
    assert p["policy"]["MaxParentalRating"] == 12          # the rest of the policy is kept


def test_own_library_included_and_others_excluded():
    folders = FOLDERS + [{"Name": "Ellie's Music", "ItemId": "el"}, {"Name": "Mac's Music", "ItemId": "mc"}]
    jf = jellyfin_with(folders=folders, users=users({"EnableAllFolders": True}) + [{"Name": "mac.sooter", "Id": "m1", "Policy": {"EnableAllFolders": False, "EnabledFolders": ["m0", "mu", "mc"]}}])
    creates, policies = plan(jf, parse_people("Ellie=ellie.sooter,Mac=mac.sooter"), "/media/music-people")
    assert creates == []
    assert [(p["user"], p["policy"]["EnabledFolders"]) for p in policies] == [("ellie.sooter", ["el", "m0", "mu"])]


def test_nothing_to_do_when_already_set():
    folders = FOLDERS + [{"Name": "Ellie's Music", "ItemId": "el"}]
    jf = jellyfin_with(folders=folders, users=users({"EnableAllFolders": False, "EnabledFolders": ["mu", "el", "m0"]}))
    assert plan(jf, parse_people("Ellie=ellie.sooter"), "/media/music-people") == ([], [])


def test_former_persons_library_stays_private():
    # Mac was removed from ADD_MUSIC_PEOPLE, but "Mac's Music" still exists.
    folders = FOLDERS + [{"Name": "Ellie's Music", "ItemId": "el", "Locations": ["/media/music-people/Ellie"]},
                         {"Name": "Mac's Music", "ItemId": "mc", "Locations": ["/media/music-people/Mac"]}]
    jf = jellyfin_with(folders=folders, users=users({"EnableAllFolders": True}))
    _, [p] = plan(jf, parse_people("Ellie=ellie.sooter"), "/media/music-people")
    assert "mc" not in p["policy"]["EnabledFolders"]
