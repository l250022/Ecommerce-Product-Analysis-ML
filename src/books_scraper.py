"""
Books to Scrape - E-commerce Product Data Collector
---------------------------------------------------
Scrapes all listing pages and visits each individual book page.
Saves one row per unique book to data/books_raw.csv.

Run from the project folder:
    python src/books_scraper.py

Required packages:
    requests
    beautifulsoup4
    pandas
"""

import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BASE_URL = "https://books.toscrape.com/"
REQUEST_DELAY = 0.4
TIMEOUT = 20

# This file is expected to be saved inside the project's src folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_FOLDER = PROJECT_ROOT / "data"
OUTPUT_FILE = DATA_FOLDER / "books_raw.csv"

COLUMNS = [
    "title",
    "product_url",
    "price",
    "star_rating",
    "availability_status",
    "stock_quantity",
    "category",
    "UPC",
    "description",
    "tax",
    "price_excluding_tax",
    "price_including_tax",
    "number_of_reviews",
]


def make_session():
    """Create a polite HTTP session with retries for temporary errors."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (compatible; StudentEducationalScraper/1.0; "
            "+https://books.toscrape.com/)"
        )
    })

    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_soup(session, url):
    """Download a page and return its BeautifulSoup object."""
    response = session.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def clean_price(value):
    """Convert a price such as '£51.77' into a number."""
    if not value:
        return None
    match = re.search(r"[\d,]+(?:\.\d+)?", value.replace(",", ""))
    return float(match.group()) if match else None


def get_rating(article_or_page):
    """Convert the site's star-rating CSS class into a number from 1 to 5."""
    rating_element = article_or_page.select_one(".star-rating")
    if rating_element is None:
        return None

    rating_names = {
        "One": 1,
        "Two": 2,
        "Three": 3,
        "Four": 4,
        "Five": 5,
    }

    for class_name in rating_element.get("class", []):
        if class_name in rating_names:
            return rating_names[class_name]

    return None


def get_listing_products(session, listing_url):
    """
    Read one catalogue page.
    Return product-page URLs and listing information.
    """
    soup = get_soup(session, listing_url)
    products = []

    for article in soup.select("article.product_pod"):
        link = article.select_one("h3 a")
        if link is None:
            continue

        product_url = urljoin(listing_url, link.get("href", ""))

        title = link.get("title") or link.get_text(strip=True)

        price_element = article.select_one(".price_color")
        listing_price = clean_price(
            price_element.get_text(strip=True) if price_element else None
        )

        availability_element = article.select_one(".availability")
        listing_availability = (
            availability_element.get_text(" ", strip=True)
            if availability_element else None
        )

        products.append({
            "title_from_listing": title,
            "product_url": product_url,
            "price_from_listing": listing_price,
            "availability_from_listing": listing_availability,
            "star_rating_from_listing": get_rating(article),
        })

    next_link = soup.select_one("li.next a")
    next_url = (
        urljoin(listing_url, next_link.get("href"))
        if next_link else None
    )

    return products, next_url


def get_product_details(session, product):
    """Visit an individual product page and extract its detailed attributes."""
    soup = get_soup(session, product["product_url"])

    title_element = soup.select_one("div.product_main h1")
    title = (
        title_element.get_text(strip=True)
        if title_element else product["title_from_listing"]
    )

    main = soup.select_one("div.product_main")

    price_element = main.select_one(".price_color") if main else None
    product_price = clean_price(
        price_element.get_text(strip=True) if price_element else None
    )

    availability_element = main.select_one(".availability") if main else None
    availability_text = (
        availability_element.get_text(" ", strip=True)
        if availability_element
        else product["availability_from_listing"]
    )

    stock_match = (
        re.search(r"\((\d+)\s+available\)", availability_text, re.IGNORECASE)
        if availability_text else None
    )
    stock_quantity = int(stock_match.group(1)) if stock_match else None

    if availability_text and "in stock" in availability_text.lower():
        availability_status = "In stock"
    elif availability_text and "out of stock" in availability_text.lower():
        availability_status = "Out of stock"
    else:
        availability_status = None

    rating = get_rating(main) if main else None
    if rating is None:
        rating = product["star_rating_from_listing"]

    # Product information is stored in a table on the individual product page.
    product_info = {}
    for row in soup.select("table.table-striped tr"):
        heading = row.find("th")
        value = row.find("td")
        if heading and value:
            product_info[heading.get_text(" ", strip=True)] = (
                value.get_text(" ", strip=True)
            )

    breadcrumb_links = soup.select("ul.breadcrumb li a")
    category = (
        breadcrumb_links[-1].get_text(strip=True)
        if len(breadcrumb_links) >= 3 else None
    )

    description_heading = soup.select_one("#product_description")
    description = None
    if description_heading:
        description_paragraph = description_heading.find_next("p")
        if description_paragraph:
            description = description_paragraph.get_text(" ", strip=True)

    # The site's listing price is normally the price including tax.
    # Keep the explicit table values as separate columns as required.
    price_excluding_tax = clean_price(product_info.get("Price (excl. tax)"))
    price_including_tax = clean_price(product_info.get("Price (incl. tax)"))
    tax = clean_price(product_info.get("Tax"))

    return {
        "title": title,
        "product_url": product["product_url"],
        "price": product_price if product_price is not None
                 else product["price_from_listing"],
        "star_rating": rating,
        "availability_status": availability_status,
        "stock_quantity": stock_quantity,
        "category": category,
        "UPC": product_info.get("UPC"),
        "description": description,
        "tax": tax,
        "price_excluding_tax": price_excluding_tax,
        "price_including_tax": price_including_tax,
        "number_of_reviews": (
            int(product_info["Number of reviews"])
            if product_info.get("Number of reviews", "").isdigit()
            else None
        ),
    }


def scrape_all_books():
    """Scrape every catalogue page and every unique product page."""
    DATA_FOLDER.mkdir(parents=True, exist_ok=True)
    session = make_session()

    all_products = []
    listing_url = BASE_URL
    visited_listing_pages = set()

    print("Starting catalogue scraping...")

    # Follow the Next link until the final catalogue page.
    while listing_url and listing_url not in visited_listing_pages:
        visited_listing_pages.add(listing_url)
        print(f"Reading listing page {len(visited_listing_pages)}: {listing_url}")

        page_products, next_url = get_listing_products(session, listing_url)
        all_products.extend(page_products)

        listing_url = next_url
        if listing_url:
            time.sleep(REQUEST_DELAY)

    # Remove duplicate product URLs before visiting individual pages.
    unique_products = {}
    for product in all_products:
        unique_products.setdefault(product["product_url"], product)

    print(f"\nListing records found: {len(all_products)}")
    print(f"Unique product URLs: {len(unique_products)}")
    print("\nVisiting individual product pages...")

    records = []
    failed_urls = []

    for index, product in enumerate(unique_products.values(), start=1):
        try:
            record = get_product_details(session, product)
            records.append(record)
            print(f"[{index}/{len(unique_products)}] Scraped: {record['title']}")
        except requests.RequestException as error:
            failed_urls.append((product["product_url"], str(error)))
            print(f"[{index}/{len(unique_products)}] Request failed: {product['product_url']}")
            print(f"Reason: {error}")
        except Exception as error:
            failed_urls.append((product["product_url"], str(error)))
            print(f"[{index}/{len(unique_products)}] Could not parse: {product['product_url']}")
            print(f"Reason: {error}")

        # Pause between product-page requests to avoid unnecessary traffic.
        time.sleep(REQUEST_DELAY)

    dataframe = pd.DataFrame(records, columns=COLUMNS)

    # A final safety check: one row per product URL.
    dataframe = dataframe.drop_duplicates(subset=["product_url"], keep="first")

    dataframe.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")

    print("\n========== SCRAPING SUMMARY ==========")
    print(f"Listing pages visited: {len(visited_listing_pages)}")
    print(f"Listing records discovered: {len(all_products)}")
    print(f"Unique product URLs discovered: {len(unique_products)}")
    print(f"Successfully scraped unique books: {len(dataframe)}")
    print(f"Failed product pages: {len(failed_urls)}")
    print(f"Duplicate listing entries excluded: {len(all_products) - len(unique_products)}")
    print(f"CSV saved to: {OUTPUT_FILE}")

    print("\nMissing values by column:")
    print(dataframe.isnull().sum())

    if failed_urls:
        failed_file = DATA_FOLDER / "failed_product_urls.csv"
        pd.DataFrame(failed_urls, columns=["product_url", "error"]).to_csv(
            failed_file, index=False, encoding="utf-8-sig"
        )
        print(f"\nFailed URLs saved for review: {failed_file}")

    print("\nFirst five records:")
    print(dataframe.head())

    return dataframe


if __name__ == "__main__":
    scrape_all_books()
