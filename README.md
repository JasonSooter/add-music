# add-music

A kid-friendly page for adding music to a [Jellyfin](https://jellyfin.org) library.
Search YouTube Music, tap **+ Add**, and the album lands in the library tagged,
with a square cover, filed as `<Album Artist>/<Album>/NN - Title.m4a`, and
Jellyfin scans it in.

- **Search**: [ytmusicapi](https://github.com/sigma67/ytmusicapi), which returns real
  albums, artists and track lists (not just videos), with art, year and track numbers.
- **Download**: [yt-dlp](https://github.com/yt-dlp/yt-dlp). The original audio stream
  is kept (AAC in M4A, or Opus), never re-encoded.
- **No explicit content**: explicit songs and albums are dropped on the server.
  They never reach the page, and a crafted request for one gets a 403.
- **Who added what**: a name picker without a password. Every request is logged
  with its name in `/state/jobs.db`.
- **Safe filing**:
  - A new album appears all at once, as one rename from a staging folder.
  - An existing album only gains the tracks it is missing. Nothing in the
    library is ever overwritten.
  - If the library folder looks unmounted, the job fails rather than writing.

## Run

```sh
docker run -d -p 8000:8000 \
  -e ADD_MUSIC_PEOPLE=Ellie,Mac,Poppy \
  -e JELLYFIN_URL=http://jellyfin:8096 -e JELLYFIN_API_KEY=... \
  -v /srv/media/music:/music -v ./state:/state \
  --user 1000:1000 ghcr.io/jasonsooter/add-music:main
```

| Variable | Default | |
|---|---|---|
| `ADD_MUSIC_PEOPLE` | (required) | Names offered by the picker, comma-separated |
| `JELLYFIN_URL` | `http://jellyfin:8096` | |
| `JELLYFIN_API_KEY` | | Dashboard → API Keys. Without it, files still land and Jellyfin finds them on its next scheduled scan |
| `ADD_MUSIC_LIBRARY` | `/music` | The Jellyfin music library folder |
| `ADD_MUSIC_STAGING` | `<library>/.add-music-staging` | Must be on the same mount as the library so the final move is a rename. Jellyfin ignores dot-folders |
| `ADD_MUSIC_STATE` | `/state` | Job database |
| `MAX_ALBUM_TRACKS` | `60` | |

All of the page's URLs are relative, so it can sit behind a reverse proxy under
a sub-path. With nginx, for example, `location /add-music/ { proxy_pass http://add-music:8000/; }`
does it.

## Develop

```sh
uv sync
uv run pytest                      # needs ffmpeg on PATH
ADD_MUSIC_STATE=./state ADD_MUSIC_LIBRARY=./music ADD_MUSIC_PEOPLE=Me uv run add-music
```

yt-dlp needs a JavaScript runtime to solve YouTube's player challenges. The image
ships deno; for local runs, install deno and keep it on your PATH. When downloads
start failing with "This video is not available", a newer yt-dlp is usually the
fix. Renovate merges yt-dlp and ytmusicapi updates as soon as they are released.
