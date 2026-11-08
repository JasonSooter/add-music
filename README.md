# add-music

A kid-friendly page for adding music to a [Jellyfin](https://jellyfin.org) library.
Search YouTube Music, tap **+ Add**, and the album lands in the library tagged,
with a square cover, filed as `<Album Artist>/<Album>/NN - Title.m4a`, and
Jellyfin scans it in.

- **Search**: [ytmusicapi](https://github.com/sigma67/ytmusicapi), which returns real
  albums, artists and track lists (not just videos), with art, year and track numbers.
- **Download**: [yt-dlp](https://github.com/yt-dlp/yt-dlp), saved as MP3 (LAME VBR V0,
  about 245 kbps) with ID3 v2.3 tags and an embedded cover. MP3 is the one format that
  played everywhere: on a Boox Go Color 7, Jellify played neither M4A (through Jellyfin's
  on-the-fly transcode) nor Opus (played directly). `add-music migrate-mp3 [--apply]`
  converts files saved by older versions, on disk, without downloading them again.
- **No explicit content**: explicit songs and albums are dropped on the server.
  They never reach the page, and a crafted request for one gets a 403.
- **A library per person**:
  - Everyone picks their name, and what they add goes into their own Jellyfin
    library ("Ellie's Music"), which only they see.
  - Everyone also keeps the shared music library.
  - `add-music setup-jellyfin` creates the libraries and sets each account's
    access. It is a dry run unless you pass `--apply`.
- **Knows what you already have**:
  - Results that are already in your library or the shared one show a badge
    instead of an Add button. Edition labels are ignored when matching, so
    "Hamilton (Original Broadway Cast Recording)" counts as the "Hamilton" you
    already have.
  - A **My library** view lists both.
  - A finished request says "In Jellyfin" only once Jellyfin itself lists it.
- **Fair queue**:
  - A family queue view shows who asked for what, and where it is.
  - Each person can have 3 requests waiting at a time.
  - A request that failed can't be retried for an hour.
  - The API enforces all of this, not just the page.
- **Safe filing**:
  - A new album appears all at once, as one rename from a staging folder.
  - An existing album only gains the tracks it is missing. Nothing is ever
    overwritten.
  - If the drive looks unmounted, the job fails rather than writing.

## Run

```sh
docker run -d -p 8000:8000 --user 1000:1000 \
  -e ADD_MUSIC_PEOPLE=Ellie=ellie.sooter,Mac=mac.sooter \
  -e JELLYFIN_URL=http://jellyfin:8096 -e JELLYFIN_API_KEY=... \
  -v /srv/media/music:/music:ro -v /srv/media/music-people:/people -v ./state:/state \
  ghcr.io/jasonsooter/add-music:main
```

Jellyfin must see the people folder at `JELLYFIN_PEOPLE_PATH` (for example,
mount `/srv/media/music-people:/media/music-people:ro`). Then set up the
libraries:

```sh
docker exec add-music add-music setup-jellyfin           # shows what it would change
docker exec add-music add-music setup-jellyfin --apply
```

| Variable | Default | |
|---|---|---|
| `ADD_MUSIC_PEOPLE` | (required) | `Name=jellyfin.user` pairs, comma-separated. The name is used for the picker, the folder and the library |
| `JELLYFIN_URL` | `http://jellyfin:8096` | |
| `JELLYFIN_API_KEY` | | Dashboard → API Keys. Without it, files still land, but there are no badges and no library view |
| `ADD_MUSIC_PEOPLE_DIR` | `/people` | Each person's music goes in `<dir>/<Name>/<Album Artist>/<Album>/` |
| `JELLYFIN_PEOPLE_PATH` | `/media/music-people` | The same folder as Jellyfin sees it |
| `ADD_MUSIC_SHARED` | `/music` | The shared library. It is only read: it is how the app checks that the drive is mounted |
| `ADD_MUSIC_STATE` | `/state` | Job database |
| `ADD_MUSIC_CAP` | `3` | Requests one person may have waiting |
| `ADD_MUSIC_COOLDOWN_MIN` | `60` | How long before a failed request may be retried |
| `MAX_ALBUM_TRACKS` | `60` | |

All of the page's URLs are relative, so it can sit behind a reverse proxy under
a sub-path. With nginx, for example, `location /add-music/ { proxy_pass http://add-music:8000/; }`
does it.

## Develop

```sh
uv sync
uv run pytest                      # needs ffmpeg on PATH
ADD_MUSIC_STATE=./state ADD_MUSIC_SHARED=./music ADD_MUSIC_PEOPLE_DIR=./people ADD_MUSIC_PEOPLE=Me uv run add-music
```

yt-dlp needs a JavaScript runtime to solve YouTube's player challenges. The image
ships deno; for local runs, install deno and keep it on your PATH. When downloads
start failing with "This video is not available", a newer yt-dlp is usually the
fix. Renovate merges yt-dlp and ytmusicapi updates as soon as they are released.
