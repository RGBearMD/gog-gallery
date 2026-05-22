#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper - WEB ONLY VERSION
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
# CONFIGURAZIONE (Nomi file aggiornati come richiesto)
# ============================================================

BASE_DIR = Path(__file__).parent

GAMES_FILE = BASE_DIR / "games.txt"
CACHE_FILE = BASE_DIR / "cachewebonly.json"  # Nuova cache dedicata
OUTPUT_DIR = BASE_DIR / "output"
HTML_OUTPUT = OUTPUT_DIR / "indexwebonly.html"  # Nuovo output dedicato

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
# HELPERS
# ============================================================

def clean_game_name(name: str) -> str:
    s = re.sub(r"\s*[\(\[][0-9]{4}[\)\]]\s*", " ", name)
    s = re.sub(r"\b(v1\.[0-9]|v[0-9])\b", " ", s, flags=re.IGNORECASE)
    return s.strip()

def slugify(name: str) -> str:
    s = name.lower().strip().replace("'", "")
    s = re.sub(r"[^\w\s-]", "_", s)
    return re.sub(r"[\s_-]+", "_", s).strip("_")

def likely_gameplay(url: str) -> bool:
    u = url.lower()
    blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon"]
    return not any(x in u for x in blacklist)

# ============================================================
# SCRAPER WEB REALE GOG
# ============================================================

def gog_scrape_direct_web(game_name: str) -> Tuple[Optional[str], List[str]]:
    cleaned_name = clean_game_name(game_name)
    game_slug = slugify(cleaned_name)
    url = f"https://www.gog.com/en/game/{game_slug}"
    
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        
        if r.status_code != 200:
            search_url = f"https://www.gog.com/en/games?query={requests.utils.quote(cleaned_name)}"
            r_search = session.get(search_url, timeout=REQUEST_TIMEOUT)
            soup_s = BeautifulSoup(r_search.text, "html.parser")
            found_tile = soup_s.select_one("a[href*='/en/game/']")
            if found_tile:
                target_path = found_tile.get("href")
                url = "https://www.gog.com" + target_path if target_path.startswith("/") else target_path
                r = session.get(url, timeout=REQUEST_TIMEOUT)
                
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            screenshots = []
            cover = None
            
            # 1. Screenshot dal Carosello
            slides = soup.select('[selenium-id="ProductCardThumbnailsSlider"] img, .productcard-thumbnails-slider__slide img, .slider__item img')
            for img in slides:
                src = img.get("src") or img.get("data-src") or img.get("srcset")
                if src:
                    src = src.split('?')[0].split(' ')[0]
                    if src.startswith("//"): src = "https:" + src
                    src = re.sub(r'_\d+x\d+\.(jpg|png|webp)', r'.\1', src)
                    src = src.replace("{formatter}", "gallery_1600")
                    if src.startswith("http") and src not in screenshots:
                        screenshots.append(src)
            
            # 2. Estrazione Cover (Fissa background CSS + Tag Img Alternativo)
            hero_element = soup.select_one(".productcard-hero-bg, .gog-galaxy-background")
            if hero_element:
                style_attr = hero_element.get("style", "")
                if "background-image" in style_attr:
                    match = re.search(r'url\([\'"]?([^\'"]+)[\'"]?\)', style_attr)
                    if match:
                        cover = match.group(1)
            
            if not cover:
                img_hero = soup.select_one("img.productcard-hero-bg, .productcard-hero-bg img")
                if img_hero:
                    cover = img_hero.get("src") or img_hero.get("data-src")

            if cover and cover.startswith("//"):
                cover = "https:" + cover

            if screenshots:
                if not cover: cover = screenshots[0]
                return cover, screenshots
    except Exception as e:
        logger.warning(f"Errore scraping web GOG per {game_name}: {e}")
    return None, []

# ============================================================
# FALLBACK: STEAM STORE
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
        
        if best_url and best_score >= 60:
            r = session.get(best_url, timeout=REQUEST_TIMEOUT)
            soup = BeautifulSoup(r.text, "html.parser")
            cover_el = soup.select_one("img.game_header_image_full")
            cover = cover_el.get("src") if cover_el else None
            
            screenshots = []
            for a in soup.select("a.highlight_screenshot_link"):
                href = a.get("href")
                if href: screenshots.append(href.split('?')[0].replace(".600x338", ""))
            return cover, screenshots
    except Exception: pass
    return None, []

# ============================================================
# PIPELINE DI ELABORAZIONE GIOCO
# ============================================================

def process_game(game_name: str) -> Dict:
    if game_name in cache and cache[game_name].get("found") and cache[game_name].get("cover"):
        return cache[game_name]

    logger.info(f"Elaborazione: {game_name}")
    result = {"name": game_name, "cover": None, "screenshots": [], "found": False}

    cover_url, screenshots = gog_scrape_direct_web(game_name)

    if len(screenshots) < MIN_SCREENSHOTS or not cover_url:
        s_cover, s_shots = steam_search_fallback(game_name)
        if not cover_url: cover_url = s_cover
        screenshots.extend(s_shots)

    screenshots = [x for x in screenshots if likely_gameplay(x)]
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
<h1>Galleria Multimediale (Web Only Mode)</h1>
"""

    for game in valid:
        html += f'<div class="game">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        
        if game.get("cover"):
            html += f'  <img class="cover" src="{game["cover"]}" onerror="handleBrokenLink(this)" onclick="openLb(this.src)">\n'
            
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
        logger.error(f"File {GAMES_FILE.name} assente.")
        return

    with open(GAMES_FILE, "r", encoding="utf-8") as f:
        games = [x.strip() for x in f.readlines() if x.strip()]

    logger.info(f"Lancio scraping in modalità Cloud per {len(games)} giochi.")
    results = []
    found_count = 0

    for game in tqdm(games, desc="Raccolta Link"):
        try:
            res = process_game(game)
            results.append(res)
            if res["found"]: found_count += 1
            
            if len(results) % 5 == 0: generate_html(results)
            time.sleep(1.2)
        except Exception as e:
            logger.exception(f"Errore su {game}: {e}")

    generate_html(results)
    save_cache(cache)
    print(f"\nCompletato!\nGiochi totali: {len(games)}\nTrovati: {found_count}")
    print(f"Cache salvata in: {CACHE_FILE.name}")
    print(f"HTML salvato in: {HTML_OUTPUT}")

if __name__ == "__main__":
    main()