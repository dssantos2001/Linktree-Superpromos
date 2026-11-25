"""Monta o catalogo de ofertas por marketplace e nicho em offers.js.

Cada marketplace tem sub-abas (nichos) com ate 12 produtos. Os produtos que ja
estao no offers.js sao mantidos e encaixados no nicho certo; as vagas que
sobram sao preenchidas com produtos buscados nas APIs de afiliado:

    Shopee      -> productOfferV2 (ja devolve o link de afiliado)
    AliExpress  -> aliexpress.affiliate.product.query (promotion_link)

Mercado Livre e Amazon bloqueiam busca automatica; para elas os candidatos
vem de um arquivo JSON (lista de ofertas com o campo "niche"), passado com
--import mercado=arquivo.json / --import amazon=arquivo.json.

    python scripts/build_catalog.py [--import amazon=amazon.json] [--rebuild shopee]

Credenciais: as mesmas do refresh_offers.py (ENV_FILE pode apontar para um .env).
"""

import argparse
import asyncio
import json
import os
import re
import time
import unicodedata
from datetime import datetime

import httpx

import refresh_offers as R

PER_NICHE = 12

# Mesmas sub-abas, com os mesmos nomes, em todos os marketplaces.
STANDARD_NICHES = [
    ("eletronicos", "Eletrônicos"),
    ("games", "Games"),
    ("casa", "Casa e cozinha"),
    ("beleza", "Beleza e cuidados"),
    ("fitness", "Fitness"),
    ("ferramentas", "Ferramentas e carro"),
]

# Palavras-chave de busca por marketplace e nicho ("busca=termo1|termo2": o titulo
# precisa ter um dos termos nas primeiras palavras). Mercado Livre e Amazon nao tem
# busca automatica, entao ficam sem palavras-chave aqui.
KEYWORDS = {
    "shopee": {
        "eletronicos": ["fone bluetooth=fone", "caixa de som bluetooth=caixa|som", "smartwatch=smartwatch|relogio inteligente",
                        "carregador turbo=carregador|fonte", "ring light=ring light|iluminador", "mouse sem fio=mouse",
                        "power bank=power bank|carregador portatil|bateria", "cabo usb c=cabo"],
        "games": ["controle gamer=controle|gamepad|joystick", "headset gamer=headset|fone", "mouse gamer=mouse",
                  "teclado gamer=teclado", "mousepad gamer=mouse pad|mousepad", "suporte controle=suporte|base",
                  "cadeira gamer=cadeira", "gatilho celular=gatilho"],
        "casa": ["organizador=organizador|caixa", "sapateira=sapateira", "jogo de lencol=lencol|jogo de cama",
                 "toalha de banho=toalha", "jogo de panelas=panela|jogo de panelas", "porta tempero=tempero|condimento",
                 "garrafa termica=garrafa", "utensilios cozinha=kit|jogo|utensilio|espatula"],
        "beleza": ["kit skincare=kit|skincare|sabonete", "perfume=perfume|eau de|colonia|body splash",
                   "protetor solar=protetor", "maquiagem=base|batom|paleta|maquiagem|po compacto|corretivo",
                   "escova secadora=escova|secador", "hidratante corporal=hidratante|creme|locao",
                   "serum facial=serum", "chapinha=chapinha|prancha"],
        "fitness": ["tenis corrida=tenis", "legging academia=legging|calca", "garrafa academia=garrafa|squeeze|coqueteleira",
                    "elastico exercicio=elastico|faixa|kit", "corda de pular=corda", "tapete yoga=tapete|colchonete",
                    "luva academia=luva", "pochete corrida=pochete"],
        "ferramentas": ["parafusadeira=parafusadeira|furadeira", "jogo de chaves=jogo|kit|chave",
                        "compressor de ar portatil=compressor|calibrador", "aspirador automotivo=aspirador",
                        "trena=trena|nivel", "kit ferramentas=kit|jogo|maleta", "lanterna led=lanterna",
                        "suporte celular carro=suporte"],
    },
    "aliexpress": {
        "eletronicos": ["wireless earbuds=fone|earbuds|tws", "smartwatch=smartwatch|relogio inteligente",
                        "bluetooth speaker=alto-falante|caixa de som|speaker", "gan charger=carregador",
                        "power bank=power bank|banco de potencia|carregador portatil", "mini projector=projetor",
                        "usb c hub=hub|adaptador", "webcam=webcam|camera"],
        "games": ["game controller=controle|gamepad|controlador|joystick", "gaming headset=fone|headset",
                  "gaming mouse=mouse", "mechanical keyboard=teclado", "retro game console=console|video game|jogo",
                  "controller charging dock=carregador|estacao|doca|base", "gaming mouse pad=mouse pad|tapete",
                  "phone game trigger=gatilho|controlador"],
        "casa": ["kitchen gadgets=cozinha|cortador|ralador|dispensador|triturador|descascador",
                 "storage organizer=organizador|caixa|prateleira", "humidifier=umidificador|difusor",
                 "led strip=fita|tira|led", "night light=luz noturna|luminaria|lampada", "cat scratcher=arranhador|gato",
                 "vacuum storage bag=saco|organizador", "silicone kitchen utensils=utensilio|espatula|conjunto|kit"],
        "beleza": ["manicure set=manicure|unha|cortador de unha", "makeup brush set=pincel|pinceis",
                   "hair straightener=alisador|chapinha|prancha|escova", "hair clipper=maquina|aparador|cortador",
                   "facial cleansing brush=escova|limpador", "nail drill=lixa|maquina|broca",
                   "eyelash curler=curvex|cilios|modelador", "electric shaver=barbeador|aparador"],
        "fitness": ["resistance bands=faixa|elastico|banda", "jump rope=corda", "sports water bottle=garrafa",
                    "yoga mat=tapete|esteira", "fitness tracker=smartband|pulseira inteligente|relogio inteligente",
                    "gym gloves=luva", "massage gun=massageador|pistola", "ab roller=roda|rolo"],
        "ferramentas": ["electric screwdriver=chave de fenda|parafusadeira|chave eletrica", "drill bit set=broca",
                        "digital multimeter=multimetro", "laser distance meter=laser|trena|telemetro",
                        "car vacuum cleaner=aspirador|soprador", "car tire inflator=bomba|compressor|inflador",
                        "car phone holder=suporte", "dash cam=camera|dash cam|dvr"],
    },
}

NICHES = {
    market: [
        {"id": niche_id, "label": label, **({"keywords": KEYWORDS[market][niche_id]} if market in KEYWORDS else {})}
        for niche_id, label in STANDARD_NICHES
    ]
    for market in ("mercado", "shopee", "aliexpress", "amazon")
}

# categoria antiga (campo "category") -> nicho, para os produtos que ja existiam
LEGACY = {
    "mercado": {"smartwatch": "eletronicos", "fone": "eletronicos", "games": "games",
                "tenis": "fitness", "creatina": "fitness", "whey protein": "fitness",
                "cozinha": "casa", "air fryer": "casa", "casa": "casa", "ferramentas": "ferramentas"},
    "shopee": {"eletronicos": "eletronicos", "casa": "casa", "cozinha": "casa", "pet": "casa",
               "beleza": "beleza", "moda": "fitness", "ferramentas": "ferramentas", "automotivo": "ferramentas"},
    "aliexpress": {"eletronicos": "eletronicos", "ferramentas": "ferramentas", "automotivo": "ferramentas",
                   "fitness": "fitness", "beleza": "beleza", "pet": "casa"},
    "amazon": {"fone": "eletronicos", "informatica": "eletronicos", "leitura": "eletronicos",
               "beleza": "beleza", "ferramentas": "ferramentas", "limpeza": "casa", "pet": "casa", "casa": "casa"},
}


def now():
    return datetime.now(R.BRT).isoformat(timespec="seconds")


def short_title(text, limit=90):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(" ,-|/(")


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "").lower())
    return re.sub(r"[^a-z0-9 ]+", " ", text.encode("ascii", "ignore").decode())


def split_keyword(spec):
    """'fone bluetooth=fone|earbuds' -> ('fone bluetooth', ['fone', 'earbuds'])"""
    search, _, match = spec.partition("=")
    terms = [normalize(t).strip() for t in (match or search.split()[0]).split("|")]
    return search.strip(), [t for t in terms if t]


# pecas avulsas/acessorios que aparecem nas buscas mas nao sao o produto do nicho
BLOCKED = re.compile(r"\b(teclas|keycaps?|scrunchies?|bicicleta|limpeza de teclado|pulseira para|capa para|pelicula|apple watch)\b")


def relevant(title, terms):
    """O produto precisa ter um dos termos logo no comeco do titulo (evita item fora do nicho)."""
    text = normalize(title)
    if BLOCKED.search(text):
        return False
    head = " " + " ".join(text.split()[:4]) + " "
    return any(f" {term}" in head for term in terms)


def title_words(title):
    return frozenset(w for w in normalize(title).split() if len(w) > 2)


def looks_repeated(title, taken):
    """Titulo muito parecido com um que ja entrou (mesmo produto em outro anuncio)."""
    words = title_words(title)
    for other in taken:
        if isinstance(other, frozenset) and other and words:
            if len(words & other) / min(len(words), len(other)) >= 0.6:
                return True
    return False


def round_robin(groups, limit, taken):
    """Escolhe intercalando as palavras-chave, sem repetir produto nem titulo parecido."""
    picked = []
    queues = [list(group) for group in groups]
    while len(picked) < limit and any(queues):
        for queue in queues:
            while queue:
                offer = queue.pop(0)
                key = offer["_key"]
                if key in taken or looks_repeated(offer["title"], taken):
                    continue
                taken.update({key, title_words(offer["title"])})
                picked.append(offer)
                break
            if len(picked) >= limit:
                break
    return picked


# ---------------------------------------------------------------- Shopee

async def shopee_candidates(client, spec):
    keyword, terms = split_keyword(spec)
    query = (
        f'query {{ productOfferV2(keyword: {json.dumps(keyword)}, limit: 30, page: 1, sortType: 2) '
        "{ nodes { itemId shopId productName price priceDiscountRate sales imageUrl offerLink ratingStar } } }"
    )
    data = await R.shopee_graphql(client, query)
    nodes = ((data.get("data") or {}).get("productOfferV2") or {}).get("nodes") or []
    out = []
    for node in nodes:
        try:
            rating = float(node.get("ratingStar") or 0)
        except ValueError:
            rating = 0
        if int(node.get("priceDiscountRate") or 0) < 10 or int(node.get("sales") or 0) < 300 or rating < 4.5:
            continue
        if not str(node.get("imageUrl", "")).startswith("https://cf.shopee.com.br/"):
            continue
        if not relevant(node.get("productName"), terms):
            continue
        offer = {
            "source": "Shopee",
            "category": keyword,
            "title": short_title(node["productName"]),
            "sales": int(node.get("sales") or 0),
            "image": node["imageUrl"],
            "url": node["offerLink"],
            "ref": {"shopId": str(node["shopId"]), "itemId": str(node["itemId"])},
            "_key": str(node["itemId"]),
        }
        price = R.parse_brl(node.get("price"))
        if R.apply_prices(offer, price, discount=int(node.get("priceDiscountRate") or 0)):
            out.append(offer)
    return out


# ---------------------------------------------------------------- AliExpress

async def aliexpress_candidates(client, spec):
    keyword, terms = split_keyword(spec)
    params = {
        "method": "aliexpress.affiliate.product.query",
        "app_key": os.environ["ALIEXPRESS_APP_KEY"],
        "timestamp": str(int(time.time() * 1000)),
        "format": "json",
        "sign_method": "md5",
        "v": "2.0",
        "keywords": keyword,
        "target_currency": "BRL",
        "target_language": "PT",
        "ship_to_country": "BR",
        "page_size": "40",
        "sort": "LAST_VOLUME_DESC",
        "tracking_id": os.getenv("ALIEXPRESS_TRACKING_ID", ""),
    }
    params = {k: v for k, v in params.items() if v}
    params["sign"] = R.ali_sign(params, os.environ["ALIEXPRESS_APP_SECRET"])
    response = await client.get(R.ALI_API, params=params)
    response.raise_for_status()
    result = (
        (response.json().get("aliexpress_affiliate_product_query_response") or {}).get("resp_result") or {}
    ).get("result") or {}
    out = []
    for product in (result.get("products") or {}).get("product") or []:
        image = str(product.get("product_main_image_url") or "")
        if not image.startswith("https://ae-pic-a1.aliexpress-media.com/"):
            continue
        volume = int(product.get("lastest_volume") or 0)
        rate = R.parse_brl(str(product.get("evaluate_rate") or "").replace("%", "")) or 0
        if volume < 100 or (rate and rate < 90) or not product.get("promotion_link"):
            continue
        if not relevant(product.get("product_title"), terms):
            continue
        sales = f"{volume:,}".replace(",", ".") + " vendidos"
        if rate:
            sales = f"{rate:g}% avaliacoes positivas | {sales}"
        offer = {
            "source": "AliExpress",
            "category": keyword,
            "title": short_title(product.get("product_title")),
            "sales": sales,
            "image": image,
            "url": product["promotion_link"],
            "ref": {"productId": str(product["product_id"])},
            "_key": str(product["product_id"]),
        }
        price = R.parse_brl(product.get("target_sale_price"))
        original = R.parse_brl(product.get("target_original_price"))
        if R.apply_prices(offer, price, original) and offer["discount"] >= 10:
            out.append(offer)
    return out


SEARCHERS = {"shopee": shopee_candidates, "aliexpress": aliexpress_candidates}


# ---------------------------------------------------------------- main

def offer_key(offer):
    ref = offer.get("ref") or {}
    return ref.get("itemId") or ref.get("productId") or re.sub(r"\?.*$", "", offer.get("url", ""))


async def build_market(client, market, existing, imported, rebuild, search=True):
    niches = NICHES[market]
    ids = [niche["id"] for niche in niches]
    buckets = {niche_id: [] for niche_id in ids}
    taken = set()

    keep = [] if rebuild else existing
    legacy_words = {title_words(offer["title"]) for offer in keep}
    for offer in keep + imported:
        niche = offer.get("niche") or LEGACY.get(market, {}).get(offer.get("category"))
        key = offer_key(offer)
        if niche not in buckets or key in taken or len(buckets[niche]) >= PER_NICHE:
            continue
        # importados so sao barrados se repetirem um produto que ja estava no site
        if offer in imported and looks_repeated(offer["title"], legacy_words):
            continue
        offer["niche"] = niche
        offer.pop("_key", None)
        taken.update({key, title_words(offer["title"])})
        buckets[niche].append(offer)

    searcher = SEARCHERS.get(market) if search else None
    for niche in niches:
        missing = PER_NICHE - len(buckets[niche["id"]])
        if missing <= 0 or not searcher or not niche.get("keywords"):
            continue
        taken.update(title_words(o["title"]) for bucket in buckets.values() for o in bucket)
        groups = []
        for keyword in niche["keywords"]:
            try:
                groups.append(await searcher(client, keyword))
            except Exception as exc:  # noqa: BLE001
                print(f"[{market}] busca '{keyword}' falhou: {exc}")
            await asyncio.sleep(0.5)
        for offer in round_robin(groups, missing, taken):
            offer["niche"] = niche["id"]
            offer.pop("_key", None)
            buckets[niche["id"]].append(offer)

    for niche in niches:
        print(f"[{market}] {niche['label']}: {len(buckets[niche['id']])} produtos")
    return [offer for niche_id in ids for offer in buckets[niche_id]]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--import", dest="imports", action="append", default=[],
                        help="market=arquivo.json com ofertas candidatas (cada uma com 'niche')")
    parser.add_argument("--rebuild", action="append", default=[],
                        help="descarta as ofertas atuais deste marketplace antes de montar")
    args = parser.parse_args()

    R.load_env_file()
    data = R.read_offers()
    imports = {}
    for item in args.imports:
        market, path = item.split("=", 1)
        imports[market] = json.loads(open(path, encoding="utf-8").read())

    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        for market in NICHES:
            missing_env = [n for n in R.REQUIRED_ENV.get(market, []) if not os.getenv(n)]
            if missing_env:
                print(f"[{market}] sem credenciais ({', '.join(missing_env)}): so reorganiza o que ja existe")
            data["markets"][market] = await build_market(
                client,
                market,
                data["markets"].get(market, []),
                imports.get(market, []),
                market in args.rebuild,
                search=not missing_env,
            )

    data["niches"] = {
        market: [{"id": n["id"], "label": n["label"]} for n in niches] for market, niches in NICHES.items()
    }
    data["updatedAt"] = now()
    R.write_offers(data)


if __name__ == "__main__":
    asyncio.run(main())
