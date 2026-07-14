# Linktree Superpromos

Site estatico da Super Promos com links oficiais para comunidades e ofertas selecionadas por marketplace.

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

Os links exibidos no site ficam embutidos no `index.html`.
