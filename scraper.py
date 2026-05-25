import os
import json
import re
import traceback
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ─── Config ──────────────────────────────────────────────
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE = "seen.json"
BASE    = "https://www.rbi.org.in"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; RBI-Bot/1.0)"}

# Primary data source: RSS feed (always used)
RSS_URL = "https://www.rbi.org.in/Scripts/BS_RSSFeed.aspx"

# Fallback scraping sources
SOURCES = [
    {"label": "📢 Press Release", "url": f"{BASE}/Scripts/BS_PressReleaseDisplay.aspx", "tag": "press"},
    {"label": "📋 Circular", "url": f"{BASE}/Scripts/BS_CircularIndexDisplay.aspx", "tag": "circular"},
    {"label": "🔔 Notification", "url": f"{BASE}/Scripts/Notifications.aspx", "tag": "notification"},
    {"label": "🌐 What's New", "url": f"{BASE}/Scripts/Whatsnewindisplay.aspx", "tag": "whatsnew"},
]

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

ENTITY_PATTERNS = [
    (r"all\s+scheduled\s+commercial\s+banks", "All Scheduled Commercial Banks"),
    (r"all\s+banks", "All Banks"),
    (r"urban\s+co-operative\s+banks?", "Urban Co-operative Banks"),
    (r"state\s+co-operative\s+banks?", "State Co-operative Banks"),
    (r"primary\s*\(\s*urban\s*\)\s*co-operative\s+banks?", "Primary (Urban) Co-operative Banks"),
    (r"non-banking\s+financial\s+companies?", "NBFCs"),
    (r"nbfcs?", "NBFCs"),
    (r"payment\s+system\s+operators?", "Payment System Operators"),
    (r"payment\s+banks?", "Payment Banks"),
    (r"small\s+finance\s+banks?", "Small Finance Banks"),
    (r"regional\s+rural\s+banks?", "Regional Rural Banks"),
    (r"asset\s+reconstruction\s+companies?", "Asset Reconstruction Companies"),
    (r"housing\s+finance\s+companies?", "Housing Finance Companies"),
    (r"co-operative\s+banks?", "Co-operative Banks"),
    (r"scheduled\s+banks", "Scheduled Banks"),
]

# ─── Helpers ─────────────────────────────────────────────
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return json.load(f)
    except:
        return {"press": [], "circular": [], "notification": [], "whatsnew": []}

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def extract_entities(text):
    if not text:
        return []
    text_lower = text.lower()
    found = []
    for pattern, label in ENTITY_PATTERNS:
        if re.search(pattern, text_lower):
            if label not in found:
                found.append(label)
    return found

def clean_summary(raw, title=""):
    """Remove RSS metadata line and repeated titles."""
    # Remove leading "Press Releases (xxx kb) Date : ..."
    cleaned = re.sub(
        r"^(Press Releases?|Circulars?|Notifications?|What's New)\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s*,?\s*\d{4}\s*",
        "", raw, flags=re.IGNORECASE).strip()
    # Remove standalone "Press Release"
    cleaned = re.sub(r"^(Press Releases?)\s*", "", cleaned, flags=re.IGNORECASE).strip()
    if title and cleaned.lower().startswith(title.lower()):
        cleaned = cleaned[len(title):].strip()
    return cleaned[:500]  # keep Telegram-friendly length

def parse_rss_date(text):
    """Parse RSS pubDate like 'Mon, 25 May 2026 12:34:56 +0530'."""
    try:
        clean = re.sub(r'^[A-Za-z]{3},\s*', '', text)
        clean = re.sub(r'\s*\+\d{4}$', '', clean)
        return datetime.strptime(clean, "%d %b %Y %H:%M:%S")
    except:
        return None

def fetch_rss():
    """Return clean items from RBI RSS feed (Atom or RSS 2.0)."""
    try:
        r = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        items = []

        # Detect if it's Atom (<feed>) or RSS (<rss>)
        if soup.find("feed"):
            entries = soup.find_all("entry")
            for entry in entries:
                title_tag = entry.find("title")
                link_tag = entry.find("link", href=True)
                pub_tag = entry.find("published") or entry.find("updated")
                desc_tag = entry.find("summary") or entry.find("content")
                title = title_tag.get_text(strip=True) if title_tag else "No title"
                link = link_tag["href"].strip() if link_tag else ""
                pub_date = parse_rss_date(pub_tag.get_text(strip=True)) if pub_tag else None
                description = desc_tag.get_text(strip=True) if desc_tag else ""
                # Clean HTML entities
                description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)
                # Category mapping
                cat_tag = entry.find("category")
                cat = cat_tag.get("term", "").lower() if cat_tag else ""
                if "press" in cat: tag, label = "press", "📢 Press Release"
                elif "circular" in cat: tag, label = "circular", "📋 Circular"
                elif "notification" in cat: tag, label = "notification", "🔔 Notification"
                elif "whatsnew" in cat: tag, label = "whatsnew", "🌐 What's New"
                else:
                    if "/BS_PressReleaseDisplay.aspx" in link: tag, label = "press", "📢 Press Release"
                    elif "/BS_CircularIndexDisplay.aspx" in link: tag, label = "circular", "📋 Circular"
                    elif "/Notifications.aspx" in link: tag, label = "notification", "🔔 Notification"
                    else: tag, label = "whatsnew", "🌐 What's New"
                items.append({"title": title, "link": link, "pub_date": pub_date, "description": description, "category": label, "tag": tag})
        else:  # RSS 2.0
            for entry in soup.find_all("item"):
                title = entry.find("title").get_text(strip=True)
                link = entry.find("link").get_text(strip=True)
                pub = entry.find("pubDate")
                pub_date = parse_rss_date(pub.get_text(strip=True)) if pub else None
                desc = entry.find("description").get_text(strip=True) if entry.find("description") else ""
                desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ", strip=True)
                cat_tag = entry.find("category")
                cat = cat_tag.get_text(strip=True).lower() if cat_tag else ""
                if "press" in cat: tag, label = "press", "📢 Press Release"
                elif "circular" in cat: tag, label = "circular", "📋 Circular"
                elif "notification" in cat: tag, label = "notification", "🔔 Notification"
                elif "whatsnew" in cat: tag, label = "whatsnew", "🌐 What's New"
                else:
                    if "/BS_PressReleaseDisplay.aspx" in link: tag, label = "press", "📢 Press Release"
                    elif "/BS_CircularIndexDisplay.aspx" in link: tag, label = "circular", "📋 Circular"
                    elif "/Notifications.aspx" in link: tag, label = "notification", "🔔 Notification"
                    else: tag, label = "whatsnew", "🌐 What's New"
                items.append({"title": title, "link": link, "pub_date": pub_date, "description": desc, "category": label, "tag": tag})
        print(f"[RSS] Parsed {len(items)} items")
        return items
    except Exception as e:
        print(f"⚠️ RSS failed: {e}")
        return []

# ─── Fallback scraper (only used if RSS fails) ───────────
def scrape_page(source):
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for row in soup.select("table tr"):
            a_tag = row.find("a", href=True)
            if not a_tag:
                continue
            href = a_tag.get("href", "").strip()
            title = a_tag.get_text(" ", strip=True)
            if not title or len(title) < 8:
                continue
            if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                continue
            # Try to find a date in the row
            dt = None
            for cell in row.find_all(["td", "th"]):
                match = re.search(r'(\d{1,2}\s+\w+\s*,?\s*\d{4})', cell.get_text(" ", strip=True))
                if match:
                    date_str = match.group(1).replace(",", "")
                    for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                        try:
                            dt = datetime.strptime(date_str, fmt)
                            break
                        except:
                            continue
                    if dt:
                        break
            abs_url = urljoin(source["url"], href)
            items.append({"date": dt, "title": title, "url": abs_url})
            if len(items) >= 20:
                break
        return items
    except Exception as e:
        print(f"⚠️ Error scraping {source['label']}: {e}")
        return []

def fetch_via_scraping():
    all_items = []
    for src in SOURCES:
        items = scrape_page(src)
        for it in items:
            it["tag"] = src["tag"]
            it["category"] = src["label"]
        all_items.extend(items)
    # Convert to RSS-like format
    result = []
    for it in all_items:
        result.append({
            "title": it["title"],
            "link": it["url"],
            "pub_date": it["date"],
            "description": "",   # no summary in fallback; we'll skip display
            "category": it["category"],
            "tag": it["tag"]
        })
    return result

def send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    MAX_LEN = 4000
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN-3] + "…"
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
        print(f"⚠️ Telegram error: {e}")

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    today_str = datetime.now().strftime("%d %b %Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching updates via RSS...")

    items = fetch_rss()
    if not items:
        print("[INFO] RSS failed, falling back to scraping...")
        items = fetch_via_scraping()

    if not items:
        send(f"🏦 <b>RBI Bot — {today_str}</b>\n⚠️ Could not fetch updates today.")
        return

    new_items = []
    for item in items:
        tag = item["tag"]
        if tag not in seen:
            seen[tag] = []
        if item["link"] not in seen[tag]:
            seen[tag].append(item["link"])
            new_items.append(item)

    print(f"[DEBUG] New items: {len(new_items)}")

    if new_items:
        send(
            f"🏦 <b>RBI Update — {today_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
        )
        for item in new_items:
            title = item["title"]
            # Use RSS description as summary (already clean)
            summary = clean_summary(item.get("description", ""), title)
            is_exam = any(kw in title.lower() for kw in EXAM_KEYWORDS)
            prefix = "⭐ <b>EXAM-RELEVANT</b>\n" if is_exam else ""
            entities = extract_entities(summary) if summary else extract_entities(title)
            affected = f"\n🎯 <b>Affected:</b> {', '.join(entities)}" if entities else ""
            date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "Recent"
            msg = f"{item['category']} ({date_str})\n{prefix}<b>{title}</b>"
            if summary:
                msg += f"\n📌 <i>{summary}</i>"
            msg += f"{affected}\n<a href='{item['link']}'>📄 Read full update</a>"
            send(msg)
        print(f"✅ Sent {len(new_items)} new items.")
    else:
        # Monthly roundup
        cutoff = datetime.now() - timedelta(days=30)
        monthly = [it for it in items if it["pub_date"] and it["pub_date"] >= cutoff]
        unique = {}
        for it in monthly:
            if it["link"] not in unique:
                unique[it["link"]] = it
        monthly = list(unique.values())
        monthly.sort(key=lambda x: x["pub_date"], reverse=True)

        if not monthly:
            send(f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today. No updates in the last 30 days.\nBot is alive! 👀")
        else:
            counts = {}
            for it in monthly:
                counts[it["category"]] = counts.get(it["category"], 0) + 1
            lines = [
                f"📊 <b>RBI Monthly Roundup (last 30 days)</b>",
                "━━━━━━━━━━━━━━━━━━━━━",
                f"📆 Period: {cutoff.strftime('%d %b')} – {today_str}",
                f"📌 Total updates: {len(monthly)}",
                "",
            ]
            for cat, cnt in counts.items():
                lines.append(f"{cat}: {cnt} item(s)")
            lines.append("")
            lines.append("━━━━━━━━━━━━━━━━━━━━━")
            lines.append("🔹 <b>Recent highlights:</b>")
            for item in monthly[:10]:
                date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "?"
                summary = clean_summary(item.get("description", ""), item["title"])
                entities = extract_entities(summary) if summary else extract_entities(item["title"])
                affected = f" | Affected: {', '.join(entities)}" if entities else ""
                lines.append(
                    f"• {item['category']} ({date_str})\n"
                    f"  <b>{item['title']}</b>\n"
                    f"  📌 <i>{summary}</i>{affected}\n"
                    f"  <a href='{item['link']}'>📄 Read more</a>"
                )
            if len(monthly) > 10:
                lines.append(f"… and {len(monthly) - 10} more")
            lines.append("")
            lines.append("🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>")
            msg = f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today.\n\n" + "\n".join(lines)
            send(msg)
            print("✅ No new items. Monthly summary sent.")

    save_seen(seen)

if __name__ == "__main__":
    main()
