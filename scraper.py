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

RSS_URL = "https://www.rbi.org.in/Scripts/BS_RSSFeed.aspx"

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
    text_lower = text.lower()
    found = []
    for pattern, label in ENTITY_PATTERNS:
        if re.search(pattern, text_lower):
            if label not in found:
                found.append(label)
    return found

def clean_summary(raw, title=""):
    if raw.startswith(title):
        raw = raw[len(title):].strip()
    cleaned = re.sub(
        r"^(Press Releases?|Circulars?|Notifications?|What's New)\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s+\d{4}\s*",
        "", raw).strip()
    return cleaned[:250] + ("…" if len(cleaned) > 250 else "")

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

# ─── RSS/Atom feed parser ────────────────────────────────
def parse_rss_date(text):
    try:
        clean = re.sub(r'^[A-Za-z]{3},\s*', '', text)
        clean = re.sub(r'\s*\+\d{4}$', '', clean)
        return datetime.strptime(clean, "%d %b %Y %H:%M:%S")
    except:
        return None

def fetch_via_rss():
    """Fetches and parses RBI feed (works with RSS 2.0 and Atom)."""
    try:
        r = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        print(f"[DEBUG] RSS HTTP status: {r.status_code}, content length: {len(r.content)}")
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        items = []

        # Detect feed type
        if soup.find("feed"):
            print("[DEBUG] Detected Atom feed")
            entries = soup.find_all("entry")
            for entry in entries:
                title_tag = entry.find("title")
                link_tag = entry.find("link", href=True)
                pub_tag = entry.find("published") or entry.find("updated")
                summary_tag = entry.find("summary") or entry.find("content")
                title = title_tag.get_text(strip=True) if title_tag else "No title"
                link = link_tag["href"].strip() if link_tag else ""
                pub_date = parse_rss_date(pub_tag.get_text(strip=True)) if pub_tag else None
                description = summary_tag.get_text(strip=True) if summary_tag else ""
                description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)
                cat_tag = entry.find("category")
                cat = cat_tag.get("term", "").lower() if cat_tag else ""
                # map category
                if "press" in cat: tag, label = "press", "📢 Press Release"
                elif "circular" in cat: tag, label = "circular", "📋 Circular"
                elif "notification" in cat: tag, label = "notification", "🔔 Notification"
                elif "whatsnew" in cat: tag, label = "whatsnew", "🌐 What's New"
                else:
                    if "/BS_PressReleaseDisplay.aspx" in link: tag, label = "press", "📢 Press Release"
                    elif "/BS_CircularIndexDisplay.aspx" in link: tag, label = "circular", "📋 Circular"
                    elif "/Notifications.aspx" in link: tag, label = "notification", "🔔 Notification"
                    else: tag, label = "whatsnew", "🌐 What's New"
                items.append({
                    "title": title, "link": link, "pub_date": pub_date,
                    "description": description, "category": label, "tag": tag
                })
        else:
            print("[DEBUG] Detected RSS 2.0 feed (or unknown XML)")
            # Try to find <item> tags ignoring namespace
            for entry in soup.find_all(re.compile(r"item")):
                title_tag = entry.find("title")
                link_tag = entry.find("link")
                pub_tag = entry.find("pubDate")
                desc_tag = entry.find("description")
                cat_tag = entry.find("category")
                title = title_tag.get_text(strip=True) if title_tag else "No title"
                link = link_tag.get_text(strip=True) if link_tag else ""
                pub_date = parse_rss_date(pub_tag.get_text(strip=True)) if pub_tag else None
                description = desc_tag.get_text(strip=True) if desc_tag else ""
                description = BeautifulSoup(description, "html.parser").get_text(separator=" ", strip=True)
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
                items.append({
                    "title": title, "link": link, "pub_date": pub_date,
                    "description": description, "category": label, "tag": tag
                })
        print(f"[DEBUG] RSS parsed: {len(items)} items")
        return items
    except Exception as e:
        print(f"⚠️ Error fetching RSS: {e}")
        traceback.print_exc()
        return []

# ─── Fallback scraper ────────────────────────────────────
def parse_date_from_cell(text):
    match = re.search(r'(\d{1,2}\s+\w+\s+\d{4})', text)
    if match:
        try:
            return parse_date_string(match.group(1))
        except:
            pass
    return None

def parse_date_string(text):
    text = text.strip().replace(",", "")
    for fmt in ["%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y"]:
        try:
            return datetime.strptime(text, fmt)
        except:
            continue
    return None

def scrape_page(source):
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for row in soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue
            cell_text = cells[0].get_text(" ", strip=True)
            dt = parse_date_from_cell(cell_text)
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
            items.append({"date": dt, "title": title, "url": abs_url})
            if len(items) >= 20:
                break
        if not any(item["date"] for item in items):
            simple_items = []
            for a in soup.select("table a[href]"):
                href = a.get("href", "").strip()
                title = a.get_text(" ", strip=True)
                if not title or len(title) < 8:
                    continue
                if any(skip in href.lower() for skip in ["javascript", "mailto", "#", "home", "sitemap"]):
                    continue
                abs_url = urljoin(source["url"], href)
                simple_items.append({"date": None, "title": title, "url": abs_url})
                if len(simple_items) >= 15:
                    break
            return simple_items
        return items
    except Exception as e:
        print(f"⚠️ Error scraping {source['label']}: {e}")
        return []

def fetch_via_scraping():
    all_items = []
    for src in SOURCES:
        page_items = scrape_page(src)
        print(f"[DEBUG] Scraped {len(page_items)} items from {src['label']}")
        for it in page_items:
            it["tag"] = src["tag"]
            it["category"] = src["label"]
        all_items.extend(page_items)
    result = []
    for it in all_items:
        pub = it.get("date")
        result.append({
            "title": it["title"], "link": it["url"], "pub_date": pub,
            "description": "", "category": it["category"], "tag": it["tag"]
        })
    print(f"[DEBUG] Total scraped items: {len(result)}")
    return result

def fetch_summary_from_url(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        if "sorry for your inconvenience" in soup.get_text().lower():
            return ""
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        content = soup.select_one("#content, article, [role='main'], .pressrelease")
        if content:
            text = content.get_text(separator=" ", strip=True)
            text = " ".join(text.split())
            if len(text) > 150:
                return clean_summary(text)
        cols = soup.select('div[class*="col-"]')
        if cols:
            best = max(cols, key=lambda c: len(c.get_text()))
            text = best.get_text(separator=" ", strip=True)
            text = " ".join(text.split())
            if len(text) > 150:
                return clean_summary(text)
        body = soup.body.get_text(separator="\n") if soup.body else ""
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        skip = ["skip to main content", "search the website", "home", "about us",
                "organisation", "site map", "screen reader"]
        clean_lines = [l for l in lines if not any(k in l.lower() for k in skip) and len(l) > 20]
        text = " ".join(clean_lines)
        return clean_summary(text)
    except:
        return ""

# ─── Main ────────────────────────────────────────────────
def main():
    seen = load_seen()
    today_str = datetime.now().strftime("%d %b %Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Fetching updates...")

    items = fetch_via_rss()
    if not items:
        print("[INFO] RSS failed or empty, falling back to page scraping...")
        items = fetch_via_scraping()

    if not items:
        send(f"🏦 <b>RBI Bot — {today_str}</b>\n⚠️ Could not fetch updates today. The bot will retry tomorrow.")
        return

    new_items = []
    for item in items:
        tag = item["tag"]
        if tag not in seen:
            seen[tag] = []
        if item["link"] not in seen[tag]:
            seen[tag].append(item["link"])
            new_items.append(item)

    if new_items:
        send(
            f"🏦 <b>RBI Update — {today_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"Found <b>{len(new_items)}</b> new item(s) today!\n"
            f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
        )
        for item in new_items:
            title = item["title"]
            desc = item.get("description", "")
            if not desc:
                desc = fetch_summary_from_url(item["link"])
            summary = clean_summary(desc, title)
            is_exam = any(kw in title.lower() for kw in EXAM_KEYWORDS)
            prefix = "⭐ <b>EXAM-RELEVANT</b>\n" if is_exam else ""
            entities = extract_entities(desc) if desc else []
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
                desc = item.get("description", "")
                if not desc:
                    desc = fetch_summary_from_url(item["link"])
                summary = clean_summary(desc, item["title"])
                entities = extract_entities(desc) if desc else []
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
