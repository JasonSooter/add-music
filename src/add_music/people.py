"""Who can use the page, and where each person's music goes."""

from __future__ import annotations

from dataclasses import dataclass

from .library import folder_name


@dataclass(frozen=True)
class Person:
    name: str            # what the picker shows: "Ellie"
    jellyfin_user: str   # their Jellyfin login: "ellie.sooter"

    @property
    def folder(self) -> str:
        """Their folder under the people root, and their library's location."""
        return folder_name(self.name)

    @property
    def library_name(self) -> str:
        return f"{self.name}'s Music"


def parse_people(spec: str) -> dict[str, Person]:
    """`Ellie=ellie.sooter,Mac=mac.sooter`. A bare name uses itself as the
    Jellyfin user."""
    out: dict[str, Person] = {}
    for item in spec.split(","):
        if not item.strip():
            continue
        name, _, user = item.partition("=")
        name, user = name.strip(), (user.strip() or name.strip())
        if not folder_name(name) or folder_name(name) != name:
            raise ValueError(f"{name!r} can't be used as a folder name")
        out[name] = Person(name, user)
    return out
