#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
GOG-FIRST Game Gallery Scraper
Python 3.11+

FEATURES
--------
- Legge giochi da games.txt
- Priorità GOG
- Fallback Steam
- Fallback MobyGames
- Fallback IGDB
- Download cover ufficiali
- Download screenshot gameplay
- Retry automatici
- Timeout robusti
- Fuzzy matching
- Cache JSON locale
- Progress bar tqdm
- Logging dettagliato
- Genera grid finale stile Steam/Netflix

OUTPUT
------
covers/
screenshots/
output/grid.jpg

INSTALL
-------
pip install requests beautifulsoup4 pillow tqdm rapidfuzz

OPTIONAL IGDB
-------------
set TWITCH_CLIENT_ID=xxx
set TWITCH_CLIENT_SECRET=xxx
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
from PIL import Image #, ImageDraw, ImageFont, ImageOps
from rapidfuzz import fuzz
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).parent

GAMES_FILE = BASE_DIR / "games.txt"

COVERS_DIR = BASE_DIR / "covers"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"
OUTPUT_DIR = BASE_DIR / "output"

CACHE_FILE = BASE_DIR / "cache.json"

#GRID_OUTPUT = OUTPUT_DIR / "grid.jpg"
HTML_OUTPUT = OUTPUT_DIR / "index.html"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 "
    "(KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36"
)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_TIMEOUT = 25

MIN_SCREENSHOTS = 3

# Layout
BG_COLOR = (16, 16, 16)
CARD_COLOR = (28, 28, 28)
TEXT_COLOR = (240, 240, 240)
BORDER_COLOR = (70, 70, 70)

PADDING = 20
INNER_PADDING = 12

COVER_W = 220
SHOT_W = 320
SHOT_H = 180
TITLE_H = 50

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("gog_gallery")

# ============================================================
# DIRECTORIES
# ============================================================

for d in [COVERS_DIR, SCREENSHOTS_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ============================================================
# SESSION
# ============================================================


def build_session() -> requests.Session:
    session = requests.Session()

    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )

    adapter = HTTPAdapter(max_retries=retry)

    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update(HEADERS)

    return session


session = build_session()

# ============================================================
# CACHE
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
validated_gog_urls = {}

def normalize_gog_image(url: str) -> str:
    """
    Normalizza URL immagini GOG.
    Gestisce:
    - {formatter}
    - %7Bformatter%7D
    - jpg/webp/png
    """

    if not url:
        return url

    # cache
    if url in validated_gog_urls:
        return validated_gog_urls[url]

    # decode formatter encoded
    url = (
        url
        .replace("%7Bformatter%7D", "{formatter}")
        .replace("%7bformatter%7d", "{formatter}")
    )

    # formatter candidates
    formatters = [
        "product_card_v2_mobile_slider_639",
        "product_tile_extended_432x243",
        "gallery_1600",
        "product_card_v2_720",
        "product_tile_116",
    ]

    if "{formatter}" not in url:
        validated_gog_urls[url] = url
        return url

    # estensioni possibili
    extensions = [
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    ]

    for fmt in formatters:

        candidate = url.replace(
            "{formatter}",
            fmt,
        )

        base = re.sub(
            r"\.(jpg|jpeg|png|webp)$",
            "",
            candidate,
            flags=re.IGNORECASE,
        )

        for ext in extensions:

            final_url = base + ext

            try:

                r = session.head(
                    final_url,
                    timeout=3,
                    allow_redirects=True,
                )

                if r.status_code == 200:

                    validated_gog_urls[url] = final_url

                    return final_url

            except Exception:
                pass

    # fallback brutale
    fallback = (
        url.replace(
            "{formatter}",
            "gallery_1600",
        )
        .replace(".jpg", ".webp")
    )

    validated_gog_urls[url] = fallback

    return fallback

def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^\w\-_. ]", "", name)
    return name.strip().replace(" ", "_")


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def likely_gameplay(url: str) -> bool:
    u = url.lower()

    blacklist = [
        "logo",
        "banner",
        "wallpaper",
        "hero",
        "capsule",
        "keyart",
        "portrait",
        "library",
    ]

    return not any(x in u for x in blacklist)


def resize_crop(img: Image.Image, size):
    return ImageOps.fit(
        img,
        size,
        method=Image.Resampling.LANCZOS,
    )


def load_font(size: int):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except Exception:
        return ImageFont.load_default()


# ============================================================
# DOWNLOAD IMAGE
# ============================================================


def download_image(url: str, path: Path) -> bool:
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()

        img = Image.open(io.BytesIO(r.content)).convert("RGB")

        img.save(path, quality=95)

        return True

    except Exception as e:
        logger.warning(f"Download failed: {url} -> {e}")
        return False


# ============================================================
# GOG SEARCH
# ============================================================

def gog_search(game_name: str):
    """
    Cerca gioco su GOG usando API pubblica JSON.
    """

    try:
        url = "https://catalog.gog.com/v1/catalog"

        params = {
            "query": game_name,
            "limit": 10,
        }

        r = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        r.raise_for_status()

        data = r.json()

        products = data.get("products", [])

        if not products:
            return None

        best = None
        best_score = 0

        for product in products:

            title = product.get("title", "")

            score = fuzz.ratio(
                game_name.lower(),
                title.lower(),
            )

            if score > best_score:
                best_score = score
                best = product

        if not best:
            return None

        if best_score < 75:
            logger.warning(
                f"GOG weak match ({best_score}) for {game_name} -> {best['title']}"
            )
            return None

        logger.info(
            f"GOG match: {game_name} -> {best['title']}"
        )

        return best

    except Exception as e:
        logger.warning(f"GOG search failed: {e}")

    return None


# ============================================================
# GOG SCRAPER
# ============================================================


def gog_scrape_assets(product):
    """
    Estrae cover e screenshots dal JSON GOG.
    Compatibile con formati multipli.
    """

    screenshots = []
    cover = None

    try:

        # ====================================================
        # COVER
        # ====================================================

        possible_cover_keys = [
            "coverHorizontal",
            "coverVertical",
            "backgroundImage",
            "image",
        ]

        for key in possible_cover_keys:

            value = product.get(key)

            if not value:
                continue

            # string URL diretta
            if isinstance(value, str):
                cover = normalize_gog_image(value)
                break

            # dict
            if isinstance(value, dict):

                for subkey in [
                    "url",
                    "image",
                    "src",
                ]:
                    if value.get(subkey):
                        cover = normalize_gog_image(
                        value[subkey]
                        )
                        break

            if cover:
                break

        # ====================================================
        # SCREENSHOTS
        # ====================================================

        gallery = product.get("screenshots", [])

        for shot in gallery:

            src = None

            # Caso 1: string URL
            if isinstance(shot, str):
                src = shot

            # Caso 2: dict
            elif isinstance(shot, dict):

                for key in [
                    "image",
                    "url",
                    "src",
                    "href",
                ]:
                    if shot.get(key):
                        src = shot[key]
                        break

            if not src:
                continue

            if not likely_gameplay(src):
                continue
            src = normalize_gog_image(src)
            screenshots.append(src)

        # ====================================================
        # FALLBACK IMAGES
        # ====================================================

        if len(screenshots) < 3:

            for key in [
                "gallery",
                "images",
                "media",
            ]:

                extra = product.get(key)

                if not extra:
                    continue

                if isinstance(extra, list):

                    for item in extra:

                        src = None

                        if isinstance(item, str):
                            src = item

                        elif isinstance(item, dict):

                            for k in [
                                "url",
                                "image",
                                "src",
                            ]:
                                if item.get(k):
                                    src = item[k]
                                    break

                        if not src:
                            continue

                        if likely_gameplay(src):
                            src = normalize_gog_image(src)
                            screenshots.append(src)

        screenshots = [
            s for s in screenshots
            if "gog-statics" in s
        ]

        screenshots = list(dict.fromkeys(screenshots))

        return cover, screenshots[:12]

    except Exception as e:
        logger.warning(
            f"GOG asset parse failed: {e}"
        )

    return None, []


# ============================================================
# STEAM FALLBACK
# ============================================================


def steam_search(game_name: str) -> Optional[str]:
    try:
        url = (
            "https://store.steampowered.com/search/"
            f"?term={requests.utils.quote(game_name)}"
        )

        r = session.get(url, timeout=REQUEST_TIMEOUT)

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        rows = soup.select("a.search_result_row")

        best_score = 0
        best_url = None

        for row in rows[:10]:
            title = row.select_one("span.title")

            if not title:
                continue

            score = fuzz.ratio(
                game_name.lower(),
                title.text.lower(),
            )

            if score > best_score:
                best_score = score
                best_url = row.get("href")

        return best_url

    except Exception as e:
        logger.warning(f"Steam search failed: {e}")

    return None


def steam_scrape_assets(
    game_url: str,
) -> Tuple[Optional[str], List[str]]:

    screenshots = []
    cover = None

    try:
        r = session.get(game_url, timeout=REQUEST_TIMEOUT)

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        cover_el = soup.select_one(
            "img.game_header_image_full"
        )

        if cover_el:
            cover = cover_el.get("src")

        for a in soup.select("a.highlight_screenshot_link"):
            href = a.get("href")

            if href:
                screenshots.append(
                    href.replace(".600x338", "")
                )

        screenshots = list(dict.fromkeys(screenshots))

        return cover, screenshots[:10]

    except Exception as e:
        logger.warning(f"Steam scrape failed: {e}")

    return None, []


# ============================================================
# MOBYGAMES
# ============================================================


def mobygames_search(game_name: str) -> List[str]:
    screenshots = []

    try:
        url = (
            "https://www.mobygames.com/search/?q="
            + requests.utils.quote(game_name)
        )

        r = session.get(url, timeout=REQUEST_TIMEOUT)

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        game_link = soup.select_one("a[href*='/game/']")

        if not game_link:
            return []

        href = game_link.get("href")

        if href.startswith("/"):
            href = "https://www.mobygames.com" + href

        r = session.get(href, timeout=REQUEST_TIMEOUT)

        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        for img in soup.select("img"):
            src = img.get("src", "")

            if "screenshot" in src.lower():
                screenshots.append(src)

    except Exception as e:
        logger.warning(f"MobyGames error: {e}")

    return list(dict.fromkeys(screenshots))[:10]


# ============================================================
# IGDB
# ============================================================


class IGDBClient:
    def __init__(self):
        self.client_id = os.getenv("TWITCH_CLIENT_ID")
        self.client_secret = os.getenv("TWITCH_CLIENT_SECRET")

        self.token = None

        if self.client_id and self.client_secret:
            self.authenticate()

    def authenticate(self):
        try:
            r = session.post(
                "https://id.twitch.tv/oauth2/token",
                params={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=REQUEST_TIMEOUT,
            )

            r.raise_for_status()

            self.token = r.json()["access_token"]

            logger.info("IGDB authenticated")

        except Exception as e:
            logger.warning(f"IGDB auth failed: {e}")

    def search(self, game_name: str) -> List[str]:

        if not self.token:
            return []

        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.token}",
        }

        query = f'''
        search "{game_name}";
        fields screenshots.image_id;
        limit 1;
        '''

        try:
            r = session.post(
                "https://api.igdb.com/v4/games",
                headers=headers,
                data=query,
                timeout=REQUEST_TIMEOUT,
            )

            r.raise_for_status()

            data = r.json()

            screenshots = []

            if data:
                shots = data[0].get(
                    "screenshots",
                    [],
                )

                for shot in shots:
                    image_id = shot.get("image_id")

                    if image_id:
                        screenshots.append(
                            "https://images.igdb.com/"
                            "igdb/image/upload/"
                            f"t_screenshot_big/{image_id}.jpg"
                        )

            return screenshots

        except Exception as e:
            logger.warning(f"IGDB error: {e}")

        return []


igdb = IGDBClient()

# ============================================================
# PROCESS GAME
# ============================================================


def process_game(game_name: str) -> Dict:

    if game_name in cache:

        cached = cache[game_name]

        cover_ok = (
            cached.get("cover")
            and Path(cached["cover"]).exists()
        )

        screenshots_ok = all(
            Path(x).exists()
            for x in cached.get(
                "screenshots",
                [],
            )
        )

        if cover_ok and screenshots_ok:

            logger.info(
                f"Using cache for {game_name}"
            )

            return cached

        logger.warning(
            f"Cache invalid for {game_name}, rebuilding..."
        )

    logger.info(f"Processing: {game_name}")

    safe = sanitize_filename(game_name)

    result = {
        "name": game_name,
        "cover": None,
        "screenshots": [],
        "found": False,
    }

    cover_url = None
    screenshots = []

    # ========================================================
    # GOG FIRST
    # ========================================================

    gog_product = gog_search(game_name)

    if gog_product:
        cover_url, screenshots = gog_scrape_assets(
            gog_product
        )

    # ========================================================
    # STEAM FALLBACK
    # ========================================================

    if len(screenshots) < MIN_SCREENSHOTS:

        steam_url = steam_search(game_name)

        if steam_url:

            s_cover, s_shots = steam_scrape_assets(
                steam_url
            )

            if not cover_url:
                cover_url = s_cover

            screenshots.extend(s_shots)

    # ========================================================
    # MOBYGAMES FALLBACK
    # ========================================================

    if len(screenshots) < MIN_SCREENSHOTS:
        screenshots.extend(
            mobygames_search(game_name)
        )

    # ========================================================
    # IGDB FALLBACK
    # ========================================================

    if len(screenshots) < MIN_SCREENSHOTS:
        screenshots.extend(
            igdb.search(game_name)
        )

    screenshots = [
        x for x in screenshots if likely_gameplay(x)
    ]

    screenshots = list(dict.fromkeys(screenshots))

    hashes = set()

    # ========================================================
    # DOWNLOAD COVER
    # ========================================================

    if cover_url:

        cover_path = COVERS_DIR / f"{safe}.jpg"

        if cover_path.exists():

            logger.info(
                f"Cover already exists for {game_name}"
            )

            result["cover"] = str(cover_path)

        else:

            if download_image(
                cover_url,
                cover_path,
            ):

                result["cover"] = str(cover_path)

                logger.info(
                    f"Saved cover for {game_name}"
                )

    # ========================================================
    # DOWNLOAD SCREENSHOTS
    # ========================================================

    downloaded = []

    for idx, url in enumerate(screenshots):

        if len(downloaded) >= MIN_SCREENSHOTS:
            break

        try:
            r = session.get(
                url,
                timeout=REQUEST_TIMEOUT,
            )

            r.raise_for_status()

            digest = md5_bytes(r.content)

            if digest in hashes:
                continue

            hashes.add(digest)

            img = Image.open(
                io.BytesIO(r.content)
            ).convert("RGB")

            out = (
                SCREENSHOTS_DIR
                / f"{safe}_{idx+1}.jpg"
            )

            if out.exists():

                logger.info(
                    f"Screenshot already exists: {out.name}"
                )

                downloaded.append(str(out))

            else:

                img.save(out, quality=95)

                downloaded.append(str(out))

                logger.info(
                    f"Saved screenshot "
                    f"{len(downloaded)}/{MIN_SCREENSHOTS} "
                    f"for {game_name}"
                )

            logger.info(
                f"Saved screenshot "
                f"{len(downloaded)}/{MIN_SCREENSHOTS} "
                f"for {game_name}"
            )

        except Exception as e:
            logger.warning(
                f"Screenshot failed: {url} -> {e}"
            )

    result["screenshots"] = downloaded

    if result["cover"] and downloaded:
        result["found"] = True

    cache[game_name] = result

    save_cache(cache)

    return result


PREVIEW_LIMIT = 50

# ============================================================
# GRID
# ============================================================

"""
def draw_game_row(
    canvas: Image.Image,
    y: int,
    game: Dict,
):

    draw = ImageDraw.Draw(canvas)

    x = PADDING

    card_h = SHOT_H + TITLE_H + INNER_PADDING * 2

    draw.rounded_rectangle(
        (
            10,
            y,
            canvas.width - 10,
            y + card_h,
        ),
        radius=16,
        fill=CARD_COLOR,
        outline=BORDER_COLOR,
        width=2,
    )

    # COVER
    if game["cover"]:
        img = Image.open(
            game["cover"]
        ).convert("RGB")

        img = resize_crop(
            img,
            (COVER_W, SHOT_H),
        )

        canvas.paste(
            img,
            (x, y + INNER_PADDING),
        )

    x += COVER_W + INNER_PADDING

    # SHOTS
    for shot in game["screenshots"][:3]:

        if not Path(shot).exists():
            continue

        img = Image.open(
            shot
        ).convert("RGB")

        img = resize_crop(
            img,
            (SHOT_W, SHOT_H),
        )

        canvas.paste(
            img,
            (x, y + INNER_PADDING),
        )

        draw.rectangle(
            (
                x,
                y + INNER_PADDING,
                x + SHOT_W,
                y + INNER_PADDING + SHOT_H,
            ),
            outline=BORDER_COLOR,
            width=2,
        )

        x += SHOT_W + INNER_PADDING

    font = load_font(24)

    draw.text(
        (
            PADDING + 10,
            y + SHOT_H + 18,
        ),
        game["name"],
        fill=TEXT_COLOR,
        font=font,
    )
"""

""" GENERA JPG FINALE CON TUTTI I GIOCHI VALIDI, IN STILE STEAM/NETFLIX
def generate_grid(results: List[Dict]):

    valid = [x for x in results if x["found"]]

    if not valid:
        logger.error("No valid games.")
        return

    row_h = SHOT_H + TITLE_H + INNER_PADDING * 3

    total_h = row_h * len(valid) + PADDING

    total_w = (
        COVER_W
        + (SHOT_W * 3)
        + INNER_PADDING * 6
        + PADDING * 2
    )

    canvas = Image.new(
        "RGB",
        (total_w, total_h),
        BG_COLOR,
    )

    y = PADDING

    for game in valid:
        draw_game_row(canvas, y, game)
        y += row_h

    canvas.save(
        GRID_OUTPUT,
        quality=95,
    )

    logger.info(
        f"Grid generated -> {GRID_OUTPUT}"
    )
"""

#=========================================================
# PREVIEW GRID
#==========================================================
def generate_html(results: List[Dict]):

    valid = [x for x in results if x["found"]]

    html = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Game Gallery</title>

<style>

body{
    background:#101010;
    color:white;
    font-family:Arial;
    margin:20px;
}

h1{
    margin-bottom:30px;
}

.game{
    background:#1c1c1c;
    border:1px solid #444;
    border-radius:14px;
    padding:14px;
    margin-bottom:22px;
}

.title{
    font-size:22px;
    margin-bottom:12px;
    font-weight:bold;
}

.row{
    display:flex;
    gap:12px;
    flex-wrap:wrap;
}

.cover{
    width:220px;
    height:180px;
    object-fit:cover;
    border-radius:10px;
}

.shot{
    width:320px;
    height:180px;
    object-fit:cover;
    border-radius:10px;
}

img{
    transition:0.2s;
}

img:hover{
    transform:scale(1.03);
}

</style>
</head>
<body>

<h1>Game Gallery</h1>
"""

    for game in valid:

        html += f"""
<div class="game">

<div class="title">
{game['name']}
</div>

<div class="row">
"""

        if game["cover"]:

            cover_rel = os.path.relpath(
                game["cover"],
                OUTPUT_DIR,
            )

            html += f"""
<img class="cover" src="../{cover_rel}">
"""

        for shot in game["screenshots"][:3]:

            shot_rel = os.path.relpath(
                shot,
                OUTPUT_DIR,
            )

            html += f"""
<img loading="lazy" class="shot" src="../{shot_rel}">
<img loading="lazy" class="cover" src="../{cover_rel}">
"""

        html += """
</div>
</div>
"""

    html += """
</body>
</html>
"""

    out = OUTPUT_DIR / "index.html"

    with open(
        out,
        "w",
        encoding="utf-8",
    ) as f:
        f.write(html)

    logger.info(
        f"HTML gallery generated -> {out}"
    )

# ============================================================
# MAIN
# ============================================================


def main():

    global found_count, missing_count
    found_count = 0
    missing_count = 0

    if not GAMES_FILE.exists():
        logger.error("games.txt missing.")
        return

    with open(
        GAMES_FILE,
        "r",
        encoding="utf-8",
    ) as f:
        games = [
            x.strip()
            for x in f.readlines()
            if x.strip()
        ]

    logger.info(
        f"Loaded {len(games)} games"
    )

    logger.info(
        f"Resume progress: "
        f"{len(cache)}/{len(games)} cached"
    )

    results = []

    for game in tqdm(
        games,
        desc="Processing games",
    ):

        try:
            result = process_game(game)
            results.append(result)
            if len(results) % 5 == 0:
                save_cache(cache)
            if result["found"]:
                found_count += 1
            else:
                missing_count += 1

            logger.info(
                f"STATS | "
                f"found={found_count} "
                f"missing={missing_count}"
            )
            # preview ogni 10 giochi
            if len(results) % 10 == 0:

                preview_results = results[-PREVIEW_LIMIT:]

                #generate_grid(preview_results)
                generate_html(preview_results)

            time.sleep(1)

        except Exception as e:
            logger.exception(
                f"Fatal error: {game} -> {e}"
            )

    #generate_grid(results)
    generate_html(results)

    save_cache(cache)

    # REPORT
    found = [
        x["name"]
        for x in results
        if x["found"]
    ]

    missing = [
        x["name"]
        for x in results
        if not x["found"]
    ]

    total_images = 0

    for r in results:
        if r["cover"]:
            total_images += 1

        total_images += len(
            r["screenshots"]
        )

    print("\n" + "=" * 60)
    print("FINAL REPORT")
    print("=" * 60)

    print(f"\nGames found ({len(found)}):")

    for g in found:
        print(f"  ✓ {g}")

    print(
        f"\nGames missing ({len(missing)}):"
    )

    for g in missing:
        print(f"  ✗ {g}")

    print(
        f"\nImages downloaded: {total_images}"
    )

    print("\nOutput:")
    #print(f"  {GRID_OUTPUT}")

    print(f"  {OUTPUT_DIR / 'index.html'}")

    print("=" * 60)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()