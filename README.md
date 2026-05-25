# 🏦 RBI Telegram Notification Bot

Sends daily RBI updates (press releases, circulars, notifications, what's new) to Telegram at **8:00 AM IST**. Fully free — no server, no cost.

---

## What it monitors

| Source | What it covers |
|---|---|
| 📢 Press Releases | Daily RBI announcements |
| 📋 Circulars | Policy guidelines & master circulars |
| 🔔 Notifications | Regulatory orders |
| 🌐 What's New | All-in-one latest section |

---

## One-time setup (15 minutes)

### Step 1 — Create your Telegram bot
1. Open Telegram, search for **@BotFather**
2. Send `/newbot`, follow the prompts
3. Copy the **HTTP API token** it gives you (looks like `123456:ABC-DEF...`)

### Step 2 — Get your Chat ID
1. Message **@userinfobot** on Telegram
2. It replies with your numeric **id** — that's your `CHAT_ID`

> **Tip:** If you want the bot to post to a group instead, add the bot to the group and use the group's negative ID as `CHAT_ID`.

### Step 3 — Create a GitHub repository
1. Go to github.com → New repository (can be private)
2. Name it anything, e.g. `rbi-bot`

### Step 4 — Push these files
```
rbi-bot/
├── .github/
│   └── workflows/
│       └── rbi_bot.yml
├── scraper.py
├── seen.json
└── README.md
```

### Step 5 — Add secrets to GitHub
1. Go to your repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** and add:
   - `TELEGRAM_BOT_TOKEN` → paste your BotFather token
   - `TELEGRAM_CHAT_ID` → paste your numeric Chat ID

### Step 6 — Test it manually
1. Go to **Actions** tab in your repo
2. Click **RBI Notification Bot** → **Run workflow**
3. You should receive a Telegram message within ~30 seconds

---

## How it works

```
GitHub Actions (cron 8AM IST)
        ↓
   scraper.py runs
        ↓
Fetches 4 RBI pages → finds new links
        ↓
Compares vs seen.json (already-sent list)
        ↓
Sends new items → Telegram Bot API → your phone
        ↓
Commits updated seen.json back to repo
```

The `seen.json` file is the bot's memory — it stores URLs of items already sent, so you never get duplicates.

---

## Free tier limits

| Service | Free limit | This bot uses |
|---|---|---|
| GitHub Actions | 2,000 min/month (public) / 500 min (private) | ~1 min/day = ~30 min/month |
| Telegram Bot API | Unlimited | Free |

Easily fits within free limits.
