import os
import json
import time
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from urllib.parse import urljoin

# ─── Config ──────────────────────────────────────────────
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE = "seen.json"
BASE    = "https://www.rbi.org.in"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RBI-Bot/1.0)"}

# ─── Exam priority keywords ──────────────────────────────
EXAM_KEYWORDS = [
    "repo rate", "reverse repo", "crr", "slr", "marginal standing facility",
    "monetary policy", "mpc", "monetary policy committee",
    "priority sector", "financial inclusion", "digital lending",
    "upi", "payment system", "banking regulation", "co-operative bank",
    "nbfc", "microfinance", "rbi act", "banking regulation act",
    "br act", "sarfaesi", "insolvency", "ibc", "forex", "foreign exchange",
    "capital adequacy", "cet", "tier", "basel", "prudential",
    "kyc", "aml", "anti money laundering", "ctr", "str",
    "ombudsman", "consumer protection", "dpt", "deposit insurance",
]

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

def is_exam_relevant(title):
    """Return True if the title contains any exam keyword (case-insensitive)."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in EXAM_KEYWORDS)

def _clean_summary(text):
    """Remove the RBI metadata line like 'Press Releases ( 472 kb ) Date : May 25, 2026'."""
    cleaned = re.sub(
        r"^(Press Releases?|Circulars?|Notifications?|What's New)\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s+\d{4}\s*",
        "",
        text
    ).strip()
    # Return first 300 chars
    return cleaned[:300] + ("…" if len(cleaned) > 300 else "")

def fetch_summary(url):
    """
    Scrape the detail page and return the first 300 characters of the
    main article text, without the RBI navigation header or metadata line.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Check if we landed on an error page
        page_text = soup.get_text()
        if "sorry for your inconvenience" in page_text.lower():
            return ""

        # Remove irrelevant elements
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()

        # Strategy 1: Common content containers
        content = soup.select_one(
            "#content, .content, .main-content, .entry-content, .pressrelease, "
            "article, [role='main'], .page-content, .post-content"
        )
        if content:
            text = content.get_text(separator=" ", strip=True)
            text = " ".join(text.split())
            if len(text) > 150:
                return _clean_summary(text)

        # Strategy 2: Column with most text
        cols = soup.select('div[class*="col-"]')
        if cols:
            best = max(cols, key=lambda c: len(c.get_text()))
            text = best.get_text(separator=" ", strip=True)
            text = " ".join(text.split())
            if len(text) > 150:
                return _clean_summary(text)

        # Strategy 3: Fallback body text filtering
        body_text = soup.body.get_text(separator="\n") if soup.body else ""
        lines = [line.strip() for line in body_text.splitlines() if line.strip()]
        skip_keywords = [
            "skip to main content", "search the website", "home", "about us",
            "organisation & functions", "organisation structure", "departments",
            "offices", "training establishment", "college of agricultural banking",
            "reserve bank staff college", "site map", "accessibility", "screen reader",
            "go to navigation", "go to content"
        ]
        clean_lines = []
        for line in lines:
            lower = line.lower()
            if any(kw in lower for kw in skip_keywords):
                continue
            if len(line) < 20:
                continue
            clean_lines.append(line)

        cleaned_text = " ".join(clean_lines)
        cleaned_text = " ".join(cleaned_text.split())
        if cleaned_text:
            return _clean_summary(cleaned_text)

        return ""

    except Exception:
        return ""

def scrape_with_dates(source):
    """Return list of {date, title, url} for top items on a page."""
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []

        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            date_cell = cells[0].get_text(strip=True)
            dt = parse_date(date_cell)
            a_tag = row.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(" ", strip=True)
            if not title or len(title) < 8:
                continue
            if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                continue

            # Properly resolve relative URLs using the listing page as base
            abs_url = urljoin(source["url"], href)

            items.append({
                "date": dt,
                "title": title,
                "url": abs_url
            })
            if len(items) >= 20:
                break

        # If no dates found, fall back: return items without dates (date=None)
        if not any(item["date"] for item in items):
            plain_items = scrape(source)
            return [{"date": None, "title": t, "url": u} for t, u in plain_items]

        return items
    except Exception as e:
        print(f"  ⚠️  Error scraping {source['label']}: {e}")
        return []

def scrape(source):
    """Fallback scraper that returns list of (title, url)."""
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

            abs_url = urljoin(source["url"], href)
            items.append((title, abs_url))
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

# ─── Monthly summary builder (with clean summaries) ──────
def get_monthly_summary():
    """Fetch items from all sources, keep those within last 30 days, return formatted summary."""
    cutoff = datetime.now() - timedelta(days=30)
    all_items = []

    for source in SOURCES:
        items = scrape_with_dates(source)
        for item in items:
            if item["date"] is None or item["date"] >= cutoff:
                all_items.append({**item, "tag": source["tag"], "label": source["label"]})

    # Deduplicate by URL
    seen_urls = set()
    unique = []
    for item in all_items:
        if item["url"] not in seen_urls:
            seen_urls.add(item["url"])
            unique.append(item)

    # Sort by date descending, items with None date go last
    unique.sort(key=lambda x: (x["date"] is not None, x["date"] or datetime.min), reverse=True)

    if not unique:
        return None

    counts = {}
    for item in unique:
        counts[item["label"]] = counts.get(item["label"], 0) + 1

    today = datetime.now().strftime("%d %b %Y")
    lines = [
        f"📊 <b>RBI Monthly Roundup (last 30 days)</b>",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"📆 Period: {cutoff.strftime('%d %b')} – {today}",
        f"📌 Total updates: {len(unique)}",
        "",
    ]
    for label, count in counts.items():
        lines.append(f"{label}: {count} item(s)")
    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🔹 <b>Recent highlights (with summaries):</b>")

    for item in unique[:10]:
        date_str = item["date"].strftime("%d %b") if item["date"] else "?"
        title_short = item["title"][:120]
        summary = fetch_summary(item["url"])
        block = f"• {item['label']} ({date_str})\n  <b>{title_short}</b>"
        if summary:
            # summary already trimmed and cleaned by fetch_summary
            block += f"\n  <i>{summary[:250]}</i>"
        block += f"\n  <a href='{item['url']}'>📄 Read full update</a>"
        lines.append(block)

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

        items = scrape_with_dates(source)
        for item in items:
            url = item["url"]
            if url not in seen[tag]:
                seen[tag].append(url)
                new_items.append({
                    "label": label,
                    "title": item["title"],
                    "url": url,
                    "exam": is_exam_relevant(item["title"])
                })

    if new_items:
        send(
            f"🏦 <b>RBI Update — {today_str}</b>\n"
            f"{'─' * 28}\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>\n"
        )
        for i, item in enumerate(new_items):
            title = item["title"][:200]
            prefix = "⭐ EXAM-RELEVANT: " if item["exam"] else ""
            summary = ""
            if i < 10:   # only fetch summary for first 10 to stay within time
                summary = fetch_summary(item["url"])
                time.sleep(0.5)

            msg = f"{item['label']}\n{prefix}<b>{title}</b>"
            if summary:
                msg += f"\n<i>{summary[:250]}</i>"
            msg += f"\n<a href='{item['url']}'>📄 Read full update</a>"
            send(msg)
        print(f"  ✅ Sent {len(new_items)} new items to Telegram.")
    else:
        monthly_msg = get_monthly_summary()
        if monthly_msg:
            msg = f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today.\n\n" + monthly_msg
            send(msg)
            print("  ✅ No new items. Monthly summary sent.")
        else:
            send(
                f"🏦 <b>RBI Bot — {today_str}</b>\n"
                f"{'─' * 28}\n"
                f"✅ No new updates from RBI today.\n"
                f"No updates in the last 30 days either.\n"
                f"Your bot is alive and watching! 👀"
            )
            print("  ✅ No updates at all. Heartbeat sent.")

    save_seen(seen)
    print("  💾 seen.json updated.")

if __name__ == "__main__":
    main()
