# GTA6Clippingtons clip scout

Every 10 minutes this checks Twitch, YouTube and Reddit for fresh GTA 6 clips. It ranks them by how fast they're gaining views and sends the top 3 new ones to a Discord channel on your phone. Each card has:

- the link to the original post, plus the creator to credit
- its momentum (views, age, views per minute)
- an on-screen hook, an X caption and a TikTok/Shorts caption
- a message you can send the creator to ask permission

It **never downloads or posts anything**. You tap a card, send the permission message if needed, paste the link into OpusClip, and schedule the post.

Every source is optional. If a source's key isn't set, that source is skipped.

---

## Setup (about 30 minutes, easier on a computer)

### 1. Discord (where the cards show up)
1. Create a Discord server (for example "GTA6Clippingtons") with a channel called `#clips`.
2. Open the channel settings, then **Integrations → Webhooks → New Webhook → Copy Webhook URL**.
3. Turn on notifications for that channel in the Discord phone app.

### 2. Twitch keys (the best source: live-stream clips)
1. Go to https://dev.twitch.tv/console/apps and click **Register Your Application**.
2. Fill in: Name `gta6-clip-scout`, OAuth Redirect URL `http://localhost`, Category `Other`, Client type `Confidential`.
3. Copy the **Client ID**, then click **New Secret** and copy the **Client Secret**.

### 3. YouTube key
1. Go to https://console.cloud.google.com, create a project, and enable **YouTube Data API v3**.
2. Go to **Credentials → Create credentials → API key**, and restrict the key to YouTube Data API v3.
3. The free quota is 10,000 units a day, and each search costs about 100. That's why YouTube only searches once an hour: 2 queries × 24 runs ≈ 4,800 units. 

### 4. Reddit keys (optional)
1. Go to https://www.reddit.com/prefs/apps and create an app of type **script**.
2. Copy the ID shown under the app name, and copy the secret.
3. Reddit has tightened API access, so new apps may need approval. If that happens, skip Reddit; the scout runs fine without it.

### 5. Claude-written captions (optional, costs a few cents a day)
Add an `ANTHROPIC_API_KEY` from https://console.anthropic.com. Without it, captions come from built-in templates. 

### 6. Put it on GitHub (free scheduler)
1. Create a **public** repository, for example `gta6-clip-scout`. Actions are free and unlimited on public repos. A private repo on the free plan gets 2,000 minutes a month, and every-10-minute runs use about 4,300.
2. Upload every file in this folder, keeping the `.github/workflows/scout.yml` path.
3. Go to **Settings → Secrets and variables → Actions → New repository secret** and add each key you have:
   `DISCORD_WEBHOOK_URL`, `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YOUTUBE_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `ANTHROPIC_API_KEY`
   Secrets stay hidden even though the repo is public.
4. In the **Actions** tab, enable workflows, open **clip-scout**, and click **Run workflow** to test. Cards should appear in Discord within about a minute.

After that it runs every 10 minutes on its own. GitHub sometimes starts scheduled runs a few minutes late when it's busy.

---

## Tuning (`config.json`)
| Setting | What it does |
|---|---|
| `picks_per_run` | cards per run (3 means up to about 18 an hour) |
| `min_views` | ignores small clips, per source |
| `max_age_hours` | only clips newer than this |
| `block_keywords` | spoilers, leaks, exploits, NSFW, "play early" tricks. Add to this list freely |
| `block_creators` | creators who asked you not to use their stuff (log takedown requests here) |
| `youtube_queries` | what to search on YouTube |
| `source_weights` | boost or demote a source in the ranking |

## Before you post a card
- Watch the clip. Titles lie, and the keyword filter won't catch every story spoiler.
- Make sure the credit is the **original** creator, not a repost account. On X, swap in their @handle.
- When you can, ask permission first (the message is on the card) and favor creators who say yes.
- Add your own hook and captions in OpusClip. Plain reuploads get buried or struck.

## Test offline
`python test_scout.py` runs the full pipeline against mocked API responses.
