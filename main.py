#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper - WEB & API HYBRID
Python 3.11+
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from PIL import Image
from rapidfuzz import fuzz
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

# ============================================================
# CONFIGURAZIONE
# ============================================================

BASE_DIR = Path(__file__).parent

GAMES_FILE = BASE_DIR / "games.txt"
CACHE_FILE = BASE_DIR / "cache.json"

COVERS_DIR = BASE_DIR / "covers"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
OUTPUT_DIR = BASE_DIR / "output"

HTML_OUTPUT = OUTPUT_DIR / "index.html"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = 25
MIN_SCREENSHOTS = 3

# ============================================================
# LOGGING & CARTELLE
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("gog_gallery")

for d in [COVERS_DIR, SCREENSHOTS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# SESSIONE HTTP (Inclusi Cookie per bypass Age-Gate di Steam)
# ============================================================

def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    
    # Cookie Steam per giochi maturi/horror
    session.cookies.set('wants_mature_content', '1', domain='store.steampowered.com')
    session.cookies.set('birthtime', '288028801', domain='store.steampowered.com')
    session.cookies.set('lastagecheckage', '1-0-1990', domain='store.steampowered.com')
    
    return session

session = build_session()

# ============================================================
# GESTIONE CACHE
# ============================================================

def load_cache() -> Dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_cache(data: Dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

cache = load_cache()

# ============================================================
# HELPERS
# ============================================================

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-_. ]", "", name)
    return name.strip().replace(" ", "_").lower()

def slugify(name: str) -> str:
    # Trasforma "Alder's Blood Prologue" in "alders_blood_prologue" per l'URL di GOG
    s = name.lower().strip()
    s = s.replace("'", "")
    s = re.sub(r"[^\w\s-]", "_", s)
    s = re.sub(r"[\s_-]+", "_", s)
    return s.strip("_")

def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()

def likely_gameplay(url: str) -> bool:
    u = url.lower()
    blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon"]
    return not any(x in u for x in blacklist)

def download_image(url: str, path: Path) -> bool:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.save(path, "JPEG", quality=85, optimize=True)
        return True
    except Exception as e:
        logger.warning(f"Download fallito: {url} -> {e}")
        return False

# ============================================================
# SCRAPER DIRETTO PAGINA WEB GOG (Risolve il problema dei Prologue)
# ============================================================

def gog_scrape_direct_web(game_name: str) -> Tuple[Optional[str], List[str]]:
    """
    Visita direttamente la pagina web del negozio GOG usando lo slug del titolo
    ed estrae le immagini dal carosello dei thumbnail.
    """
    game_slug = slugify(game_name)
    url = f"https://www.gog.com/en/game/{game_slug}"
    logger.info(f"Tentativo Scraping Diretto Web GOG: {url}")
    
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            # Secondo tentativo: se fallisce con lo slug, prova a cercare sull'HTML della ricerca di GOG
            search_url = f"https://www.gog.com/en/games?query={requests.utils.quote(game_name)}"
            r_search = session.get(search_url, timeout=REQUEST_TIMEOUT)
            soup_s = BeautifulSoup(r_search.text, "html.parser")
            found_tile = soup_s.select_one("a[href*='/en/game/']")
            if found_tile:
                url = "https://www.gog.com" + found_tile.get("href")
                r = session.get(url, timeout=REQUEST_TIMEOUT)
                
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            screenshots = []
            cover = None
            
            # Cerca nel contenitore indicato da te (ProductCardThumbnailsSlider)
            slides = soup.select('[selenium-id="ProductCardThumbnailsSlider"] img, .productcard-thumbnails-slider__slide img')
            for img in slides:
                src = img.get("src") or img.get("data-src") or img.get("srcset")
                if src:
                    # Pulisce i parametri di formattazione o query dalle immagini di GOG
                    src = src.split('?')[0].split(' ')[0]
                    if src.startswith("//"):
                        src = "https:" + src
                    
                    # Se l'immagine contiene formatter o dimensioni ridotte, prendiamo la versione pulita grande
                    src = re.sub(r'_\d+x\d+\.(jpg|png|webp)', r'.\1', src)
                    src = src.replace("{formatter}", "gallery_1600")
                    
                    if src.startswith("http") and src not in screenshots:
                        screenshots.append(src)
            
            # Trova la cover di sfondo principale della pagina se disponibile
            hero_img = soup.select_one(".gog-galaxy-background, .productcard-hero-bg img")
            if hero_img:
                h_src = hero_img.get("src") or hero_img.get("style")
                if h_src and "url(" in h_src:
                    h_src = re.search(r'url\([\'"]?([^\'"]+)[\'"]?\)', h_src).group(1)
                if h_src and h_src.startswith("http"):
                    cover = h_src
                    
            if screenshots:
                if not cover:
                    cover = screenshots[0]
                logger.info(f"Scraping Web GOG Riuscito! Trovati {len(screenshots)} asset.")
                return cover, screenshots
                
    except Exception as e:
        logger.warning(f"Errore durante lo scraping web di GOG per {game_name}: {e}")
    return None, []

# ============================================================
# MOTORE DI RICERCA REGOLARE (API CATALOGO)
# ============================================================

def gog_catalog_search(game_name: str) -> Tuple[Optional[str], List[str]]:
    try:
        url = "https://catalog.gog.com/v1/catalog"
        r = session.get(url, params={"query": game_name, "limit": 5}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        products = r.json().get("products", [])
        if not products: return None, []

        best, best_score = None, 0
        for product in products:
            score = fuzz.token_set_ratio(game_name.lower(), product.get("title", "").lower())
            if score > best_score:
                best_score, best = score, product

        if best and best_score >= 70: 
            cover = None
            for k in ["coverHorizontal", "coverVertical", "backgroundImage", "image"]:
                val = best.get(k)
                if isinstance(val, str): cover = val; break
                if isinstance(val, dict) and val.get("url"): cover = val["url"]; break
                
            screenshots = []
            for shot in best.get("screenshots", []):
                src = shot if isinstance(shot, str) else (shot.get("url") or shot.get("image") if isinstance(shot, dict) else None)
                if src: screenshots.append(src)
                
            return cover, screenshots
    except Exception:
        pass
    return None, []

# ============================================================
# MOTORE DI RICERCA FALLBACK: STEAM
# ============================================================

def steam_search_fallback(game_name: str) -> Tuple[Optional[str], List[str]]:
    try:
        url = f"https://store.steampowered.com/search/?term={requests.utils.quote(game_name)}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("a.search_result_row")
        
        best_url, best_score = None, 0
        for row in rows[:5]:
            title = row.select_one("span.title")
            if not title: continue
            score = fuzz.token_set_ratio(game_name.lower(), title.text.lower())
            if score > best_score:
                best_score, best_url = score, row.get("href")
        
        if best_url and best_score >= 65:
            r = session.get(best_url, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            cover_el = soup.select_one("img.game_header_image_full")
            cover = cover_el.get("src") if cover_el else None
            
            screenshots = []
            for a in soup.select("a.highlight_screenshot_link"):
                href = a.get("href")
                if href: 
                    screenshots.append(href.split('?')[0].replace(".600x338", ""))
            return cover, screenshots
    except Exception:
        pass
    return None, []

# ============================================================
# PIPELINE DI ELABORAZIONE GIOCO
# ============================================================

def process_game(game_name: str) -> Dict:
    if game_name in cache:
        cached = cache[game_name]
        if cached.get("found") and (cached.get("cover") or cached.get("screenshots")):
            if (not cached.get("cover") or Path(cached["cover"]).exists()) and all(Path(x).exists() for x in cached.get("screenshots", [])):
                return cached

    logger.info(f"Elaborazione: {game_name}")
    safe = sanitize_filename(game_name)
    result = {"name": game_name, "cover": None, "screenshots": [], "found": False}
    cover_url, screenshots = None, []

    # STRATEGIA 1: Scraping Diretto della Pagina Web GOG (Fornito da te, ideale per Demo/Prologue)
    cover_url, screenshots = gog_scrape_direct_web(game_name)

    # STRATEGIA 2: Fallback su Catalogo API GOG se la pagina web non risponde
    if not screenshots:
        cover_url, screenshots = gog_catalog_search(game_name)

    # STRATEGIA 3: Fallback su Steam Store
    if len(screenshots) < MIN_SCREENSHOTS or not cover_url:
        s_cover, s_shots = steam_search_fallback(game_name)
        if not cover_url: cover_url = s_cover
        screenshots.extend(s_shots)

    # Pulizia e Deduplicazione URL
    screenshots = [x for x in screenshots if likely_gameplay(x)]
    screenshots = list(dict.fromkeys(screenshots))

    # Download della Cover
    if cover_url:
        cp = COVERS_DIR / f"{safe}.jpg"
        if cp.exists() or download_image(cover_url, cp):
            result["cover"] = str(cp)

    # Download degli Screenshot con controllo dei duplicati tramite Hash MD5
    downloaded, hashes = [], set()
    for idx, url in enumerate(screenshots):
        if len(downloaded) >= 6: break 
        out = SCREENSHOTS_DIR / f"{safe}_{idx+1}.jpg"
        
        if out.exists():
            downloaded.append(str(out))
            continue
            
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            digest = md5_bytes(r.content)
            if digest in hashes: continue
            hashes.add(digest)

            img = Image.open(io.BytesIO(r.content)).convert("RGB")
            if img.width > 1280:
                img.thumbnail((1280, 720), Image.Resampling.LANCZOS)
            img.save(out, "JPEG", quality=80, optimize=True)
            
            downloaded.append(str(out))
        except Exception:
            pass

    result["screenshots"] = downloaded
    
    if result["cover"] or downloaded:
        result["found"] = True
        if not result["cover"] and downloaded:
            result["cover"] = downloaded[0]

    cache[game_name] = result
    save_cache(cache)
    return result

# ============================================================
# GENERATORE GALLERIA HTML (Solo HTML + Lightbox Galleria)
# ============================================================

def generate_html(results: List[Dict]):
    valid = [x for x in results if x.get("found")]
    
    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Game Gallery HTML</title>
<style>
*{box-sizing:border-box;}
body{background:#111;color:#eee;font-family:Arial,sans-serif;margin:30px;padding-bottom:80px;}
h1{margin-bottom:30px;font-size:28px;border-bottom:2px solid #222;padding-bottom:12px;}
.game{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:10px;padding:20px;margin-bottom:25px;}
.title{font-size:22px;font-weight:bold;margin-bottom:15px;color:#fff;}
.row{display:grid;grid-template-columns:220px repeat(auto-fill, minmax(260px, 1fr));gap:12px;}
.cover{width:220px;height:150px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #3d3d3d;background:#222;}
.shot{width:100%;height:150px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #252525;background:#222;}
img{transition:transform 0.2s, border-color 0.2s;}
img:hover{transform:scale(1.02);border-color:#666;}
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,0.92);display:none;justify-content:center;align-items:center;z-index:999;cursor:pointer;}
.lightbox img{max-width:92%;max-height:92%;border-radius:6px;box-shadow:0 0 20px rgba(255,255,255,0.1);}
</style>
</head>
<body>
<div id="lightbox" class="lightbox" onclick="this.style.display='none'"><img id="lb-img"></div>
<h1>Galleria Multimediale Giochi</h1>
"""

    for game in valid:
        html += f'<div class="game">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        
        if game.get("cover") and Path(game["cover"]).exists():
            cover_rel = os.path.relpath(game["cover"], OUTPUT_DIR).replace("\\", "/")
            html += f'  <img class="cover" src="{cover_rel}" onclick="openLb(this.src)">\n'
            
        for shot in game.get("screenshots", []):
            if Path(shot).exists():
                shot_rel = os.path.relpath(shot, OUTPUT_DIR).replace("\\", "/")
                html += f'  <img loading="lazy" class="shot" src="{shot_rel}" onclick="openLb(this.src)">\n'
            
        html += "</div>\n</div>\n"

    html += """
<script>
function openLb(src){
    if(!src)return;
    document.getElementById("lb-img").src = src;
    document.getElementById("lightbox").style.display = "flex";
}
</script>
</body>
</html>
"""

    with open(HTML_OUTPUT, "w", encoding="utf-8") as f:
        f.write(html)

# ============================================================
# AVVIO SCRAPER
# ============================================================

def main():
    if not GAMES_FILE.exists():
        logger.error(f"File {GAMES_FILE.name} non trovato.")
        return

    with open(GAMES_FILE, "r", encoding="utf-8") as f:
        games = [x.strip() for x in f.readlines() if x.strip()]

    logger.info(f"Lancio scraping per {len(games)} giochi.")
    results = []
    found_count = 0

    for game in tqdm(games, desc="Download in corso"):
        try:
            res = process_game(game)
            results.append(res)
            if res["found"]: found_count += 1
            
            if len(results) % 5 == 0:
                generate_html(results)
            time.sleep(1.2)
        except Exception as e:
            logger.exception(f"Errore critico su {game}: {e}")

    generate_html(results)
    save_cache(cache)

    print(f"\nScraping Concluso.\nGiochi totali: {len(games)}\nSalvati nell'HTML: {found_count}\nOutput: {HTML_OUTPUT}")

if __name__ == "__main__":
    main()