#!/usr/bin/env python3
"""Archive the YouTube videos embedded on the site and make web-sized copies.

raw/_videos/<id>.mp4        full-quality original from yt-dlp (archive copy)
raw/_videos/<id>.web.mp4    <=720p H.264 copy (remuxed or re-encoded), served by the mirror
raw/_videos/<id>.jpg/.vtt   poster image and English captions

build.py swaps each youtube-nocookie iframe for a local <video> when the web
copy exists. Needs yt-dlp (pipx install yt-dlp) and ffmpeg.

Run:  python3 -I tools/videos.py
"""
import json
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
OUT = os.path.join(RAW, "_videos")
MAX_HEIGHT = 720     # web copies: 720p H.264/AAC MP4, plays everywhere
VIDEO_KBPS = 1500
AUDIO_KBPS = 128


def video_ids():
    ids = set()
    for name in os.listdir(RAW):
        if name.endswith(".html"):
            text = open(os.path.join(RAW, name), encoding="utf-8", errors="replace").read()
            ids |= set(re.findall(r"youtube(?:-nocookie)?\.com/embed/([\w-]{11})", text))
    return sorted(ids)


def download(vid):
    if os.path.exists(os.path.join(OUT, f"{vid}.mp4")):
        return
    subprocess.run([
        "yt-dlp", "--no-progress", "--no-playlist",
        "-f", "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "--write-subs", "--write-auto-subs", "--sub-langs", "en", "--sub-format", "vtt",
        "--write-info-json", "-o", os.path.join(OUT, "%(id)s.%(ext)s"),
        f"https://www.youtube.com/watch?v={vid}",
    ], check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,height", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.strip()
    codec, height = out.split(",")[:2]
    return codec, int(height)


def encode(vid):
    src = os.path.join(OUT, f"{vid}.mp4")
    dst = os.path.join(OUT, f"{vid}.web.mp4")
    if os.path.exists(dst):
        return
    tmp = dst + ".part.mp4"
    codec, height = probe(src)
    if codec == "h264" and height <= MAX_HEIGHT:
        # Already web-friendly: just move the index to the front so it streams.
        args = ["-c", "copy"]
    else:
        have = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                              capture_output=True, text=True).stdout
        # Prefer x264; Fedora's stock ffmpeg only ships Cisco's OpenH264.
        vcodec = (["libx264", "-preset", "slow", "-crf", "22", "-maxrate", f"{VIDEO_KBPS}k",
                   "-bufsize", f"{VIDEO_KBPS * 2}k"]
                  if " libx264 " in have else ["libopenh264", "-b:v", f"{VIDEO_KBPS}k"])
        args = ["-vf", f"scale=-2:{MAX_HEIGHT}", "-c:v", *vcodec, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k"]
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src, *args,
                    "-movflags", "+faststart", tmp], check=True)
    if args[0] == "-c":
        # Lossless remux: make the archive copy the same bytes as the web copy, so
        # Git LFS (content-addressed) stores the video once instead of twice.
        os.replace(tmp, src)
        shutil.copyfile(src, dst)
    else:
        os.replace(tmp, dst)  # only a finished encode ever gets the final name
    print(f"{vid}: {'copied' if args[0] == '-c' else 're-encoded'} -> {os.path.getsize(dst) / 2**20:.1f} MiB")


def main():
    os.makedirs(OUT, exist_ok=True)
    for vid in video_ids():
        download(vid)
        encode(vid)


if __name__ == "__main__":
    main()
