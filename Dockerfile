# Pinned by digest; Renovate proposes digest updates weekly (renovate.json).
FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

# Pinned by digest: a moving tag would silently change the builder.
COPY --from=ghcr.io/astral-sh/uv:0.11.24@sha256:99ea34acedc870ba4ad11a1f540a1c04267c9f30aadc465a94406f52dfda2c36 /uv /bin/uv
# yt-dlp has to run YouTube's player JS to get stream URLs, and deno is the
# runtime it looks for first. Without it most tracks report "not available".
COPY --from=denoland/deno:bin-2.9.8@sha256:b507583d3c68ba69f3ed2ce5969ae5b8b70f43421c781279c7aea06435263543 /deno /usr/local/bin/deno

# ffmpeg: yt-dlp pulls Opus out of its WebM container (stream copy, no transcode).
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ADD_MUSIC_STATE=/state \
    ADD_MUSIC_SHARED=/music \
    ADD_MUSIC_PEOPLE_DIR=/people \
    ADD_MUSIC_PORT=8000 \
    HOME=/state

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Install exactly what uv.lock records, then the project itself without
# re-resolving, so two builds of one commit get the same dependencies.
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
 && uv pip install --system --no-cache -r /tmp/requirements.txt \
 && uv pip install --system --no-cache --no-deps . \
 && rm /tmp/requirements.txt

RUN mkdir -p /state /music /people && chown 1000:1000 /state /music /people
USER 1000:1000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status==200 else 1)"

# Last on purpose: it changes every commit, and only the label rebuilds.
ARG GIT_REVISION=unknown
LABEL org.opencontainers.image.revision="$GIT_REVISION"
LABEL org.opencontainers.image.source="https://github.com/JasonSooter/add-music"
LABEL org.opencontainers.image.title="add-music"

CMD ["add-music"]
