import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, render_template_string

DATABASE = os.getenv("INVENI_DATABASE", "inveni.db")
SEARCH_TERM = os.getenv("INVENI_SEARCH_TERM", "needoh")
INTERVAL_SECONDS = max(300, int(os.getenv("INVENI_INTERVAL_SECONDS", "3600")))
AMAZON_DOMAIN = os.getenv("INVENI_AMAZON_DOMAIN", "www.amazon.com")
AMAZON_STORE_URL = os.getenv(
    "INVENI_AMAZON_STORE_URL",
    "https://www.amazon.com/stores/page/ECDC79D7-0634-4BAF-880C-28E572491C9E",
)

app = Flask(__name__)
scrape_lock = threading.Lock()
last_run = {"started": None, "finished": None, "error": None, "count": 0}


def get_connection():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                price TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                image TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL
            )
            """
        )


def absolute_url(url):
    if url.startswith("http"):
        return url.split("?")[0]
    return f"https://{AMAZON_DOMAIN}{url.split('?')[0]}"


def scrape_amazon():
    url = AMAZON_STORE_URL or f"https://{AMAZON_DOMAIN}/s?k={quote_plus(SEARCH_TERM)}"
    response = requests.get(
        url,
        headers={
            "User-Agent": os.getenv(
                "INVENI_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131 Safari/537.36",
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=20,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    page_text = soup.get_text(" ", strip=True).lower()
    if "bm-verify" in response.text or "captcha" in page_text or "robot check" in page_text:
        raise RuntimeError(
            "Amazon returned an anti-bot challenge. Use the Product Advertising API "
            "or try again later; no listings were imported."
        )
    results = []
    cards = soup.select(
        '[data-component-type="s-search-result"], [data-asin], '
        '[data-testid*="product"], [class*="product-card"], [class*="ProductCard"]'
    )
    if not cards:
        cards = [link.parent for link in soup.select("a[href*='/dp/']")]
    for card in cards:
        link = card.select_one("h2 a, a.a-link-normal, a[href*='/dp/']") or (
            card if card.name == "a" else None
        )
        title = card.select_one("h2, [data-cy='title-recipe'], [data-testid*='title']")
        price = card.select_one(".a-price .a-offscreen")
        image = card.select_one("img.s-image")
        if not title and link:
            title_text = link.get("aria-label") or link.get_text(" ", strip=True)
        else:
            title_text = title.get_text(" ", strip=True) if title else ""
        if not link or not title_text or not price:
            continue
        results.append({
            "title": title_text,
            "price": price.get_text(" ", strip=True),
            "url": absolute_url(link.get("href", "")),
            "image": image.get("src") if image else "",
        })
    return results


def save_listings(listings):
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as connection:
        for listing in listings:
            connection.execute(
                """
                INSERT INTO listings (title, price, url, image, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title, price = excluded.price,
                    image = excluded.image, last_seen = excluded.last_seen
                """,
                (listing["title"], listing["price"], listing["url"], listing["image"], now, now),
            )


def run_scrape():
    if not scrape_lock.acquire(blocking=False):
        return
    last_run["started"] = datetime.now(timezone.utc).isoformat()
    last_run["error"] = None
    try:
        listings = scrape_amazon()
        save_listings(listings)
        last_run["count"] = len(listings)
    except requests.RequestException as error:
        last_run["error"] = f"Amazon request failed: {error}"
    except Exception as error:
        last_run["error"] = str(error)
    finally:
        last_run["finished"] = datetime.now(timezone.utc).isoformat()
        scrape_lock.release()


def scheduler():
    while True:
        run_scrape()
        time.sleep(INTERVAL_SECONDS)


HTML = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inveni — Needoh listings</title>
<style>
body{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}
.meta{color:#666;margin-bottom:1.5rem}.error{color:#a00}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:1rem}
.card{border:1px solid #ddd;border-radius:8px;padding:1rem;display:flex;flex-direction:column;gap:.6rem}
.card img{width:100%;height:190px;object-fit:contain}.price{font-weight:bold}a{color:#06c;text-decoration:none}
button{padding:.5rem .8rem;cursor:pointer}
</style></head><body><h1>Needoh listings</h1>
<div class="meta">Amazon search: <strong>{{ term }}</strong> · Last result count: {{ status.count }} ·
<form method="post" action="/scrape" style="display:inline"><button type="submit">Search now</button></form></div>
{% if status.error %}<p class="error">{{ status.error }}</p>{% endif %}<div class="grid">
{% for item in listings %}<article class="card">{% if item.image %}<img src="{{ item.image }}" alt="">{% endif %}
<a href="{{ item.url }}" target="_blank" rel="noopener">{{ item.title }}</a><span class="price">{{ item.price }}</span>
<small>First seen: {{ item.first_seen[:19].replace('T', ' ') }}</small></article>
{% else %}<p>No listings stored yet. Run a search to begin.</p>{% endfor %}</div></body></html>
"""


@app.get("/")
def dashboard():
    with get_connection() as connection:
        listings = connection.execute(
            "SELECT title, price, url, image, first_seen FROM listings ORDER BY last_seen DESC"
        ).fetchall()
    return render_template_string(HTML, listings=listings, term=SEARCH_TERM, status=last_run)


@app.post("/scrape")
def scrape_now():
    run_scrape()
    return dashboard()


@app.get("/api/listings")
def listings_api():
    with get_connection() as connection:
        listings = [dict(row) for row in connection.execute("SELECT * FROM listings ORDER BY last_seen DESC")]
    return jsonify(listings)


if __name__ == "__main__":
    initialize_database()
    threading.Thread(target=scheduler, daemon=True).start()
    app.run(host=os.getenv("INVENI_HOST", "127.0.0.1"), port=int(os.getenv("INVENI_PORT", "5000")))
