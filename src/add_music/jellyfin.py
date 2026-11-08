"""The one Jellyfin call this needs: start a library scan."""

from __future__ import annotations

import httpx


def refresher(url: str, api_key: str):
    def refresh() -> None:
        if not api_key:
            raise RuntimeError("JELLYFIN_API_KEY is not set")
        r = httpx.post(f"{url.rstrip('/')}/Library/Refresh", headers={"X-Emby-Token": api_key}, timeout=15)
        r.raise_for_status()

    return refresh
