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

def clean_text(text, title=""):
    """Remove RBI boilerplate, metadata, and repeated titles."""
    # Remove metadata line like "Press Releases ( 153 kb ) Date : May 22, 2026"
    text = re.sub(
        r"^(?:[A-Za-z\s]+)?\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s*,?\s*\d{4}\s*",
        "", text, flags=re.IGNORECASE).strip()
    # Remove leading "Press Release" etc.
    text = re.sub(r"^(Press Releases?|Circulars?|Notifications?|What's New)\s*", "", text, flags=re.IGNORECASE).strip()
    # Navigation junk
    nav = ["skip to main content", "search the website", "home", "notifications index",
           "to rbi circulars index", "site map", "screen reader", "go to navigation",
           "go to content", "about us", "organisation", "functions", "departments"]
    for kw in nav:
        text = re.sub(rf"\b{re.escape(kw)}\b", "", text, flags=re.IGNORECASE)
    # Month/year selection blocks
    text = re.sub(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b", "", text)
    text = re.sub(r"\b\d{4}\s+All\s+Months\b", "", text)
    # Signature lines
    sig = ["yours sincerely", "chief general manager", "principal chief general manager"]
    for kw in sig:
        text = re.sub(rf"\b{re.escape(kw)}\b[^.]*\.?", "", text, flags=re.IGNORECASE)
    # Normalise spaces
    text = re.sub(r'\s+', ' ', text).strip()
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].strip()
    return text

def extract_date_from_page(url):
    """
    Try to find a date on the article page itself.
    Returns a datetime or None.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Remove scripts, styles
        for tag in soup(["script", "style"]):
            tag.decompose()
        # Look for a "Date : ..." pattern
        text = soup.get_text(" ", strip=True)
        match = re.search(r'Date\s*:\s*(\d{1,2}\s+\w+\s*,?\s*\d{4})', text)
        if match:
            date_str = match.group(1).replace(",", "")
            for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                try:
                    return datetime.strptime(date_str, fmt)
                except:
                    continue
    except:
        pass
    return None

def generate_summary_and_date(url, title=""):
    """
    Fetches the full article, cleans it, extracts a summary (first 2 meaningful sentences)
    and also tries to extract the publication date from the page.
    Returns (summary, date_or_None).
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # ---- date extraction from page ----
        page_date = None
        # Find date in meta line
        text = soup.get_text(" ", strip=True)
        m = re.search(r'Date\s*:\s*(\d{1,2}\s+\w+\s*,?\s*\d{4})', text)
        if m:
            ds = m.group(1).replace(",", "")
            for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                try:
                    page_date = datetime.strptime(ds, fmt)
                    break
                except:
                    continue

        # ---- summary extraction ----
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "aside", "form"]):
            tag.decompose()
        content = soup.select_one("#content, article, [role='main'], .pressrelease, .main-content")
        if not content:
            content = soup.body or soup
        text = content.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        text = clean_text(text, title)

        # Pick first 2 meaningful sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        meaningful = []
        for s in sentences:
            s = s.strip()
            if len(s) < 30 or re.search(r'\(\s*\d+\s*kb\s*\)', s):
                continue
            meaningful.append(s)
            if len(meaningful) >= 2:
                break
        summary = " ".join(meaningful)
        if not summary:
            summary = text[:300] + ("…" if len(text) > 300 else "")
        return summary[:500], page_date
    except Exception as e:
        print(f"⚠️ Failed to process page {url}: {e}")
        return "", None

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

# ─── Scraper (only data source) ──────────────────────────
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
            # Try to grab date from the row (may be None)
            dt = None
            for cell in row.find_all(["td", "th"]):
                m = re.search(r'(\d{1,2}\s+\w+\s*,?\s*\d{4})', cell.get_text(" ", strip=True))
                if m:
                    ds = m.group(1).replace(",", "")
                    for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                        try:
                            dt = datetime.strptime(ds, fmt)
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

def fetch_items():
    all_items = []
    for src in SOURCES:
        items = scrape_page(src)
        print(f"[DEBUG] Scraped {len(items)} items from {src['label']}")
        for it in items:
            it["tag"] = src["tag"]
            it["category"] = src["label"]
        all_items.extend(items)
    # Enrich with summary and page date
    enriched = []
    for it in all_items:
        summary, page_date = generate_summary_and_date(it["url"], it["title"])
        # Use page_date if row date was missing
        final_date = it["date"] if it["date"] else page_date
        enriched.append({
            "title": it["title"],
            "link": it["url"],
            "pub_date": final_date,
            "description": summary,
            "category": it["category"],
            "tag": it["tag"]
        })
    return enriched

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    today_str = datetime.now().strftime("%d %b %Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping RBI website...")

    items = fetch_items()
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
            summary = clean_text(item.get("description", ""), title)
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
        cutoff = datetime.now() - timedelta(days=30)
        monthly = [it for it in items if it["pub_date"] is None or it["pub_date"] >= cutoff]
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
                summary = clean_text(item.get("description", ""), item["title"])
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
