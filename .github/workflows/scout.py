#!/usr/bin/env python3
"""
GTA6Clippingtons clip scout.

Every run (GitHub Actions, every ~10 min):
  1. Pulls fresh GTA 6 clips/videos from Twitch, YouTube and Reddit.
  2. Drops spoilers, leaks, exploits, NSFW and stuff already seen.
  3. Ranks by view velocity (views per minute since posted).
  4. Sends the top new picks to Discord as ready-to-post cards:
     link, creator credit, hook, X caption, TikTok/Shorts caption, permission DM.

It never downloads or posts anything. You approve, paste the link into
OpusClip (or your editor), and schedule.

Every source is optional: if its keys aren't set, it's skipped.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
STATE_PATH = ROOT / "state" / "seen.json"
QUEUE_PATH = ROOT / "state" / "queue.csv"
UA = "GTA6ClippingtonsScout/1.0 (fan page clip finder)"
NOW = datetime.now(timezone.utc)


# --------------------------------------------------------------------------- model
@dataclass
class Candidate:
    source: str          # twitch | youtube | reddit
    id: str              # unique per source
    title: str
    url: str             # link to the ORIGINAL post
    creator: str         # name to credit
    creator_url: str
    created_at: datetime
    views: int
    thumbnail: str = ""
    duration_s: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.id}"

    @property
    def age_min(self) -> float:
        return max((NOW - self.created_at).total_seconds() / 60, 5.0)

    @property
    def velocity(self) -> float:
        """Views per minute, the main 'is this blowing up' signal."""
        return self.views / self.age_min

    @property
    def score(self) -> float:
        w = CONFIG["source_weights"].get(self.source, 1.0)
        return round(self.velocity * w, 2)


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# --------------------------------------------------------------------------- filters
def blocked_reason(c: Candidate) -> str | None:
    t = c.title.lower()
    for word in CONFIG["block_keywords"]:
        if re.search(rf"(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])", t):
            return f"keyword:{word}"
    if c.creator.lower() in {x.lower() for x in CONFIG["block_creators"]}:
        return "blocked creator"
    if c.extra.get("nsfw"):
        return "nsfw"
    if c.views < CONFIG["min_views"].get(c.source, 0):
        return "too few views"
    if c.age_min > CONFIG["max_age_hours"] * 60:
        return "too old"
    return None


# --------------------------------------------------------------------------- sources
def fetch_twitch() -> list[Candidate]:
    cid, secret = os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        print("twitch: skipped (no keys)")
        return []
    tok = requests.post(
        "https://id.twitch.tv/oauth2/token",
        params={"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"},
        timeout=20,
    ).json()["access_token"]
    h = {"Client-Id": cid, "Authorization": f"Bearer {tok}"}
    games = requests.get(
        "https://api.twitch.tv/helix/games",
        params={"name": CONFIG["twitch_game_name"]}, headers=h, timeout=20,
    ).json().get("data", [])
    if not games:
        print("twitch: game not found")
        return []
    started = (NOW - timedelta(hours=CONFIG["max_age_hours"])).isoformat().replace("+00:00", "Z")
    clips = requests.get(
        "https://api.twitch.tv/helix/clips",
        params={"game_id": games[0]["id"], "started_at": started, "first": 100},
        headers=h, timeout=20,
    ).json().get("data", [])
    out = []
    for c in clips:
        out.append(Candidate(
            source="twitch", id=c["id"], title=c["title"], url=c["url"],
            creator=c["broadcaster_name"],
            creator_url=f"https://twitch.tv/{c['broadcaster_name'].lower()}",
            created_at=parse_ts(c["created_at"]), views=c["view_count"],
            thumbnail=c.get("thumbnail_url", ""), duration_s=c.get("duration"),
            extra={"clipped_by": c.get("creator_name"), "language": c.get("language")},
        ))
    print(f"twitch: {len(out)} clips")
    return out


def _youtube_search_due() -> bool:
    """YouTube search costs 100 quota units (10k/day free), so only search every N minutes."""
    every = CONFIG["youtube_search_every_min"]
    minute_of_day = NOW.hour * 60 + NOW.minute
    return minute_of_day % every < 10  # matches one ~10-min run per window


def fetch_youtube() -> list[Candidate]:
    key = os.getenv("YOUTUBE_API_KEY")
    if not key:
        print("youtube: skipped (no key)")
        return []
    if not _youtube_search_due() and not os.getenv("FORCE_YOUTUBE"):
        print("youtube: skipped this run (quota pacing)")
        return []
    after = (NOW - timedelta(hours=CONFIG["max_age_hours"])).isoformat().replace("+00:00", "Z")
    ids: list[str] = []
    for q in CONFIG["youtube_queries"]:
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params={
            "part": "snippet", "q": q, "type": "video", "order": "viewCount",
            "publishedAfter": after, "maxResults": 25, "relevanceLanguage": "en", "key": key,
        }, timeout=20).json()
        ids += [i["id"]["videoId"] for i in r.get("items", [])]
    ids = list(dict.fromkeys(ids))[:50]
    if not ids:
        return []
    vids = requests.get("https://www.googleapis.com/youtube/v3/videos", params={
        "part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": key,
    }, timeout=20).json().get("items", [])
    out = []
    for v in vids:
        sn, st = v["snippet"], v.get("statistics", {})
        out.append(Candidate(
            source="youtube", id=v["id"], title=sn["title"],
            url=f"https://www.youtube.com/watch?v={v['id']}",
            creator=sn["channelTitle"],
            creator_url=f"https://www.youtube.com/channel/{sn['channelId']}",
            created_at=parse_ts(sn["publishedAt"]), views=int(st.get("viewCount", 0)),
            thumbnail=sn.get("thumbnails", {}).get("high", {}).get("url", ""),
            extra={"duration": v.get("contentDetails", {}).get("duration")},
        ))
    print(f"youtube: {len(out)} videos")
    return out


def fetch_reddit() -> list[Candidate]:
    cid, secret = os.getenv("REDDIT_CLIENT_ID"), os.getenv("REDDIT_CLIENT_SECRET")
    if not (cid and secret):
        print("reddit: skipped (no keys)")
        return []
    tok = requests.post(
        "https://www.reddit.com/api/v1/access_token",
        auth=(cid, secret), data={"grant_type": "client_credentials"},
        headers={"User-Agent": UA}, timeout=20,
    ).json()["access_token"]
    h = {"Authorization": f"bearer {tok}", "User-Agent": UA}
    out = []
    for sub in CONFIG["subreddits"]:
        for listing in ("hot", "rising"):
            r = requests.get(f"https://oauth.reddit.com/r/{sub}/{listing}",
                             params={"limit": 50}, headers=h, timeout=20).json()
            for ch in r.get("data", {}).get("children", []):
                p = ch["data"]
                is_video = p.get("is_video") or any(
                    d in p.get("url", "") for d in ("youtube.com", "youtu.be", "twitch.tv", "x.com", "twitter.com", "streamable.com", "tiktok.com"))
                if not is_video:
                    continue
                out.append(Candidate(
                    source="reddit", id=p["id"], title=p["title"],
                    url="https://www.reddit.com" + p["permalink"],
                    creator=f"u/{p['author']}",
                    creator_url=f"https://www.reddit.com/user/{p['author']}",
                    created_at=datetime.fromtimestamp(p["created_utc"], tz=timezone.utc),
                    views=int(p.get("score", 0)) * CONFIG["reddit_upvote_to_view"],
                    thumbnail=p.get("thumbnail", "") if str(p.get("thumbnail", "")).startswith("http") else "",
                    extra={"nsfw": p.get("over_18", False), "spoiler": p.get("spoiler", False),
                           "media_url": p.get("url"), "subreddit": sub, "upvotes": p.get("score", 0)},
                ))
    # Reddit has its own spoiler flag
    out = [c for c in out if not c.extra.get("spoiler")]
    print(f"reddit: {len(out)} video posts")
    return out


# --------------------------------------------------------------------------- drafting
HOOK_RULES = [
    (("car", "drive", "driving", "crash", "chase", "bike", "boat", "plane"), "THE DRIVING IN GTA 6 IS UNREAL"),
    (("npc", "pedestrian", "reaction", "cop", "police"), "GTA 6 NPCS ARE ON ANOTHER LEVEL"),
    (("graphics", "lighting", "weather", "rain", "sunset", "4k"), "GTA 6 GRAPHICS LOOK REAL"),
    (("map", "vice city", "leonida", "secret", "hidden", "easter egg", "found"), "HIDDEN DETAIL MOST PEOPLE MISSED"),
    (("funny", "lol", "fail", "bruh", "wtf"), "THIS GTA 6 MOMENT IS CRAZY"),
    (("how to", "tip", "money", "guide"), "HOW TO DO THIS IN GTA 6"),
]


def template_drafts(c: Candidate) -> dict:
    t = c.title.lower()
    def has(k: str) -> bool:
        return re.search(rf"(?<![a-z0-9]){re.escape(k)}s?(?![a-z0-9])", t) is not None
    hook = next((h for keys, h in HOOK_RULES if any(has(k) for k in keys)), "YOU NEED TO SEE THIS GTA 6 CLIP")
    credit = f"🎥 {c.creator}"
    short = re.sub(r"\s+", " ", c.title).strip()[:90]
    return {
        "hook": hook,
        "x_caption": f"{short}\n\n{credit}",
        "tiktok_caption": f"{short} {credit} #gta6 #gtavi #gaming",
        "permission_dm": f"Love this clip! Ok if GTA6Clippingtons shares it with full credit to you?",
    }


def claude_drafts(c: Candidate) -> dict | None:
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    prompt = (
        "You write captions for GTA6Clippingtons, a GTA 6 fan page (not Rockstar). "
        "Given this clip, return ONLY JSON with keys hook, x_caption, tiktok_caption.\n"
        "Rules: hook = ALL CAPS on-screen text, max 7 words, no spoilers, must match what the title says "
        "(never promise something not shown). x_caption = one short punchy line, blank line, then '🎥 CREDIT'. "
        "No hashtags on X. tiktok_caption = short line + '🎥 CREDIT' + 3 relevant hashtags. Max one emoji besides 🎥. "
        "Never claim facts beyond the title.\n"
        f"Title: {c.title}\nCREDIT: {c.creator}\nSource: {c.source}"
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json",
        }, json={"model": CONFIG["claude_model"], "max_tokens": 300,
                 "messages": [{"role": "user", "content": prompt}]}, timeout=30)
        text = r.json()["content"][0]["text"]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        return {k: data[k] for k in ("hook", "x_caption", "tiktok_caption")}
    except Exception as e:  # never let captioning break the run
        print(f"claude drafts failed: {e}")
        return None


def drafts_for(c: Candidate) -> dict:
    d = template_drafts(c)
    d.update(claude_drafts(c) or {})
    return d


# --------------------------------------------------------------------------- output
def discord_payload(c: Candidate, d: dict, rank: int) -> dict:
    age = int(c.age_min)
    age_txt = f"{age} min" if age < 90 else f"{age // 60} h"
    fields = [
        {"name": "Credit", "value": f"[{c.creator}]({c.creator_url})", "inline": True},
        {"name": "Momentum", "value": f"{c.views:,} {'pts' if c.source == 'reddit' else 'views'} · {age_txt} old · {c.velocity:.0f}/min", "inline": True},
        {"name": "Hook (on-screen)", "value": f"`{d['hook']}`"},
        {"name": "X caption", "value": f"```{d['x_caption']}```"},
        {"name": "TikTok / Shorts caption", "value": f"```{d['tiktok_caption']}```"},
        {"name": "Ask permission", "value": f"```{d['permission_dm']}```"},
    ]
    if c.source == "reddit" and c.extra.get("media_url"):
        fields.insert(2, {"name": "Original media", "value": c.extra["media_url"]})
    if c.source == "twitch" and c.duration_s:
        fields.insert(2, {"name": "Length", "value": f"{c.duration_s:.0f}s", "inline": True})
    emb = {
        "title": f"#{rank} [{c.source.upper()}] {c.title[:200]}",
        "url": c.url,
        "color": {"twitch": 0x9146FF, "youtube": 0xFF0000, "reddit": 0xFF4500}.get(c.source, 0xFF2E88),
        "fields": fields,
        "footer": {"text": "Check: no story spoilers · original creator · add your hook · then OpusClip"},
    }
    if c.thumbnail:
        emb["thumbnail"] = {"url": c.thumbnail}
    return {"username": "GTA6Clippingtons Scout", "embeds": [emb]}


def send_discord(payloads: list[dict]) -> None:
    hook = os.getenv("DISCORD_WEBHOOK_URL")
    if not hook:
        print("discord: no webhook set, printing instead")
        for p in payloads:
            print(json.dumps(p, ensure_ascii=False, indent=1))
        return
    for p in payloads:
        r = requests.post(hook, json=p, timeout=20)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)))
            requests.post(hook, json=p, timeout=20)
        time.sleep(0.6)


def append_queue(rows: list[tuple[Candidate, dict]]) -> None:
    QUEUE_PATH.parent.mkdir(exist_ok=True)
    new = not QUEUE_PATH.exists()
    import csv
    with QUEUE_PATH.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["found_at", "source", "title", "url", "creator", "views", "velocity_per_min",
                        "hook", "x_caption", "tiktok_caption", "status"])
        for c, d in rows:
            w.writerow([NOW.isoformat(), c.source, c.title, c.url, c.creator, c.views,
                        round(c.velocity, 1), d["hook"], d["x_caption"], d["tiktok_caption"], "new"])


# --------------------------------------------------------------------------- state
def load_seen() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def save_seen(seen: dict) -> None:
    cutoff = (NOW - timedelta(days=3)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}  # keep file small
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(seen, indent=0, sort_keys=True))


# --------------------------------------------------------------------------- main
def pick(cands: list[Candidate], seen: dict) -> list[Candidate]:
    fresh, why, this_run = [], {}, set()
    for c in cands:
        if c.key in seen or c.key in this_run:
            continue
        this_run.add(c.key)
        r = blocked_reason(c)
        if r:
            why[r] = why.get(r, 0) + 1
            continue
        fresh.append(c)
    if why:
        print("filtered:", why)
    fresh.sort(key=lambda c: c.score, reverse=True)
    # don't flood with one streamer: max N per creator per run
    out, per = [], {}
    for c in fresh:
        if per.get(c.creator, 0) >= CONFIG["max_per_creator_per_run"]:
            continue
        per[c.creator] = per.get(c.creator, 0) + 1
        out.append(c)
        if len(out) >= CONFIG["picks_per_run"]:
            break
    return out


def main() -> int:
    seen = load_seen()
    cands: list[Candidate] = []
    for fn in (fetch_twitch, fetch_youtube, fetch_reddit):
        try:
            cands += fn()
        except Exception as e:  # one source failing shouldn't kill the run
            print(f"{fn.__name__} failed: {e}")
    picks = pick(cands, seen)
    print(f"picked {len(picks)} of {len(cands)}")
    rows = [(c, drafts_for(c)) for c in picks]
    send_discord([discord_payload(c, d, i + 1) for i, (c, d) in enumerate(rows)])
    if rows:
        append_queue(rows)
    for c in picks:
        seen[c.key] = NOW.isoformat()
    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
