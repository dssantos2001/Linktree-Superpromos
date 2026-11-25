# Linktree Bigode Promos

Site estatico da Bigode Promos com links oficiais para comunidades e ofertas selecionadas por marketplace.

## Estrutura

- `index.html`: pagina publica do site.
- `assets/`: imagens usadas no site.
- `data/`: snapshots de ofertas usados como referencia interna.
- `scripts/`: scripts auxiliares para atualizar os snapshots de ofertas.
- `vercel.json`: headers de seguranca para hospedagem na Vercel.

## Publicacao na Vercel

Este projeto nao precisa de build. A Vercel pode publicar a raiz do repositorio como site estatico.

Arquivos auxiliares como `scripts/`, `data/`, `.agents/` e `.codex/` ficam fora do pacote de deploy por causa do `.vercelignore`.

## Manutencao das ofertas

Execute os scripts a partir da raiz do projeto para atualizar os arquivos em `data/`.

```powershell
python scripts/fetch_selected_shopee_links.py
python scripts/fetch_selected_amazon_links.py
python scripts/fetch_marketplace_deals.py
```

Os produtos exibidos na aba Ofertas ficam em `offers.js`.

## Nichos (sub-abas)

Todos os marketplaces usam as mesmas sub-abas, com até 12 produtos cada:
Eletrônicos, Games, Casa e cozinha, Beleza e cuidados, Fitness e Ferramentas e carro.
Os nichos (`STANDARD_NICHES`) e as palavras-chave de busca (`KEYWORDS`) ficam no topo
de `scripts/build_catalog.py`.

```powershell
$env:ENV_FILE = "C:\Users\daniel\Documents\BlueBot-main\.env"
python scripts/build_catalog.py
```

- Shopee e AliExpress: as vagas livres de cada nicho são preenchidas pelas APIs de afiliado.
- Mercado Livre e Amazon: bloqueiam busca automática; os candidatos vêm de um JSON
  (`--import amazon=arquivo.json`), cada item com o campo `niche`.
- `--rebuild shopee` descarta os produtos atuais daquele marketplace antes de montar.

Link direto para um nicho: `/#ofertas/shopee/beleza`.
