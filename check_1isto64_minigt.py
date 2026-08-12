#!/usr/bin/env python3
"""
1ISTO64 Mini GT new-arrival watcher.

1isto64.com is a Shopify store; its Mini GT collection is paginated and
sorted by "Featured" (merchant-curated, not date-based) by default. A
"newest first" sort_by param exists on paper, but it behaved inconsistently
when tested, so instead of trusting any single page or sort order, this
walks every page of the collection each run and tracks the complete
current set of product handles. Any handle that wasn't present last check
is a genuine new arrival, regardless of where it lands in "Featured" order.

Each product's "Sold Out" status is also recorded in state -- not used
for notifications in this build (which only alerts on new arrivals, per
what was asked for), but there if a future restock-alert version wants it.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://1isto64.com/collections/mini-gt"
STATE_FILE = Path(__file__).parent / "state_1isto64_minigt.json"
MAX_PAGES = 20  # safety cap only; real pagination self-terminates on an empty page

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

HANDLE_RE = re.compile(r"/products/([a-z0-9\-]+)")


def _extract_page_products(soup):
    """Return {handle: {name, url, sold_out}} for every product linked on one page."""
    by_handle = {}
    for link in soup.find_all("a", href=True):
        m = HANDLE_RE.search(link["href"])
        if not m:
            continue
        handle = m.group(1)
        text = link.get_text(strip=True)
        entry = by_handle.setdefault(
            handle,
            {"name": "", "url": f"https://1isto64.com/products/{handle}", "sold_out": False},
        )
        if "sold out" in text.lower():
            entry["sold_out"] = True
        cleaned = text.replace("Sold Out", "").replace("SOLD OUT", "").strip()
        # This theme renders a few link variants pointing at the same handle:
        # a messy doubled alt-text (image wrapper), a raw "/products/<handle>"
        # path used as literal link text, and the real clean title. Reject the
        # first two outright rather than relying on length alone to sort them out.
        if not cleaned or cleaned.startswith("/") or cleaned.startswith("javascript:"):
            continue
        if not entry["name"] or len(cleaned) < len(entry["name"]):
            entry["name"] = cleaned
    return by_handle


def fetch_all_products():
    all_products = {}
    for page in range(1, MAX_PAGES + 1):
        resp = requests.get(BASE_URL, params={"page": page}, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        page_products = _extract_page_products(soup)
        if not page_products:
            break  # ran past the last real page
        all_products.update(page_products)
    return all_products


def load_previous_products():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("products", {})
    return {}


def save_state(products):
    data = {
        "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "products": products,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env vars — skipping send.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 3500):
        chunk = text[i:i + 3500]
        resp = requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=15
        )
        if not resp.ok:
            print(f"Telegram send failed: {resp.status_code} {resp.text}")


def main():
    try:
        current = fetch_all_products()
    except requests.RequestException as e:
        print(f"ERROR fetching page: {e}")
        sys.exit(1)

    if not current:
        print("WARNING: parsed 0 products across all pages — the site markup may have changed. Leaving state untouched.")
        sys.exit(1)

    previous = load_previous_products()
    new_handles = [h for h in current if h not in previous]

    print(f"Checked {len(current)} products across the collection, {len(new_handles)} new.")

    if previous and new_handles:
        lines = ["New MINI GT arrival(s) on 1ISTO64:", ""]
        for h in new_handles:
            p = current[h]
            status = " (currently Sold Out)" if p["sold_out"] else ""
            lines.append(f"- {p['name']}{status}")
            lines.append(f"  {p['url']}")
        send_telegram_message("\n".join(lines))
    elif not previous:
        print("First run — saving baseline, no notification sent.")

    save_state(current)


if __name__ == "__main__":
    main()
