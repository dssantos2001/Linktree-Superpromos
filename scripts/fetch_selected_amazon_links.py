import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BLUEBOT_DIR = Path(r"C:\Users\daniel\Documents\BlueBot-main")
sys.path.insert(0, str(BLUEBOT_DIR))
load_dotenv(BLUEBOT_DIR / ".env")


LINKS = [
    (
        "https://link.amazon/B0dFdOa7U",
        "JBL, Caixa de Som, Boombox 4, Bluetooth, Som JBL Pro, AI Sound Boost, Graves Personalizaveis, Bateria de ate 34h, IP68, Audio Lossless - Laranja",
    ),
    (
        "https://link.amazon/B09Jkum7y",
        "Maquina de Lavar 17kg Electrolux Essential Care com Cesto Inox, Jet&Clean e Ultra Filter (LED17)",
    ),
    (
        "https://link.amazon/B0iKiZxqR",
        "Panela de Pressao Brinox Ceramic Life 4,2L, Vanilla | Antiaderente, com Fundo de Inducao, Pressure",
    ),
    (
        "https://link.amazon/B09J1IQEJ",
        "Jogo de Panelas Tramontina Inox | Solar 65120026, com Revestimento Interno Cermico",
    ),
    (
        "https://link.amazon/B0aglmU6B",
        "Limpeza Automotiva Completa Shampoo V-floc Revitalizador Intense Cera Tok Final Limpador Sintra Fast Pano Vonixx",
    ),
    (
        "https://link.amazon/B0hmoQjvY",
        "ELG, SHCR600, Camera Robo 360 Full HD 1080P Inteligente, Conexao WI-FI 2.4GHz, Compativel com Alexa, Audio Bidirecional, Zoom Digital 6x, Branco",
    ),
    (
        "https://link.amazon/B0aqjkzkg",
        "BOLD Snacks Barra de Proteina Caixa Mix - Caixa com 12 Unidades - Zero Adicao de Acucar",
    ),
    (
        "https://link.amazon/B0d072cNR",
        "Mixer Vertical Elgin 200W | Turbo Chef, 3 em 1, Preto, 110v",
    ),
    (
        "https://link.amazon/B06Xsx1Yn",
        "Kit 12 Potes Hermeticos de Plastico Retangulares Electrolux | Multiuso, Vedacao Silicone, BPA Free, Porta Mantimentos",
    ),
]

AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", os.getenv("AMAZON_STORE_ID", "")).strip()
AMAZON_STORE_ID = os.getenv("AMAZON_STORE_ID", AMAZON_AFFILIATE_TAG).strip()
AMAZON_COOKIE = os.getenv("AMAZON_COOKIE", "").strip()
AMAZON_MARKETPLACE_ID = os.getenv("AMAZON_MARKETPLACE_ID", "526970").strip()
AMAZON_SHORT_URL_ENDPOINT = os.getenv(
    "AMAZON_SHORT_URL_ENDPOINT",
    "https://www.amazon.com.br/associates/sitestripe/getShortUrl",
)


def brl_text(value):
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.startswith("BRL "):
        raw = text.removeprefix("BRL ").strip()
        try:
            number = float(raw)
            return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except ValueError:
            return text
    return text.replace("\xa0", " ")


def original_from_text(text):
    if not text:
        return None
    patterns = [
        r"(?:De|Preco anterior|Pre\u00e7o anterior|List Price|Was)[:\s]*R\$\s*[\d\.\,]+",
        r"R\$\s*[\d\.\,]+",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = re.search(r"R\$\s*[\d\.\,]+", match.group(0))
            if value:
                return value.group(0)
    return None


def discount_percent(price, original):
    def parse_money(value):
        if not value:
            return None
        match = re.search(r"[\d\.\,]+", str(value))
        if not match:
            return None
        return float(match.group(0).replace(".", "").replace(",", "."))

    current = parse_money(price)
    before = parse_money(original)
    if not current or not before or before <= current:
        return 0
    return round((1 - current / before) * 100)


def normalize_amazon_product_url(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "amzn.to" in host:
        return url

    asin_match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", parsed.path, re.IGNORECASE)
    if not asin_match:
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))

    asin = asin_match.group(1).upper()
    query = parse_qs(parsed.query)
    kept_query = {}
    for key in ("th", "psc", "smid"):
        value = query.get(key)
        if value:
            kept_query[key] = value[0]

    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or "www.amazon.com.br",
            f"/dp/{asin}",
            "",
            urlencode(kept_query),
            "",
        )
    )


async def expand_amazon_short_url(url):
    lowered = (url or "").lower()
    if not lowered or ("amzn.to/" not in lowered and "link.amazon/" not in lowered):
        return url

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            return str(response.url)
    except Exception:
        return url


def build_amazon_sitestripe_long_url(product_url):
    parsed = urlparse(product_url)
    query = parse_qs(parsed.query)
    query["tag"] = [AMAZON_AFFILIATE_TAG]
    query.setdefault("linkCode", ["sl2"])
    query.setdefault("ref_", ["as_li_ss_tl"])
    return urlunparse((parsed.scheme or "https", parsed.netloc or "www.amazon.com.br", parsed.path, "", urlencode(query, doseq=True), ""))


async def generate_amazon_short_link(product_url):
    if not (AMAZON_AFFILIATE_TAG and AMAZON_COOKIE):
        return build_amazon_sitestripe_long_url(product_url) if AMAZON_AFFILIATE_TAG else None

    long_url = build_amazon_sitestripe_long_url(product_url)
    params = {
        "longUrl": long_url,
        "marketplaceId": AMAZON_MARKETPLACE_ID,
        "storeId": AMAZON_STORE_ID or AMAZON_AFFILIATE_TAG,
    }
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "Cookie": AMAZON_COOKIE,
        "Referer": product_url,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            response = await client.get(AMAZON_SHORT_URL_ENDPOINT, params=params, headers=headers)
            if response.status_code != 200:
                return long_url
            data = response.json()
            short_url = data.get("shortUrl") or data.get("short_url")
            return str(short_url) if short_url else long_url
    except Exception:
        return long_url


def first_text(soup, selectors):
    for selector in selectors:
        for element in soup.select(selector):
            text = element.get("content") or element.get("src") or element.get_text(" ", strip=True)
            text = (text or "").strip()
            if text:
                return text
    return None


def text_price_from_element(element):
    if not element:
        return None

    offscreen = element.select_one(".a-offscreen")
    if offscreen and offscreen.get_text(strip=True):
        return normalize_price_text(offscreen.get_text(" ", strip=True))

    symbol = element.select_one(".a-price-symbol")
    whole = element.select_one(".a-price-whole")
    fraction = element.select_one(".a-price-fraction")
    if whole:
        value = whole.get_text("", strip=True).replace(",", "").replace(".", "")
        cents = fraction.get_text("", strip=True) if fraction else "00"
        return normalize_price_text(f"{symbol.get_text('', strip=True) if symbol else 'R$'} {value},{cents}")

    return normalize_price_text(element.get_text(" ", strip=True))


def first_price(soup, selectors):
    for selector in selectors:
        for element in soup.select(selector):
            value = text_price_from_element(element)
            if value:
                return value
    return None


def normalize_price_text(value):
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
    match = re.search(r"R\$\s*[\d\.\,]+", text)
    return match.group(0).replace("R$ ", "R$ ") if match else brl_text(text)


async def scrape_amazon_page(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }
    if AMAZON_COOKIE:
        headers["Cookie"] = AMAZON_COOKIE

    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    title = first_text(soup, ["#productTitle", "meta[property='og:title']", "title"])
    image = first_text(soup, ["#landingImage", "#imgTagWrapperId img", "meta[property='og:image']"])
    price = first_price(
        soup,
        [
            "#corePriceDisplay_desktop_feature_div .priceToPay",
            "#corePrice_feature_div .priceToPay",
            "#corePriceDisplay_mobile_feature_div .priceToPay",
            "#apex_desktop .priceToPay",
            ".reinventPricePriceToPayMargin.priceToPay",
            "#priceblock_dealprice",
            "#price_inside_buybox",
            "#tp_price_block_total_price_ww .a-price",
        ],
    )
    original = first_price(
        soup,
        [
            ".basisPrice .a-price.a-text-price",
            ".basisPrice .a-offscreen",
            ".a-price.a-text-price",
            "#listPrice",
            "#price .a-text-strike",
            ".priceBlockStrikePriceString",
        ],
    )
    if original == price:
        original = None

    return {
        "title": title,
        "price": price,
        "originalPrice": original,
        "image": image,
    }


async def fetch_one(source_url, fallback_title):
    expanded = await expand_amazon_short_url(source_url)
    normalized = normalize_amazon_product_url(expanded)
    info = {}
    for candidate_url in dict.fromkeys([normalized, expanded, source_url]):
        for _ in range(2):
            info = await scrape_amazon_page(candidate_url)
            title = (info.get("title") or "").strip()
            if info.get("price") or info.get("image") or (title and title.lower() != "amazon.com.br"):
                break
            await asyncio.sleep(1)
        title = (info.get("title") or "").strip()
        if info.get("price") or info.get("image") or (title and title.lower() != "amazon.com.br"):
            break
    affiliate = await generate_amazon_short_link(normalized)

    scraped_title = (info.get("title") or "").strip()
    title = scraped_title if scraped_title and scraped_title.lower() != "amazon.com.br" else fallback_title
    price = brl_text(info.get("price"))
    original = brl_text(info.get("originalPrice"))
    discount = discount_percent(price, original)

    return {
        "source": "Amazon",
        "category": "",
        "title": title,
        "price": price,
        "originalPrice": original,
        "discount": discount,
        "sales": "Amazon",
        "image": info.get("image"),
        "url": source_url,
        "expandedUrl": expanded,
        "normalizedUrl": normalized,
        "generatedAffiliateUrl": affiliate,
    }


async def main():
    results = []
    for url, title in LINKS:
        print(f"Fetching {url}...")
        try:
            item = await fetch_one(url, title)
        except Exception as exc:
            item = {
                "source": "Amazon",
                "category": "",
                "title": title,
                "price": None,
                "originalPrice": None,
                "discount": 0,
                "sales": "Amazon",
                "image": None,
                "url": url,
                "error": str(exc),
            }
        results.append(item)
        print(f"  -> {item.get('title')} | {item.get('price')} | {item.get('originalPrice')}")

    output_path = Path("data/selected_amazon_deals.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
