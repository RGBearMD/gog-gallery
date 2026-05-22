#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper - WEB ONLY VERSION (SLUG & SEARCH OPTIMIZED)
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
from rapidfuzz import fuzz
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

# MAPPATURA MANUALE (Risolve istantaneamente le eccezioni di GOG fornite dall'utente)
GOG_HARD_MAPPING = {
    "alone in the dark 1": "alone_in_the_dark_the_trilogy_123",
    "alone in the dark 2": "alone_in_the_dark_the_trilogy_123",
    "alone in the dark 3": "alone_in_the_dark_the_trilogy_123",
    "amerzone the explorer's legacy": "amerzone_the_explorer_legacy",
    "amerzone": "amerzone_the_explorer_legacy",
    "beyond good & evil": "beyond_good_and_evil",
    "beyond good and evil": "beyond_good_and_evil"
}

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
            with open(CACHE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {}

def save_cache(data: Dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

cache = load_cache()

# ============================================================
# HELPERS DI PULIZIA E SLUGIFY MULTI-FORMATO
# ============================================================

def clean_game_name(name: str) -> str:
    s = name.replace("™", "").replace("®", "").replace("©", "")
    s = re.sub(r"\s*[\(\[][0-9]{4}[\)\]]\s*", " ", s)  # Rimuove gli anni (1999)
    s = s.replace(":", " ").replace("-", " ")
    s = re.sub(r"\b(v1\.[0-9]|v[0-9]|remastered|remaster)\b", " ", s, flags=re.IGNORECASE)
    return " ".join(s.split())

def generate_slug_variants(cleaned_name: str) -> List[str]:
    """Genera varianti di slug sia con trattino basso che alto per massima compatibilità GOG"""
    base = cleaned_name.lower().strip().replace("'", "")
    # Sostituisce caratteri speciali non alfa-numerici
    base = re.sub(r"[^\w\s]", "", base)
    
    slug_underscore = re.sub(r"\s+", "_", base)
    slug_dash = re.sub(r"\s+", "-", base)
    
    # Ritorna una lista rimuovendo duplicati mantenendo l'ordine
    return list(dict.fromkeys([slug_underscore, slug_dash]))

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
            if "gog-statics.com" in url or "steamstatic" in url:
                candidates.append(url)
    if candidates:
        high_res = [c for c in candidates if "_2x" in c or "1600" in c or "product_card_v2_mobile_slider" not in c]
        if high_res:
            return high_res[-1]
        return candidates[-1]
    return None

# ============================================================
# INTERFACCIA DI RICERCA INTERNA ED ESTRAZIONE
# ============================================================

def gog_scrape_direct_web(game_name: str) -> Tuple[Optional[str], List[str]]:
    cleaned_name = clean_game_name(game_name)
    lookup_key = cleaned_name.lower()
    
    # Verifica immediata della tabella di mappatura hardcoded per i link forniti
    slugs_to_try = []
    if lookup_key in GOG_HARD_MAPPING:
        slugs_to_try.append(GOG_HARD_MAPPING[lookup_key])
    else:
        slugs_to_try.extend(generate_slug_variants(cleaned_name))

    html_text = ""
    # Tentativo di recupero tramite gli slug diretti calcolati o mappati
    for slug in slugs_to_try:
        url = f"https://www.gog.com/en/game/{slug}"
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                html_text = r.text
                break
        except Exception:
            continue

    # FALLBACK SE L'URL DIRETTO FALLISCE: Interrogazione del catalogo AJAX di ricerca di GOG
    if not html_text:
        try:
            search_api_url = f"https://www.gog.com/en/games?query={requests.utils.quote(cleaned_name)}"
            r_search = session.get(search_api_url, timeout=REQUEST_TIMEOUT)
            if r_search.status_code == 200:
                soup_s = BeautifulSoup(r_search.text, "html.parser")
                found_tile = soup_s.select_one("a[href*='/en/game/']")
                if found_tile:
                    target_path = found_tile.get("href")
                    url = "https://www.gog.com" + target_path if target_path.startswith("/") else target_path
                    r_final = session.get(url, timeout=REQUEST_TIMEOUT)
                    if r_final.status_code == 200:
                        html_text = r_final.text
        except Exception as e:
            logger.debug(f"Ricerca AJAX fallita per {game_name}: {e}")

    # PARSING DELL'HTML OTTENUTO (Dalle strutture responsive reali del sito)
    if html_text:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            screenshots = []
            cover = None
            
            # 1. Recupero della Cover principale (Analisi approfondita dei tag picture e og:image)
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

            # 2. Recupero degli screenshots dai tag picture interni allo slider multimediale
            slider_items = soup.select("[selenium-id='ProductCardThumbnailsSlider'] picture, .productcard-thumbnails-slider__slide picture")
            for item in slider_items:
                for source in item.select("source, img"):
                    srcset = source.get("srcset") or source.get("src")
                    img_url = extract_highest_res_from_srcset(srcset)
                    if img_url and img_url not in screenshots:
                        # Ricostruisce l'immagine a piena risoluzione eliminando i suffissi per dispositivi mobile
                        img_url = re.sub(r'_(product_card_v2_mobile_slider|product_card_v2_thumbnail)_\d+\.(jpg|png|webp)', r'.\2', img_url)
                        img_url = img_url.replace("_product_card_v2_mobile_slider_450", "").replace("_product_card_v2_mobile_slider_639", "")
                        screenshots.append(img_url)

            # 3. Rete a strascico regex finale in caso di slider renderizzato puramente via JavaScript di terze parti
            if not screenshots:
                links_in_raw = re.findall(r'(https?://images\.gog-statics\.com/[a-f0-9_]+(?:\.[a-zA-Z0-9]+)?)', html_text)
                for raw_url in links_in_raw:
                    if raw_url not in screenshots and "product_tile" not in raw_url:
                        screenshots.append(raw_url)

            # Pulizia e ordinamento liste
            blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon", "product_tile"]
            screenshots = [x for x in screenshots if not any(b in x.lower() for b in blacklist)]
            
            if cover in screenshots:
                screenshots.remove(cover)

            if screenshots and not cover:
                cover = screenshots[0]

            return cover, screenshots
        except Exception as e:
            logger.warning(f"Errore durante l'estrazione dati GOG per {game_name}: {e}")

    return None, []

# ============================================================
# FALLBACK IN CASO DI GIOCHI MANCANTI SU GOG (STEAM STORE)
# ============================================================

def steam_search_fallback(game_name: str) -> Tuple[Optional[str], List[str]]:
    cleaned_name = clean_game_name(game_name)
    try:
        url = f"https://store.steampowered.com/search/?term={requests.utils.quote(cleaned_name)}"
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("a.search_result_row")
        
        best_url, best_score = None, 0
        for row in rows[:5]:
            title = row.select_one("span.title")
            if not title: continue
            score = fuzz.token_set_ratio(cleaned_name.lower(), title.text.lower())
            if score > best_score:
                best_score, best_url = score, row.get("href")
        
        if best_url and best_score >= 55:
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
    except Exception: pass
    return None, []

# ============================================================
# PIPELINE DI ELABORAZIONE GIOCO
# ============================================================

def process_game(game_name: str) -> Dict:
    # Ignora la cache se precedentemente salvata senza dati multimediali validi
    if game_name in cache and cache[game_name].get("found") and len(cache[game_name].get("screenshots", [])) >= MIN_SCREENSHOTS:
        return cache[game_name]

    logger.info(f"Elaborazione in corso: {game_name}")
    result = {"name": game_name, "cover": None, "screenshots": [], "found": False}

    cover_url, screenshots = gog_scrape_direct_web(game_name)

    # Se GOG è incompleto o bloccato, lancia la ricerca incrociata su Steam
    if len(screenshots) < MIN_SCREENSHOTS or not cover_url:
        s_cover, s_shots = steam_search_fallback(game_name)
        if not cover_url: cover_url = s_cover
        screenshots.extend(s_shots)

    # Rimozione duplicati a parità di URL e limitazione a 6 elementi per riga HTML
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
# GENERATORE GALLERIA HTML (indexwebonly.html)
# ============================================================

def generate_html(results: List[Dict]):
    valid = [x for x in results if x.get("found")]
    
    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<title>Cloud Game Gallery - Web Only</title>
<style>
*{box-sizing:border-box;}
body{background:#111;color:#eee;font-family:Arial,sans-serif;margin:30px;padding-bottom:80px;}
h1{margin-bottom:30px;font-size:28px;border-bottom:2px solid #222;padding-bottom:12px;}
.game{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:10px;padding:20px;margin-bottom:25px;}
.title{font-size:22px;font-weight:bold;margin-bottom:15px;color:#fff;}
.row{display:grid;grid-template-columns:220px repeat(auto-fill, minmax(260px, 1fr));gap:12px;}
.cover{width:220px;height:150px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #ffaa00;background:#222;}
.shot{width:100%;height:150px;object-fit:cover;border-radius:6px;cursor:pointer;border:1px solid #252525;background:#222;}
img{transition:transform 0.2s, border-color 0.2s;}
img:hover{transform:scale(1.02);border-color:#666;}
.lightbox{position:fixed;inset:0;background:rgba(0,0,0,0.92);display:none;justify-content:center;align-items:center;z-index:999;cursor:pointer;}
.lightbox img{max-width:92%;max-height:92%;border-radius:6px;box-shadow:0 0 20px rgba(255,255,255,0.1);}
</style>
</head>
<body>
<div id="lightbox" class="lightbox" onclick="this.style.display='none'"><img id="lb-img"></div>
<h1>Galleria Multimediale (Web Only Mode)</h1>
"""

    for game in valid:
        html += f'<div class="game">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        
        if game.get("cover"):
            html += f'  <img class="cover" src="{game["cover"]}" title="Cover" onerror="handleBrokenLink(this)" onclick="openLb(this.src)">\n'
            
        for shot in game.get("screenshots", []):
            html += f'  <img loading="lazy" class="shot" src="{shot}" onerror="handleBrokenLink(this)" onclick="openLb(this.src)">\n'
            
        html += "</div>\n</div>\n"

    html += """
<script>
function openLb(src){
    if(!src || src.includes('placehold.co')) return;
    document.getElementById("lb-img").src = src;
    document.getElementById("lightbox").style.display = "flex";
}
function handleBrokenLink(image) {
    image.onerror = null;
    image.src = "https://placehold.co/600x400/222/777?text=No+Preview";
    image.style.cursor = "default";
    image.style.borderColor = "#333";
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
        logger.error(f"File {GAMES_FILE.name} mancante nella directory di esecuzione.")
        return

    with open(GAMES_FILE, "r", encoding="utf-8") as f:
        games = [x.strip() for x in f.readlines() if x.strip()]

    logger.info(f"Lancio dello scraping web ottimizzato per i classici. Totale giochi: {len(games)}")
    results = []
    found_count = 0

    for game in tqdm(games, desc="Download in corso"):
        try:
            res = process_game(game)
            results.append(res)
            if res["found"]: found_count += 1
            
            if len(results) % 5 == 0: generate_html(results)
            time.sleep(1.2)
        except Exception as e:
            logger.exception(f"Errore critico sul gioco {game}: {e}")

    generate_html(results)
    save_cache(cache)
    print(f"\nProcedura completata!\nGiochi scansionati: {len(games)}\nTrovati con successo: {found_count}")
    print(f"File Cache aggiornato: {CACHE_FILE.name}")
    print(f"File Interfaccia Web generato: {HTML_OUTPUT}")

if __name__ == "__main__":
    main()