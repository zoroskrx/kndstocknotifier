#!/usr/bin/env python3
"""
FirstCry Hot Wheels new-arrival watcher.

Uses FirstCry's Hot Wheels page sorted by New Arrivals and tracks the
first few products returned by that page.

A Telegram message is sent when a product ID appears that was not present
in the previous check.

The first run only creates the baseline and does not send notifications.
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


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

TARGET_URL = (
    "https://www.firstcry.com/hotwheels/5/0/113"
    "?sort=new-arrivals&q=ard-hotwheels"
)

STATE_FILE = Path(__file__).parent / "state_firstcry_hotwheels.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Number of newest products to remember.
# Keeping this small avoids scanning/remembering the whole catalog.
TRACK_COUNT = 10


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# FirstCry product URLs look something like:
# /123456/product-detail/...
PRODUCT_ID_RE = re.compile(r"/(\d+)/product-detail")


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def clean_url(href):
    """Convert a relative FirstCry URL into a clean absolute URL."""
    if href.startswith("/"):
        href = "https://www.firstcry.com" + href

    parts = urlsplit(href)

    if not parts.scheme or not parts.netloc:
        return href

    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def links_to_products(elements):
    """
    Extract FirstCry products from a collection of <a> tags.

    Returns:
        {
            product_id: {
                "name": "...",
                "url": "..."
            }
        }
    """
    items = {}

    for el in elements:
        href = el.get("href", "")

        match = PRODUCT_ID_RE.search(href)
        if not match:
            continue

        product_id = match.group(1)

        name = el.get_text(" ", strip=True)

        if not name:
            continue

        items.setdefault(
            product_id,
            {
                "name": name,
                "url": clean_url(href),
            },
        )

    return items


def find_new_arrivals(soup):
    """
    Get the first TRACK_COUNT products from the page.

    The TARGET_URL is already sorted by New Arrivals, so the first products
    returned by the page are treated as the newest products.
    """
    all_products = links_to_products(
        soup.find_all("a", href=True)
    )

    if not all_products:
        return {}

    # Keep only the first TRACK_COUNT products in page order.
    return dict(list(all_products.items())[:TRACK_COUNT])


# ---------------------------------------------------------
# FETCH
# ---------------------------------------------------------

def fetch_products():
    """Fetch the FirstCry page and return its newest products."""
    response = requests.get(
        TARGET_URL,
        headers=HEADERS,
        timeout=30,
    )

    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    products = find_new_arrivals(soup)

    if not products:
        print("ERROR: No products were found on the FirstCry page.")
        return {}, "new_arrivals_sorted_page"

    return products, "new_arrivals_sorted_page"


# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------

def load_previous_state():
    """Load the previous saved state."""
    if not STATE_FILE.exists():
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return json.load(file)

    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: Could not read state file: {exc}")
        return {}


def save_state(products, mode):
    """Save the current products and metadata."""
    data = {
        "last_checked_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "last_mode": mode,
        "products": products,
    }

    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram_message(text):
    """Send a message to the configured Telegram chat."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID "
            "environment variables — skipping Telegram send."
        )
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    # Telegram has message-size limits, so split long messages.
    for start in range(0, len(text), 3500):
        chunk = text[start:start + 3500]

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
            },
            timeout=15,
        )

        if not response.ok:
            print(
                f"Telegram send failed: "
                f"{response.status_code} {response.text}"
            )


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    try:
        current, mode = fetch_products()

    except requests.RequestException as exc:
        print(f"ERROR fetching FirstCry page: {exc}")
        sys.exit(1)

    if not current:
        print(
            "WARNING: Parsed 0 products. "
            "Leaving previous state untouched."
        )
        sys.exit(1)

    previous_state = load_previous_state()

    previous_products = previous_state.get("products", {})
    previous_mode = previous_state.get("last_mode")

    print(f"Mode: {mode}. Checked {len(current)} products.")

    # If the detection method changes, do not compare the two sets.
    # Otherwise a bunch of false "new" products could be reported.
    if (
        previous_products
        and previous_mode
        and previous_mode != mode
    ):
        print(
            f"Detection mode changed "
            f"({previous_mode} -> {mode}) — "
            "resetting baseline, no notification this run."
        )

        save_state(current, mode)
        return

    new_ids = [
        product_id
        for product_id in current
        if product_id not in previous_products
    ]

    print(f"{len(new_ids)} new.")

    # First successful run = baseline only.
    if not previous_products:
        print(
            "First run — saving baseline, "
            "no notification sent."
        )

        save_state(current, mode)
        return

    # Existing baseline + new products = Telegram alert.
    if new_ids:
        lines = [
            "🚨 New Hot Wheels arrival(s) on FirstCry:",
            "",
        ]

        for product_id in new_ids:
            product = current[product_id]

            lines.append(f"- {product['name']}")
            lines.append(f"  {product['url']}")
            lines.append("")

        send_telegram_message("\n".join(lines))

    # Always save the latest state.
    save_state(current, mode)


if __name__ == "__main__":
    main()
