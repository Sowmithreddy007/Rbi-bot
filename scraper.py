import os
import json
import re
import traceback
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words
from sumy.nlp.stemmers import Stemmer

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

LANGUAGE = "english"
SENTENCES_COUNT = 3   # slightly longer summary now that we have clean text

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
    """
    Aggressively remove any RBI metadata line and repeated titles.
    """
    # Remove lines like "Press Releases ( 153 kb ) Date : May 22, 2026" or just "( 153 kb ) Date : May 22, 2026"
    text = re.sub(
        r"^(?:[A-Za-z\s]+)?\s*\(\s*\d+\s*kb\s*\)\s*Date\s*:\s*\d{1,2}\s+\w+\s*,?\s*\d{4}\s*",
        "", text, flags=re.IGNORECASE).strip()
    # Remove any standalone "Press Release" at the beginning
    text = re.sub(r"^(Press Releases?)\s*", "", text, flags=re.IGNORECASE).strip()
    # If title appears again at the start (after cleaning), remove it
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].strip()
    return text

def extract_article_content(url):
    """
    Fetch the article page, extract the main body text without navigation,
    and return cleaned text.
    """
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        # Remove irrelevant tags
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        # Try to get main content area
        content = soup.select_one("#content, article, [role='main'], .pressrelease")
        if not content:
            content = soup
        # Extract text and normalize spaces
        text = content.get_text(separator=" ", strip=True)
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        print(f"⚠️ Could not extract article content: {e}")
        return ""

def generate_summary(url, title=""):
    """
    Generate a true summary of the full article content.
    Uses sumy on cleaned full text; falls back to a smart first-two-sentences extract.
    """
    # Fetch and clean the full article
    full_text = extract_article_content(url)
    if not full_text:
        return ""

    # Remove any remaining metadata line from the full text
    cleaned_full = clean_text(full_text, title)

    # Try AI summary via sumy using the cleaned plain text
    try:
        parser = PlaintextParser.from_string(cleaned_full, Tokenizer(LANGUAGE))
        stemmer = Stemmer(LANGUAGE)
        summarizer = LsaSummarizer(stemmer)
        summarizer.stop_words = get_stop_words(LANGUAGE)
        sentences = summarizer(parser.document, SENTENCES_COUNT)
        # Keep the original order
        ordered = [str(s) for s in parser.document.sentences if s in sentences]
        summary = " ".join(ordered)
        summary = re.sub(r'\s+', ' ', summary).strip()
        # Final cleaning in case sumy picked up any leftover junk
        summary = clean_text(summary, title)
        if summary:
            return summary[:600]
    except Exception as e:
        print(f"⚠️ Sumy summarization failed, using fallback: {e}")

    # Fallback: grab the first two non‑trivial sentences from the cleaned text
    sentences = re.split(r'(?<=[.!?])\s+', cleaned_full)
    meaningful = []
    for sent in sentences:
        s = sent.strip()
        # Skip very short sentences and those still containing metadata markers
        if len(s) < 30 or re.search(r'\(\s*\d+\s*kb\s*\)', s):
            continue
        meaningful.append(s)
        if len(meaningful) >= 2:
            break
    fallback = " ".join(meaningful)
    return fallback[:500] if fallback else cleaned_full[:300]

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

def find_date_in_row(row):
    """
    Scan all cells in a row for a date. Returns a datetime or None.
    """
    date_regex = re.compile(r'(\d{1,2}\s+\w+\s*,?\s*\d{4})')
    for cell in row.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)
        match = date_regex.search(text)
        if match:
            date_str = match.group(1).replace(",", "")
            for fmt in ("%d %B %Y", "%B %d %Y", "%d %b %Y", "%b %d %Y"):
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
    return None

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
            dt = find_date_in_row(row)
            abs_url = urljoin(source["url"], href)
            items.append({"date": dt, "title": title, "url": abs_url})
            if len(items) >= 20:
                break
        return items
    except Exception as e:
        print(f"⚠️ Error scraping {source['label']}: {e}")
        return []

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

    items = []
    for it in all_items:
        items.append({
            "title": it["title"],
            "link": it["url"],
            "pub_date": it["date"],
            "category": it["category"],
            "tag": it["tag"]
        })

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
                # Generate summary using full article content (for all items now, not just first 10)
                summary = generate_summary(item["link"], title)
                is_exam = any(kw in title.lower() for kw in EXAM_KEYWORDS)
                prefix = "⭐ <b>EXAM-RELEVANT</b>\n" if is_exam else ""
                entities = extract_entities(summary) if summary else extract_entities(title)
                affected = f"\n🎯 <b>Affected:</b> {', '.join(entities)}" if entities else ""
                date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "Recent"
                msg = (
                    f"{item['category']} ({date_str})\n"
                    f"{prefix}"
                    f"<b>{title}</b>"
                )
                if summary:
                    summary_display = clean_text(summary, title)   # final safety
                    if summary_display:
                        msg += f"\n📌 <i>{summary_display}</i>"
                msg += f"{affected}\n<a href='{item['link']}'>📄 Read full update</a>"
                send(msg)
            except Exception as e:
                print(f"⚠️ Failed to process item {idx}: {e}")
                traceback.print_exc()
        print(f"✅ Sent {len(new_items)} new items.")
    else:
        # Monthly roundup
        cutoff = datetime.now() - timedelta(days=30)
        monthly = []
        for it in items:
            if it["pub_date"] is None or it["pub_date"] >= cutoff:
                monthly.append(it)
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
                    date_str = item["pub_date"].strftime("%d %b") if item["pub_date"] else "Recent"
                    summary = generate_summary(item["link"], item["title"])
                    summary_display = clean_text(summary, item["title"]) if summary else ""
                    entities = extract_entities(summary_display) if summary_display else extract_entities(item["title"])
                    affected = f" | Affected: {', '.join(entities)}" if entities else ""
                    lines.append(
                        f"• {item['category']} ({date_str})\n"
                        f"  <b>{item['title']}</b>\n"
                        f"  📌 <i>{summary_display}</i>{affected}\n"
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
