import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

import sys

sys.path.insert(0, r"C:\Users\daniel\Documents\BlueBot-main")

from dotenv import load_dotenv

load_dotenv(r"C:\Users\daniel\Documents\BlueBot-main\.env")

from Affiliates.deal_finder import _post_shopee_graphql

LINKS = [
    "https://s.shopee.com.br/18m2hNH4n",
    "https://s.shopee.com.br/W52dcLN3u",
    "https://s.shopee.com.br/LlcRJM0Ot",
    "https://s.shopee.com.br/2BDGcgF1gG",
    "https://s.shopee.com.br/8V7KANl6e2",
    "https://s.shopee.com.br/8Knty4ljz1",
    "https://s.shopee.com.br/8pkAYzjpy8",
    "https://s.shopee.com.br/5q6Yza9GUG",
    "https://s.shopee.com.br/5fn8nH9tpF",
    "https://s.shopee.com.br/5VTiayAXAE",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def brl_from_shopee(value):
    if value in (None, "", 0):
        return None
    number = float(value)
    if number > 100000:
        number = number / 100000
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def extract_ids(url):
    patterns = [
        r"-i\.(\d+)\.(\d+)",
        r"/product/(\d+)/(\d+)",
        r"/i\.(\d+)\.(\d+)",
        r"/(\d+)/(\d+)(?:\?|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
    return None, None


def image_url(image_id):
    if not image_id:
        return None
    if str(image_id).startswith("http"):
        return str(image_id)
    return f"https://cf.shopee.com.br/file/{image_id}"


async def fetch_item(client, shop_id, item_id):
    query = f"""
    query {{
      productOfferV2(limit: 1, page: 1, shopId: {shop_id}, itemId: {item_id}) {{
        nodes {{
          itemId
          shopId
          productName
          price
          priceDiscountRate
          sales
          imageUrl
          productLink
          offerLink
        }}
      }}
    }}
    """
    data = await _post_shopee_graphql(query)
    nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", []) or []
    return nodes[0] if nodes else {}


async def collect(link):
    async with httpx.AsyncClient(follow_redirects=True, timeout=25, headers=HEADERS) as client:
        redirect_response = await client.get(link)
        expanded_url = str(redirect_response.url)
        shop_id, item_id = extract_ids(expanded_url)

        if not shop_id or not item_id:
            text = redirect_response.text
            match = re.search(r'"shopid"\s*:\s*(\d+).*?"itemid"\s*:\s*(\d+)', text, re.S)
            if match:
                shop_id, item_id = match.group(1), match.group(2)

        if not shop_id or not item_id:
            return {"url": link, "expandedUrl": expanded_url, "error": "ids_not_found"}

        item = await fetch_item(client, shop_id, item_id)
        name = item.get("productName") or "Produto Shopee"
        price = item.get("price")
        original = None
        discount = int(item.get("priceDiscountRate") or 0)
        sold = item.get("sales") or 0
        image = item.get("imageUrl")

        if not original and price and discount:
            original = float(price) / (1 - discount / 100)

        return {
            "source": "Shopee",
            "category": "",
            "title": name,
            "price": brl_from_shopee(price),
            "originalPrice": brl_from_shopee(original),
            "discount": discount,
            "sales": sold,
            "image": image,
            "url": link,
            "expandedUrl": expanded_url,
            "shopId": shop_id,
            "itemId": item_id,
        }


async def main():
    results = []
    for link in LINKS:
        try:
            results.append(await collect(link))
        except Exception as exc:
            results.append({"url": link, "error": f"{type(exc).__name__}: {exc}"})
    output_path = Path("data/selected_shopee_deals.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_path.resolve())


asyncio.run(main())
