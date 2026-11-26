#!/usr/bin/env python3
"""Recorre las tiendas configuradas y guarda el catálogo en productos.json.

Uso:
  python scraper.py                 # recorre todas las tiendas
  python scraper.py --debug akiba   # guarda el HTML de la 1.ª página en debug_akiba.html
                                    # y muestra cuántos productos detecta
"""
import datetime
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path(__file__).with_name("productos.json")
UA = "Mozilla/5.0 (compatible; MetabuscadorRetro/1.0; uso personal)"
DELAY = 1.5      # segundos entre pedidos, para no cargar a las tiendas
MAX_PAGES = 60   # tope de seguridad por categoría
PRICE_RE = re.compile(r"\$\s*([\d\.]+(?:,\d{1,2})?)")

# Cada categoría: (ruta, categoría que se muestra, plataforma o "—")
# En Akiba "consolas" va primero para que las consolas queden como "Consolas".
STORES = [
    {
        "id": "akiba", "name": "Akiba Retro Game Store", "short": "Akiba",
        "base": "https://akibastore.mitiendanube.com", "kind": "tiendanube",
        "categories": [
            ("/videojuegos/consolas/", "Consolas", "—"),
            ("/videojuegos/playstation/", "Juegos", "PS1"),
            ("/videojuegos/playstation-2/", "Juegos", "PS2"),
            ("/videojuegos/playstation-3/", "Juegos", "PS3"),
            ("/videojuegos/playstation-4/", "Juegos", "PS4"),
            ("/videojuegos/playstation-5/", "Juegos", "PS5"),
            ("/videojuegos/playstation-portable-psp/", "Juegos", "PSP"),
            ("/videojuegos/nintendo-gamecube/", "Juegos", "GameCube"),
            ("/videojuegos/nintendo-wii/", "Juegos", "Wii"),
            ("/videojuegos/nintendo-64/", "Juegos", "N64"),
            ("/videojuegos/nintendo-switch/", "Juegos", "Switch"),
            ("/videojuegos/sega-saturn/", "Juegos", "Saturn"),
            ("/videojuegos/sega-dreamcast/", "Juegos", "Dreamcast"),
            ("/videojuegos/sega-genesis/", "Juegos", "Genesis"),
            ("/figuras/", "Figuras", "—"),
            ("/bandas-sonoras-ost-93qr7/", "Bandas sonoras", "—"),
        ],
    },
    {
        "id": "amigo", "name": "Tienda de Amigo", "short": "Tienda de Amigo",
        "base": "https://www.tiendadeamigo.com.ar", "kind": "empretienda",
        "categories": [
            ("/videojuegos/gameboy", "Juegos", "Game Boy"),
            ("/videojuegos/famicom", "Juegos", "Famicom"),
            ("/videojuegos/super-famicom", "Juegos", "Super Famicom"),
            ("/videojuegos/sega-megadrive", "Juegos", "Megadrive"),
            ("/videojuegos/playstation-1", "Juegos", "PS1"),
            ("/videojuegos/playstation-2", "Juegos", "PS2"),
            ("/videojuegos/playstation-3", "Juegos", "PS3"),
            ("/videojuegos/playstation-4", "Juegos", "PS4"),
            ("/videojuegos/playstation-5", "Juegos", "PS5"),
            ("/videojuegos/nintendo-64", "Juegos", "N64"),
            ("/videojuegos/xbox", "Juegos", "Xbox"),
            ("/videojuegos/psp", "Juegos", "PSP"),
            ("/videojuegos/sega-saturn", "Juegos", "Saturn"),
            ("/videojuegos/nes", "Juegos", "NES"),
            ("/videojuegos/disk-system", "Juegos", "Disk System"),
            ("/artbooks", "Artbooks", "—"),
            ("/coleccionables", "Coleccionables", "—"),
            ("/manga-comic-y-revistas", "Manga, cómics y revistas", "—"),
            ("/accesorios", "Accesorios", "—"),
            ("/consolas-de-emulacion", "Consolas de emulación", "—"),
            ("/figuras", "Figuras", "—"),
            ("/vinilos-lp-ep", "Vinilos", "—"),
            ("/vhs-y-dvd", "VHS y DVD", "—"),
        ],
    },
]

session = requests.Session()
session.headers["User-Agent"] = UA


def to_number(text):
    """'$289.900,00' o '289.900,00' -> 289900.0"""
    t = text.replace("$", "").strip().replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def first_price(text):
    m = PRICE_RE.search(text)
    return to_number(m.group(1)) if m else None


def abs_url(base, href):
    return href if href.startswith("http") else base + (href if href.startswith("/") else "/" + href)


def card_generic(h3):
    """Sube desde un <h3> hasta el contenedor de UN solo producto que tenga precio."""
    node = h3
    while node.parent is not None and node.parent.name not in ("body", "html"):
        parent = node.parent
        if len(parent.find_all("h3")) > 1:
            break
        node = parent
        if "$" in node.get_text():
            break
    return node


def parse_empretienda(soup, base):
    items = []
    for h3 in soup.find_all("h3"):
        a = h3.find("a", href=True)
        if not a:
            continue
        card = card_generic(h3)
        text = card.get_text(" ", strip=True)
        price = first_price(text)
        stock = 0 if re.search(r"sin stock", text, re.I) else 1
        items.append({"n": a.get_text(" ", strip=True), "u": abs_url(base, a["href"]),
                      "pr": price, "tr": None, "st": stock})
    return items


def parse_tiendanube(soup, base):
    items = []
    cards = soup.select(".js-item-product")
    for card in cards:
        a = card.select_one("a[href*='/productos/']")
        if not a:
            continue
        name_el = card.select_one(".js-item-name")
        name = (name_el.get_text(" ", strip=True) if name_el else "") or a.get("title") or a.get_text(" ", strip=True)
        text = card.get_text(" ", strip=True)
        price = None
        price_el = card.select_one(".js-price-display")
        if price_el is not None:
            raw = price_el.get("data-product-price")
            if raw and raw.isdigit():
                price = int(raw) / 100
            else:
                price = first_price(price_el.get_text(" ", strip=True))
        if price is None:
            price = first_price(text)
        m = re.search(r"\$\s*([\d\.]+(?:,\d{1,2})?)\s*con Transferencia", text, re.I)
        transfer = to_number(m.group(1)) if m else None
        stock = 0 if re.search(r"sin stock", text, re.I) or card.select_one(".label-soldout") else 1
        if not price:      # precio $0 = sin precio publicado
            price, transfer = None, None
        if not stock:
            price, transfer = None, None
        items.append({"n": name, "u": abs_url(base, a["href"]), "pr": price, "tr": transfer, "st": stock})
    if not cards:          # plan B si cambió el diseño de la tienda
        items = parse_empretienda(soup, base)
    return items


def crawl_store(store, debug=False):
    parser = parse_tiendanube if store["kind"] == "tiendanube" else parse_empretienda
    base, found = store["base"], {}
    for path, cat, plat in store["categories"]:
        seen_here = set()
        for page in range(1, MAX_PAGES + 1):
            url = f"{base}{path}?page={page}"
            try:
                r = session.get(url, timeout=30)
            except requests.RequestException as e:
                print(f"  ! {url}: {e}")
                break
            if r.status_code == 404:
                break
            if r.status_code != 200:
                print(f"  ! {url}: HTTP {r.status_code}")
                break
            if debug and page == 1 and not found:
                Path(f"debug_{store['id']}.html").write_text(r.text, encoding="utf-8")
            items = parser(BeautifulSoup(r.text, "html.parser"), base)
            new = [i for i in items if i["u"] not in seen_here]
            if debug and page == 1:
                print(f"  {path}: {len(items)} productos detectados en la página 1")
            if not new:
                break
            for i in new:
                seen_here.add(i["u"])
                if i["u"] not in found:
                    found[i["u"]] = {"s": store["id"], "n": i["n"], "p": plat, "c": cat,
                                     "pr": i["pr"], "tr": i["tr"], "st": i["st"], "u": i["u"]}
            time.sleep(DELAY)
        time.sleep(DELAY)
        print(f"  {cat} / {plat}: {len(seen_here)} productos")
        if debug:
            break          # en modo debug solo la primera categoría
    return list(found.values())


def main():
    args = sys.argv[1:]
    debug = "--debug" in args
    only = [a for a in args if not a.startswith("--")]
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"items": []}
    items, failed = [], False
    for store in STORES:
        if only and store["id"] not in only:
            items += [i for i in old["items"] if i["s"] == store["id"]]
            continue
        print(f"{store['name']}:")
        new = crawl_store(store, debug)
        prev = [i for i in old["items"] if i["s"] == store["id"]]
        if debug:
            items += prev
        elif not new or (prev and len(new) < 0.5 * len(prev)):
            print(f"  !! Solo {len(new)} productos (antes {len(prev)}). Conservo los datos anteriores.")
            items += prev
            failed = True
        else:
            items += new
    if debug:
        return
    data = {
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stores": [{"id": s["id"], "name": s["name"], "short": s["short"]} for s in STORES],
        "items": items,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Listo: {len(items)} productos en total.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
