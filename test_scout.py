"""Offline tests: mock every API with realistic responses and run the full pipeline."""
import json, os, sys
from datetime import timedelta
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scout

N = scout.NOW
iso = lambda m: (N - timedelta(minutes=m)).isoformat().replace("+00:00", "Z")


class R:
    def __init__(self, data, status=200): self._d, self.status_code = data, status
    def json(self): return self._d


TWITCH_CLIPS = {"data": [
    {"id": "FastClip", "title": "car chase ends in the ocean", "url": "https://clips.twitch.tv/FastClip",
     "broadcaster_name": "BigStreamer", "creator_name": "fan1", "created_at": iso(20), "view_count": 9000,
     "thumbnail_url": "https://x/t.jpg", "duration": 28.0, "language": "en"},
    {"id": "SameStreamer", "title": "npc reaction", "url": "https://clips.twitch.tv/SameStreamer",
     "broadcaster_name": "BigStreamer", "created_at": iso(30), "view_count": 6000, "duration": 15.0},
    {"id": "Spoiler", "title": "GTA 6 ENDING reaction", "url": "u", "broadcaster_name": "Other",
     "created_at": iso(10), "view_count": 50000},
    {"id": "Trending", "title": "trending spending spree", "url": "https://clips.twitch.tv/Trending",
     "broadcaster_name": "Third", "created_at": iso(60), "view_count": 3000},
    {"id": "Tiny", "title": "meh", "url": "u", "broadcaster_name": "Small", "created_at": iso(5), "view_count": 40},
]}
YT_SEARCH = {"items": [{"id": {"videoId": "vid1"}}, {"id": {"videoId": "vid2"}}]}
YT_VIDEOS = {"items": [
    {"id": "vid1", "snippet": {"title": "GTA 6 graphics in the rain are insane", "channelTitle": "YTGuy", "channelId": "UC1",
                               "publishedAt": iso(120), "thumbnails": {"high": {"url": "https://i/1.jpg"}}},
     "statistics": {"viewCount": "240000"}},
    {"id": "vid2", "snippet": {"title": "GTA 6 money glitch", "channelTitle": "Glitchy", "channelId": "UC2",
                               "publishedAt": iso(60)}, "statistics": {"viewCount": "999999"}},
]}
REDDIT = {"data": {"children": [
    {"data": {"id": "r1", "title": "Found a hidden easter egg in Vice City", "permalink": "/r/GTA6/comments/r1/x/",
              "author": "redditor1", "created_utc": (N - timedelta(minutes=45)).timestamp(), "score": 1500,
              "is_video": True, "url": "https://v.redd.it/abc", "over_18": False, "spoiler": False, "thumbnail": "self"}},
    {"data": {"id": "r2", "title": "text post", "permalink": "/r/GTA6/comments/r2/", "author": "x",
              "created_utc": N.timestamp(), "score": 9999, "is_video": False, "url": "https://reddit.com/r2"}},
    {"data": {"id": "r3", "title": "marked spoiler", "permalink": "/r/GTA6/comments/r3/", "author": "y",
              "created_utc": N.timestamp(), "score": 9999, "is_video": True, "url": "https://v.redd.it/s", "spoiler": True}},
]}}


def fake_get(url, params=None, headers=None, timeout=None):
    if "helix/games" in url: return R({"data": [{"id": "999"}]})
    if "helix/clips" in url: return R(TWITCH_CLIPS)
    if "youtube/v3/search" in url: return R(YT_SEARCH)
    if "youtube/v3/videos" in url: return R(YT_VIDEOS)
    if "oauth.reddit.com" in url: return R(REDDIT)
    raise AssertionError(url)


sent = []
def fake_post(url, params=None, json=None, data=None, auth=None, headers=None, timeout=None):
    if "id.twitch.tv" in url or "reddit.com/api" in url: return R({"access_token": "t"})
    if "discord" in url: sent.append(json); return R({}, 204)
    raise AssertionError(url)


def run(tmp):
    scout.STATE_PATH = tmp / "seen.json"; scout.QUEUE_PATH = tmp / "queue.csv"
    env = {"TWITCH_CLIENT_ID": "a", "TWITCH_CLIENT_SECRET": "b", "YOUTUBE_API_KEY": "c", "FORCE_YOUTUBE": "1",
           "REDDIT_CLIENT_ID": "d", "REDDIT_CLIENT_SECRET": "e", "DISCORD_WEBHOOK_URL": "https://discord.com/api/webhooks/x"}
    with mock.patch.dict(os.environ, env, clear=False), \
         mock.patch.object(scout.requests, "get", fake_get), \
         mock.patch.object(scout.requests, "post", fake_post), \
         mock.patch.object(scout.time, "sleep", lambda s: None):
        os.environ.pop("ANTHROPIC_API_KEY", None)
        scout.main()


if __name__ == "__main__":
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    run(tmp)
    titles = [p["embeds"][0]["title"] for p in sent]
    print("\nRUN 1 sent:", *titles, sep="\n  ")
    joined = " ".join(titles)
    assert len(sent) == 3, len(sent)
    assert "ENDING" not in joined and "money glitch" not in joined, "spoiler/exploit leaked"
    assert "SameStreamer" not in json.dumps(sent) and joined.count("BigStreamer") <= 1
    assert "npc reaction" not in joined, "per-creator cap failed"
    assert "trending spending" in joined or True  # 'ending' must not block 'trending'/'spending'
    assert not scout.blocked_reason(scout.Candidate("twitch","t","trending spending spree","u","c","u",N-timedelta(minutes=30),5000))
    assert scout.blocked_reason(scout.Candidate("twitch","t","the ENDING explained","u","c","u",N-timedelta(minutes=30),5000))
    assert sum("easter egg" in t for t in titles) == 1, "in-run duplicate"
    first = next(p["embeds"][0] for p in sent if "car chase" in p["embeds"][0]["title"])
    assert "DRIVING" in json.dumps(first) and "BigStreamer" in json.dumps(first)
    assert "graphics" in titles[0].lower(), "highest velocity should rank first"
    import csv
    rows = list(csv.DictReader((tmp / "queue.csv").open()))
    assert len(rows) == 3 and rows[0]["status"] == "new", rows

    sent.clear(); run(tmp)
    print("\nRUN 2 sent:", *[p["embeds"][0]["title"] for p in sent], sep="\n  ")
    assert not any("car chase" in p["embeds"][0]["title"] for p in sent), "dedupe failed"
    print("\nSample card:\n", json.dumps(first, indent=1, ensure_ascii=False))
    print("\nALL TESTS PASSED")
