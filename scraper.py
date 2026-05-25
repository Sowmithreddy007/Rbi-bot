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

def parse_date_from_cell(cell_text):
    # cell_text might contain extra whitespace/newlines; look for a date pattern
    match = re.search(r'(\d{1,2}\s+\w+\s+\d{4})', cell_text)
    if match:
        date_str = match.group(1)
        for fmt in ["%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"]:
            try:
                return datetime.strptime(date_str, fmt)
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
            # Get the full text content of the first cell
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
        # If we got any items, return them; if none, return empty
        return items
    except Exception as e:
        print(f"⚠️ Error scraping {source['label']}: {e}")
        return []

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
    except Exception as e:
        print(f"⚠️ Error fetching summary from {url}: {e}")
        return ""

def main():
    seen = load_seen()
    today_str = datetime.now().strftime("%d %b %Y")
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraping RBI website...")

    all_items = []
    for src in SOURCES:
        items = scrape_page(src)
        print(f"[DEBUG] Scraped {len(items)} items from {src['label']}")
        for it in items:
            it["tag"] = src["tag"]
            it["category"] = src["label"]
        all_items.extend(items)

    # Convert to unified format
    items = []
    for it in all_items:
        items.append({
            "title": it["title"],
            "link": it["url"],
            "pub_date": it["date"],
            "description": "",
            "category": it["category"],
            "tag": it["tag"]
        })

    if not items:
        send(f"🏦 <b>RBI Bot — {today_str}</b>\n⚠️ Could not fetch updates today.")
        return

    # Identify new items (not in seen.json)
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
        try:
            send(
                f"🏦 <b>RBI Update — {today_str}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"Found <b>{len(new_items)}</b> new item(s) today!\n"
                f"🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>"
            )
        except Exception as e:
            print(f"⚠️ Failed to send header: {e}")

        for idx, item in enumerate(new_items):
            try:
                title = item["title"]
                # Only fetch summary for first 10 to stay within time
                desc = fetch_summary_from_url(item["link"]) if idx < 10 else ""
                summary = clean_summary(desc, title) if desc else ""
                is_exam = any(kw in title.lower() for kw in EXAM_KEYWORDS)
                prefix = "⭐ <b>EXAM-RELEVANT</b>\n" if is_exam else ""
                entities = extract_entities(desc) if desc else []
                affected = f"\n🎯 <b>Affected:</b> {', '.join(entities)}" if entities else ""
                date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "?"
                msg = (
                    f"{item['category']} ({date_str})\n"
                    f"{prefix}"
                    f"<b>{title}</b>"
                )
                if summary:
                    msg += f"\n<i>{summary}</i>"
                msg += f"{affected}\n<a href='{item['link']}'>📄 Read full update</a>"
                send(msg)
            except Exception as e:
                print(f"⚠️ Failed to process item {idx}: {e}")
                traceback.print_exc()
        print(f"✅ Sent {len(new_items)} new items.")
    else:
        # Monthly roundup (include items even if no date)
        cutoff = datetime.now() - timedelta(days=30)
        monthly = []
        for it in items:
            if it["pub_date"] is None:
                # No date? Assume it's recent (top of the listing)
                monthly.append(it)
            elif it["pub_date"] >= cutoff:
                monthly.append(it)

        # Deduplicate
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
                try:
                    date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "?"
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
                except Exception as e:
                    print(f"⚠️ Error building monthly item: {e}")
            if len(monthly) > 10:
                lines.append(f"… and {len(monthly) - 10} more")
            lines.append("")
            lines.append("🔗 <a href='https://www.rbi.org.in'>rbi.org.in</a>")
            msg = f"🏦 <b>RBI Bot — {today_str}</b>\n✅ No new updates today.\n\n" + "\n".join(lines)
            try:
                send(msg)
            except Exception as e:
                print(f"⚠️ Failed to send monthly roundup: {e}")
            print("✅ No new items. Monthly summary sent.")

    save_seen(seen)

if __name__ == "__main__":
    main()
