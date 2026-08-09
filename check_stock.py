#!/usr/bin/env python3
"""
Mini GT stock watcher for karzanddolls.com

Checks the Mini GT collection page for newly listed products and
sends a Telegram message when new ones appear. Meant to be run on
a schedule (e.g. via GitHub Actions) rather than continuously.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

COLLECTION_URL = "https://www.karzanddolls.com/mini-gt/mini-gt"
STATE_FILE = Path(__file__).parent / "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}


def fetch_products():
    """Return {slug: name} for every product currently on the collection page.

    We key off /details/<slug> links rather than any CSS class, since that's
    the one pattern confirmed stable across every product card on the page.
    """
    resp = requests.get(COLLECTION_URL, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    products = {}
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "/details/" not in href:
            continue
        slug = href.split("/details/")[-1].strip("/")
        name = link.get_text(strip=True)
        if slug and name:
            products.setdefault(slug, name)  # keep the first (cleanest) name seen
    return products


def load_previous_products():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("products", {})
    return {}


def save_state(products):
    # Stamping a fresh timestamp means this file changes on every run, even
    # when no new product appears — which keeps the repo "active" so GitHub
    # never auto-disables the schedule after 60 quiet days.
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
    # Telegram caps messages at 4096 chars; chunk defensively just in case.
    for i in range(0, len(text), 3500):
        chunk = text[i:i + 3500]
        resp = requests.post(
            url, data={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=15
        )
        if not resp.ok:
            print(f"Telegram send failed: {resp.status_code} {resp.text}")


def main():
    try:
        current = fetch_products()
    except requests.RequestException as e:
        print(f"ERROR fetching page: {e}")
        sys.exit(1)

    if not current:
        print("WARNING: parsed 0 products — the site markup may have changed. Leaving state untouched.")
        sys.exit(1)

    previous = load_previous_products()
    new_slugs = [slug for slug in current if slug not in previous]

    print(f"Checked {len(current)} products, {len(new_slugs)} new.")

    if previous and new_slugs:
        lines = ["New Mini GT arrival(s):", ""]
        for slug in new_slugs:
            lines.append(f"- {current[slug]}")
            lines.append(f"  https://www.karzanddolls.com/details/{slug}")
        send_telegram_message("\n".join(lines))
    elif not previous:
        print("First run — saving baseline, no notification sent.")

    save_state(current)


if __name__ == "__main__":
    main()
