import os
import json
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ─── Config ──────────────────────────────────────────────
TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
SEEN_FILE = "seen.json"
# ✅ Correct RSS feed URL
RSS_URL  = "https://www.rbi.org.in/Scripts/BS_RSSFeed.aspx"
HEADERS  = {"User-Agent": "Mozilla/5.0 (compatible; RBI-Bot/1.0)"}

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

# ─── Entity extraction patterns ──────────────────────────
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
    except Exception:
        return {"press": [], "circular": [], "notification": [], "whatsnew": []}

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f, indent=2)

def parse_rss_date(text):
    """Parse RSS pubDate like 'Mon, 25 May 2026 12:34:56 +0530'."""
    try:
        # Remove day name and timezone offset
        clean = re.sub(r'^[A-Za-z]{3},\s*', '', text)
        clean = re.sub(r'\s*\+\d{4}$', '', clean)
        return datetime.strptime(clean, "%d %b %Y %H:%M:%S")
    except Exception:
        return None

def extract_entities(text):
    """Find which banks/entities are mentioned in the description."""
    text_lower = text.lower()
    found = []
    for pattern, label in ENTITY_PATTERNS:
        if re.search(pattern, text_lower):
            if label not in found:
                found.append(label)
    return found

def fetch_rss():
    """Return a list of dicts: {title, link, pubDate, description, category, tag} from RSS."""
    try:
        r = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        items = []
        for entry in soup.find_all("item"):
            title = entry.find("title").get_text(strip=True)
            link = entry.find("link").get_text(strip=True)
            pub = entry.find("pubDate")
            pub_date = parse_rss_date(pub.get_text(strip=True)) if pub else None
            desc = entry.find("description")
            description = desc.get_text(strip=True) if desc else ""
            # Clean CDATA / HTML
            description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)
            category_tag = entry.find("category")
            cat = category_tag.get_text(strip=True).lower() if category_tag else ""

            # Map category to our tags
            if "press release" in cat:
                tag = "press"
                label = "📢 Press Release"
            elif "circular" in cat:
                tag = "circular"
                label = "📋 Circular"
            elif "notification" in cat:
                tag = "notification"
                label = "🔔 Notification"
            elif "what's new" in cat or "whatsnew" in cat:
                tag = "whatsnew"
                label = "🌐 What's New"
            else:
                # Fallback by link URL
                if "/BS_PressReleaseDisplay.aspx" in link:
                    tag = "press"
                    label = "📢 Press Release"
                elif "/BS_CircularIndexDisplay.aspx" in link:
                    tag = "circular"
                    label = "📋 Circular"
                elif "/Notifications.aspx" in link:
                    tag = "notification"
                    label = "🔔 Notification"
                else:
                    tag = "whatsnew"
                    label = "🌐 What's New"

            items.append({
                "title": title,
                "link": link,
                "pub_date": pub_date,
                "description": description,
                "category": label,
                "tag": tag
            })
        return items
    except Exception as e:
        print(f"⚠️ Error fetching RSS: {e}")
        return []

def send(text):
    """Send a Telegram message, splitting if needed."""
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
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching RBI RSS feed...")
    items = fetch_rss()
    if not items:
        send(f"🏦 <b>RBI Bot — {today_str}</b>\n⚠️ Could not fetch updates today. The bot will retry tomorrow.")
        return

    # Filter new items (not in seen)
    new_items = []
    for item in items:
        tag = item["tag"]
        if tag not in seen:
            seen[tag] = []
        if item["link"] not in seen[tag]:
            seen[tag].append(item["link"])
            new_items.append(item)

    # ── Daily updates if something new ──────────────────
    if new_items:
        send(
            f"🏦 <b>RBI Update — {today_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
        )
        for item in new_items:
            title = item["title"]
            desc = item["description"]
            if desc.startswith(title):
                desc = desc[len(title):].strip()
            summary = desc[:250] + ("…" if len(desc) > 250 else "")
            is_exam = any(kw in title.lower() for kw in EXAM_KEYWORDS)
            prefix = "⭐ <b>EXAM-RELEVANT</b>\n" if is_exam else ""
            entities = extract_entities(desc)
            affected = f"\n🎯 <b>Affected:</b> {', '.join(entities)}" if entities else ""
            date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "?"
            msg = (
                f"{item['category']} ({date_str})\n"
                f"{prefix}"
                f"<b>{title}</b>\n"
                f"<i>{summary}</i>"
                f"{affected}\n"
                f"<a href='{item['link']}'>📄 Read full update</a>"
            )
            send(msg)
        print(f"✅ Sent {len(new_items)} new items.")
    else:
        # ── Monthly roundup if nothing new ───────────────
        cutoff = datetime.now() - timedelta(days=30)
        monthly = [it for it in items if it["pub_date"] and it["pub_date"] >= cutoff]
        # Deduplicate by link
        unique = {}
        for it in monthly:
            if it["link"] not in unique:
                unique[it["link"]] = it
        monthly = list(unique.values())
        monthly.sort(key=lambda x: x["pub_date"], reverse=True)

        if not monthly:
            send(f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today. No updates in the last 30 days.\nBot is alive! 👀")
            save_seen(seen)
            return

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
            desc = item["description"]
            if desc.startswith(item["title"]):
                desc = desc[len(item["title"]):].strip()
            summary = desc[:200] + ("…" if len(desc) > 200 else "")
            entities = extract_entities(desc)
            affected = f" | Affected: {', '.join(entities)}" if entities else ""
            lines.append(
                f"• {item['category']} ({date_str})\n"
                f"  <b>{item['title']}</b>\n"
                f"  <i>{summary}</i>{affected}\n"
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
