#!/usr/bin/env python3
"""Recorre las tiendas configuradas y guarda el catálogo en productos.json.

Uso:
  python scraper.py                 # recorre todas las tiendas
  python scraper.py --debug akiba   # guarda el HTML de la 1.ª página en debug_akiba.html
                                    # y muestra cuántos productos detecta
"""
import datetime
import gzip
import html
import urllib.parse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from bs4 import BeautifulSoup

OUT = Path(__file__).with_name("productos.json")
UA = "Mozilla/5.0 (compatible; MetabuscadorRetro/1.0; uso personal)"
DELAY = 0.7      # segundos entre pedidos de una misma tienda, para no cargarla
WORKERS = 3      # páginas de producto que se leen a la vez en la pasada del sitemap
MAX_PAGES = 60   # tope de seguridad por categoría
MAX_EXTRA = 5000      # tope de páginas sueltas a revisar por tienda en la pasada del sitemap
SITEMAP_BUDGET = 1500  # segundos máximos por tienda leyendo páginas sueltas (lo que falte sigue en la próxima corrida)
PRICE_RE = re.compile(r"\$\s*([\d\.]+(?:,\d{1,2})?)")

# Cada categoría: (ruta, categoría que se muestra, plataforma o "—")
# En Akiba "consolas" va primero para que las consolas queden como "Consolas".
STORES = [
    {
        "id": "akiba", "name": "Akiba Retro Game Store", "short": "Akiba",
        "base": "https://akibastore.mitiendanube.com", "kind": "tiendanube",
        "sitemap": "https://akibastore.mitiendanube.com/sitemap.xml",
        "product_re": r"^https?://akibastore\.mitiendanube\.com/productos/[^/]+/?$",
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
        "sitemap": "https://tiendadeamigo.com.ar/sitemap.xml",
        "product_re": None,
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
    {
        "id": "jm", "name": "JM Juegos", "short": "JM Juegos",
        "base": "https://jmjuegos.mitiendanube.com", "kind": "tiendanube",
        "sitemap": "https://jmjuegos.mitiendanube.com/sitemap.xml",
        "product_re": r"^https?://jmjuegos\.mitiendanube\.com/productos/[^/]+/?$",
        "categories": [("/productos/", "Otros", "—")],
    },
    {
        "id": "club", "name": "El Club del Coleccionismo", "short": "El Club",
        "base": "https://www.elclubdelcoleccionismo.com.ar", "kind": "tiendanube",
        "sitemap": "https://www.elclubdelcoleccionismo.com.ar/sitemap.xml",
        "product_re": r"^https?://(www\.)?elclubdelcoleccionismo\.com\.ar/productos/[^/]+/?$",
        "categories": [("/productos/", "Otros", "—")],
    },
    {
        "id": "express", "name": "Express Game", "short": "Express Game",
        "base": "https://www.expressgame.com.ar", "kind": "tiendanube",
        "sitemap": "https://www.expressgame.com.ar/sitemap.xml",
        "product_re": r"^https?://(www\.)?expressgame\.com\.ar/productos/[^/]+/?$",
        "categories": [("/productos/", "Otros", "—")],
    },
    {
        # No pude abrir su sitemap desde acá: se prueba igual y, si no se puede leer, se omite.
        "id": "retro", "name": "Retro Family", "short": "Retro Family",
        "base": "https://www.retrofamily.com.ar", "kind": "tiendanube", "optional": True,
        "sitemap": "https://www.retrofamily.com.ar/sitemap.xml",
        "product_re": r"^https?://(www\.)?retrofamily\.com\.ar/productos/[^/]+/?$",
        "categories": [("/productos/", "Otros", "—")],
    },
    {
        # Pency carga los productos con JavaScript: lectura experimental.
        "id": "frodo", "name": "Frodo Store", "short": "Frodo Store",
        "base": "https://pency.app/frodostore", "kind": "pency", "optional": True,
        "categories": [],
    },
]

session = requests.Session()
session.headers["User-Agent"] = UA
tl = threading.local()
print_lock = threading.Lock()


def say(msg):
    name = getattr(tl, "name", "")
    with print_lock:               # evita que se mezclen las líneas de las dos tiendas
        print(f"[{name}] {msg}" if name else msg, flush=True)


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


def key(u):
    return urllib.parse.urlparse(u).path.rstrip("/")


def fetch_text(url):
    for attempt in range(4):
        r = session.get(url, timeout=30)
        if r.status_code in (429, 503) and attempt < 3:
            time.sleep(5 * 2 ** attempt)
            continue
        r.raise_for_status()
        break
    data = r.content
    if data[:2] == b"\x1f\x8b":          # sitemap comprimido (.gz)
        data = gzip.decompress(data)
    return data.decode("utf-8", "replace")


def sitemap_urls(url, depth=0):
    # Devuelve todas las URLs de un sitemap (entrando a los sitemaps anidados, menos el del blog).
    try:
        text = fetch_text(url)
    except Exception as e:
        say(f"  ! sitemap {url}: {e}")
        return []
    out = []
    for loc in re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.S):
        loc = html.unescape(loc)
        if re.search(r"\.xml(\.gz)?/?$", loc):
            if depth < 2 and "blog" not in loc:
                time.sleep(DELAY)
                out += sitemap_urls(loc, depth + 1)
        else:
            out.append(loc)
    return out


PLATFORMS = [(r"\bps1\b|playstation 1\b", "PS1"), (r"\bps2\b|playstation 2\b", "PS2"),
             (r"\bps3\b|playstation 3\b", "PS3"), (r"\bps4\b|playstation 4\b", "PS4"),
             (r"\bps5\b|playstation 5\b", "PS5"), (r"\bpsp\b", "PSP"), (r"ps vita|\bvita\b", "PS Vita"),
             (r"\bn64\b|nintendo 64", "N64"), (r"saturn", "Saturn"), (r"dreamcast", "Dreamcast"),
             (r"genesis|megadrive|mega drive", "Genesis"), (r"switch 2", "Switch 2"), (r"switch", "Switch"),
             (r"gamecube", "GameCube"), (r"wii ?u", "Wii U"), (r"\bwii\b", "Wii"), (r"\b3ds\b", "3DS"),
             (r"nintendo ds|\bnds\b|\bds\b", "DS"), (r"xbox 360", "Xbox 360"), (r"xbox one", "Xbox One"),
             (r"game ?boy", "Game Boy")]


def guess_platform(text):
    t = " " + text.lower().replace("-", " ") + " "
    for pat, name in PLATFORMS:
        if re.search(pat, t):
            return name
    return "—"


def guess_category(name):
    t = name.lower()
    if re.search(r"figura|funko|blokees|figuarts|marvel legends|zoteki|hasbro|neca", t):
        return "Figuras"
    if re.search(r"\bconsola\b|classic mini|\bslim\b|\bfat\b|wii mini|ds lite|game ?boy (dmg|pocket|color|advance)|game and watch|portatil", t):
        return "Consolas"
    if re.search(r"joystick|\bcontrol\b|controller|mando|cargador|cable|memory card|estuche|funda|soporte|auricular|everdrive|arcade stick|powerbank", t):
        return "Accesorios"
    return "Juegos"


def parse_product_page(url):
    # Lee una página de producto suelta. Devuelve None si la página no es un producto.
    r = get_page(url)
    if r is None:
        return False
    soup = BeautifulSoup(r.text, "html.parser")
    name, price, stock = None, None, 1
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except ValueError:
            continue
        for d in (data if isinstance(data, list) else [data]):
            nodes = d.get("@graph", [d]) if isinstance(d, dict) else []
            for g in nodes:
                if isinstance(g, dict) and g.get("@type") == "Product":
                    name = g.get("name") or name
                    offers = g.get("offers")
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if isinstance(offers, dict):
                        try:
                            price = float(offers.get("price") or offers.get("lowPrice"))
                        except (TypeError, ValueError):
                            pass
                        if "OutOfStock" in str(offers.get("availability", "")):
                            stock = 0
    if not name:
        og = soup.find("meta", property="og:type")
        if not (og and og.get("content") == "product"):
            return None
        t = soup.find("meta", property="og:title")
        name = t.get("content") if t else None
        for prop in ("product:price:amount", "og:price:amount"):
            m = soup.find("meta", property=prop)
            if m and m.get("content"):
                try:
                    price = float(m["content"])
                except ValueError:
                    pass
    if not name:
        return None
    if soup.select_one(".label-soldout") or price == 0:
        stock = 0
    if not stock:
        price = None
    return {"n": name.strip(), "pr": price, "st": stock}


def completar_con_sitemap(store, found, debug=False):
    # Control de cobertura: compara lo encontrado por categorías con el sitemap de la tienda.
    base = store["base"]
    skip = {key(base + p) for p, _, _ in store["categories"]} | {"", "/productos", "/contacto"}
    rx = re.compile(store["product_re"]) if store.get("product_re") else None
    in_map = {}
    for u in sitemap_urls(store["sitemap"]):
        k = key(u)
        if k in skip or (rx and not rx.match(u)):
            continue
        in_map.setdefault(k, u)
    known = {key(u) for u in found}
    missing = [u for k, u in in_map.items() if k not in known]
    say(f"  Sitemap: {len(in_map)} páginas candidatas, {len(in_map) - len(missing)} ya cubiertas "
          f"por las categorías, {len(missing)} faltaban")
    store["stats"] = {"sitemap": len(in_map), "agregados": 0, "sin_leer": len(missing)}
    if debug or not missing:
        return
    cats = [(key(base + p), c, pl) for p, c, pl in store["categories"]]

    def from_path(u):
        k = key(u)
        for cp, c, pl in cats:
            if cp and c != "Otros" and k.startswith(cp + "/"):
                return c, pl
        return None

    today = datetime.date.today()
    old = store.get("old", {})

    def fresh(o):
        # Un agotado leído hace menos de 7 días, o cualquier producto leído hoy, se reutiliza.
        try:
            age = (today - datetime.date.fromisoformat(o.get("t", ""))).days
        except ValueError:
            return False
        return age < (7 if o.get("st") == 0 else 1)

    todo, reused = [], 0
    for u in missing:
        o = old.get(u)
        if o and fresh(o):
            found[u] = o
            reused += 1
        else:
            todo.append(u)
    todo.sort(key=lambda u: u in old)          # primero los productos nunca vistos
    deadline = time.time() + SITEMAP_BUDGET

    def fetch(u):
        if time.time() > deadline:
            return u, "skip"
        time.sleep(DELAY / 2)
        return u, parse_product_page(u)

    with ThreadPoolExecutor(WORKERS) as ex:
        results = list(ex.map(fetch, todo[:MAX_EXTRA]))
    added = skipped = failed = 0
    notprod = []
    for u, p in results:
        if p == "skip":
            skipped += 1
            if u in old:
                found[u] = old[u]              # dejo lo anterior hasta la próxima corrida
            continue
        if p is False:
            failed += 1
        if p is None:
            notprod.append(u)
        if not p:
            continue
        c, pl = from_path(u) or (guess_category(p["n"]), guess_platform(p["n"] + " " + u))
        found[u] = {"s": store["id"], "n": p["n"], "p": pl, "c": c,
                    "pr": p["pr"], "tr": None, "st": p["st"], "u": u, "t": today.isoformat()}
        added += 1
    skipped += max(0, len(todo) - MAX_EXTRA)
    store["stats"]["agregados"] = added + reused
    store["stats"]["sin_leer"] = skipped + failed
    store["stats"]["otros"] = len(notprod)
    if notprod:
        say(f"  Sitemap: {len(notprod)} páginas no se reconocieron como producto (ej.: " + ", ".join(notprod[:3]) + ")")
    note = f"; {skipped} quedan para la próxima corrida" if skipped else ""
    say(f"  Sitemap: {added} leídos, {reused} reutilizados de la corrida anterior, {failed} con error{note}")


def get_page(url):
    r = None
    for attempt in range(4):
        try:
            r = session.get(url, timeout=30)
        except requests.RequestException as e:
            say(f"  ! {url}: {e}")
            return None
        if r.status_code in (429, 503) and attempt < 3:
            try:
                wait = min(int(getattr(r, "headers", {}).get("Retry-After", "")), 60)
            except ValueError:
                wait = 5 * 2 ** attempt
            time.sleep(wait)          # la tienda pidió esperar: reintento con pausa
            continue
        break
    if r.status_code != 200:
        if r.status_code != 404:
            say(f"  ! {url}: HTTP {r.status_code}")
        return None
    return r


def crawl_store(store, debug=False):
    parser = parse_tiendanube if store["kind"] == "tiendanube" else parse_empretienda
    base, found = store["base"], {}
    store.setdefault("page_param", "page")
    probed = False
    for path, cat, plat in store["categories"]:
        seen_here = set()
        page = 1
        while page <= MAX_PAGES:
            r = get_page(f"{base}{path}?{store['page_param']}={page}")
            if r is None:
                break
            if debug and page == 1 and not found:
                Path(f"debug_{store['id']}.html").write_text(r.text, encoding="utf-8")
            items = parser(BeautifulSoup(r.text, "html.parser"), base)
            new = [i for i in items if i["u"] not in seen_here]
            if debug and page == 1:
                say(f"  {path}: {len(items)} productos detectados en la página 1")
            if not new and page == 2 and not probed and len(seen_here) >= 10:
                # La página 2 no trae nada nuevo con "?page=2": pruebo otros nombres de parámetro.
                probed = True
                for alt in ("pagina", "p", "pg", "pag"):
                    time.sleep(DELAY)
                    r2 = get_page(f"{base}{path}?{alt}=2")
                    if r2 is None:
                        continue
                    its = parser(BeautifulSoup(r2.text, "html.parser"), base)
                    fresh = [i for i in its if i["u"] not in seen_here]
                    if fresh:
                        store["page_param"] = alt
                        new = fresh
                        say(f"  (la paginación de esta tienda usa ?{alt}=N)")
                        break
                else:
                    say("  (no pude detectar la paginación; completo con el sitemap)")
            if not new:
                break
            for i in new:
                seen_here.add(i["u"])
                if i["u"] not in found:
                    found[i["u"]] = {"s": store["id"], "n": i["n"], "p": plat if plat != "—" else guess_platform(i["n"] + " " + i["u"]),
                                     "c": cat if cat != "Otros" else guess_category(i["n"]),
                                     "pr": i["pr"], "tr": i["tr"], "st": i["st"], "u": i["u"]}
            page += 1
            time.sleep(DELAY)
        say(f"  {cat} / {plat}: {len(seen_here)} productos")
        if debug:
            break          # en modo debug solo la primera categoría
    completar_con_sitemap(store, found, debug)
    return list(found.values())


def crawl_pency(store, debug=False):
    # Pency (Next.js): intenta leer los productos que vengan dentro de los datos de la página.
    r = get_page(store["base"])
    if r is None:
        return []
    if debug:
        Path(f"debug_{store['id']}.html").write_text(r.text, encoding="utf-8")
    tag = BeautifulSoup(r.text, "html.parser").find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        say("  Pency: no encontré los datos de productos en la página (se cargan con JavaScript).")
        return []
    try:
        data = json.loads(tag.string)
    except ValueError:
        return []
    found = {}

    def walk(o):
        if isinstance(o, dict):
            name = o.get("title") or o.get("name")
            price = o.get("price")
            if isinstance(name, str) and isinstance(price, (int, float)) and not isinstance(price, bool):
                pid = str(o.get("id") or o.get("_id") or name)
                avail = o.get("available", o.get("stock"))
                st = 0 if (avail is False or avail == 0) else 1
                found.setdefault(pid, {"s": store["id"], "n": name.strip(),
                                       "p": guess_platform(name), "c": guess_category(name),
                                       "pr": float(price) if st and price else None, "tr": None,
                                       "st": st, "u": store["base"]})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    say(f"  Pency: {len(found)} productos leídos")
    return list(found.values())


def run_store(store, debug):
    tl.name = store["short"]
    if store["kind"] == "pency":
        return store["id"], crawl_pency(store, debug)
    return store["id"], crawl_store(store, debug)


def main():
    t0 = time.time()
    args = sys.argv[1:]
    debug = "--debug" in args
    only = [a for a in args if not a.startswith("--")]
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"items": []}
    for st in STORES:
        st["old"] = {i["u"]: i for i in old["items"] if i["s"] == st["id"]}
    todo = [st for st in STORES if not only or st["id"] in only]
    with ThreadPoolExecutor(max(1, len(todo))) as ex:       # una tienda por hilo
        results = dict(ex.map(lambda st: run_store(st, debug), todo))
    items, failed = [], False
    for store in STORES:
        prev = [i for i in old["items"] if i["s"] == store["id"]]
        if debug or store["id"] not in results:
            items += prev
            continue
        new = results[store["id"]]
        if not new and store.get("optional"):
            say(f"{store['short']}: no se pudo leer (tienda opcional), se omite.")
            items += prev
            continue
        if not new or (prev and len(new) < 0.5 * len(prev)):
            say(f"!! {store['short']}: solo {len(new)} productos (antes {len(prev)}). Conservo los datos anteriores.")
            items += prev
            failed = True
        else:
            items += new
    if debug:
        return
    stats = dict(old.get("stats", {}))
    for store in STORES:
        if store["id"] in results and store.get("stats") and not debug:
            n = sum(1 for i in items if i["s"] == store["id"])
            stats[store["id"]] = {**store["stats"], "total": n,
                                  "en_stock": sum(1 for i in items if i["s"] == store["id"] and i["st"])}
    data = {
        "stats": stats,
        "updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "stores": [{"id": st["id"], "name": st["name"], "short": st["short"]} for st in STORES],
        "items": items,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"Listo: {len(items)} productos en total ({int(time.time() - t0)} s).")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
