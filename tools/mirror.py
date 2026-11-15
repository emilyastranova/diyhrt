#!/usr/bin/env python3
"""Crawl diyhrt.wiki into raw/ and write raw/manifest.json.

Every same-host URL reachable from the seed pages (HTML links, CSS url(),
JS string literals, fetch() calls) is downloaded. Resources embedded from
other hosts (fonts, images, scripts, stylesheets) are downloaded too, into
raw/_ext/<host>/... . Links to other sites' *pages* are left alone.

Run:  python3 -I tools/mirror.py   (then tools/build.py)
"""
import hashlib
import html.parser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections import deque

ORIGIN = "https://diyhrt.wiki"
HOST = "diyhrt.wiki"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
UA = "Mozilla/5.0 (X11; Linux x86_64) diyhrt-mirror/1.0"
DELAY = 0.4  # be gentle: the origin is already under load

SEEDS = [
    "/", "/index", "/transfem", "/transmasc", "/injectionsupplies",
    "/bloodtests", "/apps", "/nav.html", "/age-check.html", "/404.html",
    "/site.webmanifest", "/robots.txt", "/favicon.ico", "/sitemap.xml",
    "/images/embed.png", "/CNAME", "/ownership.txt", "/cow.txt",
    "/images/kofi.png", "/images/sean.jpg", "/contact.html",
]
# External hosts whose resources we embed locally when referenced as a resource.
RESOURCE_ATTRS = {"src", "srcset", "data-src", "poster"}
JS_STR = re.compile(r"""["'`]([^"'`\s]+\.(?:html?|js|css|json|png|jpe?g|gif|svg|webp|ico|pdf|woff2?|ttf|otf|mp4|webm|txt|xml|webmanifest))["'`]""", re.I)
JS_FETCH = re.compile(r"""fetch\(\s*["'`]([^"'`]+)["'`]""")
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""")
CSS_IMPORT = re.compile(r"""@import\s+['"]([^'"]+)['"]""")


class LinkParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.pages, self.resources, self.inline_css, self.inline_js = [], [], [], []
        self.frames = []
        self._in = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k, v in attrs:
            if not v:
                continue
            if k in ("srcset",):
                self.resources += [p.strip().split()[0] for p in v.split(",") if p.strip()]
            elif k in RESOURCE_ATTRS:
                # Third-party iframes (YouTube embeds) stay embeds; don't crawl players.
                (self.frames if tag in ("iframe", "frame", "embed") else self.resources).append(v)
            elif k == "href":
                rel = (a.get("rel") or "").lower()
                if tag == "link" and rel not in ("canonical", "alternate", "next", "prev"):
                    self.resources.append(v)
                else:
                    self.pages.append(v)
            elif k == "style":
                self.inline_css.append(v)
            elif k == "content" and tag == "meta" and re.match(r"https?://|/", v):
                if re.search(r"\.(png|jpe?g|gif|svg|webp|ico)$", v, re.I):
                    self.resources.append(v)
                else:
                    self.pages.append(v)
            elif k == "content" and tag == "meta" and "url" in v.lower():
                m = re.search(r"url\s*=\s*(\S+)", v, re.I)
                if m:
                    self.pages.append(m.group(1).strip("'\""))
        if tag in ("style", "script"):
            self._in = tag

    def handle_endtag(self, tag):
        self._in = None

    def handle_data(self, data):
        if self._in == "style":
            self.inline_css.append(data)
        elif self._in == "script":
            self.inline_js.append(data)


def local_path(url, is_html):
    """Map an absolute URL to a path under raw/ (same as the published layout)."""
    u = urllib.parse.urlsplit(url)
    path = urllib.parse.unquote(u.path) or "/"
    if u.netloc != HOST:
        path = f"/_ext/{u.netloc}{path}"
    if path.endswith("/"):
        path += "index.html" if is_html else "index"
    if u.query:
        stem, ext = os.path.splitext(path)
        h = hashlib.sha1(u.query.encode()).hexdigest()[:10]
        if u.netloc == "fonts.googleapis.com" or not ext:
            ext = ".css" if u.netloc == "fonts.googleapis.com" else ext
        path = f"{stem}.{h}{ext}"
    if is_html and not path.endswith((".html", ".htm")):
        path += ".html"
    path = os.path.normpath(path).lstrip("/")
    if path.startswith(".."):
        raise ValueError(url)
    return path


def norm(base, ref):
    ref = ref.strip()
    if not ref or ref.startswith(("#", "mailto:", "tel:", "javascript:", "data:", "blob:")):
        return None
    absu = urllib.parse.urljoin(base, ref.replace(" ", "%20"))
    u = urllib.parse.urlsplit(absu)
    if u.scheme not in ("http", "https"):
        return None
    if u.netloc == "www." + HOST:
        u = u._replace(netloc=HOST)
    return urllib.parse.urlunsplit(("https" if u.netloc == HOST else u.scheme, u.netloc,
                                    u.path or "/", u.query, ""))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def main():
    os.makedirs(RAW, exist_ok=True)
    # Parse robots.txt ourselves (RobotFileParser fetches with a UA that gets 403'd).
    robots = urllib.robotparser.RobotFileParser()
    robots.parse(fetch(ORIGIN + "/robots.txt")[2].decode("utf-8", "replace").splitlines())

    queue = deque()
    seen = set()
    manifest = {}  # url -> {"path", "type", "status"}
    skipped = {}

    def enqueue(url, as_resource):
        if not url or url in seen:
            return
        host = urllib.parse.urlsplit(url).netloc
        if host != HOST and not as_resource:
            return  # external page link: keep as a link only
        if host == HOST and not robots.can_fetch("*", url):
            skipped[url] = "robots.txt"
            return
        seen.add(url)
        queue.append(url)

    for s in SEEDS:
        enqueue(ORIGIN + s, True)

    while queue:
        url = queue.popleft()
        try:
            status, ctype, body = fetch(url)
        except Exception as e:  # network error
            print(f"ERR  {url}: {e}", file=sys.stderr)
            skipped[url] = str(e)
            continue
        time.sleep(DELAY)
        if status != 200:
            print(f"{status}  {url}")
            skipped[url] = f"HTTP {status}"
            continue
        ctype = ctype.split(";")[0].strip().lower()
        is_html = ctype == "text/html"
        path = local_path(url, is_html)
        dest = os.path.join(RAW, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(body)
        manifest[url] = {"path": path, "type": ctype}
        print(f"200  {url} -> {path}")

        text = body.decode("utf-8", "replace") if ctype.startswith("text/") or ctype in (
            "application/javascript", "application/json", "application/manifest+json",
            "application/xml") or path.endswith((".js", ".css", ".webmanifest", ".json")) else None
        if text is None:
            continue
        if is_html:
            p = LinkParser()
            p.feed(text)
            for r in p.pages:
                enqueue(norm(url, r), False)
            for r in p.resources:
                enqueue(norm(url, r), True)
            for r in p.frames:
                enqueue(norm(url, r), False)  # same-host frames only
            css = "\n".join(p.inline_css)
            js = "\n".join(p.inline_js)
        else:
            css = text if ctype == "text/css" or path.endswith(".css") else ""
            js = text if "javascript" in ctype or path.endswith(".js") else ""
            if path.endswith((".webmanifest", ".json")):
                for m in re.finditer(r'"src"\s*:\s*"([^"]+)"', text):
                    enqueue(norm(url, m.group(1)), True)
            if ctype in ("application/xml", "text/xml"):
                for m in re.finditer(r"<loc>([^<]+)</loc>", text):
                    enqueue(norm(url, m.group(1)), False)
        for m in CSS_URL.finditer(css):
            enqueue(norm(url, m.group(1)), True)
        for m in CSS_IMPORT.finditer(css):
            enqueue(norm(url, m.group(1)), True)
        for rx in (JS_FETCH, JS_STR):
            for m in rx.finditer(js):
                enqueue(norm(url, m.group(1)), True)

    with open(os.path.join(RAW, "manifest.json"), "w") as f:
        json.dump({"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "files": manifest, "skipped": skipped}, f, indent=1, sort_keys=True)
    print(f"\n{len(manifest)} files saved, {len(skipped)} skipped")


if __name__ == "__main__":
    main()
