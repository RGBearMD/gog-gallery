#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG Library Hybrid Scraper - JSON LINKED
Prende l'input dal browser (inclusa la cover della libreria) e genera la galleria.
Output: output/indexwebonly.html | Cache: cachewebonly.json
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

# CONFIGURAZIONE
BASE_DIR = Path(__file__).parent
JSON_INPUT_FILE = BASE_DIR / "games_data.json"
CACHE_FILE = BASE_DIR / "cachewebonly.json"  
OUTPUT_DIR = BASE_DIR / "output"
HTML_OUTPUT = OUTPUT_DIR / "indexwebonly.html"  

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8"}
REQUEST_TIMEOUT = 15
MIN_SCREENSHOTS = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("gog_library")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# RETRY SESSION
def build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=1.0, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session

session = build_session()

def load_cache() -> Dict:
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {}

def save_cache(data: Dict):
    with open(CACHE_FILE, "w", encoding="utf-8") as f: json.dump(data, f, indent=2, ensure_ascii=False)

cache = load_cache()

def extract_highest_res_from_srcset(srcset_text: str) -> Optional[str]:
    if not srcset_text: return None
    candidates = []
    for part in srcset_text.split(','):
        tokens = part.strip().split()
        if tokens:
            url = tokens[0].strip()
            if url.startswith("//"): url = "https:" + url
            if "gog-statics.com" in url: candidates.append(url)
    if candidates:
        high_res = [c for c in candidates if "_2x" in c or "1600" in c]
        return high_res[-1] if high_res else candidates[-1]
    return None

def scrape_only_screenshots(url: str) -> List[str]:
    """Visita la pagina pubblica solo per raccogliere gli screenshot di gameplay"""
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            screenshots = []
            
            # Estrattore Slider HTML5
            slider_items = soup.select("[selenium-id='ProductCardThumbnailsSlider'] picture, .productcard-thumbnails-slider__slide picture")
            for item in slider_items:
                for source in item.select("source, img"):
                    srcset = source.get("srcset") or source.get("src")
                    img_url = extract_highest_res_from_srcset(srcset)
                    if img_url and img_url not in screenshots:
                        img_url = re.sub(r'_(product_card_v2_mobile_slider|product_card_v2_thumbnail|thumbnail)_\d+x?\d*\.(jpg|png|webp)', r'.\2', img_url)
                        screenshots.append(img_url)

            # Fallback Regex stringhe
            if not screenshots:
                links_in_raw = re.findall(r'(https?://images\.gog-statics\.com/[a-f0-9_]+(?:\.[a-zA-Z0-9]+)?)', r.text)
                for raw_url in links_in_raw:
                    if raw_url not in screenshots and "product_tile" not in raw_url:
                        screenshots.append(raw_url)

            blacklist = ["logo", "banner", "wallpaper", "hero", "capsule", "keyart", "portrait", "library", "icon", "product_tile"]
            return [x for x in screenshots if not any(b in x.lower() for b in blacklist)]
    except Exception:
        pass
    return []

def generate_html(results: List[Dict]):
    valid = [x for x in results if x.get("found")]
    
    html = """<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>GOG Personal Library Gallery</title>
<style>
*{box-sizing:border-box; scroll-behavior: smooth;}
body{background:#111;color:#eee;font-family: Arial, sans-serif;margin:20px;padding-bottom:80px;}

/* INTESTAZIONE E PULSANTE INDICE */
.header-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 2px solid #222;
  padding-bottom: 15px;
  margin-bottom: 25px;
}
h1 { margin: 0; font-size: 26px; color: #fff; }

.btn-index {
  background: #ffaa00;
  color: #111;
  border: none;
  padding: 10px 20px;
  font-size: 14px;
  font-weight: bold;
  border-radius: 20px;
  cursor: pointer;
  transition: background 0.2s, transform 0.1s;
  display: flex;
  align-items: center;
  gap: 8px;
  box-shadow: 0 4px 12px rgba(255, 170, 0, 0.2);
}
.btn-index:hover { background: #ffbb33; transform: translateY(-2px); }
.btn-index:active { transform: translateY(0); }

/* SIDEBAR INDICE DEI TITOLI */
.sidebar {
  position: fixed; top: 0; right: -350px; width: 350px; height: 100%;
  background: rgba(20, 20, 20, 0.95);
  backdrop-filter: blur(10px);
  box-shadow: -5px 0 25px rgba(0,0,0,0.5);
  z-index: 1001;
  transition: right 0.3s cubic-bezier(0.4, 0, 0.2, 1);
  display: flex; flex-direction: column;
  border-left: 1px solid #2c2c2c;
}
.sidebar.open { right: 0; }
.sidebar-header {
  padding: 20px; border-bottom: 1px solid #2c2c2c;
  display: flex; justify-content: space-between; align-items: center;
}
.sidebar-header h2 { margin: 0; font-size: 18px; color: #fff; }
.sidebar-close {
  background: none; border: none; color: #888; font-size: 20px; cursor: pointer; transition: color 0.2s;
}
.sidebar-close:hover { color: #ff5555; }
.sidebar-content {
  padding: 10px 0; overflow-y: auto; flex-grow: 1;
}
.index-item {
  display: block; padding: 12px 20px; color: #ccc; text-decoration: none;
  font-size: 14px; border-bottom: 1px solid #1a1a1a; transition: background 0.2s, color 0.2s;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.index-item:hover { background: rgba(255, 170, 0, 0.1); color: #ffaa00; }

/* GRIGLIA GALLERIA GIOCHI */
.game{background:#1a1a1a;border:1px solid #2c2c2c;border-radius:12px;padding:20px;margin-bottom:30px; scroll-margin-top: 30px;}
.title{font-size:20px;font-weight:bold;margin-bottom:15px;color:#fff;border-left:4px solid #ffaa00;padding-left:10px;}

.row{
  display: grid;
  grid-template-columns: 180px repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  width: 100%;
}
.cover{width:100%;height:120px;object-fit:cover;border-radius:8px;cursor:pointer;border:2px solid #ffaa00;background:#222;}
.shot{width:100%;height:120px;object-fit:cover;border-radius:8px;cursor:pointer;border:1px solid #333;background:#222;}
.cover, .shot { transition: transform 0.2s, border-color 0.2s, filter 0.2s; }
.cover:hover, .shot:hover { transform: scale(1.03); border-color: #888; filter: brightness(1.1); }

/* LIGHTBOX DESIGN MODERNO ED ELEGANTE */
.lightbox{
  position:fixed;inset:0;background:rgba(10,10,10,0.96);
  display:none;justify-content:center;align-items:center;
  z-index:999;
}
.lightbox-content-wrapper {
  position: relative;
  max-width: 85%;
  max-height: 85%;
  display: flex;
  justify-content: center;
  align-items: center;
}
.lightbox img{
  max-width:100%;
  max-height:85vh;
  border-radius:12px;
  box-shadow: 0 10px 40px rgba(0,0,0,0.8);
  display: block;
}

/* PULSANTI LIGHTBOX STILIZZATI CON CONTORNI GEOMETRICI */
.lb-btn {
  position: absolute;
  background: rgba(40, 40, 40, 0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  width: 54px;
  height: 54px;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s, transform 0.2s, border-color 0.2s;
  z-index: 1000;
}
.lb-btn svg { width: 22px; height: 22px; fill: none; stroke: #fff; stroke-width: 2.5; stroke-linecap: round; stroke-linejoin: round; }
.lb-btn:hover { background: rgba(255, 170, 0, 0.9); border-color: #ffaa00; transform: scale(1.08); }
.lb-btn:hover svg { stroke: #111; }

.lb-prev { left: -80px; }
.lb-next { right: -80px; }
.lb-close { top: -65px; right: 0; width: 44px; height: 44px; background: rgba(255, 255, 255, 0.05); }
.lb-close:hover { background: rgba(255, 85, 85, 0.9); border-color: #ff5555; }
.lb-close:hover svg { stroke: #fff; }

/* RESPONSIVE DESIGN */
@media(max-width: 900px) {
  .lb-prev { left: 15px; bottom: -80px; top: auto; }
  .lb-next { right: 15px; bottom: -80px; top: auto; }
  .lb-close { top: -65px; right: 0; }
  .sidebar { width: 100%; right: -100%; }
}
@media(max-width: 800px) {
  .row { grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); }
  .cover { grid-column: span 2; height: 140px; } 
}
</style>
</head>
<body>

<div id="indexSidebar" class="sidebar">
  <div class="sidebar-header">
    <h2>Indice dei Giochi</h2>
    <button class="sidebar-close" onclick="toggleSidebar()">✕</button>
  </div>
  <div class="sidebar-content">
  """
    # Popoliamo l'indice testuale dinamicamente
    for game_idx, game in enumerate(valid):
        # Usiamo un ID univoco sicuro riga-gioco pulito da spazi
        safe_id = f"game-anchor-{game_idx}"
        html += f'    <a href="#{safe_id}" class="index-item" onclick="toggleSidebar()">{game["name"]}</a>\n'

    html += """  </div>
</div>

<div id="lightbox" class="lightbox" onclick="closeLbIfClickOutside(event)">
  <div class="lightbox-content-wrapper">
    <button class="lb-btn lb-close" onclick="closeLb()" title="Chiudi (ESC)">
      <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
    </button>
    <button class="lb-btn lb-prev" id="lb-prev-btn" onclick="prevImg(event)" title="Precedente (Freccia Sinistra)">
      <svg viewBox="0 0 24 24"><polyline points="15 18 9 12 15 6"></polyline></svg>
    </button>
    <img id="lb-img" src="" alt="Galleria Ingrandita">
    <button class="lb-btn lb-next" id="lb-next-btn" onclick="nextImg(event)" title="Successiva (Freccia Destra)">
      <svg viewBox="0 0 24 24"><polyline points="9 18 15 12 9 6"></polyline></svg>
    </button>
  </div>
</div>

<div class="header-container">
  <h1>La mia Libreria GOG</h1>
  <button class="btn-index" onclick="toggleSidebar()">
    <svg style="width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:2.5; stroke-linecap:round;" viewBox="0 0 24 24"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
    Apri Indice Giochi
  </button>
</div>
"""
    
    # Generazione dei blocchi della galleria
    for game_idx, game in enumerate(valid):
        safe_id = f"game-anchor-{game_idx}"
        html += f'<div class="game" id="{safe_id}">\n<div class="title">{game["name"]}</div>\n<div class="row">\n'
        
        # MODIFICA: La copertina ora apre il link di GOG invece del Lightbox e non interrompe il carosello delle frecce
        if game.get("cover"):
            html += f'  <img class="cover" src="{game["cover"]}" loading="lazy" title="Apri pagina ufficiale su GOG" onerror="this.style.display=\'none\'" onclick="window.open(\'{game["url"]}\', \'_blank\')">\n'
            
        for shot in game.get("screenshots", []):
            thumb_url = shot
            if "gog-statics.com" in shot and not any(x in shot for x in ["_thumbnail", "_slider"]):
                thumb_url = shot.replace(".jpg", "_product_card_v2_thumbnail_271.jpg").replace(".png", "_product_card_v2_thumbnail_271.png")

            html += f'  <img class="shot gal-item" src="{thumb_url}" data-full="{shot}" loading="lazy" onerror="handleBrokenLink(this)" onclick="openLb(this)">\n'
            
        html += "</div>\n</div>\n"

    html += """
<script>
let currentActiveElement = null;

// Gestione apertura/chiusura della Sidebar dell'Indice
function toggleSidebar() {
    const sidebar = document.getElementById("indexSidebar");
    sidebar.classList.toggle("open");
}

function openLb(element){
    currentActiveElement = element;
    updateLightboxContent();
    document.getElementById("lightbox").style.display = "flex";
}

function closeLb() {
    document.getElementById("lightbox").style.display = "none";
    currentActiveElement = null;
}

function closeLbIfClickOutside(event) {
    if (event.target.classList.contains('lightbox')) {
        closeLb();
    }
}

function updateLightboxContent() {
    if(!currentActiveElement) return;
    const fullSrc = currentActiveElement.getAttribute("data-full");
    document.getElementById("lb-img").src = fullSrc;
    
    const parentRow = currentActiveElement.closest('.row');
    const items = Array.from(parentRow.querySelectorAll('.gal-item'));
    const currentIndex = items.indexOf(currentActiveElement);
    
    document.getElementById("lb-prev-btn").style.visibility = (currentIndex > 0) ? "visible" : "hidden";
    document.getElementById("lb-next-btn").style.visibility = (currentIndex < items.length - 1) ? "visible" : "hidden";
}

function nextImg(event) {
    if(event) event.stopPropagation();
    if(!currentActiveElement) return;
    const parentRow = currentActiveElement.closest('.row');
    const items = Array.from(parentRow.querySelectorAll('.gal-item'));
    const currentIndex = items.indexOf(currentActiveElement);
    if(currentIndex < items.length - 1) {
        currentActiveElement = items[currentIndex + 1];
        updateLightboxContent();
    }
}

function prevImg(event) {
    if(event) event.stopPropagation();
    if(!currentActiveElement) return;
    const parentRow = currentActiveElement.closest('.row');
    const items = Array.from(parentRow.querySelectorAll('.gal-item'));
    const currentIndex = items.indexOf(currentActiveElement);
    if(currentIndex > 0) {
        currentActiveElement = items[currentIndex - 1];
        updateLightboxContent();
    }
}

document.addEventListener('keydown', function(event) {
    const lb = document.getElementById("lightbox");
    if (lb.style.display === "flex") {
        if (event.key === "Escape") {
            closeLb();
        } else if (event.key === "ArrowRight") {
            nextImg(null);
        } else if (event.key === "ArrowLeft") {
            prevImg(null);
        }
    }
});

function handleBrokenLink(image) {
    image.onerror = null;
    var fullSrc = image.getAttribute("data-full");
    if (image.src !== fullSrc) { image.src = fullSrc; } 
    else { image.src = "https://placehold.co/300x200/222/777?text=No+Preview"; }
}
</script>
</body>
</html>
"""
    with open(HTML_OUTPUT, "w", encoding="utf-8") as f: f.write(html)

def main():
    if not JSON_INPUT_FILE.exists():
        print(f"[ERRORE] Il file {JSON_INPUT_FILE.name} non è presente. Generalo dalla console di GOG.")
        return

    with open(JSON_INPUT_FILE, "r", encoding="utf-8") as f:
        raw_games = json.load(f)

    print(f"[INFO] File JSON caricato correttamente. Trovati {len(raw_games)} giochi da elaborare.")
    
    results = []
    for item in tqdm(raw_games, desc="Estrazione Screenshot"):
        name = item["title"]
        url = item["link"]
        cover = item["cover"]

        # Controllo Cache per velocizzare i riavvii dello script
        if name in cache and len(cache[name].get("screenshots", [])) >= MIN_SCREENSHOTS:
            res = cache[name]
            # Aggiorna la cover se quella vecchia in cache era rotta
            if cover and not res.get("cover"): res["cover"] = cover
        else:
            # Scarica solo gli screenshot (La copertina l'ha già estratta il browser!)
            shots = scrape_only_screenshots(url)
            res = {"name": name, "url": url, "cover": cover, "screenshots": list(dict.fromkeys(shots))[:6]}
            cache[name] = res
            save_cache(cache)
            time.sleep(1.1)

        results.append(res)
        if len(results) % 5 == 0: generate_html(results)

    generate_html(results)
    print(f"\n[OK] Galleria completata! Trovi l'HTML dentro: {HTML_OUTPUT.resolve()}")

if __name__ == "__main__":
    main()