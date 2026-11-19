"""Prepare an approved clip for CapCut or submit a finished edit to Buffer."""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import requests

OUT = Path("production")
PLATFORMS = ("youtube", "tiktok", "x")


def https(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use a direct HTTPS URL.")
    return url


def download(url, path):
    limit = 250 * 1024 * 1024
    with requests.get(https(url), stream=True, timeout=(10, 30), allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError(f"Media request returned HTTP {response.status_code}.")
        if "text/" in response.headers.get("Content-Type", ""):
            raise ValueError("Media URL returned text. Supply a direct video URL.")
        with path.open("wb") as file:
            size = 0
            for chunk in response.iter_content(chunk_size=256 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError("Source exceeds 250 MB.")
                file.write(chunk)


def twitch_media(url):
    parts = urlsplit(url)
    fields = parts.path.strip("/").split("/")
    if parts.hostname == "clips.twitch.tv" and len(fields) == 1:
        clip_id = fields[0]
    elif parts.hostname in ("twitch.tv", "www.twitch.tv") and len(fields) == 3 and fields[1] == "clip":
        clip_id = fields[2]
    else:
        raise ValueError("Supply a Twitch clip link or a creator-provided MP4 URL.")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", clip_id):
        raise ValueError("Invalid Twitch clip ID.")
    client = os.environ["TWITCH_CLIENT_ID"]
    token = os.getenv("TWITCH_EDITOR_ACCESS_TOKEN")
    editor = os.environ["TWITCH_EDITOR_ID"]
    if not token:
        response = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": client, "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        }, timeout=20)
        if not response.ok:
            raise ValueError(f"Twitch authorization failed (HTTP {response.status_code}).")
        token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Client-Id": client}
    base = "https://api.twitch.tv/helix/clips"
    response = requests.get(base, params={"id": clip_id}, headers=headers, timeout=20)
    response.raise_for_status()
    clips = response.json()["data"]
    if not clips:
        raise ValueError("Twitch clip was unavailable.")
    response = requests.get(base + "/downloads", params={"clip_id": clip_id,
        "broadcaster_id": clips[0]["broadcaster_id"], "editor_id": editor}, headers=headers, timeout=20)
    if not response.ok:
        raise ValueError(f"Twitch download authorization returned HTTP {response.status_code}.")
    for item in response.json()["data"]:
        if item["clip_id"] == clip_id:
            media = item.get("portrait_download_url") or item.get("landscape_download_url")
            if media:
                return media
    raise ValueError("Twitch provided no authorized download. Ask the creator for an MP4.")


def run_ffmpeg(*args):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    *args], check=True, timeout=240)


def timestamp(sec):
    ms = max(0, round(sec * 1000))
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"


def transcribe(audio, subtitles):
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), vad_filter=True, beam_size=1)
    entries = []
    for segment in segments:
        line = " ".join(segment.text.split())
        if line:
            entries.append(f"{len(entries)+1}\n{timestamp(segment.start)} --> "
                           f"{timestamp(segment.end)}\n{line}\n")
    subtitles.write_text("\n".join(entries), encoding="utf-8")
    return bool(entries)


def prepare(source, credit, media, start, duration):
    OUT.mkdir(exist_ok=True)
    work = OUT / ".work"
    work.mkdir(exist_ok=True)
    raw = work / "source.mp4"
    download(media or twitch_media(source), raw)
    # Keep the complete landscape frame for clips with action near the edges.
    clean = OUT / "capcut.mp4"
    run_ffmpeg("-protocol_whitelist", "file", "-ss", str(start), "-i", str(raw),
               "-t", str(duration), "-map", "0:v:0", "-map", "0:a:0?",
               "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                      "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x191321,setsar=1",
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-movflags", "+faststart", str(clean))
    audio = work / "audio.wav"
    streams = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                      "-show_entries", "stream=index", "-of", "csv=p=0",
                                      str(clean)], timeout=15)
    srt = OUT / "captions.srt"
    srt.write_text("", encoding="utf-8")
    has_captions = False
    if streams.strip():
        run_ffmpeg("-i", str(clean), "-vn", "-ac", "1", "-ar", "16000", str(audio))
        has_captions = transcribe(audio, srt)
    (OUT / "credit.txt").write_text(f"Footage: {credit}", encoding="utf-8")
    filters = ["drawtext=textfile=production/credit.txt:expansion=none:fontcolor=white:"
               "fontsize=30:x=(w-tw)/2:y=h*0.27:box=1:boxcolor=black@0.75:boxborderw=8"]
    if has_captions:
        filters.append("subtitles=production/captions.srt:force_style='FontSize=12,MarginV=60,Outline=2'")
    run_ffmpeg("-i", str(clean), "-vf", ",".join(filters), "-c:v", "libx264",
               "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "copy",
               "-movflags", "+faststart", str(OUT / "ready.mp4"))


def buffer_posts(video, source, title, credit, caption, destinations, queue):
    key = os.environ["BUFFER_API_KEY"]
    channels = {p: os.environ.get("BUFFER_" + p.upper() + "_CHANNEL_ID") for p in destinations}
    missing = [p for p, channel in channels.items() if not channel]
    if missing:
        raise ValueError("Missing Buffer channel IDs: " + ", ".join(missing))
    if len(set(channels.values())) != len(channels):
        raise ValueError("Set a distinct Buffer channel ID for each destination.")
    mutation = """mutation Submit($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess { post { id } }
        ... on MutationError { message }
      }
    }"""
    receipts = {}
    failures = []
    for name, channel in channels.items():
        attribution = f"\nFootage: {credit}\n{source}"
        # Leave room for X's weighted URLs and wide characters.
        text = caption[:max(0, 125-len(credit))] + attribution if name == "x" else caption + attribution
        payload = {"channelId": channel, "text": text, "mode": "addToQueue",
                   "schedulingType": "automatic", "saveToDraft": not queue, "needsApproval": False,
                   "assets": [{"video": {"url": https(video)}}]}
        if name == "youtube":
            payload["metadata"] = {"youtube": {"title": title[:100], "categoryId": "20"}}
        try:
            response = requests.post("https://api.buffer.com",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": mutation, "variables": {"input": payload}}, timeout=35)
            response.raise_for_status()
            data = response.json()
            result = (data.get("data") or {}).get("createPost") or {}
            if not result.get("post"):
                failures.append(name)
                print(f"{name}: Buffer rejected the request. Verify channel and media settings.")
                continue
            receipts[name] = result["post"]["id"]
            print(f"{name}: {receipts[name]}")
        except (requests.RequestException, ValueError):
            failures.append(name)
            print(f"{name}: response unavailable. Check Buffer before retrying.")
    if failures:
        raise ValueError("Check Buffer before retrying only these destinations: " + ", ".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "draft", "queue"))
    parser.add_argument("--source", required=True)
    parser.add_argument("--credit", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--caption", default="")
    parser.add_argument("--media", default="")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--duration", type=float, default=45)
    parser.add_argument("--destinations", default="youtube,tiktok,x")
    args = parser.parse_args()
    if not (args.credit.strip() and args.title.strip()) or len(args.credit) > 60:
        parser.error("Provide a title and a credit line up to 60 characters.")
    try:
        source = https(args.source)
        if args.action == "prepare":
            if not (0 <= args.start <= 36000 and 0 < args.duration <= 60):
                parser.error("Start must be nonnegative and duration must be within 60 seconds.")
            prepare(source, args.credit, args.media, args.start, args.duration)
        else:
            destinations = [p.strip() for p in args.destinations.split(",")]
            if any(p not in PLATFORMS for p in destinations) or len(set(destinations)) != len(destinations):
                parser.error("Select unique destinations from youtube,tiktok,x.")
            buffer_posts(https(args.media), source, args.title, args.credit,
                         args.caption or args.title, destinations, args.action == "queue")
    except (KeyError, ValueError, subprocess.SubprocessError, requests.RequestException) as err:
        # URLs, access tokens, and remote response bodies are not emitted in runner logs.
        if isinstance(err, ValueError):
            parser.exit(1, f"{err}\n")
        if isinstance(err, KeyError):
            parser.exit(1, f"Missing setting or response field: {err}\n")
        parser.exit(1, f"Production failed ({type(err).__name__}). Check the source and settings.\n")


if __name__ == "__main__":
    main()
