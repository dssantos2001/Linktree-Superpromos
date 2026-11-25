"""Atualiza os precos das ofertas em offers.js.

Roda a cada hora pelo GitHub Actions (.github/workflows/atualizar-ofertas.yml),
mas tambem pode ser executado localmente:

    python scripts/refresh_offers.py

Credenciais (variaveis de ambiente):
    SHOPEE_PARTNER_ID, SHOPEE_API_KEY
    ALIEXPRESS_APP_KEY, ALIEXPRESS_APP_SECRET, ALIEXPRESS_TRACKING_ID
    AMAZON_COOKIE (opcional)
    ENV_FILE (opcional): caminho de um .env para carregar, ex. o do BlueBot.

Se a consulta de um produto falhar, o ultimo preco conhecido e mantido.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OFFERS_FILE = ROOT / "offers.js"
PREFIX = "window.BIGODE_OFFERS = "
BRT = timezone(timedelta(hours=-3))

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def load_env_file():
    path = os.getenv("ENV_FILE")
    if not path or not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------- precos

def parse_brl(value):
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("R$", "").replace("\xa0", " ").strip()
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    text = re.sub(r"[^\d.]", "", text)
    try:
        number = float(text)
    except ValueError:
        return None
    return number if number > 0 else None


def fmt_brl(number):
    return "R$ " + f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def apply_prices(offer, price, original=None, discount=None):
    """Grava preco/preco original/desconto na oferta. Retorna True se aplicou."""
    if not price or price <= 0:
        return False
    if original and original <= price:
        original = None
    if not original and discount and 0 < discount < 95:
        original = price / (1 - discount / 100)
    if original:
        discount = round((1 - price / original) * 100)
    else:
        discount = 0
    offer["price"] = fmt_brl(price)
    offer["originalPrice"] = fmt_brl(original) if original else ""
    offer["discount"] = discount
    offer["checkedAt"] = datetime.now(BRT).isoformat(timespec="seconds")
    return True


async def resolve(client, url):
    response = await client.get(url, headers=BROWSER_HEADERS)
    return str(response.url), response.text


# ---------------------------------------------------------------- Shopee

SHOPEE_GRAPHQL = "https://open-api.affiliate.shopee.com.br/graphql"


async def shopee_graphql(client, query):
    app_id = os.environ["SHOPEE_PARTNER_ID"]
    secret = os.environ["SHOPEE_API_KEY"]
    payload = json.dumps({"query": query}, separators=(",", ":"))
    timestamp = str(int(time.time()))
    signature = hashlib.sha256(f"{app_id}{timestamp}{payload}{secret}".encode()).hexdigest()
    response = await client.post(
        SHOPEE_GRAPHQL,
        content=payload,
        headers={
            "Authorization": f"SHA256 Credential={app_id}, Timestamp={timestamp}, Signature={signature}",
            "Content-Type": "application/json",
        },
    )
    response.raise_for_status()
    return response.json()


async def refresh_shopee(client, offer):
    ref = offer.setdefault("ref", {})
    if not ref.get("itemId"):
        expanded, text = await resolve(client, offer["url"])
        for pattern in (r"-i\.(\d+)\.(\d+)", r"/product/(\d+)/(\d+)", r"/i\.(\d+)\.(\d+)", r"/(\d+)/(\d+)(?:\?|$)"):
            match = re.search(pattern, expanded)
            if match:
                ref["shopId"], ref["itemId"] = match.group(1), match.group(2)
                break
        else:
            match = re.search(r'"shopid"\s*:\s*(\d+).*?"itemid"\s*:\s*(\d+)', text, re.S)
            if not match:
                raise RuntimeError(f"ids nao encontrados em {expanded}")
            ref["shopId"], ref["itemId"] = match.group(1), match.group(2)

    query = (
        f"query {{ productOfferV2(limit: 1, page: 1, shopId: {int(ref['shopId'])}, itemId: {int(ref['itemId'])}) "
        "{ nodes { price priceDiscountRate sales } } }"
    )
    data = await shopee_graphql(client, query)
    if data.get("errors"):
        raise RuntimeError(data["errors"])
    nodes = ((data.get("data") or {}).get("productOfferV2") or {}).get("nodes") or []
    if not nodes:
        raise RuntimeError("produto nao retornado pela API (pode ter saido do ar)")
    node = nodes[0]
    price = parse_brl(node.get("price"))
    if price and price > 100000:
        price = price / 100000
    if node.get("sales"):
        offer["sales"] = int(node["sales"])
    return apply_prices(offer, price, discount=int(node.get("priceDiscountRate") or 0))


# ---------------------------------------------------------------- AliExpress

ALI_API = "https://api-sg.aliexpress.com/sync"


def ali_sign(params, secret):
    raw = secret + "".join(f"{k}{v}" for k, v in sorted(params.items())) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest().upper()


async def refresh_aliexpress(client, offer):
    ref = offer.setdefault("ref", {})
    if not ref.get("productId"):
        expanded, text = await resolve(client, offer["url"])
        match = (
            re.search(r"/item/(\d{8,})\.html", expanded)
            or re.search(r"productIds=(\d{8,})", expanded)
            or re.search(r"/item/(\d{8,})\.html", text)
            or re.search(r'"productId"\s*:\s*"?(\d{8,})', text)
        )
        if not match:
            raise RuntimeError(f"id nao encontrado em {expanded}")
        ref["productId"] = match.group(1)

    params = {
        "method": "aliexpress.affiliate.productdetail.get",
        "app_key": os.environ["ALIEXPRESS_APP_KEY"],
        "timestamp": str(int(time.time() * 1000)),
        "format": "json",
        "sign_method": "md5",
        "v": "2.0",
        "product_ids": ref["productId"],
        "target_currency": "BRL",
        "target_language": "PT",
        "country": "BR",
        "tracking_id": os.getenv("ALIEXPRESS_TRACKING_ID", ""),
    }
    params = {k: v for k, v in params.items() if v}
    params["sign"] = ali_sign(params, os.environ["ALIEXPRESS_APP_SECRET"])
    for attempt in range(3):
        response = await client.get(ALI_API, params=params)
        response.raise_for_status()
        data = response.json()
        # limite de chamadas por segundo da API: espera um pouco e tenta de novo
        if (data.get("error_response") or {}).get("code") != "ApiCallLimit":
            break
        await asyncio.sleep(3 * (attempt + 1))
    if data.get("error_response"):
        raise RuntimeError(data["error_response"])
    result = (
        (data.get("aliexpress_affiliate_productdetail_get_response") or {}).get("resp_result") or {}
    ).get("result") or {}
    products = (result.get("products") or {}).get("product") or []
    if not products:
        raise RuntimeError("produto nao retornado pela API (pode ter saido do ar)")
    product = products[0]
    price = parse_brl(product.get("target_sale_price") or product.get("sale_price"))
    original = parse_brl(product.get("target_original_price") or product.get("original_price"))
    discount = parse_brl(str(product.get("discount") or "").replace("%", ""))
    return apply_prices(offer, price, original, discount)


# ---------------------------------------------------------------- Mercado Livre

def ml_money(element):
    if not element:
        return None
    label = element.get("aria-label") or ""
    match = re.search(r"([\d.]+)\s*reais(?:\s*com\s*(\d+)\s*centavos)?", label)
    if match:
        return float(match.group(1).replace(".", "")) + (int(match.group(2)) / 100 if match.group(2) else 0)
    fraction = element.select_one(".andes-money-amount__fraction")
    cents = element.select_one(".andes-money-amount__cents")
    if fraction:
        return float(fraction.get_text(strip=True).replace(".", "")) + (
            int(cents.get_text(strip=True)) / 100 if cents else 0
        )
    return None


def ml_featured_card(text):
    """Os links meli.la abrem o perfil de afiliado com o produto em destaque."""
    start = text.find("card-featured")
    if start < 0:
        return None, None
    match = re.search(
        r'"type":"price","id":"price"[^{]*"price":\{(.{0,200}?)"current_price":\{"value":([\d.]+)',
        text[start:start + 20000],
    )
    if not match:
        return None, None
    previous = re.search(r'"previous_price":\{"value":([\d.]+)', match.group(1))
    return float(match.group(2)), float(previous.group(1)) if previous else None


async def refresh_mercado(client, offer):
    expanded, text = await resolve(client, offer["url"])
    if "account-verification" in expanded or "/login" in expanded:
        raise RuntimeError("bloqueado pelo Mercado Livre (verificacao)")

    if "/social/" in expanded:
        price, original = ml_featured_card(text)
        if not price:
            og = re.search(r'property="og:title" content="[^"]*- R\$ ([\d.,]+)"', text)
            price, original = (parse_brl(og.group(1)) if og else None), None
        if not price:
            raise RuntimeError("produto em destaque nao encontrado no perfil")
        return apply_prices(offer, price, original)

    soup = BeautifulSoup(text, "html.parser")
    price = None
    meta = soup.select_one('meta[itemprop="price"]')
    if meta and meta.get("content"):
        price = parse_brl(meta["content"])
    if not price:
        price = ml_money(soup.select_one(".ui-pdp-price__second-line .andes-money-amount"))
    if not price:
        for script in soup.select('script[type="application/ld+json"]'):
            match = re.search(r'"price"\s*:\s*"?([\d.]+)', script.string or "")
            if match:
                price = float(match.group(1))
                break
    original = ml_money(soup.select_one(".ui-pdp-price__original-value, s.andes-money-amount--previous"))
    if not price:
        raise RuntimeError(f"preco nao encontrado em {expanded[:90]}")
    return apply_prices(offer, price, original)


# ---------------------------------------------------------------- Amazon

def amazon_price(soup, selectors):
    for selector in selectors:
        for element in soup.select(selector):
            offscreen = element.select_one(".a-offscreen")
            text = offscreen.get_text(" ", strip=True) if offscreen else element.get_text(" ", strip=True)
            match = re.search(r"R\$\s*([\d.,]+)", text)
            if match:
                value = parse_brl(match.group(1))
                if value:
                    return value
    return None


async def refresh_amazon(client, offer):
    headers = dict(BROWSER_HEADERS)
    if os.getenv("AMAZON_COOKIE"):
        headers["Cookie"] = os.environ["AMAZON_COOKIE"]
    for attempt in range(3):
        response = await client.get(offer["url"], headers=headers)
        if response.status_code != 503:
            break
        await asyncio.sleep(5 * (attempt + 1))
    if response.status_code != 200 or "validateCaptcha" in response.text:
        raise RuntimeError(f"bloqueado pela Amazon (HTTP {response.status_code})")
    soup = BeautifulSoup(response.text, "html.parser")
    price = amazon_price(
        soup,
        [
            "#corePriceDisplay_desktop_feature_div .priceToPay",
            "#corePrice_feature_div .priceToPay",
            "#corePriceDisplay_mobile_feature_div .priceToPay",
            "#apex_desktop .priceToPay",
            ".reinventPricePriceToPayMargin.priceToPay",
            "#priceblock_dealprice",
            "#price_inside_buybox",
            "#corePrice_feature_div .a-price",
        ],
    )
    original = amazon_price(
        soup,
        [
            "#corePriceDisplay_desktop_feature_div .basisPrice .a-text-price",
            "#corePrice_feature_div .basisPrice .a-text-price",
            ".basisPrice .a-text-price",
            "#listPrice",
            "#price .a-text-strike",
        ],
    )
    if not price:
        raise RuntimeError("preco nao encontrado (produto indisponivel?)")
    return apply_prices(offer, price, original)


# ---------------------------------------------------------------- main

REFRESHERS = {
    "mercado": refresh_mercado,
    "shopee": refresh_shopee,
    "aliexpress": refresh_aliexpress,
}
# A Amazon bloqueia consultas automaticas, entao os precos dela ficam fixos.
# Para tentar mesmo assim (ex. no seu PC): REFRESH_AMAZON=1
if os.getenv("REFRESH_AMAZON") == "1":
    REFRESHERS["amazon"] = refresh_amazon
REQUIRED_ENV = {
    "shopee": ["SHOPEE_PARTNER_ID", "SHOPEE_API_KEY"],
    "aliexpress": ["ALIEXPRESS_APP_KEY", "ALIEXPRESS_APP_SECRET"],
}


def read_offers():
    text = OFFERS_FILE.read_text(encoding="utf-8").strip()
    if not text.startswith(PREFIX):
        raise SystemExit("offers.js em formato inesperado")
    return json.loads(text[len(PREFIX):].rstrip(";"))


def write_offers(data):
    OFFERS_FILE.write_text(
        PREFIX + json.dumps(data, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
        newline="\n",
    )


async def refresh_market(client, market, offers):
    """Atualiza as ofertas de um marketplace. Retorna (ok, falhas)."""
    refresher = REFRESHERS.get(market)
    missing = [name for name in REQUIRED_ENV.get(market, []) if not os.getenv(name)]
    if not refresher or missing:
        print(f"[{market}] pulado: {'faltam ' + ', '.join(missing) if missing else 'sem atualizador'}")
        return 0, 0
    ok = failed = 0
    for offer in offers:
        before = (offer.get("price"), offer.get("originalPrice"))
        try:
            if await refresher(client, offer):
                ok += 1
                after = (offer.get("price"), offer.get("originalPrice"))
                mark = "" if after == before else f"  (antes {before[0]})"
                print(f"[{market}] ok   {offer['price']:>12}  {offer['title'][:50]}{mark}")
        except Exception as exc:  # noqa: BLE001 - mantem ultimo preco conhecido
            failed += 1
            print(f"[{market}] FALHA {offer['title'][:50]}: {type(exc).__name__}: {str(exc)[:160]}")
        await asyncio.sleep(1)
    return ok, failed


async def main():
    load_env_file()
    data = read_offers()

    # cada marketplace roda em paralelo (sao sites diferentes), um produto por vez em cada
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        results = await asyncio.gather(
            *(refresh_market(client, market, offers) for market, offers in data["markets"].items())
        )
    ok = sum(r[0] for r in results)
    failed = sum(r[1] for r in results)

    if ok:
        data["updatedAt"] = datetime.now(BRT).isoformat(timespec="seconds")
    write_offers(data)
    print(f"\n{ok} atualizadas, {failed} falharam (mantido o ultimo preco).")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
