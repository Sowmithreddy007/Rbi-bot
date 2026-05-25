import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime

# ─── Config ──────────────────────────────────────────────
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE = "seen.json"
BASE    = "https://www.rbi.org.in"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RBI-Bot/1.0)"}

# ─── Sources to scrape ───────────────────────────────────
SOURCES = [
    {
        "label": "📢 Press Release",
        "url": f"{BASE}/Scripts/BS_PressReleaseDisplay.aspx",
        "tag": "press"
    },
    {
        "label": "📋 Circular",
        "url": f"{BASE}/Scripts/BS_CircularIndexDisplay.aspx",
        "tag": "circular"
    },
    {
        "label": "🔔 Notification",
        "url": f"{BASE}/Scripts/Notifications.aspx",
        "tag": "notification"
    },
    {
        "label": "🌐 What's New",
        "url": f"{BASE}/Scripts/Whatsnewindisplay.aspx",
        "tag": "whatsnew"
    },
]

# ─── Helpers ─────────────────────────────────────────────
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def scrape(source):
    """Scrape a single RBI listing page and return top items."""
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for a in soup.select("table a[href]"):
            href  = a.get("href", "").strip()
            title = a.get_text(" ", strip=True)
            if not title or len(title) < 8:
                continue
            # Skip navigation/header links
            if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                continue
            if not href.startswith("http"):
                href = BASE + "/" + href.lstrip("/")
            items.append({"title": title, "url": href})
            if len(items) >= 15:   # only check latest 15 per source
                break
        return items
    except Exception as e:
        print(f"  ⚠️  Error scraping {source['label']}: {e}")
        return []

def send(text):
    """Send a Telegram message."""
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  Telegram error: {e}")

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    new_items = []
    today = datetime.now().strftime("%d %b %Y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting RBI scrape...")

    for source in SOURCES:
        tag   = source["tag"]
        label = source["label"]
        print(f"  Scraping: {label}")

        if tag not in seen:
            seen[tag] = []

        items = scrape(source)
        for item in items:
            if item["url"] not in seen[tag]:
                seen[tag].append(item["url"])
                new_items.append({"label": label, **item})

    # ── Send results ──────────────────────────────────────
    if new_items:
        # Header
        send(
            f"🏦 <b>RBI Update — {today}</b>\n"
            f"{'─' * 28}\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
        )
        # One message per new item (keeps it readable on mobile)
        for item in new_items:
            title = item["title"][:200]  # Telegram limit safety
            msg = (
                f"{item['label']}\n"
                f"<b>{title}</b>\n"
                f"<a href='{item['url']}'>📄 Read full update</a>"
            )
            send(msg)
        print(f"  ✅ Sent {len(new_items)} new items to Telegram.")
    else:
        # Daily heartbeat — your brother knows the bot is alive
        send(
            f"🏦 <b>RBI Bot — {today}</b>\n"
            f"{'─' * 28}\n"
            f"✅ No new updates from RBI today.\n"
            f"Your bot is alive and watching! 👀"
        )
        print("  ✅ No new items. Heartbeat sent.")

    save_seen(seen)
    print("  💾 seen.json updated.")

if __name__ == "__main__":
    main()
