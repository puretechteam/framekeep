# FrameKeep - (C) 2025 Pure Tech
# Licensed under the MIT License
"""Download the pinned FFmpeg build into ./ffmpeg.

It is a minimal FFmpeg made by tools/build_ffmpeg.sh (only what FrameKeep uses: H.266/VVenC, SVT-AV1, x264, x265,
GPU encoders, screen capture, Opus/AAC for desktop audio, cameras and the FrameKeep player), published by .github/workflows/ffmpeg.yml.

Usage: python tools/fetch_ffmpeg.py [--force] [--zip PATH]
  --zip PATH  install a zip you built yourself with tools/build_ffmpeg.sh instead of downloading (no checksum)
"""

import hashlib, io, os, shutil, sys, urllib.request, zipfile

VERSION = "9.0.2-fk3"
URL = f"https://github.com/puretechteam/framekeep/releases/download/ffmpeg-{VERSION}/framekeep-ffmpeg-{VERSION}-win64.zip"
SHA256 = "3126b4762825bbc044f4b320fd171f22054ee8fb83933b563b547419d0bc4412"
DEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ffmpeg")
STAMP = os.path.join(DEST, "VERSION")

def download():
    print(f"Downloading FFmpeg {VERSION} (~21 MB) from {URL}")
    buf = io.BytesIO()
    with urllib.request.urlopen(URL) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        while chunk := resp.read(1 << 20):
            buf.write(chunk)
            if total:
                print(f"\r  {buf.tell() * 100 // total}%", end="", flush=True)
    print()
    digest = hashlib.sha256(buf.getvalue()).hexdigest()
    if digest != SHA256:
        print(f"Checksum mismatch!\n  expected {SHA256}\n  got      {digest}\nNothing was installed.")
        return None
    return buf

def main():
    force = "--force" in sys.argv
    local = sys.argv[sys.argv.index("--zip") + 1] if "--zip" in sys.argv else None
    if not local and not force and os.path.exists(STAMP) and open(STAMP).read().strip() == VERSION:
        print(f"FFmpeg {VERSION} already present in {DEST}")
        return 0
    source = local or download()
    if source is None:
        return 1
    os.makedirs(DEST, exist_ok=True)
    # framekeep.spec bundles every exe/dll in DEST, so clear out a previous build's files first
    for name in os.listdir(DEST):
        if name.lower().endswith((".exe", ".dll")):
            os.remove(os.path.join(DEST, name))
    with zipfile.ZipFile(source) as zf:
        for name in zf.namelist():
            parts = name.split("/")
            if len(parts) == 3 and parts[1] == "bin" and parts[2]:
                target = parts[2]
            elif len(parts) == 2 and parts[1] in ("LICENSE", "BUILDINFO.txt"):
                target = parts[1]
            else:
                continue
            with zf.open(name) as src, open(os.path.join(DEST, target), "wb") as dst:
                shutil.copyfileobj(src, dst)
            print(f"  {target}")
    with open(STAMP, "w") as f:
        f.write(VERSION)
    print(f"FFmpeg {VERSION} installed to {DEST}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
