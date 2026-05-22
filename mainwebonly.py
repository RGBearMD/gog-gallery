#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper - WEB ONLY VERSION (URL-PARSED & MOBILE OPTIMIZED)
Output: indexwebonly.html | Cache: cachewebonly.json
Python 3.11+
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

# ============================================================
# CONFIGURAZIONE
# ============================================================

BASE_DIR = Path(__file__).parent

GAMES_FILE = BASE_DIR / "games.txt"
CACHE_FILE = BASE_DIR / "cachewebonly.json"  
OUTPUT_DIR = BASE_DIR / "output"
HTML_OUTPUT = OUTPUT_DIR / "indexwebonly.html"  

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
MIN_SCREENSHOTS = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("gog_gallery_web")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# SESSIONE HTTP
# ============================================================

def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=5, connect=5, read=5, backoff_factor=1.2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

session = build_session()

# ============================================================
# GESTIONE CACHE
# ============================================================

def load_cache() -> Dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {}

def save_cache(data: Dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

cache = load_cache()

# ============================================================
# HELPERS MULTIMEDIALI ED ESTRAZIONE STRUTTURE GOG
# ============================================================

def likely_gameplay(url: str) -> bool:
    u = url.lower()
    blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon", "product_tile"]
    return not any(x in u for x in blacklist)

def extract_highest_res_from_srcset(srcset_text: str) -> Optional[str]:
    """ Estrae l'URL pulito ad alta risoluzione dai blocchi srcset responsivi di GOG """
    if not srcset_text:
        return None
    candidates = []
    for part in srcset_text.split(','):
        tokens = part.strip().split()
        if tokens:
            url = tokens[0].strip()
            if url.startswith("//"):
                url = "https:" + url
            if "gog-statics.com" in url:
                candidates.append(url)
    if candidates:
        high_res = [c for c in candidates if "_2x" in c or "1600" in c or "product_card_v2_mobile_slider" not in c]
        if high_res:
            return high_res[-1]
        return candidates[-1]
    return None

# ============================================================
# SCRAPER DIRETTO DA URL SPECIFICO
# ============================================================

def gog_scrape_by_direct_url(url: str) -> Tuple[Optional[str], List[str]]:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            html_text = r.text
            soup = BeautifulSoup(html_text, "html.parser")
            screenshots = []
            cover = None
            
            # 1. Parsing della Cover Principale (Dai metadati o dal tag picture di testa)
            cover_picture = soup.select_one(".product-tile__cover-picture, .js-cover-image, meta[property='og:image']")
            if cover_picture:
                if cover_picture.name == 'meta':
                    cover = cover_picture.get("content")
                else:
                    sources = cover_picture.select("source, img")
                    for src_tag in sources:
                        srcset = src_tag.get("srcset") or src_tag.get("src")
                        res_url = extract_highest_res_from_srcset(srcset)
                        if res_url:
                            cover = res_url
                            break

            # 2. Parsing degli Screenshot dallo slider responsivo
            slider_items = soup.select("[selenium-id='ProductCardThumbnailsSlider'] picture, .productcard-thumbnails-slider__slide picture")
            for item in slider_items:
                for source in item.select("source, img"):
                    srcset = source.get("srcset") or source.get("src")
                    img_url = extract_highest_res_from_srcset(srcset)
                    if img_url and img_url not in screenshots:
                        # Pulisce i suffissi mobile lasciando il nome del file intatto per il data-full
                        img_url = re.sub(r'_(product_card_v2_mobile_slider|product_card_v2_thumbnail|thumbnail)_\d+x?\d*\.(jpg|png|webp)', r'.\2', img_url)
                        img_url = img_url.replace("_product_card_v2_mobile_slider_450", "").replace("_product_card_v2_mobile_slider_639", "")
                        screenshots.append(img_url)

            # 3. Rete a strascico regex di emergenza (Se i moduli JS nascondono lo slider responsivo)
            if not screenshots:
                links_in_raw = re.findall(r'(https?://images\.gog-statics\.com/[a-f0-9_]+(?:\.[a-zA-Z0-9]+)?)', html_text)
                for raw_url in links_in_raw:
                    if raw_url not in screenshots and "product_tile" not in raw_url:
                        screenshots.append(raw_url)

            # Filtri di pulizia
            screenshots = [x for x in screenshots if likely_gameplay(x)]
            if cover in screenshots:
                screenshots.remove(cover)

            if screenshots and not cover:
                cover = screenshots[0]

            return cover, screenshots
    except Exception as e:
        logger.warning(f"Errore caricamento diretto per URL {url}: {e}")
    return None, []

# ============================================================
# PIPELINE DI ELABORAZIONE GIOCO
# ============================================================

def process_game(game_name: str, url: str) -> Dict:
    # Controlla se il gioco è già memorizzato correttamente con i suoi screenshot
    if game_name in cache and cache[game_name].get("found") and len(cache[game_name].get("screenshots", [])) >= MIN_SCREENSHOTS:
        return cache[game_name]

    logger.info(f"Scraping diretto: {game_name}")
    result = {"name": game_name, "url": url, "cover": None, "screenshots": [], "found": False}

    cover_url, screenshots = gog_scrape_by_direct_url(url)

    # Rimuove i duplicati mantenendo l'ordine e imposta il limite a 6 screenshot per gioco
    screenshots = list(dict.fromkeys(screenshots))[:6]

    if cover_url: result["cover"] = cover_url
    result["screenshots"] = screenshots
    
    if result["cover"] or screenshots:
        result["found"] = True
        if not result["cover"] and screenshots:
            result["cover"] = screenshots[0]

    cache[game_name] = result
    save_cache(cache)
    return result

# ============================================================
# GENERATORE HTML (RISPARMIO DATI CELLULARE + LAZY LOADING)
# ============================================================

def generate_html(results: List[Dict]):
    valid = [x for x in results if x.get("found")]
    
    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cloud Game Gallery - Mobile Ultra-Light</title>
<style>
*{box-sizing:border-box;}
body{background:#111;color:#eee;font-family:Arial,sans-serif;margin:15px;padding-bottom:80px;}
h1{margin-bottom:20px;font-size:24px;border-bottom:2px solid #222;padding-bottom:12px;}
.game{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:10px;padding:15px;margin-bottom:25px;}
.title{font-size:18px;font-weight:bold;margin-bottom:12px;color:#fff;}
.row{display:grid;grid-template-columns:140px repeat(auto-fill, minmax(130px, 1fr));gap:10px;}
.cover{width:140px;height:100px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #ffaa00;background:#222;}
.shot{width:100%;height:100px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #252525;background:#222;}
img{transition:transform 0.2s, border-color 0.2s;}
img:hover{transform:scale(1.02);border-color:#666;}
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,0.95);display:none;justify-content:center;align-items:center;z-index:999;cursor:pointer;}
.lightbox img{max-width:95%;max-height:92%;border-radius:6px;box-shadow:0 0 20px rgba(0,0,0,0.7);}
@media(max-width: 600px) {
  .row { grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); }
  .cover { width: 100%; }
}
</style>
</head>
<body>
<div id="lightbox" class="lightbox" onclick="this.style.display='none'"><img id="lb-img"></div>
<h1>Galleria Multimediale (Mobile Optimized)</h1>
"""

    for game in valid:
        html += f'<div class="game">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        
        # Salviamo la risorsa 1080p nativa dentro data-full. Il browser scaricherà solo la miniatura iniziale leggera
        if game.get("cover"):
            html += f'  <img class="cover" src="{game["cover"]}" data-full="{game["cover"]}" loading="lazy" title="Cover" onerror="handleBrokenLink(this)" onclick="openLb(this)">\n'
            
        for shot in game.get("screenshots", []):
            # OTTIMIZZAZIONE GB CELLULARE: Chiede al server GOG una versione ridotta (thumbnail) per consumare meno dati nella griglia
            thumb_url = shot
            if "gog-statics.com" in shot and not any(x in shot for x in ["_thumbnail", "_slider"]):
                thumb_url = shot.replace(".jpg", "_product_card_v2_thumbnail_271.jpg").replace(".png", "_product_card_v2_thumbnail_271.png")

            html += f'  <img class="shot" src="{thumb_url}" data-full="{shot}" loading="lazy" onerror="handleBrokenLink(this)" onclick="openLb(this)">\n'
            
        html += "</div>\n</div>\n"

    html += """
<script>
function openLb(element){
    var fullSrc = element.getAttribute("data-full");
    if(!fullSrc || fullSrc.includes('placehold.co')) return;
    
    var lbImg = document.getElementById("lb-img");
    lbImg.src = fullSrc; // Il download dell'immagine da 2MB scatta SOLO ADESSO al click dell'utente
    document.getElementById("lightbox").style.display = "flex";
}
function handleBrokenLink(image) {
    image.onerror = null;
    var fullSrc = image.getAttribute("data-full");
    // Se la miniatura generata restituisce un errore 404, carica automaticamente il file originale a piena risoluzione
    if (image.src !== fullSrc) {
        image.src = fullSrc;
    } else {
        image.src = "https://placehold.co/300x200/222/777?text=No+Preview";
        image.style.cursor = "default";
    }
}
</script>
</body>
</html>
"""

    with open(HTML_OUTPUT, "w", encoding="utf-8") as f: f.write(html)

# ============================================================
# AVVIO SCRAPER
# ============================================================

def main():
    if not GAMES_FILE.exists():
        logger.error(f"File {GAMES_FILE.name} mancante.")
        return

    # Lettura e parsing strutturato della lista (Separazione Nome - URL)
    games_to_process = []
    with open(GAMES_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and " - " in line:
                parts = line.split(" - ", 1)
                name = parts[0].strip()
                url = parts[1].strip()
                if url.startswith("http"):
                    games_to_process.append((name, url))

    logger.info(f"Trovati {len(games_to_process)} giochi validi nel file txt. Avvio estrazione diretta...")
    results = []
    found_count = 0

    for name, url in tqdm(games_to_process, desc="Scraping"):
        try:
            res = process_game(name, url)
            results.append(res)
            if res["found"]: found_count += 1
            
            # Rigenera progressivamente l'HTML ogni 5 giochi elaborati
            if len(results) % 5 == 0: generate_html(results)
            time.sleep(1.2)
        except Exception as e:
            logger.exception(f"Errore sul gioco {name}: {e}")

    generate_html(results)
    save_cache(cache)
    print(f"\nScraping concluso!\nGiochi processati: {len(games_to_process)}\nTrovati: {found_count}")
    print(f"Cache aggiornata: {CACHE_FILE.name}\nHTML Generato (Mobile Ready): {HTML_OUTPUT}")

if __name__ == "__main__":
    main()