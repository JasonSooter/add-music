"""`add-music setup-jellyfin [--apply]`: one library per person, and access to match.

For each person in ADD_MUSIC_PEOPLE:
  - a music library "<Name>'s Music" at <people path>/<Name>, with the same
    options as the existing shared music library (real-time monitoring etc.);
  - their Jellyfin account switched from "all libraries" to an explicit list:
    every library that is NOT a person's library, plus their own.

So everyone keeps Movies, Shows, Collections and the shared Music, sees their
own additions, and nobody -- parents included -- sees anyone else's. An admin
can still tick another person's library for themselves in the Dashboard when
they need to look. The catch of an explicit list: a library created later must
be ticked for each person by hand.

Without --apply it only prints what it would change. It is idempotent: run it
again after adding a person.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
from pathlib import Path

from .jellyfin import Jellyfin
from .people import Person, parse_people


def _id(s: str) -> str:
    return s.replace("-", "").lower()


def plan(jf: Jellyfin, people: dict[str, Person], people_path: str) -> tuple[list[dict], list[dict]]:
    """(libraries to create, policies to change). Policies can only be
    planned exactly for libraries that already exist; run again after creating."""
    folders = jf._get("/Library/VirtualFolders").json()
    by_name = {f["Name"]: f for f in folders}
    person_libs = {p.library_name for p in people.values()}
    template = next((f for f in folders if f.get("CollectionType") == "music" and f["Name"] not in person_libs
                     and not any((loc or "").startswith(people_path.rstrip("/") + "/") for loc in f.get("Locations") or [])), None)
    if template is None:
        raise SystemExit("no shared music library to copy options from")

    creates = []
    for p in people.values():
        if p.library_name not in by_name:
            creates.append({"name": p.library_name, "path": f"{people_path.rstrip('/')}/{p.folder}",
                            "options": template.get("LibraryOptions") or {}})

    # A library is private if it is named for a person OR lives under the
    # people path -- so the library of someone since removed from (or renamed
    # in) ADD_MUSIC_PEOPLE is never mistaken for a shared one and handed out.
    root = people_path.rstrip("/") + "/"

    def private(f) -> bool:
        return f["Name"] in person_libs or any((loc or "").startswith(root) for loc in f.get("Locations") or [])

    common = sorted(_id(f["ItemId"]) for f in folders if not private(f))
    users = {u["Name"].casefold(): u for u in jf._get("/Users").json()}
    names = {_id(f["ItemId"]): f["Name"] for f in folders}
    policies = []
    for p in people.values():
        u = users.get(p.jellyfin_user.casefold())
        if u is None:
            raise SystemExit(f"no Jellyfin user {p.jellyfin_user!r} for {p.name}")
        own = by_name.get(p.library_name)
        want = sorted(common + ([_id(own["ItemId"])] if own else []))
        pol = u["Policy"]
        have = sorted(_id(x) for x in pol.get("EnabledFolders") or [])
        if pol.get("EnableAllFolders") or have != want:
            new = copy.deepcopy(pol)
            new["EnableAllFolders"] = False
            new["EnabledFolders"] = want
            policies.append({
                "user": u["Name"], "id": u["Id"], "policy": new,
                "before": "ALL libraries" if pol.get("EnableAllFolders") else ", ".join(names.get(x, x) for x in have),
                "after": ", ".join(names.get(x, x) for x in want) + ("" if own else f" (+ {p.library_name} once created)"),
            })
    return creates, policies


def main(argv: list[str]) -> None:
    ap = argparse.ArgumentParser(prog="add-music setup-jellyfin", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="make the changes (default: only print them)")
    args = ap.parse_args(argv)

    env = os.environ.get
    people = parse_people(env("ADD_MUSIC_PEOPLE", ""))
    people_path = env("JELLYFIN_PEOPLE_PATH", "/media/music-people")
    people_dir = Path(env("ADD_MUSIC_PEOPLE_DIR", "/people"))
    jf = Jellyfin(env("JELLYFIN_URL", "http://jellyfin:8096"), env("JELLYFIN_API_KEY", ""), people_path)
    if not jf.enabled:
        sys.exit("JELLYFIN_API_KEY is not set")

    creates, policies = plan(jf, people, people_path)
    missing_keep = [p.name for p in people.values() if not (people_dir / p.folder / ".keep").exists()]
    if not creates and not policies and not missing_keep:
        print("Nothing to change.")
        return
    for name in missing_keep:
        print(f"KEEP-FILE {people_dir / name / '.keep'}")
    for c in creates:
        print(f"CREATE library {c['name']!r} at {c['path']}")
    for pol in policies:
        print(f"ACCESS {pol['user']}:\n    before: {pol['before']}\n    after:  {pol['after']}")
    if not args.apply:
        print("\nDry run. Re-run with --apply to make these changes.")
        return

    for c in creates:
        folder = people_dir / c["path"].rsplit("/", 1)[1]
        folder.mkdir(parents=True, exist_ok=True)   # Jellyfin rejects a path that doesn't exist
        options = {**c["options"], "PathInfos": [{"Path": c["path"]}]}
        jf._post("/Library/VirtualFolders", json={"LibraryOptions": options},
                 name=c["name"], collectionType="music", refreshLibrary="false")
        print(f"created {c['name']}")
    # An EMPTY library folder is skipped by Jellyfin's scan ("inaccessible or
    # empty") as if the drive were missing, so deleting a person's last album
    # left it listed (2026-11-08). A placeholder keeps every folder non-empty.
    for p in people.values():
        keep = people_dir / p.folder / ".keep"
        keep.parent.mkdir(parents=True, exist_ok=True)
        if not keep.exists():
            keep.write_text("Keeps this library folder non-empty so Jellyfin scans it "
                            "(an empty one is skipped as possibly unmounted).\n")
    # Re-plan: the new libraries have ids now, and policies must include them.
    _, policies = plan(jf, people, people_path)
    for pol in policies:
        jf._post(f"/Users/{pol['id']}/Policy", json=pol["policy"])
        print(f"updated {pol['user']}: {pol['after']}")
    print("Done.")
