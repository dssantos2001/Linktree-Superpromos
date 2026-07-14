import asyncio
import html
import json
import os
import re
import sys
from pathlib import Path

BLUEBOT_DIR = Path(r"C:\Users\daniel\Documents\BlueBot-main")
sys.path.insert(0, str(BLUEBOT_DIR))

keywords = [
    "air fryer",
    "microondas",
    "maquina de lavar",
    "whey protein",
    "creatina",
    "furadeira",
    "caixa de ferramentas",
    "kit de roupas",
    "kit de panelas",
    "kit de utensilios",
]

os.environ.setdefault("ALIEXPRESS_DEAL_KEYWORDS", ",".join(keywords))
os.environ.setdefault("ALIEXPRESS_MAX_KEYWORDS_PER_RUN", str(len(keywords)))
os.environ.setdefault("ALIEXPRESS_MAX_DEALS_PER_QUERY", "2")
os.environ.setdefault("ALIEXPRESS_DEAL_PAGES", "1")
os.environ.setdefault("ALIEXPRESS_DEAL_LIMIT", "24")
os.environ.setdefault("SHOPEE_DEAL_LIMIT", "30")
os.environ.setdefault("SHOPEE_DEAL_PAGES", "2")
os.environ.setdefault("AUTO_CURATOR_MIN_DISCOUNT_PERCENT", "10")

from dotenv import load_dotenv

load_dotenv(BLUEBOT_DIR / ".env")

from Affiliates.deal_finder import _post_shopee_graphql, fetch_aliexpress_deals


def keyword_for(title: str) -> str:
    normalized = title.lower()
    aliases = {
        "air fryer": ["air fryer", "fritadeira"],
        "microondas": ["microondas", "micro-ondas"],
        "maquina de lavar": ["maquina de lavar", "lavadora", "lava roupas"],
        "whey protein": ["whey", "protein"],
        "creatina": ["creatina"],
        "furadeira": ["furadeira", "parafusadeira"],
        "caixa de ferramentas": ["caixa de ferramentas", "ferramentas"],
        "kit de roupas": ["kit roupa", "kit roupas", "cueca", "camiseta", "meia"],
        "kit de panelas": ["kit panela", "panelas"],
        "kit de utensilios": ["kit utensilios", "utensilios", "utensílios"],
    }
    for key, values in aliases.items():
        if any(value in normalized for value in values):
            return key
    return ""


def serialize(deal):
    if isinstance(deal, dict):
        return deal
    return {
        "source": deal.source,
        "category": deal.source_query or keyword_for(deal.title),
        "title": deal.title,
        "price": deal.price,
        "originalPrice": None,
        "discount": deal.discount_percent,
        "sales": None,
        "image": deal.image_url,
        "url": deal.affiliate_url or deal.original_url,
        "score": deal.score,
    }


def money(value):
    if value in (None, ""):
        return None
    raw = str(value).strip()
    try:
        cleaned = raw.replace("R$", "").strip()
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        number = float(cleaned)
    except ValueError:
        return raw
    return f"R$ {number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def original_from_discount(price, discount):
    if not price or not discount:
        return None
    try:
        cleaned = str(price).replace("R$", "").strip()
        if "," in cleaned:
            cleaned = cleaned.replace(".", "").replace(",", ".")
        number = float(cleaned)
        original = number / (1 - (float(discount) / 100))
        return money(original)
    except (ValueError, ZeroDivisionError):
        return None


def clean_title(value):
    text = html.unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text


async def fetch_shopee_keyword_deals(limit_per_keyword=1):
    selected = []
    seen = set()
    for keyword in keywords:
        query = f"""
        query {{
          productOfferV2(limit: 20, page: 1, keyword: "{keyword}") {{
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
        try:
            data = await _post_shopee_graphql(query)
        except Exception:
            continue
        nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", []) or []
        ranked = []
        for item in nodes:
            item_id = item.get("itemId")
            shop_id = item.get("shopId")
            key = f"{shop_id}:{item_id}"
            discount = int(item.get("priceDiscountRate") or 0)
            sales = int(item.get("sales") or 0)
            if key in seen or not item.get("imageUrl") or not item.get("price"):
                continue
            if discount < 10 and sales < 5:
                continue
            ranked.append((discount + min(sales, 100), item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        for _, item in ranked[:limit_per_keyword]:
            seen.add(f"{item.get('shopId')}:{item.get('itemId')}")
            discount = int(item.get("priceDiscountRate") or 0)
            selected.append(
                {
                    "source": "Shopee",
                    "category": keyword,
                    "title": clean_title(item.get("productName")),
                    "price": money(item.get("price")),
                    "originalPrice": original_from_discount(item.get("price"), discount),
                    "discount": discount,
                    "sales": int(item.get("sales") or 0),
                    "image": item.get("imageUrl"),
                    "url": item.get("offerLink") or item.get("productLink"),
                    "score": discount + min(int(item.get("sales") or 0), 100),
                }
            )
    return selected


async def main():
    shopee, aliexpress = await asyncio.gather(
        fetch_shopee_keyword_deals(limit_per_keyword=1),
        fetch_aliexpress_deals(limit=24),
    )
    data = {
        "shopee": [serialize(deal) for deal in shopee],
        "aliexpress": [serialize(deal) for deal in aliexpress],
    }
    output_path = Path("data/marketplace_deals.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path.resolve()))


asyncio.run(main())
