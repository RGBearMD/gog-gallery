#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper - WEB ONLY VERSION (UNBLOCKED & VERBOSE)
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

REQUEST_TIMEOUT = 15  # Abbassato il timeout per evitare blocchi infiniti
MIN_SCREENSHOTS = 2

# Configurazione Log espliciti visibili subito a schermo
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("gog_gallery_web")

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# SESSIONE HTTP
# ============================================================

def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, connect=3, read=3, backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
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
            print(f"[DEBUG] Caricamento file di cache: {CACHE_FILE.name}...")
            with open(CACHE_FILE, "r", encoding="utf-8") as f: 
                data = json.load(f)
                print(f"[DEBUG] Cache caricata con successo ({len(data)} elementi).")
                return data
        except Exception as e:
            print(f"[ATTENZIONE] Cache corrotta o illeggibile: {e}. Creo una nuova cache.")
    return {}

def save_cache(data: Dict):
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Impossibile salvare la cache: {e}")

cache = load_cache()

# ============================================================
# HELPERS MULTIMEDIALI
# ============================================================

def likely_gameplay(url: str) -> bool:
    u = url.lower()
    blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon", "product_tile"]
    return not any(x in u for x in blacklist)

def extract_highest_res_from_srcset(srcset_text: str) -> Optional[str]:
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
# SCRAPER DIRETTO DA URL
# ============================================================

def gog_scrape_by_direct_url(url: str) -> Tuple[Optional[str], List[str]]:
    try:
        if not url.startswith("http"):
            return None, []
            
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            html_text = r.text
            soup = BeautifulSoup(html_text, "html.parser")
            screenshots = []
            cover = None
            
            # 1. Cover
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

            # 2. Slider Screenshots
            slider_items = soup.select("[selenium-id='ProductCardThumbnailsSlider'] picture, .productcard-thumbnails-slider__slide picture")
            for item in slider_items:
                for source in item.select("source, img"):
                    srcset = source.get("srcset") or source.get("src")
                    img_url = extract_highest_res_from_srcset(srcset)
                    if img_url and img_url not in screenshots:
                        img_url = re.sub(r'_(product_card_v2_mobile_slider|product_card_v2_thumbnail|thumbnail)_\d+x?\d*\.(jpg|png|webp)', r'.\2', img_url)
                        img_url = img_url.replace("_product_card_v2_mobile_slider_450", "").replace("_product_card_v2_mobile_slider_639", "")
                        screenshots.append(img_url)

            # 3. Rete a strascico regex
            if not screenshots:
                links_in_raw = re.findall(r'(https?://images\.gog-statics\.com/[a-f0-9_]+(?:\.[a-zA-Z0-9]+)?)', html_text)
                for raw_url in links_in_raw:
                    if raw_url not in screenshots and "product_tile" not in raw_url:
                        screenshots.append(raw_url)

            screenshots = [x for x in screenshots if likely_gameplay(x)]
            if cover in screenshots:
                screenshots.remove(cover)

            if screenshots and not cover:
                cover = screenshots[0]

            return cover, screenshots
    except Exception as e:
        logger.debug(f"Errore connessione per URL {url}: {e}")
    return None, []

# ============================================================
# PIPELINE DI ELABORAZIONE GIOCO
# ============================================================

def process_game(game_name: str, url: str) -> Dict:
    if game_name in cache and cache[game_name].get("found") and len(cache[game_name].get("screenshots", [])) >= MIN_SCREENSHOTS:
        return cache[game_name]

    result = {"name": game_name, "url": url, "cover": None, "screenshots": [], "found": False}
    cover_url, screenshots = gog_scrape_by_direct_url(url)
    
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
# GENERATORE HTML
# ============================================================

def generate_html(results: List[Dict]):
    valid = [x for x in results if x.get("found")]
    
    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cloud Game Gallery</title>
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
.lightbox img{max-width:95%;max-height:92%;border-radius:6px;}
@media(max-width: 600px) {
  .row { grid-template-columns: repeat(auto-fill, minmax(100px, 1fr)); }
  .cover { width: 100%; }
}
</style>
</head>
<body>
<div id="lightbox" class="lightbox" onclick="this.style.display='none'"><img id="lb-img"></div>
<h1>Galleria Multimediale</h1>
"""

    for game in valid:
        html += f'<div class="game">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        if game.get("cover"):
            html += f'  <img class="cover" src="{game["cover"]}" data-full="{game["cover"]}" loading="lazy" onerror="handleBrokenLink(this)" onclick="openLb(this)">\n'
        for shot in game.get("screenshots", []):
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
    document.getElementById("lb-img").src = fullSrc;
    document.getElementById("lightbox").style.display = "flex";
}
function handleBrokenLink(image) {
    image.onerror = null;
    var fullSrc = image.getAttribute("data-full");
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
# AVVIO SCRAPER PRINCIPALE
# ============================================================

def main():
    print("[DEBUG] Controllo esistenza file games.txt...")
    if not GAMES_FILE.exists():
        print(f"[ERRORE] Il file {GAMES_FILE.name} non esiste nella cartella dello script!")
        return

    print("[DEBUG] Lettura in corso di games.txt...")
    games_to_process = []
    
    with open(GAMES_FILE, "r", encoding="utf-8") as f:
        for index, line in enumerate(f, 1):
            line_str = line.strip()
            if not line_str:
                continue
            
            # Trova l'URL di GOG dentro la riga
            url_match = re.search(r"(https?://(?:www\.)?gog\.com/[^\s]+)", line_str, re.IGNORECASE)
            
            if url_match:
                url = url_match.group(1).strip()
                raw_name = line_str[:url_match.start()]
                name = re.sub(r"[\s\-\–\—\=\_]+$", "", raw_name).strip()
                if not name:
                    name = url.split('/')[-1].replace('_', ' ').title()
            else:
                name = line_str
                url = f"https://www.gog.com/en/games?query={requests.utils.quote(name)}"
            
            games_to_process.append((name, url))

    total_games = len(games_to_process)
    print(f"[DEBUG] Analisi completata. Trovati esattamente {total_games} giochi da elaborare.")
    
    if total_games == 0:
        print("[ERRORE] Nessun gioco estratto dal file games.txt! Verifica il contenuto.")
        return

    results = []
    found_count = 0

    print("[DEBUG] Inizio ciclo di scraping multimediale...")
    # La barra tqdm ora è forzata a stampare subito a schermo
    for name, url in tqdm(games_to_process, desc="Avanzamento", total=total_games, mininterval=0.1):
        try:
            res = process_game(name, url)
            results.append(res)
            if res["found"]: 
                found_count += 1
            
            if len(results) % 5 == 0: 
                generate_html(results)
                
            time.sleep(1.1)
        except Exception as e:
            print(f"\n[ERRORE CRITICO] Errore nell'elaborazione di {name}: {e}")

    print("[DEBUG] Generazione HTML finale...")
    generate_html(results)
    print("[DEBUG] Salvataggio della cache...")
    save_cache(cache)
    
    print(f"\n=========================================")
    print(f" SCRAPING COMPLETATO CON SUCCESSO")
    print(f"=========================================")
    print(f"Giochi totali processati: {total_games}")
    print(f"Giochi configurati con successo: {found_count}")
    print(f"File Interfaccia Generato: {HTML_OUTPUT.resolve()}")
    print(f"File Cache Aggiornato: {CACHE_FILE.resolve()}")

if __name__ == "__main__":
    main()