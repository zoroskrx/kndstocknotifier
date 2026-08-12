#!/usr/bin/env python3
"""
FirstCry Hot Wheels new-arrival watcher.

FirstCry's Hot Wheels listing is paginated (209 items, only a batch loads
per request), so instead of diffing the whole catalog we track the page's
own "New Arrivals" widget — a small, curated list of the newest SKUs that
FirstCry already surfaces on this exact page. Sends a Telegram message
when a product ID shows up there that wasn't there last check.

Falls back to a page-wide scan if that widget can't be found (e.g. after
a layout change) — logged clearly, since that mode is less precise.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests
from bs4 import BeautifulSoup

TARGET_URL = "https://www.firstcry.com/hotwheels/5/0/113?sort=bestseller&q=ard-hotwheels&ref2=q_ard_hotwheels&asid=53241"
STATE_FILE = Path(__file__).parent / "state_firstcry_hotwheels.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

PRODUCT_ID_RE = re.compile(r"/(\d+)/product-detail")


def _clean_url(href):
    if href.startswith("/"):
        href = "https://www.firstcry.com" + href
    parts = urlsplit(href)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def _links_to_products(elements):
    items = {}
    for el in elements:
        href = el.get("href", "")
        m = PRODUCT_ID_RE.search(href)
        if not m:
            continue
        name = el.get_text(strip=True)
        if name:
            items.setdefault(m.group(1), {"name": name, "url": _clean_url(href)})
    return items


def find_new_arrivals(soup):
    """Find the page's own 'New Arrivals' widget, not its sort-dropdown option.

    The real widget is a short, bounded list that ends at the next heading
    within a few items. The sort dropdown's "New Arrivals" option instead
    sits right before the full (100+ item) product grid, which runs on for
    many links before any heading appears. So we require BOTH: a small
    item count AND that the scan ended because we hit a heading (not
    because we hit our own scan limit) -- an open-ended grid fails the
    second condition and is rejected automatically.
    """
    SCAN_LIMIT = 15
    for marker in soup.find_all(string=re.compile(r"New Arrivals")):
        nearby = []
        ended_at_heading = False
        for el in marker.find_all_next(["a", "h2", "h3", "h4"], limit=SCAN_LIMIT):
            if el.name in ("h2", "h3", "h4"):
                ended_at_heading = True
                break
            nearby.append(el)

        if not ended_at_heading:
            continue  # ran into our scan cap first -> this is an open-ended grid, skip it

        items = _links_to_products(nearby)
        if 3 <= len(items) <= 10:
            return items
    return {}


def fetch_products():
    resp = requests.get(TARGET_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    items = find_new_arrivals(soup)
    if items:
        return items, "new_arrivals_widget"

    print("New Arrivals widget not found -- falling back to a page-wide scan "
          "(less precise: bestseller-order reshuffles can look like 'new').")
    items = _links_to_products(soup.find_all("a", href=True))
    return items, "full_page_fallback"


def load_previous_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(products, mode):
    data = {
        "last_checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "last_mode": mode,
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
        current, mode = fetch_products()
    except requests.RequestException as e:
        print(f"ERROR fetching page: {e}")
        sys.exit(1)

    if not current:
        print("WARNING: parsed 0 products — the site markup may have changed. Leaving state untouched.")
        sys.exit(1)

    prev_state = load_previous_state()
    previous = prev_state.get("products", {})
    previous_mode = prev_state.get("last_mode")

    print(f"Mode: {mode}. Checked {len(current)} products.")

    if previous and previous_mode and previous_mode != mode:
        # The two modes track different-sized sets; comparing across a mode
        # switch would flag a pile of false "new" items. Reset quietly instead.
        print(f"Detection mode changed ({previous_mode} -> {mode}) — resetting baseline, not notifying this run.")
    else:
        new_ids = [pid for pid in current if pid not in previous]
        print(f"{len(new_ids)} new.")
        if previous and new_ids:
            lines = ["New Hot Wheels arrival(s) on FirstCry:", ""]
            for pid in new_ids:
                lines.append(f"- {current[pid]['name']}")
                lines.append(f"  {current[pid]['url']}")
            send_telegram_message("\n".join(lines))
        elif not previous:
            print("First run — saving baseline, no notification sent.")

    save_state(current, mode)


if __name__ == "__main__":
    main()
