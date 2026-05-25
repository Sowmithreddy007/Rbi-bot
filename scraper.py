import os
import json
import re
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

def parse_date(text):
    """Parse a date string with multiple formats."""
    text = text.replace(",", "").strip()
    for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
        try:
            return datetime.strptime(text, fmt)
        except:
            continue
    return None

def fetch_detail(url):
    """
    Fetch the detail page and return (date, summary_text).
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # --- Extract date ---
        page_date = None
        # Look for "Date : 25 May 2026" pattern
        date_match = re.search(r'Date\s*:\s*(\d{1,2}\s+\w+\s*,?\s*\d{4})', soup.get_text())
        if date_match:
            page_date = parse_date(date_match.group(1))

        # --- Extract summary (first 2 meaningful sentences) ---
        # Remove nav, scripts, etc.
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()

        # Get main content
        content = soup.select_one("#content, article, [role='main'], .pressrelease")
        if not content:
            content = soup.body or soup
        text = content.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()

        # Clean boilerplate
        # Remove metadata line "Press Releases ( 153 kb ) Date : May 22, 2026"
        text = re.sub(r"Press Releases?\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s*,?\s*\d{4}\s*", "", text)
        # Remove other similar lines
        text = re.sub(r"Circulars?\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s*,?\s*\d{4}\s*", "", text)
        # Normalise
        text = re.sub(r'\s+', ' ', text).strip()

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        meaningful = []
        for s in sentences:
            s = s.strip()
            # skip short, metadata or navigation
            if len(s) < 30 or re.search(r'\(\s*\d+\s*kb\s*\)', s):
                continue
            meaningful.append(s)
            if len(meaningful) >= 2:
                break

        summary = " ".join(meaningful)
        if not summary:
            summary = text[:300] + ("…" if len(text) > 300 else "")

        return page_date, summary[:500]
    except Exception as e:
        print(f"⚠️ Detail error for {url}: {e}")
        return None, ""

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

# ─── Scrape listing pages ───────────────────────────────
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
            abs_url = urljoin(source["url"], href)
            items.append({"title": title, "url": abs_url, "tag": source["tag"], "category": source["label"]})
            if len(items) >= 20:
                break
        return items
    except Exception as e:
        print(f"⚠️ Error scraping {source['label']}: {e}")
        return []

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    today_str = datetime.now().strftime("%d %b %Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping listing pages...")

    all_items = []
    for src in SOURCES:
        items = scrape_page(src)
        print(f"[DEBUG] {src['label']}: {len(items)} items")
        all_items.extend(items)

    # Enrich with detail data
    enriched = []
    for item in all_items:
        dt, summary = fetch_detail(item["url"])
        # Use date from detail if available, else try to find in title (rare)
        pub_date = dt
        enriched.append({
            "title": item["title"],
            "link": item["url"],
            "pub_date": pub_date,
            "description": summary,
            "category": item["category"],
            "tag": item["tag"]
        })

    if not enriched:
        send(f"🏦 <b>RBI Bot — {today_str}</b>\n⚠️ Could not fetch updates today.")
        return

    new_items = []
    for item in enriched:
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
            summary = item.get("description", "")
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
        monthly = [it for it in enriched if it["pub_date"] is None or it["pub_date"] >= cutoff]
        unique = {}
        for it in monthly:
            if it["link"] not in unique:
                unique[it["link"]] = it
        monthly = list(unique.values())
        monthly.sort(key=lambda x: (x["pub_date"] is not None, x["pub_date"] or datetime.min), reverse=True)

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
                date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "Recent"
                summary = item.get("description", "")
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
