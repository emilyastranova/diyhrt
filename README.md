# diyhrt.wiki mirror

A static, self-contained mirror of <https://diyhrt.wiki>. Every page has a banner
pinned to the bottom that links to the matching page on the original site, plus a
`rel=canonical` link pointing there so search engines credit the original.

## Get the files (Git LFS required)

The videos are stored with [Git LFS](https://git-lfs.com). Without it, git checks
out tiny text placeholders instead of the MP4s, and the players show
*"No video with supported format and MIME type found"*. Install LFS **before**
cloning (Alpine: `apk add git-lfs`, Debian/Ubuntu: `apt install git-lfs`,
Fedora: `dnf install git-lfs`), then:

```bash
git lfs install
```

```bash
git clone git@github.com:emilyastranova/diyhrt.git
```

Already cloned without LFS? Fetch the real files into the existing checkout:

```bash
git lfs install && git lfs pull
```

To check, `git lfs ls-files` should mark every video with `*` (downloaded), not
`-` (placeholder only). Each `site/_videos/*.mp4` should be tens of MB, not ~130
bytes. On a server, `git pull` fetches LFS files automatically once LFS is
installed.

## Run it

```bash
docker compose up -d
```

To keep a different port without typing it every time, `cp .env.example .env` and
edit `MIRROR_PORT`.

That serves `site/` with nginx on <http://localhost:8080>. If 8080 is taken, pick
another host port:

```bash
MIRROR_PORT=8088 docker compose up -d
```

The container only speaks plain HTTP. For your public domain, put your usual
TLS reverse proxy (Caddy, Traefik, nginx-proxy, Cloudflare Tunnel…) in front of it and
forward to that port. The container mounts `site/` read-only, so after a re-sync
the new files are live straight away, with no restart needed.

[`nginx/default.conf`](nginx/default.conf) handles what the site needs:
- Extensionless URLs like the original (`/transfem` → `transfem.html`) and the custom 404
- `charset=utf-8`
- Byte-range requests, so the video players can seek
- `text/vtt` for captions

## Layout

| Path | What it is |
|---|---|
| `tools/mirror.py` | Crawls the origin into `raw/` and writes `raw/manifest.json` |
| `tools/videos.py` | Downloads the embedded YouTube videos and makes web copies |
| `tools/build.py` | Turns `raw/` into the deployable `site/` (link rewriting, banner, local video players) |
| `tools/serve.py` | Docker-free preview at http://127.0.0.1:8765 |
| `raw/` | Exactly what the origin served, plus full-quality video originals |
| `site/` | What gets served |
| `compose.yaml`, `nginx/` | Hosting |

## Refresh the mirror

```bash
python3 -I tools/mirror.py
```

```bash
python3 -I tools/videos.py
```

```bash
python3 -I tools/build.py
```

The crawler waits 0.4 s between requests so it doesn't add load to the origin. If the
origin is down, **don't** re-run `mirror.py`: a partial crawl would overwrite
`raw/manifest.json` with an incomplete one. Keep the last good `site/`.

Social-preview tags (`og:image` etc.) need absolute URLs. By default they point at
diyhrt.wiki. To point them at your mirror, set `MIRROR_ORIGIN` when building:

```bash
MIRROR_ORIGIN=https://mirror.example.org python3 -I tools/build.py
```

`videos.py` needs `yt-dlp` (`pipx install yt-dlp`) and `ffmpeg`. It skips videos it
already has.

## What's included

- All 7 pages (`/`, `/transfem`, `/transmasc`, `/bloodtests`, `/injectionsupplies`,
  `/apps`, 404), the nav and age-gate fragments, all CSS/JS/images
- `ownership.txt`, `cow.txt` and two unlinked images that archive.org knew about
- Google Fonts (CSS and font files), served locally so no third-party requests
- **Both YouTube videos** (subcutaneous and intramuscular injection guides). They play
  from the mirror with English captions, and each player links to the YouTube
  original. `raw/_videos/` keeps the full-quality downloads, thumbnails, captions and
  yt-dlp metadata. `site/_videos/` has the ≤720p copies the players use.

Changes the mirror makes beyond the banner:
- Declares UTF-8 in each page (the origin relies on its HTTP header for that).
- Pins the pages to their light design (`color-scheme: light` and a white page
  background). The original never sets a background, so browsers that paint a dark
  canvas in dark mode render its grey text almost invisibly. The original has that
  problem too.
- Video, poster and caption URLs carry a `?v=` version tag, so browsers never keep a
  stale copy after a re-sync (nginx lets them cache media for a week).

What's left out on purpose:
- **`/contact`**: the origin's `robots.txt` disallows it, so the mirror links to the
  original contact page instead.
- Outbound links to other sites (hrt.coffee, vendors, labs) are unchanged.
- Cloudflare-obfuscated email addresses stay obfuscated; the decoder script is
  mirrored, so they still display correctly.

## Git and large files

The repo uses **Git LFS for `*.mp4`** (see `.gitattributes`). The original video
downloads are about 105 MB each, over GitHub's hard 100 MB per-file limit, and keeping
video in plain git would bloat every clone forever. See
[Get the files](#get-the-files-git-lfs-required) for setup.

LFS stores files by content hash, so identical files count once. `videos.py` takes
advantage of that: a video that only needed a lossless remux is byte-identical in
`raw/` and `site/`. GitHub's free LFS quota is 10 GiB of storage and bandwidth per
month. Each fresh clone of this repo pulls about 260 MB of video, so if the quota becomes a problem, deploy
with `GIT_LFS_SKIP_SMUDGE=1 git clone …` followed by `git lfs pull --include="site/**"`
to fetch only the web copies.

## Hosting without Docker

GitHub Pages, Cloudflare Pages and Netlify all handle the extensionless URLs. Note
that Cloudflare Pages rejects files over 25 MiB, which the video copies exceed. For
a bare nginx or Caddy, reuse the `location` blocks from `nginx/default.conf`, or in Caddy:

```
mirror.example.org {
    root * /srv/diyhrt-mirror/site
    try_files {path} {path}.html
    file_server
    handle_errors {
        rewrite * /404.html
        file_server
    }
}
```
