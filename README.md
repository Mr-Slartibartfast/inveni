# Inveni

Inveni periodically searches Amazon for Needoh listings and displays the title, price, URL, and image in a local web dashboard.

## Run

```powershell
python -m pip install -r requirements.txt
python inveni.py
```

Open http://127.0.0.1:5000. The first search runs when the application starts; later searches run hourly.

## Configuration

The default source is the Needoh Amazon Store page. Set `INVENI_AMAZON_STORE_URL` to another store page, or set `INVENI_SEARCH_TERM` and leave the store URL empty to use Amazon search. Other options are `INVENI_INTERVAL_SECONDS` (minimum 300), `INVENI_AMAZON_DOMAIN`, `INVENI_DATABASE`, `INVENI_HOST`, and `INVENI_PORT`.

Amazon can rate-limit or block automated requests. Use this only in accordance with Amazon's terms and applicable laws. For production, replace `scrape_amazon` with Amazon's Product Advertising API adapter.
