import os
import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

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

def parse_date(text):
    """Parse RBI date format like 'May 24, 2026' or '24 May 2026'."""
    text = text.strip().replace(",", "")
    formats = ["%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y"]
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None

def scrape_with_dates(source):
    """Return list of {date, title, url} for top items on a page."""
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []

        # Most RBI listing pages use a table with rows containing date + link
        # Common pattern: <tr> with <td class="tableheader">date</td> then <td> <a>title</a> </td>
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            # Try to find a date in the first cell
            date_cell = cells[0].get_text(strip=True)
            dt = parse_date(date_cell)
            # Look for a link in any cell
            a_tag = row.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(" ", strip=True)
            if not title or len(title) < 8:
                continue
            # Clean URL
            if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                continue
            if not href.startswith("http"):
                href = BASE + "/" + href.lstrip("/")

            items.append({
                "date": dt,
                "title": title,
                "url": href
            })
            if len(items) >= 20:   # grab enough for weekly recap
                break

        # If no dates found, fall back: return items without dates (date=None)
        if not any(item["date"] for item in items):
            # Just scrape titles/urls without dates
            plain_items = scrape(source)
            return [{"date": None, "title": t, "url": u} for t, u in plain_items]

        return items
    except Exception as e:
        print(f"  ⚠️  Error scraping {source['label']}: {e}")
        return []

def scrape(source):
    """Original scraper (kept for backward compat) returns list of (title, url)."""
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
            if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                continue
            if not href.startswith("http"):
                href = BASE + "/" + href.lstrip("/")
            items.append((title, href))
            if len(items) >= 15:
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

# ─── Weekly summary builder ──────────────────────────────
def get_weekly_summary():
    """Fetch items from all sources, keep those within last 7 days, return formatted summary string."""
    cutoff = datetime.now() - timedelta(days=7)
    all_items = []

    for source in SOURCES:
        items = scrape_with_dates(source)
        for item in items:
            if item["date"] and item["date"] >= cutoff:
                all_items.append({**item, "tag": source["tag"], "label": source["label"]})

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for item in all_items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique.append(item)

    # Sort by date descending
    unique.sort(key=lambda x: x["date"], reverse=True)

    if not unique:
        return None

    # Count by category
    counts = {}
    for item in unique:
        counts[item["label"]] = counts.get(item["label"], 0) + 1

    # Build message
    today = datetime.now().strftime("%d %b %Y")
    lines = [
        f"📊 <b>RBI Weekly Roundup (last 7 days)</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"📆 Period: {cutoff.strftime('%d %b')} – {today}",
        f"📌 Total updates: {len(unique)}",
        "",
    ]
    for label, count in counts.items():
        lines.append(f"{label}: {count} item(s)")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🔹 <b>Recent highlights:</b>")

    # Show the 10 most recent items
    for item in unique[:10]:
        date_str = item["date"].strftime("%d %b") if item["date"] else "?"
        title_short = item["title"][:120]
        lines.append(
            f"• {item['label']} ({date_str})\n"
            f"  <a href='{item['url']}'>{title_short}</a>"
        )

    if len(unique) > 10:
        lines.append(f"… and {len(unique) - 10} more")

    lines.append("")
    lines.append(f"🔗 <a href='https://www.rbi.org.in'>Visit rbi.org.in</a>")

    return "\n".join(lines)

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    new_items = []
    today_str = datetime.now().strftime("%d %b %Y")

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting RBI scrape...")

    for source in SOURCES:
        tag   = source["tag"]
        label = source["label"]
        print(f"  Scraping: {label}")

        if tag not in seen:
            seen[tag] = []

        # Use scraper that gives dates, but we only care about URL for new detection
        items = scrape_with_dates(source)
        for item in items:
            url = item["url"]
            if url not in seen[tag]:
                seen[tag].append(url)
                new_items.append({"label": label, "title": item["title"], "url": url})

    # ── Send daily results ────────────────────────────────
    if new_items:
        # Header
        send(
            f"🏦 <b>RBI Update — {today_str}</b>\n"
            f"{'─' * 28}\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
        )
        for item in new_items:
            title = item["title"][:200]
            msg = (
                f"{item['label']}\n"
                f"<b>{title}</b>\n"
                f"<a href='{item['url']}'>📄 Read full update</a>"
            )
            send(msg)
        print(f"  ✅ Sent {len(new_items)} new items to Telegram.")
    else:
        # No new updates → send weekly roundup
        weekly_msg = get_weekly_summary()
        if weekly_msg:
            # Prepend a note that today there were no new items
            msg = f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today.\n\n" + weekly_msg
            send(msg)
            print("  ✅ No new items. Weekly summary sent.")
        else:
            # Even weekly summary came back empty (unlikely but possible)
            send(
                f"🏦 <b>RBI Bot — {today_str}</b>\n"
                f"{'─' * 28}\n"
                f"✅ No new updates from RBI today.\n"
                f"No updates in the last 7 days either.\n"
                f"Your bot is alive and watching! 👀"
            )
            print("  ✅ No updates at all. Heartbeat sent.")

    save_seen(seen)
    print("  💾 seen.json updated.")

if __name__ == "__main__":
    main()
