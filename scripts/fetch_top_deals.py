"""Busca ofertas mais vendidas, bem avaliadas e com desconto na Shopee e AliExpress.

Usa as credenciais do BlueBot. Gera data/top_deals.json com candidatos por categoria.
Execute a partir da raiz do projeto: python scripts/fetch_top_deals.py
"""

import asyncio
import html
import json
import re
import sys
from pathlib import Path

import httpx

BLUEBOT_DIR = Path(r"C:\Users\daniel\Documents\BlueBot-main")
sys.path.insert(0, str(BLUEBOT_DIR))

from dotenv import load_dotenv

load_dotenv(BLUEBOT_DIR / ".env")

from Affiliates import deal_finder as df  # noqa: E402

CATEGORIES = {
    "eletronicos": ["fone bluetooth", "smartwatch", "caixa de som bluetooth"],
    "casa": ["jogo de cama", "organizador", "luminaria led"],
    "cozinha": ["air fryer", "jogo de panelas", "garrafa termica"],
    "beleza": ["secador de cabelo", "perfume", "kit skincare"],
    "moda": ["tenis masculino", "kit camisetas", "bolsa feminina"],
    "fitness": ["creatina", "whey protein", "garrafa academia"],
    "ferramentas": ["parafusadeira", "jogo de chaves", "trena laser"],
    "infantil": ["brinquedo infantil", "lego", "pelucia"],
    "automotivo": ["aspirador automotivo", "compressor portatil", "suporte celular carro"],
    "pet": ["cama pet", "arranhador gato", "comedouro pet"],
}


def money(number):
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(value):
    if value in (None, ""):
        return None
    text = str(value).replace("R$", "").replace("BRL", "").replace("%", "").strip()
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def clean(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


async def shopee_keyword(keyword, category):
    # sortType 2 = mais vendidos
    query = f"""
    query {{
      productOfferV2(limit: 50, page: 1, keyword: "{keyword}", sortType: 2) {{
        nodes {{ itemId shopId productName price priceMin priceMax priceDiscountRate sales ratingStar
                 imageUrl productLink offerLink shopName }}
      }}
    }}
    """
    data = {}
    for attempt in range(4):
        try:
            data = await df._post_shopee_graphql(query)
        except Exception as exc:
            data = {"errors": str(exc)}
        if not data.get("errors"):
            break
        await asyncio.sleep(3 * (attempt + 1))
    if data.get("errors"):
        print("shopee", keyword, data["errors"])
        return []
    out = []
    for item in data.get("data", {}).get("productOfferV2", {}).get("nodes", []) or []:
        discount = int(item.get("priceDiscountRate") or 0)
        sales = int(item.get("sales") or 0)
        rating = num(item.get("ratingStar")) or 0
        price = num(item.get("priceMin") or item.get("price"))
        if not price or discount < 15 or sales < 300 or rating < 4.6:
            continue
        out.append(
            {
                "source": "Shopee",
                "category": category,
                "keyword": keyword,
                "title": clean(item.get("productName")),
                "price": money(price),
                "originalPrice": money(price / (1 - discount / 100)),
                "discount": discount,
                "sales": sales,
                "rating": round(rating, 1),
                "image": item.get("imageUrl"),
                "url": item.get("offerLink") or item.get("productLink"),
                "key": f"{item.get('shopId')}:{item.get('itemId')}",
            }
        )
    return out


async def aliexpress_keyword(client, keyword, category):
    out = []
    for method in ("aliexpress.affiliate.hotproduct.query", "aliexpress.affiliate.product.query"):
        data = await df._ali_get(
            client,
            method,
            {
                "fields": df.ALIEXPRESS_PRODUCT_FIELDS,
                "keywords": keyword,
                "page_no": 1,
                "page_size": 50,
                "sort": "LAST_VOLUME_DESC",
                "tracking_id": df.ALI_TRACKING_ID,
                "target_currency": "BRL",
                "target_language": "PT",
                "ship_to_country": "BR",
            },
        )
        products = df._extract_aliexpress_products(data)
        for p in products:
            price = num(p.get("target_sale_price") or p.get("sale_price"))
            original = num(p.get("target_original_price") or p.get("original_price"))
            sales = int(num(p.get("lastest_volume") or p.get("latest_volume")) or 0)
            rate = num(p.get("evaluate_rate"))  # porcentagem de avaliacoes positivas
            if not price or not original or original <= price:
                continue
            discount = round((1 - price / original) * 100)
            if discount < 15 or sales < 300 or (rate is not None and rate < 95):
                continue
            out.append(
                {
                    "source": "AliExpress",
                    "category": category,
                    "keyword": keyword,
                    "title": clean(p.get("product_title")),
                    "price": money(price),
                    "originalPrice": money(original),
                    "discount": discount,
                    "sales": sales,
                    "rating": rate,
                    "image": p.get("product_main_image_url"),
                    "url": p.get("promotion_link") or p.get("product_detail_url"),
                    "key": str(p.get("product_id")),
                }
            )
        if out:
            break
    return out


async def main():
    shopee, ali = [], []
    async with httpx.AsyncClient(timeout=30) as client:
        for category, keywords in CATEGORIES.items():
            for kw in keywords:
                shopee += await shopee_keyword(kw, category)
                ali += await aliexpress_keyword(client, kw, category)
    result = {"shopee": shopee, "aliexpress": ali}
    out = Path("data/top_deals.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("shopee", len(shopee), "aliexpress", len(ali), "->", out.resolve())


if __name__ == "__main__":
    asyncio.run(main())
