#!/usr/bin/env python3
"""Turn raw/ (from mirror.py) into a deployable static site in site/.

- Same-site absolute URLs (https://diyhrt.wiki/...) become root-relative, so
  the mirror never depends on the origin. Social-card <meta> tags need absolute
  URLs: they point at $MIRROR_ORIGIN (e.g. https://mirror.example.org) if set,
  otherwise they keep pointing at the original site.
- Third-party resources we downloaded (Google Fonts etc.) are served locally.
- Pages excluded by the origin's robots.txt (/contact) link back to the origin.
- Every full HTML page gets a mirror banner and a rel=canonical to the original.

Run:  python3 -I tools/build.py
"""
import html
import json
import os
import re
import shutil
import urllib.parse

ORIGIN = "https://diyhrt.wiki"
MIRROR_ORIGIN = os.environ.get("MIRROR_ORIGIN", "").rstrip("/")
SOCIAL = "\x00SOCIAL_ORIGIN\x00"  # placeholder so the generic rewrite below skips it
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
SITE = os.path.join(ROOT, "site")
TEXT_EXT = (".html", ".htm", ".css", ".js", ".json", ".webmanifest", ".txt", ".xml")

BANNER = """
<!-- mirror banner (added by the diyhrt mirror build) -->
<style>
#mirror-banner{{position:fixed;left:0;right:0;bottom:0;z-index:2147483647;
  display:flex;align-items:center;justify-content:center;gap:12px;flex-wrap:wrap;
  padding:10px 44px 10px 16px;box-sizing:border-box;
  background:linear-gradient(to right,#0f59d1,#630d4e);color:#fff;
  font:14px/1.4 'Poppins',system-ui,sans-serif;text-align:center;
  box-shadow:0 -2px 10px rgba(0,0,0,.35)}}
#mirror-banner a{{color:#fff;text-decoration:underline;font-weight:600}}
#mirror-banner button{{position:absolute;right:8px;top:50%;transform:translateY(-50%);
  background:none;border:0;color:#fff;font-size:22px;line-height:1;cursor:pointer;padding:4px 8px}}
body{{padding-bottom:64px}}
@media (max-width:600px){{#mirror-banner{{font-size:12.5px}}body{{padding-bottom:96px}}}}
</style>
<div id="mirror-banner" role="note">
  <span>This is an independent <strong>mirror</strong> of the DIY HRT Directory, kept so the
  information stays available. The original site is
  <a href="{orig}">{orig_label}</a>. Last synced {date}.</span>
  <button type="button" aria-label="Hide this notice"
    onclick="this.parentNode.style.display='none';document.body.style.paddingBottom='0';try{{sessionStorage.setItem('mirrorBannerHidden','1')}}catch(e){{}}">&times;</button>
</div>
<script>try{{if(sessionStorage.getItem('mirrorBannerHidden')){{document.getElementById('mirror-banner').style.display='none';document.body.style.paddingBottom='0'}}}}catch(e){{}}</script>
<!-- /mirror banner -->
"""


def versioned(videos_dir, name):
    """/_videos/<name>?v=<size+mtime>: nginx caches media for a week, so a re-synced
    file must get a new URL or returning visitors keep the old (or a broken) copy."""
    st = os.stat(os.path.join(videos_dir, name))
    return f"/_videos/{name}?v={st.st_size:x}{int(st.st_mtime):x}"


def local_video(m, videos_dir):
    """Replace a YouTube iframe with a self-hosted player if tools/videos.py made one."""
    vid = m.group(1)
    if not os.path.exists(os.path.join(videos_dir, f"{vid}.web.mp4")):
        return m.group(0)
    track = (f'<track kind="captions" src="{versioned(videos_dir, vid + ".en.vtt")}" srclang="en" label="English">'
             if os.path.exists(os.path.join(videos_dir, f"{vid}.en.vtt")) else "")
    poster = (f' poster="{versioned(videos_dir, vid + ".jpg")}"'
              if os.path.exists(os.path.join(videos_dir, f"{vid}.jpg")) else "")
    # Same box as the original iframe (.video-container iframe in css/main.css).
    return (f'<video controls preload="metadata" playsinline{poster} '
            f'style="position:absolute;top:0;left:0;width:55%;height:100%;background:#000">'
            f'<source src="{versioned(videos_dir, vid + ".web.mp4")}" type="video/mp4">{track}'
            f'<a href="https://www.youtube.com/watch?v={vid}">Watch on YouTube</a></video>')


def page_url(path):
    """Original URL for a mirrored HTML file (clean URLs, like GitHub Pages)."""
    if path == "index.html":
        return ORIGIN + "/"
    return ORIGIN + "/" + path[:-5] if path.endswith(".html") else ORIGIN + "/" + path


def main():
    manifest = json.load(open(os.path.join(RAW, "manifest.json")))
    files = manifest["files"]
    date = manifest["fetched_at"][:10]

    # Map external resource URLs -> local root-relative paths. Longest first so
    # a URL is never partially replaced by a shorter prefix.
    ext_map = {}
    for url, info in files.items():
        if urllib.parse.urlsplit(url).netloc != "diyhrt.wiki":
            local = "/" + urllib.parse.quote(info["path"])
            ext_map[url] = local
            ext_map[url.replace("%20", " ")] = local
            ext_map[url.replace("&", "&amp;")] = local
            ext_map[url.replace("%20", " ").replace("&", "&amp;")] = local
    ext_keys = sorted(ext_map, key=len, reverse=True)

    # Empty site/ in place rather than deleting it: the Docker bind mount is tied to
    # this directory, and a recreated one would leave the container serving nothing.
    os.makedirs(SITE, exist_ok=True)
    for name in os.listdir(SITE):
        p = os.path.join(SITE, name)
        shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.remove(p)
    def skip(d, names):
        # Full-quality video originals and yt-dlp metadata stay in raw/ (archive only).
        if os.path.basename(d) == "_videos":
            return [n for n in names if not n.endswith((".web.mp4", ".jpg", ".vtt"))]
        return [n for n in names if n == "manifest.json"]
    shutil.copytree(RAW, SITE, ignore=skip, dirs_exist_ok=True)
    videos_dir = os.path.join(RAW, "_videos")

    for dirpath, _, names in os.walk(SITE):
        for name in names:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, SITE)
            if not rel.endswith(TEXT_EXT) and not rel.startswith("_ext/fonts.googleapis.com/"):
                continue
            with open(full, encoding="utf-8", errors="surrogateescape") as f:
                text = f.read()
            orig = text

            for k in ext_keys:
                text = text.replace(k, ext_map[k])

            if rel.endswith((".html", ".htm")):
                # Social cards need absolute URLs: point them at the mirror.
                text = re.sub(r'(<meta\s+(?:property|name)="(?:og|twitter):[^"]*"\s+content=")https?://(?:www\.)?diyhrt\.wiki',
                              lambda m: m.group(1) + SOCIAL, text)
                # robots.txt excludes /contact; send people to the original for it.
                text = re.sub(r'href="/?contact(?:\.html)?"', f'href="{ORIGIN}/contact"', text)
            text = re.sub(r"https?://(?:www\.)?diyhrt\.wiki(?=[\"'\s)]|$)", "/", text)  # bare origin
            text = re.sub(r"https?://(?:www\.)?diyhrt\.wiki(?=/)", "", text)
            text = text.replace(SOCIAL, MIRROR_ORIGIN or ORIGIN)

            if rel.endswith((".html", ".htm")):
                text = re.sub(r"<iframe[^>]+youtube(?:-nocookie)?\.com/embed/([\w-]{11})[^>]*>\s*</iframe>",
                              lambda m: local_video(m, videos_dir), text)

            if rel.endswith(".html") and re.search(r"<body[^>]*>", text):
                # Origin pages rely on the HTTP header for UTF-8; declare it in the
                # document so emoji don't turn into mojibake on hosts that omit it.
                if not re.search(r"<meta[^>]+charset", text, re.I):
                    text = re.sub(r"(<head[^>]*>)", r'\1\n    <meta charset="utf-8">', text, count=1, flags=re.I)
                # The site is designed light-only but never sets a page background, so
                # browsers that paint a dark default canvas in dark mode put its grey
                # text on near-black. Pin the intended light look (site CSS still wins
                # wherever it sets its own colours).
                if 'name="color-scheme"' not in text:
                    anchor = (r"(<meta[^>]+charset[^>]*>)" if re.search(r"<meta[^>]+charset", text, re.I)
                              else r"(<head[^>]*>)")
                    text = re.sub(anchor,
                                  r'\1\n    <meta name="color-scheme" content="light">'
                                  r'\n    <style>html{background:#fff;color-scheme:light}</style>',
                                  text, count=1, flags=re.I)
                orig_url = page_url(rel)
                banner = BANNER.format(orig=html.escape(orig_url),
                                       orig_label=html.escape(orig_url.replace("https://", "")),
                                       date=date)
                text = re.sub(r"(<body[^>]*>)", lambda m: m.group(1) + banner, text, count=1)
                if 'rel="canonical"' not in text:
                    text = text.replace("</head>",
                                        f'    <link rel="canonical" href="{html.escape(orig_url)}">\n</head>', 1)

            if text != orig:
                with open(full, "w", encoding="utf-8", errors="surrogateescape") as f:
                    f.write(text)

    print(f"built {SITE} from {len(files)} files (synced {date})")


if __name__ == "__main__":
    main()
