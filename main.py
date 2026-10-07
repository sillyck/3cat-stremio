"""
Addon Stremio combinat: 3Cat + AniDD + Xarxa Catalana + Fansubs.cat + RTVE +
Pluto TV + Plex + Runtime + Atresplayer — v8.0

Scraper escalable de pel·lícules i sèries en català i castellà.
  - 3Cat: catàleg general en català (via API pública)
  - AniDD: contingut d'animació/anime doblat (via scraping)
  - Xarxa Catalana: One Piece en català (via API)
  - Fansubs.cat: anime subtitulat en català (via scraping)
  - RTVE: catàleg general en castellà (via API pública + desxifrat de vídeo)
  - Pluto TV: catàleg AVOD gratuït (pel·lícules i sèries, sense DRM)
  - Plex: catàleg "Watch Free" gratuït (només títols sense DRM)
  - Runtime: catàleg AVOD gratuït (pel·lícules i sèries, sense DRM)
  - Atresplayer: catàleg "en abierto" (pel·lícules i sèries, sense DRM)
"""

import unicodedata
import re
import html as html_module
import logging
import asyncio
import time
import urllib.parse
import base64
import io
import struct
import json
import os
import uuid

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx
from bs4 import BeautifulSoup

from hls import reescriure_master_hls

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("3cat")

class Suprimeix404Filter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "404" not in record.getMessage()

logging.getLogger("uvicorn.access").addFilter(Suprimeix404Filter())
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── App FastAPI ───────────────────────────────────────────────────────────────
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Constants ─────────────────────────────────────────────────────────────────
API_3CAT_CERCA = "https://api.3cat.cat/cercador/tot"
API_3CAT_MEDIA = "http://dinamics.ccma.cat/pvideo/media.jsp"
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "")

ANIDD_BASE = "https://anidd.space"
ANIDD_PASSWORD = os.environ.get("ANIDD_PASSWORD", "")
ANIDD_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)

XARXACAT_API = "https://gestio.multimedia.xarxacatala.cat/api/v2"
XARXACAT_SHOW_ID = 4
XARXACAT_IMDB = "tt0388629"
XARXACAT_TMDB_ID = 37854
XARXACAT_PLAYLISTS_IGNORAR = {118}
XARXACAT_PLAYLIST_PELIS = 2

FANSUBSCAT_BASE = "https://anime.fansubs.cat"
FANSUBSCAT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)

RTVE_SEARCH_API = "https://api.rtve.es/api/search/contents"
RTVE_SEARCH_PROGRAMS_API = "https://api.rtve.es/api/search/programs"
RTVE_THUMBNAIL_BASE = "http://www.rtve.es/ztnr/movil/thumbnail"
RTVE_QUALITY_ORDRE = ["HD_FULL", "HD_READY", "HQ", "HIGH", "MED", "Media", "Alta"]
RTVE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)

FORMATS_PELICULA = {"Película/Telefilm"}
FORMATS_SERIE = {
    "Sèrie dramàtica", "Sèries", "Sèrie animació",
    "Docu-drama (Documental dramatitzat) ", "Documental",
}
DURADA_MIN_PELICULA = 50 * 60
DURADA_MIN_EPISODI = 5 * 60
FONT_TIMEOUT_SEGONS = 15.0  # cap dur per font individual, perquè una de lenta no allargui tota la resposta
# AIOStreams abandona els addons als ~10 s: responem amb el que hi ha en DEADLINE segons i deixem acabar les fonts
# lentes en segon pla (omplen la cache per a la propera petició).
DEADLINE_RESPOSTA_SEGONS = 8.5

# URL pública d'aquest addon (la que veu el reproductor). Si està definida, els streams de 3Cat
# passen per /hls/<id>.m3u8, que serveix el master HLS amb la millor qualitat primer.
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

# Tolerància (en minuts) entre la durada real d'una pel·lícula (TMDB/Cinemeta)
# i la durada de l'item trobat a la font, per descartar coincidències de
# títol amb una pel·lícula diferent que es diu igual (ex. "Obsession" té
# mitja dotzena de pel·lícules diferents amb aquest nom exacte). Es fa
# servir com a substitut de l'any quan la font no exposa cap camp d'any
# fiable (3Cat i Pluto TV).
DURADA_TOLERANCIA_BAIXA_MIN = 8
DURADA_TOLERANCIA_ALTA_3CAT_MIN = 10
DURADA_TOLERANCIA_ALTA_PLUTO_MIN = 25  # Pluto és AVOD: la durada inclou talls publicitaris

# ─── Manifest Stremio ─────────────────────────────────────────────────────────
MANIFEST = {
    "id": "cat.3cat.anidd.xarxacat.fansubscat.rtve.stremio.addon",
    "version": "8.0.0",
    "name": "3Cat + AniDD + Xarxa Catalana + Fansubs.cat + RTVE + Pluto TV + Plex + Runtime + Atresplayer",
    "description": (
        "Pel·lícules i sèries en català i castellà: catàleg de 3Cat (CCMA), "
        "animació/anime doblat d'AniDD, One Piece de la Xarxa Catalana, "
        "anime subtitulat en català de Fansubs.cat, catàleg general de RTVE, "
        "i catàlegs gratuïts AVOD de Pluto TV, Plex, Runtime i Atresplayer "
        "(només contingut sense DRM)"
    ),
    "logo": "https://img.3cat.cat/multimedia/png/0/2/1697462461920.png",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "catalogs": [],
    "idPrefixes": ["tt"],
}

# ═══════════════════════════════════════════════════════════════════════════════
#  NORMALITZACIÓ
# ═══════════════════════════════════════════════════════════════════════════════

def normalitzar(text: str) -> str:
    if not text:
        return ""
    text = text.lower()
    text = text.replace("·", "")
    text = text.replace("-", " ").replace("–", " ").replace("—", " ")
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"\s+", " ", text).strip()
    return text

def textos_coincideixen(cercat: str, api: str, llindar: float = 0.7) -> bool:
    a, b = normalitzar(cercat), normalitzar(api)
    if not a or not b:
        return False
    if a == b:
        return True
    curt, llarg = (a, b) if len(a) <= len(b) else (b, a)
    if curt in llarg and len(curt) / len(llarg) >= 0.7:
        return True
    pa, pb = set(a.split()), set(b.split())
    if not pa or not pb:
        return False
    return len(pa & pb) / len(pa | pb) >= llindar

# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITATS DE PARSING
# ═══════════════════════════════════════════════════════════════════════════════

def durada_a_segons(d: str) -> int:
    if not d:
        return 0
    try:
        p = d.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
    except (ValueError, IndexError):
        return 0

def obtenir_format_desc(item: dict) -> set:
    return {f.get("desc", "") for f in item.get("formats", [])}

def es_pelicula(item: dict) -> bool:
    return bool(obtenir_format_desc(item) & FORMATS_PELICULA)

def obtenir_temporada(item: dict) -> int:
    for t in item.get("temporades") or []:
        m = re.search(r"PUTEMP_(\d+)", t.get("id", ""))
        if m:
            return int(m.group(1))
    m = re.search(r"T(\d+)x?C", item.get("titol", ""), re.I)
    return int(m.group(1)) if m else -1

def obtenir_capitol(item: dict) -> int:
    ct = item.get("capitol_temporada", -1)
    if ct and ct > 0:
        return ct
    m = re.search(r"T\d+x?C(\d+)", item.get("titol", ""), re.I)
    return int(m.group(1)) if m else -1

def obtenir_nom_programa(item: dict) -> str:
    progs = item.get("programes_tv", [])
    return (progs[0].get("titol", "") or progs[0].get("desc", "")) if progs else ""

def generar_termes_cerca(text: str) -> list[str]:
    termes = [text]
    for sep in [":", " - "]:
        if sep in text:
            parts = text.split(sep, 1)
            for part in parts:
                part = part.strip()
                if part and part not in termes and len(part) >= 3:
                    termes.append(part)
    return termes

# ═══════════════════════════════════════════════════════════════════════════════
#  TMDB — OBTENCIÓ DE NOMS I ESTRUCTURA DE TEMPORADES
# ═══════════════════════════════════════════════════════════════════════════════

_cinemeta_temporades_cache: dict[str, dict[int, int]] = {}
_cinemeta_temporades_lock = asyncio.Lock()

async def obtenir_estructura_temporades_cinemeta(imdb_id: str) -> dict[int, int]:
    """
    Consulta Cinemeta per obtenir l'estructura de temporades d'una sèrie.
    Retorna {season_number: episode_count}.
    Cinemeta és la font de metadades per defecte de Stremio i la seva
    numeració coincideix amb la majoria d'addons de metadades.
    """
    if imdb_id in _cinemeta_temporades_cache:
        return _cinemeta_temporades_cache[imdb_id]

    async with _cinemeta_temporades_lock:
        if imdb_id in _cinemeta_temporades_cache:
            return _cinemeta_temporades_cache[imdb_id]

        temporades: dict[int, int] = {}
        try:
            url = f"https://v3-cinemeta.strem.io/meta/series/{imdb_id}.json"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                meta = r.json().get("meta", {})
                videos = meta.get("videos", [])

                for v in videos:
                    season = v.get("season", -1)
                    if isinstance(season, int) and season > 0:
                        temporades[season] = temporades.get(season, 0) + 1

            if temporades:
                logger.info(
                    f"[CINEMETA] Temporades de {imdb_id}: {len(temporades)} temporades, "
                    f"total {sum(temporades.values())} episodis | "
                    f"Detall: {dict(sorted(temporades.items()))}"
                )
            else:
                logger.warning(f"[CINEMETA] No s'han trobat temporades per {imdb_id}")
        except Exception as e:
            logger.error(f"[CINEMETA] Error obtenint estructura temporades de {imdb_id}: {e}")

        _cinemeta_temporades_cache[imdb_id] = temporades
        return temporades


_tmdb_temporades_cache: dict[int, dict[int, int]] = {}
_tmdb_temporades_lock = asyncio.Lock()

async def obtenir_temporades_tmdb(tmdb_id: int) -> dict[int, int]:
    """
    Retorna un dict {season_number: episode_count} per a una sèrie de TMDB.
    Cachejat permanentment en memòria. Es fa servir com a FALLBACK si
    Cinemeta no té dades.
    """
    if tmdb_id in _tmdb_temporades_cache:
        return _tmdb_temporades_cache[tmdb_id]

    async with _tmdb_temporades_lock:
        if tmdb_id in _tmdb_temporades_cache:
            return _tmdb_temporades_cache[tmdb_id]

        temporades: dict[int, int] = {}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"https://api.themoviedb.org/3/tv/{tmdb_id}",
                    params={"api_key": TMDB_API_KEY},
                )
                r.raise_for_status()
                data = r.json()
                for s in data.get("seasons", []):
                    sn = s.get("season_number", -1)
                    if sn > 0:
                        temporades[sn] = s.get("episode_count", 0)
            logger.info(f"[TMDB] Temporades de {tmdb_id}: {len(temporades)} temporades (fallback)")
        except Exception as e:
            logger.error(f"[TMDB] Error obtenint temporades de {tmdb_id}: {e}")

        _tmdb_temporades_cache[tmdb_id] = temporades
        return temporades


def convertir_a_episodi_absolut(
    temporada: int, capitol: int, episodis_per_temporada: dict[int, int],
) -> int:
    """
    Converteix temporada:episodi a número d'episodi absolut.
    La numeració és seqüencial: després de l'últim episodi de la temporada N,
    el primer episodi de la temporada N+1 és el següent número.
    Ex: Si S1 té 8 episodis → S2E1 = episodi absolut 9.

    Algunes fonts de metadades (Cinemeta) agrupen els episodis per ANY de
    l'emissió en lloc de per número de temporada seqüencial (ex. un late
    night com "La Revuelta" té claus {2024: 159, 2025: 140} en lloc de
    {1: 159, 2: 140}). Si Stremio ens demana "temporada=1" però la clau "1"
    no existeix, i el nombre de temporades disponibles quadra amb l'índex
    demanat, mapegem POSICIONALMENT (1a temporada cronològica = clau més
    petita, 2a = següent, etc.) en lloc de rendir-nos.
    """
    if not episodis_per_temporada:
        logger.warning(
            f"[MAPATGE] No hi ha dades de temporades! "
            f"S{temporada}E{capitol} → usant {capitol} directament"
        )
        return capitol

    claus_ordenades = sorted(episodis_per_temporada.keys())

    if temporada in episodis_per_temporada:
        clau_temporada = temporada
    elif 1 <= temporada <= len(claus_ordenades):
        clau_temporada = claus_ordenades[temporada - 1]
        logger.warning(
            f"[MAPATGE] Temporada {temporada} no existeix literalment com a "
            f"clau (temporades disponibles: {claus_ordenades}). Com que "
            f"{temporada} és un índex vàlid dins del rang, mapejant "
            f"POSICIONALMENT a la clau real '{clau_temporada}' "
            f"(probablement Cinemeta agrupa per any d'emissió, no per "
            f"número de temporada seqüencial)"
        )
    else:
        logger.warning(
            f"[MAPATGE] Temporada {temporada} no existeix a les dades i tampoc "
            f"és un índex posicional vàlid! Temporades disponibles: "
            f"{claus_ordenades} ({len(claus_ordenades)} en total). "
            f"S{temporada}E{capitol} → usant {capitol} directament"
        )
        return capitol

    max_ep_temporada = episodis_per_temporada[clau_temporada]

    if capitol > max_ep_temporada:
        logger.warning(
            f"[MAPATGE] S{temporada}E{capitol} fora de rang! "
            f"Temporada {temporada} (clau real '{clau_temporada}') té "
            f"{max_ep_temporada} episodis. "
            f"Pot ser que {capitol} ja sigui absolut → usant {capitol}"
        )
        return capitol

    idx_temporada = claus_ordenades.index(clau_temporada)
    offset = sum(
        episodis_per_temporada[c] for c in claus_ordenades[:idx_temporada]
    )
    absolut = offset + capitol
    logger.info(
        f"[MAPATGE] S{temporada}E{capitol} (clau real '{clau_temporada}') → "
        f"episodi absolut {absolut} (temporades anteriors "
        f"{claus_ordenades[:idx_temporada]} sumen {offset} episodis, "
        f"+ posició {capitol} dins temporada {temporada})"
    )
    return absolut


async def obtenir_noms_tmdb(imdb_id: str, tipus: str) -> tuple[list[str], int | None, bool, int | None]:
    noms = []
    any_estrena = None
    es_animacio = False
    durada_min = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://api.themoviedb.org/3/find/{imdb_id}",
                params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
            )
            r.raise_for_status()
            data = r.json()

            tmdb_id, tmdb_type, result = None, None, None
            if tipus == "movie" and data.get("movie_results"):
                result = data["movie_results"][0]
                tmdb_id, tmdb_type = result["id"], "movie"
            elif tipus == "series" and data.get("tv_results"):
                result = data["tv_results"][0]
                tmdb_id, tmdb_type = result["id"], "tv"
            elif data.get("tv_results"):
                result = data["tv_results"][0]
                tmdb_id, tmdb_type = result["id"], "tv"
            elif data.get("movie_results"):
                result = data["movie_results"][0]
                tmdb_id, tmdb_type = result["id"], "movie"

            if not result:
                return [], None, False, None

            es_animacio = 16 in (result.get("genre_ids") or [])

            name_key = "title" if tmdb_type == "movie" else "name"
            orig_key = "original_title" if tmdb_type == "movie" else "original_name"
            date_key = "release_date" if tmdb_type == "movie" else "first_air_date"

            nom_principal = result.get(name_key, "")
            nom_original = result.get(orig_key, "")
            data_estrena = result.get(date_key, "")

            if data_estrena and len(data_estrena) >= 4:
                try:
                    any_estrena = int(data_estrena[:4])
                except ValueError:
                    any_estrena = None

            if nom_principal:
                noms.append(nom_principal)
            if nom_original and nom_original not in noms:
                noms.append(nom_original)

            r2 = await client.get(
                f"https://api.themoviedb.org/3/{tmdb_type}/{tmdb_id}/translations",
                params={"api_key": TMDB_API_KEY},
            )
            r2.raise_for_status()

            nom_ca, nom_es, nom_es_es = None, None, None
            for tr in r2.json().get("translations", []):
                iso = tr.get("iso_639_1", "")
                country = tr.get("iso_3166_1", "")
                td = tr.get("data", {})
                title = td.get("title") or td.get("name")
                if not title:
                    continue
                if iso == "ca" and not nom_ca:
                    nom_ca = title
                elif iso == "es" and country == "ES" and not nom_es_es:
                    nom_es_es = title
                elif iso == "es" and not nom_es:
                    nom_es = title

            for n in [nom_ca, nom_es_es, nom_es]:
                if n and n not in noms:
                    noms.append(n)

            if tmdb_type == "movie":
                r3 = await client.get(
                    f"https://api.themoviedb.org/3/movie/{tmdb_id}",
                    params={"api_key": TMDB_API_KEY},
                )
                r3.raise_for_status()
                runtime = r3.json().get("runtime")
                if isinstance(runtime, int) and runtime > 0:
                    durada_min = runtime

    except Exception as e:
        logger.error(f"Error TMDB per {imdb_id}: {e}")

    return noms, any_estrena, es_animacio, durada_min


_CINEMETA_RUNTIME_REGEX = re.compile(r"(\d+)")

async def obtenir_noms_cinemeta(imdb_id: str, tipus: str) -> tuple[list[str], int | None, bool, int | None]:
    cinemeta_type = "movie" if tipus == "movie" else "series"
    url = f"https://v3-cinemeta.strem.io/meta/{cinemeta_type}/{imdb_id}.json"
    noms = []
    any_estrena = None
    es_animacio = False
    durada_min = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            meta = r.json().get("meta", {})

            nom = meta.get("name", "")
            if nom:
                noms.append(nom)
                logger.info(f"[CINEMETA] Trobat nom '{nom}' per {imdb_id}")

            for alt in meta.get("aliases", []):
                if alt and alt not in noms:
                    noms.append(alt)

            generes = [g.lower() for g in (meta.get("genres") or [])]
            es_animacio = "animation" in generes or "animació" in generes

            any_str = meta.get("year", "") or meta.get("released", "")
            if any_str and len(any_str) >= 4:
                try:
                    any_estrena = int(any_str[:4])
                except ValueError:
                    any_estrena = None

            if tipus == "movie":
                runtime_str = meta.get("runtime", "") or ""
                m = _CINEMETA_RUNTIME_REGEX.search(runtime_str)
                if m:
                    durada_min = int(m.group(1))

    except Exception as e:
        logger.error(f"Error Cinemeta per {imdb_id}: {e}")

    return noms, any_estrena, es_animacio, durada_min


def obtenir_any_item_3cat(item: dict) -> int | None:
    data_pub = item.get("data_publicacio", "")
    match = re.search(r"(\d{4})", data_pub)
    if match:
        return int(match.group(1))
    return None

def any_coincideix(any_objectiu: int | None, any_item: int | None, tolerancia: int = 2) -> bool:
    if any_objectiu is None or any_item is None:
        return True
    return abs(any_objectiu - any_item) <= tolerancia

def durada_coincideix(
    durada_tmdb_min: int | None, durada_item_min: int | None,
    tolerancia_baixa: int = DURADA_TOLERANCIA_BAIXA_MIN,
    tolerancia_alta: int = DURADA_TOLERANCIA_BAIXA_MIN,
) -> bool:
    """Compara la durada real d'una pel·lícula (TMDB/Cinemeta) amb la durada
    de l'item trobat a la font. S'usa com a substitut de l'any quan la font
    no exposa cap camp d'any fiable. Finestra asimètrica: l'item mai hauria
    de ser MÉS CURT que la durada real (tolerancia_baixa cobreix arrodoniment
    i talls de crèdits), però pot ser més LLARG si la font hi insereix
    publicitat (tolerancia_alta, més gran a Pluto que a 3Cat)."""
    if durada_tmdb_min is None or durada_item_min is None:
        return True
    return (durada_tmdb_min - tolerancia_baixa) <= durada_item_min <= (durada_tmdb_min + tolerancia_alta)

# ═══════════════════════════════════════════════════════════════════════════════
#  API DE 3CAT — CERCA
# ═══════════════════════════════════════════════════════════════════════════════

CERCA_3CAT_TTL_SEGONS = 30 * 60
_cerca_3cat_cache: dict[tuple[str, int], tuple[float, list]] = {}
_cerca_3cat_en_curs: dict[tuple[str, int], "asyncio.Future[list]"] = {}
_client_3cat: httpx.AsyncClient | None = None


def _client_http_3cat() -> httpx.AsyncClient:
    """Un sol client (connexions reutilitzades): evita una encaixada TLS per pàgina."""
    global _client_3cat
    if _client_3cat is None or _client_3cat.is_closed:
        _client_3cat = httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_keepalive_connections=10))
    return _client_3cat


async def _pagina_3cat(text: str, pagina: int) -> tuple[list, int]:
    params = {
        "version": "2.0", "_format": "json",
        "text": text, "tipologia": "DTY_VIDEO_MM",
        "items_pagina": 300, "pagina": pagina, "master": "yes",
    }
    r = await _client_http_3cat().get(API_3CAT_CERCA, params=params)
    r.raise_for_status()
    resp = r.json().get("resposta", {})
    return resp.get("items", {}).get("item", []), resp.get("paginacio", {}).get("total_pagines", 1)


async def _cercar_api_3cat_directe(text: str, max_pagines: int) -> list:
    try:
        tots, total = await _pagina_3cat(text, 1)
    except Exception as e:
        logger.error(f"Error API 3Cat per '{text}' pàg 1: {e}")
        return []
    tots = list(tots)
    resta = list(range(2, min(total, max_pagines) + 1))
    if resta:  # la resta de pàgines, totes alhora (abans eren seqüencials: ~4 s per pàgina)
        resultats = await asyncio.gather(*(_pagina_3cat(text, p) for p in resta), return_exceptions=True)
        for p, r in zip(resta, resultats):
            if isinstance(r, Exception):
                logger.error(f"Error API 3Cat per '{text}' pàg {p}: {r}")
            else:
                tots.extend(r[0])
    return tots


async def cercar_api_3cat(text: str, max_pagines: int = 5) -> list:
    clau = (text, max_pagines)
    entrada = _cerca_3cat_cache.get(clau)
    if entrada and time.time() - entrada[0] < CERCA_3CAT_TTL_SEGONS:
        return entrada[1]
    en_curs = _cerca_3cat_en_curs.get(clau)
    if en_curs is not None:  # una altra petició ja ho està demanant: s'hi enganxa
        return await asyncio.shield(en_curs)
    tasca = asyncio.ensure_future(_cercar_api_3cat_directe(text, max_pagines))
    _cerca_3cat_en_curs[clau] = tasca
    try:
        items = await tasca
    finally:
        _cerca_3cat_en_curs.pop(clau, None)
    if items:  # no es cachegen els errors ni els buits
        if len(_cerca_3cat_cache) > 300:
            _cerca_3cat_cache.clear()
        _cerca_3cat_cache[clau] = (time.time(), items)
    return items

# ═══════════════════════════════════════════════════════════════════════════════
#  API DE 3CAT — EXTRACCIÓ D'STREAM
# ═══════════════════════════════════════════════════════════════════════════════

async def obtenir_stream_url(video_id: int) -> str | None:
    params = {
        "media": "video",
        "versio": "vast",
        "idint": str(video_id),
        "format": "dm",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            r = await client.get(API_3CAT_MEDIA, params=params)
            r.raise_for_status()
            data = r.json()

            urls_trobades = []
            for fmt in data.get("media", data.get("informacio", data.get("format", []))):
                if isinstance(fmt, dict):
                    url = fmt.get("file") or fmt.get("url")
                    if url:
                        urls_trobades.append(url)

            if not urls_trobades:
                text_resp = r.text
                for pattern in [
                    r'https?://[^\s"<>]+\.mpd[^\s"<>]*',
                    r'https?://[^\s"<>]+\.m3u8[^\s"<>]*',
                    r'https?://[^\s"<>]+\.mp4[^\s"<>]*',
                ]:
                    matches = re.findall(pattern, text_resp)
                    urls_trobades.extend(matches)

            if urls_trobades:
                for url in urls_trobades:
                    if ".m3u8" in url:
                        logger.info(f"[STREAM] Trobat HLS per {video_id}: {url[:80]}...")
                        return url
                for url in urls_trobades:
                    if ".mpd" in url:
                        logger.info(f"[STREAM] Trobat DASH per {video_id}: {url[:80]}...")
                        return url
                logger.info(f"[STREAM] Trobat media per {video_id}: {urls_trobades[0][:80]}...")
                return urls_trobades[0]

            logger.warning(f"[STREAM] API media no ha retornat URLs per {video_id}")

    except Exception as e:
        logger.error(f"[STREAM] Error API media per {video_id}: {e}")

    return None

_master_hls_cache: dict[int, tuple[float, str, str]] = {}  # video_id -> (instant, url del master, text)
MASTER_HLS_TTL_SEGONS = 10 * 60


async def obtenir_master_hls(video_id: int) -> tuple[str, str] | None:
    """(url, text) del master HLS de 3Cat (576p/720p/1080p). None si no n'hi ha."""
    entrada = _master_hls_cache.get(video_id)
    if entrada and time.time() - entrada[0] < MASTER_HLS_TTL_SEGONS:
        return entrada[1], entrada[2]
    try:
        client = _client_http_3cat()
        r = await client.get(
            API_3CAT_MEDIA,
            params={"media": "video", "versio": "vast", "idint": str(video_id), "format": "hls"},
            follow_redirects=True,
        )
        r.raise_for_status()
        trobades = re.findall(r'https?://[^\s"<>]+[.]m3u8[^\s"<>]*', r.text.replace("\\/", "/"))
        if not trobades:
            return None
        url = trobades[0]
        m = await client.get(url, follow_redirects=True)
        m.raise_for_status()
        if "#EXT-X-STREAM-INF" not in m.text:
            return None
        _master_hls_cache[video_id] = (time.time(), url, m.text)
        return url, m.text
    except Exception as e:
        logger.warning(f"[STREAM] Sense master HLS per {video_id}: {type(e).__name__}: {e}")
        return None

# ═══════════════════════════════════════════════════════════════════════════════
#  ANIDD — GESTIÓ DE SESSIÓ
# ═══════════════════════════════════════════════════════════════════════════════

_anidd_client: httpx.AsyncClient | None = None
_anidd_lock = asyncio.Lock()

def anidd_sessio_caducada(cos_html: str) -> bool:
    return 'name="password_protected_pwd"' in cos_html

async def anidd_fer_login() -> httpx.AsyncClient:
    client = httpx.AsyncClient(
        base_url=ANIDD_BASE,
        headers={"User-Agent": ANIDD_USER_AGENT},
        follow_redirects=True,
        timeout=15.0,
    )
    try:
        resp = await client.post(
            "/",
            params={
                "password-protected": "login",
                "redirect_to": f"{ANIDD_BASE}/",
            },
            data={
                "password_protected_pwd": ANIDD_PASSWORD,
                "password_protected_cookie_test": "1",
                "password-protected": "login",
                "redirect_to": f"{ANIDD_BASE}/",
                "wp-submit": "Entra",
            },
        )
        if anidd_sessio_caducada(resp.text):
            logger.error("[ANIDD] Login fallit")
        else:
            logger.info(f"[ANIDD] Login correcte (status {resp.status_code})")
    except Exception as e:
        logger.error(f"[ANIDD] Error fent login: {e}")
    return client

async def anidd_obtenir_client() -> httpx.AsyncClient:
    global _anidd_client
    if _anidd_client is None:
        async with _anidd_lock:
            if _anidd_client is None:
                _anidd_client = await anidd_fer_login()
    return _anidd_client

async def anidd_get(path: str, **kwargs) -> str:
    global _anidd_client
    client = await anidd_obtenir_client()
    resp = await client.get(path, **kwargs)
    if anidd_sessio_caducada(resp.text):
        logger.info("[ANIDD] Sessió caducada, refent login...")
        async with _anidd_lock:
            _anidd_client = await anidd_fer_login()
        resp = await _anidd_client.get(path, **kwargs)
    return resp.text

# ═══════════════════════════════════════════════════════════════════════════════
#  ANIDD — CERCA I EXTRACCIÓ DE PLAYLIST
# ═══════════════════════════════════════════════════════════════════════════════

_ANIDD_DIV_PATTERN = re.compile(
    r'<div class="mvp-playlist-item[^"]*"[^>]*data-download="[^"]*"[^>]*>',
    re.IGNORECASE,
)
_ANIDD_ATTR_MEDIA_ID = re.compile(r'data-media-id="(\d+)"')
_ANIDD_ATTR_TITLE = re.compile(r'data-title="([^"]*)"')
_ANIDD_ATTR_DOWNLOAD = re.compile(r'data-download="([^"]*)"')
_ANIDD_TXCY = re.compile(r"[Tt](\d+)\s*[Xx]\s*[Cc](\d+)")

async def anidd_cercar(terme: str) -> list[dict]:
    try:
        cos_html = await anidd_get("/", params={"s": terme, "ajax_search": "true"})
    except Exception as e:
        logger.error(f"[ANIDD] Error cercant '{terme}': {e}")
        return []

    soup = BeautifulSoup(cos_html, "html.parser")
    resultats = []
    for card in soup.select(".common_card"):
        titol_el = card.select_one("h6 a")
        link_el = card.select_one("a[href]")
        if not titol_el or not link_el:
            continue
        href = link_el.get("href", "")
        titol = titol_el.get_text(strip=True)
        if "/tvshows/" in href:
            tipus_anidd = "tvshows"
        elif "/movies/" in href:
            tipus_anidd = "movies"
        else:
            continue
        resultats.append({"titol": titol, "url": href, "tipus": tipus_anidd})

    if resultats:
        logger.info(f"[ANIDD] '{terme}' -> {len(resultats)} resultat(s)")
    return resultats

def anidd_parsejar_playlist(cos_html: str) -> list[dict]:
    episodis = []
    for div_html in _ANIDD_DIV_PATTERN.findall(cos_html):
        m_id = _ANIDD_ATTR_MEDIA_ID.search(div_html)
        m_title = _ANIDD_ATTR_TITLE.search(div_html)
        m_dl = _ANIDD_ATTR_DOWNLOAD.search(div_html)
        if not (m_id and m_dl):
            continue
        episodis.append({
            "media_id": m_id.group(1),
            "titol": html_module.unescape(m_title.group(1)) if m_title else "",
            "url": html_module.unescape(m_dl.group(1)),
        })
    return episodis

async def anidd_obtenir_playlist(url_fitxa: str) -> list[dict]:
    url_player = url_fitxa.rstrip("/") + "/player/"
    try:
        cos_html = await anidd_get(url_player)
    except Exception as e:
        logger.error(f"[ANIDD] Error obtenint playlist de {url_player}: {e}")
        return []
    return anidd_parsejar_playlist(cos_html)

async def anidd_cercar_contingut(
    noms: list[str], tipus: str, temporada: int | None, capitol: int | None
) -> dict | None:
    for nom in noms:
        for terme in generar_termes_cerca(nom):
            resultats = await anidd_cercar(terme)
            for r in resultats:
                variants_candidat = generar_termes_cerca(r["titol"])
                coincideix = any(
                    textos_coincideixen(terme, variant, llindar=0.6)
                    for variant in variants_candidat
                )
                if not coincideix:
                    continue
                if tipus == "movie" and r["tipus"] != "movies":
                    continue
                if tipus == "series" and r["tipus"] != "tvshows":
                    continue

                episodis = await anidd_obtenir_playlist(r["url"])
                if not episodis:
                    continue

                if tipus == "movie":
                    logger.info(f"[ANIDD] Trobat: '{r['titol']}'")
                    return episodis[0]

                te_txcy = any(_ANIDD_TXCY.search(e["titol"]) for e in episodis)
                if te_txcy:
                    for e in episodis:
                        m = _ANIDD_TXCY.search(e["titol"])
                        if m and int(m.group(1)) == temporada and int(m.group(2)) == capitol:
                            logger.info(f"[ANIDD] Trobat: '{r['titol']}' T{temporada}xC{capitol}")
                            return e
                    continue

                idx = (capitol or 1) - 1
                if 0 <= idx < len(episodis):
                    logger.info(f"[ANIDD] Trobat: '{r['titol']}' (posició {idx + 1})")
                    return episodis[idx]

    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  XARXA CATALANA — ONE PIECE (tt0388629)
# ═══════════════════════════════════════════════════════════════════════════════

_xarxacat_episodis: dict[int, dict] | None = None
_xarxacat_pelis: list[dict] | None = None
_xarxacat_lock = asyncio.Lock()

async def xarxacat_carregar_dades() -> tuple[dict[int, dict], list[dict]]:
    global _xarxacat_episodis, _xarxacat_pelis

    if _xarxacat_episodis is not None:
        return _xarxacat_episodis, _xarxacat_pelis or []

    async with _xarxacat_lock:
        if _xarxacat_episodis is not None:
            return _xarxacat_episodis, _xarxacat_pelis or []

        episodis: dict[int, dict] = {}
        pelis: list[dict] = []

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{XARXACAT_API}/shows/{XARXACAT_SHOW_ID}")
                r.raise_for_status()
                show = r.json()

                ep_playlist_ids = []
                for pl in show.get("playlists", []):
                    pl_id = pl.get("id")
                    if pl_id in XARXACAT_PLAYLISTS_IGNORAR:
                        continue
                    if pl_id == XARXACAT_PLAYLIST_PELIS:
                        continue
                    ep_playlist_ids.append((pl_id, pl.get("nom", "")))

                ep_playlist_ids.sort(key=lambda x: x[1])

                async def fetch_playlist(pl_id: int) -> dict:
                    resp = await client.get(f"{XARXACAT_API}/playlists/{pl_id}")
                    resp.raise_for_status()
                    return resp.json()

                tasques_ep = [fetch_playlist(pl_id) for pl_id, _ in ep_playlist_ids]
                tasca_pelis = fetch_playlist(XARXACAT_PLAYLIST_PELIS)
                resultats = await asyncio.gather(*tasques_ep, tasca_pelis, return_exceptions=True)

                for i, resultat in enumerate(resultats[:-1]):
                    if isinstance(resultat, Exception):
                        logger.error(f"[XARXACAT] Error carregant playlist: {resultat}")
                        continue
                    for video in resultat.get("videos", []):
                        nom = video.get("nom", "")
                        m = re.search(r"(\d+)", nom)
                        if m:
                            num_ep = int(m.group(1))
                            episodis[num_ep] = {
                                "url": video.get("url", ""),
                                "nom": nom,
                            }

                resultat_pelis = resultats[-1]
                if not isinstance(resultat_pelis, Exception):
                    for video in resultat_pelis.get("videos", []):
                        pelis.append({
                            "url": video.get("url", ""),
                            "nom": video.get("nom", ""),
                        })

                logger.info(
                    f"[XARXACAT] Carregats {len(episodis)} episodis i "
                    f"{len(pelis)} pel·lícules de One Piece"
                )

        except Exception as e:
            logger.error(f"[XARXACAT] Error carregant dades: {e}")

        _xarxacat_episodis = episodis
        _xarxacat_pelis = pelis
        return episodis, pelis


async def cercar_xarxacat_stream(
    imdb_id: str, tipus: str, temporada: int | None, capitol: int | None,
    nom_bonic: str, ep_absolut: int | None,
) -> dict | None:
    if imdb_id != XARXACAT_IMDB:
        return None

    episodis, pelis = await xarxacat_carregar_dades()

    if tipus == "series" and ep_absolut is not None:
        logger.info(
            f"[XARXACAT] Cercant episodi absolut {ep_absolut} "
            f"(rebut S{temporada}E{capitol}) entre {len(episodis)} episodis disponibles"
        )
        ep = episodis.get(ep_absolut)
        if ep and ep.get("url"):
            logger.info(f"[XARXACAT] TROBAT episodi {ep_absolut}: {ep['nom']}")
            return {
                "url": ep["url"],
                "name": "🎬 En català",
                "description": f"{nom_bonic}\n{ep['nom']}\n📡 Xarxa Catalana",
                "behaviorHints": {"notWebReady": False},
            }
        else:
            logger.info(
                f"[XARXACAT] Episodi {ep_absolut} NO disponible. "
                f"Rang disponible: 1-{max(episodis.keys()) if episodis else 0}"
            )

    if tipus == "movie":
        for peli in pelis:
            if peli.get("url"):
                if textos_coincideixen(nom_bonic, peli["nom"], llindar=0.5):
                    logger.info(f"[XARXACAT] Trobat pel·lícula: {peli['nom']}")
                    return {
                        "url": peli["url"],
                        "name": "🎬 En català",
                        "description": f"{peli['nom']}\n📡 Xarxa Catalana",
                        "behaviorHints": {"notWebReady": False},
                    }

    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  FANSUBS.CAT — ANIME SUBTITULAT EN CATALÀ
# ═══════════════════════════════════════════════════════════════════════════════

async def fansubscat_cercar(terme: str) -> list[dict]:
    url = f"{FANSUBSCAT_BASE}/autocomplete.php"
    params = {"query": terme}
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": FANSUBSCAT_USER_AGENT},
            follow_redirects=True,
        ) as client:
            r = await client.post(url, params=params)
            r.raise_for_status()
            html_text = r.text
    except Exception as e:
        logger.error(f"[FANSUBS.CAT] Error cercant '{terme}': {e}")
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    resultats = []

    for item in soup.select("a.autocomplete-item"):
        href = item.get("href", "")
        name_el = item.select_one("div.autocomplete-name")
        type_el = item.select_one("div.autocomplete-type")

        if not name_el or not href:
            continue

        if not href.startswith(FANSUBSCAT_BASE):
            logger.info(
                f"[FANSUBS.CAT] Ignorant resultat fora d'anime.fansubs.cat: {href}"
            )
            continue

        titol = name_el.get_text(strip=True)
        type_text = type_el.get_text(strip=True) if type_el else ""

        es_film = "Film" in type_text or "film" in type_text
        if "Sèrie" in type_text or "temporad" in type_text:
            es_film = False

        slug = href.replace(f"{FANSUBSCAT_BASE}/", "").strip("/")

        resultats.append({
            "titol": titol,
            "slug": slug,
            "es_film": es_film,
            "type_text": type_text,
        })

    if resultats:
        logger.info(f"[FANSUBS.CAT] '{terme}' -> {len(resultats)} resultat(s)")
    return resultats


async def fansubscat_obtenir_pagina(url: str) -> str | None:
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": FANSUBSCAT_USER_AGENT},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.error(f"[FANSUBS.CAT] Error carregant {url}: {e}")
        return None


def fansubscat_parsejar_versions(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    versions = []
    for tab in soup.select("div.version-tab"):
        slug = tab.get("data-version-slug", "")
        version_id = tab.get("data-version-id", "")
        fansub_el = tab.select_one("div.version-tab-text")
        fansub_name = ""
        if fansub_el:
            text = fansub_el.get_text(strip=True)
            fansub_name = text.replace("Versió de ", "").replace("Versió d'", "").strip()
        if slug and version_id:
            versions.append({
                "slug": slug,
                "version_id": version_id,
                "fansub": fansub_name,
            })
    return versions


def _parsejar_file_launchers(elements) -> list[dict]:
    episodis = []
    for ep in elements:
        pos = ep.get("data-position", "")
        file_id = ep.get("data-file-id", "")
        title_short = ep.get("data-title-short", "")
        if pos and file_id:
            try:
                pos_int = int(float(pos))
            except (ValueError, TypeError):
                continue
            episodis.append({
                "position": pos_int,
                "file_id": file_id,
                "title": title_short,
            })
    return episodis


def fansubscat_parsejar_episodis_versio(html_text: str, version_id: str) -> tuple[list[dict], list[list[dict]]]:
    soup = BeautifulSoup(html_text, "html.parser")
    tots = []
    per_divisio = []

    for container in soup.select(f"div.division-container[id*='-{version_id}-']"):
        episodis_div = _parsejar_file_launchers(
            container.select("div.file-launcher.episode")
        )
        tots.extend(episodis_div)
        per_divisio.append(episodis_div)

    if not tots:
        vc = soup.select_one(f"#version-content-{version_id}")
        if vc:
            tots = _parsejar_file_launchers(
                vc.select("div.file-launcher.episode")
            )
            if tots:
                per_divisio = [tots]
                logger.info(
                    f"[FANSUBS.CAT] Episodis trobats fora de divisions "
                    f"(version-content-{version_id}): {len(tots)}"
                )

    return tots, per_divisio


_RE_NUMERO_CAPITOL = re.compile(
    r"(?:Cap[íi]tol|Episodi|Episode|Ep\.?)\s+(\d+)", re.IGNORECASE
)

def fansubscat_extreure_numero_capitol(titol: str) -> int | None:
    """
    Extreu el número d'episodi del títol.
    Ex: "Capítol 747: La fortalesa..." → 747
    Ex: "Episodi 3" → 3
    """
    m = _RE_NUMERO_CAPITOL.search(titol)
    return int(m.group(1)) if m else None


def fansubscat_trobar_episodi(
    tots_episodis: list[dict],
    per_divisio: list[list[dict]],
    temporada: int,
    capitol: int,
    ep_absolut: int,
    fansub_nom: str,
) -> dict | None:
    """
    Troba l'episodi correcte a fansubs.cat.
    Usa l'episodi absolut per verificar que retornem l'episodi correcte.
    """
    if not tots_episodis:
        logger.info(f"[FANSUBS.CAT] [{fansub_nom}] No hi ha episodis disponibles")
        return None

    logger.info(
        f"[FANSUBS.CAT] [{fansub_nom}] Cercant episodi absolut {ep_absolut} "
        f"(S{temporada}E{capitol}) entre {len(tots_episodis)} episodis, "
        f"{len(per_divisio)} divisions. "
        f"Posicions disponibles: {tots_episodis[0]['position']}-{tots_episodis[-1]['position']}"
    )

    # Estratègia 1: Buscar per número d'episodi al títol
    for ep in tots_episodis:
        num_titol = fansubscat_extreure_numero_capitol(ep.get("title", ""))
        if num_titol is not None and num_titol == ep_absolut:
            logger.info(
                f"[FANSUBS.CAT] [{fansub_nom}] TROBAT per títol: "
                f"'{ep['title']}' (pos={ep['position']}, file_id={ep['file_id']})"
            )
            return ep

    # Estratègia 2: Buscar per posició == absolut (verificant títol no contradigui)
    for ep in tots_episodis:
        if ep["position"] == ep_absolut:
            num_titol = fansubscat_extreure_numero_capitol(ep.get("title", ""))
            if num_titol is None or num_titol == ep_absolut:
                logger.info(
                    f"[FANSUBS.CAT] [{fansub_nom}] TROBAT per posició: "
                    f"pos={ep_absolut}, title='{ep.get('title')}'"
                )
                return ep
            else:
                logger.info(
                    f"[FANSUBS.CAT] [{fansub_nom}] Posició {ep_absolut} existeix "
                    f"però el títol diu capítol {num_titol}, no {ep_absolut} → DESCARTAT"
                )

    logger.info(
        f"[FANSUBS.CAT] [{fansub_nom}] Episodi absolut {ep_absolut} "
        f"NO trobat en aquesta versió"
    )
    return None


async def fansubscat_obtenir_video_url(file_id: str) -> str | None:
    url = f"{FANSUBSCAT_BASE}/get_file_data.php"
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": FANSUBSCAT_USER_AGENT},
            follow_redirects=True,
        ) as client:
            r = await client.post(url, data={"file_id": file_id})
            r.raise_for_status()
            data = r.json()

            if data.get("result") != "ok":
                logger.warning(f"[FANSUBS.CAT] get_file_data error: {data.get('result')}")
                return None

            sources = data.get("data", {}).get("data_sources", [])
            if sources:
                video_url = sources[0].get("url", "")
                if video_url:
                    logger.info(f"[FANSUBS.CAT] URL vídeo obtinguda per file_id={file_id}")
                    return video_url

    except Exception as e:
        logger.error(f"[FANSUBS.CAT] Error obtenint vídeo per file_id={file_id}: {e}")

    return None


async def fansubscat_cercar_streams(
    noms: list[str], tipus: str, temporada: int | None, capitol: int | None,
    nom_bonic: str, ep_absolut: int | None,
) -> list[dict]:
    streams: list[dict] = []

    logger.info(
        f"[FANSUBS.CAT] Iniciant cerca: noms={noms}, tipus={tipus}, "
        f"S{temporada}E{capitol}, ep_absolut={ep_absolut}"
    )

    slugs_processats: set[str] = set()

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            resultats = await fansubscat_cercar(terme)

            if not resultats:
                logger.info(f"[FANSUBS.CAT] Cerca '{terme}' → 0 resultats")
                continue

            for r in resultats:
                logger.info(
                    f"[FANSUBS.CAT] Avaluant resultat: '{r['titol']}' "
                    f"(slug={r['slug']}, film={r['es_film']}, type={r['type_text']})"
                )

                if tipus == "movie" and not r["es_film"]:
                    logger.info(f"[FANSUBS.CAT] Descartat: no és film")
                    continue
                if tipus == "series" and r["es_film"]:
                    logger.info(f"[FANSUBS.CAT] Descartat: és film i busquem sèrie")
                    continue

                slug_serie = r["slug"].split("/")[0]
                if slug_serie in slugs_processats:
                    logger.info(
                        f"[FANSUBS.CAT] Sèrie '{slug_serie}' ja processada, salt"
                    )
                    continue
                slugs_processats.add(slug_serie)

                html_text = await fansubscat_obtenir_pagina(f"{FANSUBSCAT_BASE}/{r['slug']}")
                if not html_text:
                    continue

                versions = fansubscat_parsejar_versions(html_text)
                if not versions:
                    logger.info(f"[FANSUBS.CAT] No s'han trobat versions a la pàgina")
                    continue

                logger.info(
                    f"[FANSUBS.CAT] Versions trobades per '{r['titol']}': "
                    f"{[v['fansub'] for v in versions]}"
                )

                async def processar_versio(versio: dict, html_base: str) -> dict | None:
                    fansub_nom = versio.get("fansub", "?")

                    episodis, per_divisio = fansubscat_parsejar_episodis_versio(
                        html_base, versio["version_id"]
                    )

                    if not episodis and versio["slug"] != r["slug"]:
                        html_v = await fansubscat_obtenir_pagina(
                            f"{FANSUBSCAT_BASE}/{versio['slug']}"
                        )
                        if html_v:
                            episodis, per_divisio = fansubscat_parsejar_episodis_versio(
                                html_v, versio["version_id"]
                            )

                    if not episodis:
                        logger.info(
                            f"[FANSUBS.CAT] [{fansub_nom}] "
                            f"No hi ha episodis per version_id={versio['version_id']}"
                        )
                        return None

                    nums_titol = [
                        fansubscat_extreure_numero_capitol(e.get("title", ""))
                        for e in episodis
                    ]
                    nums_valids = [n for n in nums_titol if n is not None]
                    rang_str = ""
                    if nums_valids:
                        rang_str = f", capítols {min(nums_valids)}-{max(nums_valids)}"
                    logger.info(
                        f"[FANSUBS.CAT] [{fansub_nom}] "
                        f"{len(episodis)} episodis, {len(per_divisio)} divisions"
                        f"{rang_str}"
                    )

                    if tipus == "movie":
                        ep = episodis[0]
                    else:
                        ep = fansubscat_trobar_episodi(
                            episodis, per_divisio,
                            temporada or 1, capitol or 1,
                            ep_absolut or capitol or 1,
                            fansub_nom,
                        )

                    if not ep:
                        return None

                    video_url = await fansubscat_obtenir_video_url(ep["file_id"])
                    if not video_url:
                        logger.warning(
                            f"[FANSUBS.CAT] [{fansub_nom}] "
                            f"No s'ha pogut obtenir URL de vídeo per file_id={ep['file_id']}"
                        )
                        return None

                    linia_episodi = ""
                    if tipus == "series" and ep.get("title"):
                        linia_episodi = f"\n{ep['title']}"

                    logger.info(
                        f"[FANSUBS.CAT] [{fansub_nom}] STREAM TROBAT: "
                        f"'{ep.get('title')}' → {video_url[:60]}..."
                    )

                    return {
                        "url": video_url,
                        "name": "🎬 En català",
                        "description": (
                            f"{nom_bonic}{linia_episodi}"
                            f"\n🔤 Subtítols en català"
                            f"\n📡 Fansubs.cat ({fansub_nom})"
                        ),
                        "behaviorHints": {"notWebReady": False},
                    }

                tasques_versions = [
                    processar_versio(v, html_text) for v in versions
                ]
                resultats_versions = await asyncio.gather(*tasques_versions)

                for rv in resultats_versions:
                    if rv is not None:
                        streams.append(rv)

            if streams:
                logger.info(
                    f"[FANSUBS.CAT] Trobats {len(streams)} stream(s) amb terme '{terme}', "
                    f"continuant cercant amb altres noms..."
                )

    logger.info(f"[FANSUBS.CAT] Total final: {len(streams)} stream(s)")
    return streams

# ═══════════════════════════════════════════════════════════════════════════════
#  CERCA PRINCIPAL 3CAT
# ═══════════════════════════════════════════════════════════════════════════════

async def cercar_pelicula(noms: list[str], any_objectiu: int | None, durada_min: int | None = None) -> dict | None:
    logger.info(f"[3CAT] Iniciant cerca de pel·lícula: noms={noms}, any≈{any_objectiu}, durada≈{durada_min}min")
    cap_terme_amb_items = False

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await cercar_api_3cat(terme, max_pagines=2)
            if not items:
                logger.info(f"[3CAT] Terme '{terme}' -> 0 items retornats per l'API")
                continue

            cap_terme_amb_items = True
            rebutjats_no_pelicula = 0
            rebutjats_durada = 0
            rebutjats_titol = 0
            rebutjats_durada_real = 0
            for item in items:
                if not es_pelicula(item):
                    rebutjats_no_pelicula += 1
                    continue
                durada_item_seg = durada_a_segons(item.get("durada", ""))
                if durada_item_seg < DURADA_MIN_PELICULA:
                    rebutjats_durada += 1
                    continue
                titol_api = item.get("titol", "") or item.get("permatitle", "")
                if not textos_coincideixen(terme, titol_api):
                    rebutjats_titol += 1
                    continue
                # 3Cat no exposa cap camp d'any de producció fiable (el
                # "data_publicacio" és la data d'emissió/publicació al seu
                # CMS, no l'any d'estrena real), així que fem servir la
                # durada real (TMDB/Cinemeta) com a substitut per no
                # confondre pel·lícules diferents amb el mateix títol.
                if not durada_coincideix(
                    durada_min, durada_item_seg / 60,
                    tolerancia_alta=DURADA_TOLERANCIA_ALTA_3CAT_MIN,
                ):
                    logger.info(
                        f"[3CAT] '{titol_api}' descartada: durada {durada_item_seg/60:.0f}min "
                        f"no coincideix amb la durada real esperada ({durada_min}min)"
                    )
                    rebutjats_durada_real += 1
                    continue
                logger.info(
                    f"[3CAT] TROBAT: '{titol_api}' (terme='{terme}', "
                    f"durada={durada_item_seg}s)"
                )
                return item

            logger.info(
                f"[3CAT] Terme '{terme}' -> {len(items)} items, cap vàlid "
                f"(rebutjats: {rebutjats_no_pelicula} per no ser pel·lícula, "
                f"{rebutjats_durada} per durada insuficient, "
                f"{rebutjats_titol} per no coincidir el títol, "
                f"{rebutjats_durada_real} per durada real no coincident)"
            )

    if not cap_terme_amb_items:
        logger.warning(
            f"[3CAT] Cap terme de cerca ha retornat resultats de l'API per "
            f"noms={noms} — 3Cat probablement no té aquest contingut"
        )
    else:
        logger.warning(
            f"[3CAT] S'han trobat items però cap ha superat els filtres "
            f"(pel·lícula/durada/títol) per noms={noms}"
        )
    return None

async def cercar_episodi(noms: list[str], temporada: int, capitol: int, any_objectiu: int | None) -> dict | None:
    logger.info(
        f"[3CAT] Iniciant cerca d'episodi: noms={noms}, S{temporada}E{capitol}"
    )
    cap_terme_amb_items = False

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await cercar_api_3cat(terme, max_pagines=5)
            if not items:
                logger.info(f"[3CAT] Terme '{terme}' -> 0 items retornats per l'API")
                continue

            cap_terme_amb_items = True
            logger.info(f"[3CAT] Terme '{terme}' -> {len(items)} items retornats")

            candidats = []
            rebutjats_pelicula = 0
            rebutjats_nom = 0
            rebutjats_durada = 0
            for item in items:
                if es_pelicula(item):
                    # Una pel·lícula mai pot ser candidata a "episodi" —
                    # evita que un títol de pel·lícula amb text similar
                    # (ex. "La princesa promesa" vs "La promesa") es coli
                    # com si fos un capítol de la sèrie cercada.
                    rebutjats_pelicula += 1
                    continue

                nom_prog = obtenir_nom_programa(item)
                if nom_prog:
                    # Si l'item pertany a un programa, EXIGIM que el nom del
                    # programa coincideixi — el títol de l'episodi és sovint
                    # genèric (ex. "La promesa") i pot coincidir per atzar
                    # amb el terme cercat encara que sigui un programa
                    # completament diferent (ex. un episodi de Saint Seiya
                    # titulat "La promesa").
                    if not textos_coincideixen(terme, nom_prog, llindar=0.6):
                        rebutjats_nom += 1
                        continue
                else:
                    if not textos_coincideixen(terme, item.get("titol", ""), llindar=0.5):
                        rebutjats_nom += 1
                        continue
                if durada_a_segons(item.get("durada", "")) < DURADA_MIN_EPISODI:
                    rebutjats_durada += 1
                    continue
                candidats.append(item)

            logger.info(
                f"[3CAT] Terme '{terme}': {len(candidats)} candidats vàlids "
                f"(rebutjats: {rebutjats_pelicula} per ser pel·lícula, "
                f"{rebutjats_nom} per no coincidir el nom, "
                f"{rebutjats_durada} per durada insuficient)"
            )

            if not candidats:
                continue

            es_pla = all(it.get("capitol_temporada", -1) == -1 for it in candidats)
            logger.info(
                f"[3CAT] Terme '{terme}': estructura {'plana (sense temporada/capítol explícits)' if es_pla else 'amb temporada/capítol explícits'}"
            )

            if not es_pla:
                temporades_capitols_vistos = []
                for item in candidats:
                    temp_item = obtenir_temporada(item)
                    cap_item = obtenir_capitol(item)
                    temporades_capitols_vistos.append(f"T{temp_item}E{cap_item}")
                    if temp_item < 0 and cap_item < 0:
                        continue
                    if temp_item == temporada and cap_item == capitol:
                        logger.info(
                            f"[3CAT] TROBAT (estructura explícita): '{item.get('titol')}' "
                            f"programa='{obtenir_nom_programa(item)}' T{temp_item}E{cap_item}"
                        )
                        return item
                logger.info(
                    f"[3CAT] Terme '{terme}': cap candidat amb T{temporada}E{capitol} exacte. "
                    f"Combinacions T/E vistes: {temporades_capitols_vistos}"
                )
                continue

            millor_item, millor_diferencia = None, None
            for item in candidats:
                cap_abs = item.get("capitol", -1)
                if not isinstance(cap_abs, int) or cap_abs < 0:
                    continue
                diferencia = abs(cap_abs - capitol)
                if millor_diferencia is None or diferencia < millor_diferencia:
                    millor_item, millor_diferencia = item, diferencia

            if millor_item:
                logger.info(
                    f"[3CAT] TROBAT (estructura plana, per proximitat): "
                    f"'{millor_item.get('titol')}' programa='{obtenir_nom_programa(millor_item)}' "
                    f"(capítol de l'item={millor_item.get('capitol')}, demanat={capitol}, "
                    f"diferència={millor_diferencia})"
                )
                return millor_item
            else:
                logger.info(
                    f"[3CAT] Terme '{terme}': estructura plana però cap candidat "
                    f"tenia un camp 'capitol' numèric vàlid per comparar"
                )

    if not cap_terme_amb_items:
        logger.warning(
            f"[3CAT] Cap terme de cerca ha retornat resultats de l'API per "
            f"noms={noms} (S{temporada}E{capitol}) — 3Cat probablement no té aquest contingut"
        )
    else:
        logger.warning(
            f"[3CAT] S'han trobat items però cap ha superat els filtres de "
            f"coincidència/durada/estructura per noms={noms} S{temporada}E{capitol}"
        )
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  RTVE.ES — CERCA I RESOLUCIÓ DE VÍDEO
# ═══════════════════════════════════════════════════════════════════════════════

async def rtve_cercar(terme: str, pagina: int = 1, mida: int = 15) -> list[dict]:
    params = {
        "search": terme,
        "page": pagina,
        "size": mida,
        "context": "tve",
        "type": "completo",
        "tipology": "video",
        "isExpanded": "true",
        "isChild": "true",
        "useOntology": "false",
    }
    try:
        async with httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": RTVE_USER_AGENT}
        ) as client:
            r = await client.get(RTVE_SEARCH_API, params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get("page", {}).get("items", [])
    except Exception as e:
        logger.error(
            f"[RTVE] Error cercant contingut '{terme}': {type(e).__name__}: {e} "
            f"(url={RTVE_SEARCH_API}, params={params})"
        )
        return []

    if items:
        logger.info(f"[RTVE] Cerca de contingut '{terme}' -> {len(items)} items")
    else:
        logger.info(
            f"[RTVE] Cerca de contingut '{terme}' -> 0 items "
            f"(resposta bruta: {str(data)[:200]!r})"
        )
    return items


async def rtve_cercar_programes(terme: str, mida: int = 10) -> list[dict]:
    params = {
        "search": terme,
        "page": 1,
        "size": mida,
        "context": "tve",
        "isExpanded": "true",
        "isChild": "true",
        "useOntology": "false",
    }
    try:
        async with httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": RTVE_USER_AGENT}
        ) as client:
            r = await client.get(RTVE_SEARCH_PROGRAMS_API, params=params)
            r.raise_for_status()
            data = r.json()
            items = data.get("page", {}).get("items", [])
    except Exception as e:
        logger.error(
            f"[RTVE] Error cercant programa '{terme}': {type(e).__name__}: {e} "
            f"(url={RTVE_SEARCH_PROGRAMS_API}, params={params})"
        )
        return []

    if items:
        titols = [it.get("title") for it in items]
        logger.info(f"[RTVE] Cerca de programes '{terme}' -> {len(items)}: {titols}")
    else:
        logger.info(
            f"[RTVE] Cerca de programes '{terme}' -> 0 resultats "
            f"(resposta bruta: {str(data)[:200]!r})"
        )
    return items


RTVE_PROGRAMES_REINTENTS = 3
RTVE_PROGRAMES_ESPERA_REINTENT_S = 1.5


async def rtve_cercar_programes_fiable(
    terme: str, imdb_id: str | None = None, mida: int = 10,
) -> list[dict]:
    """
    Embolcall de rtve_cercar_programes() amb reintents.

    L'API de cerca de programes de RTVE és INTERMITENTMENT poc fiable:
    la mateixa consulta, idèntica, pot tornar 3 resultats correctes en un
    intent i només 1 (i incorrecte) en el següent — probablement rèpliques
    de cache/índex no sincronitzades al seu backend. Quan sabem l'IMDb ID
    que busquem, si el primer intent no el conté, reintentem uns segons
    després abans de rendir-nos, ja que sovint el segon/tercer intent sí
    que torna el catàleg complet.
    """
    ultims_items: list[dict] = []
    for intent in range(1, RTVE_PROGRAMES_REINTENTS + 1):
        items = await rtve_cercar_programes(terme, mida=mida)
        ultims_items = items

        if not imdb_id:
            return items

        if not items:
            # 0 resultats no és un "resultat incomplet" (la flakiness coneguda
            # de RTVE és tornar un subconjunt N>0 dels resultats reals) — no
            # té sentit pagar el cost del reintent+espera quan no hi ha res.
            return items

        te_confirmat = any(
            (it.get("idImdb") or "").strip().lower() == imdb_id.strip().lower()
            for it in items
        )
        if te_confirmat:
            if intent > 1:
                logger.info(
                    f"[RTVE] Cerca de programes '{terme}': coincidència per "
                    f"IMDb trobada a l'intent {intent}/{RTVE_PROGRAMES_REINTENTS} "
                    f"(l'API de RTVE havia tornat un resultat incomplet abans)"
                )
            return items

        if intent < RTVE_PROGRAMES_REINTENTS:
            logger.info(
                f"[RTVE] Cerca de programes '{terme}': l'intent {intent} "
                f"({len(items)} resultats) no conté l'IMDb {imdb_id} — "
                f"reintentant en {RTVE_PROGRAMES_ESPERA_REINTENT_S}s per si "
                f"l'API de RTVE ha tornat un resultat incomplet..."
            )
            await asyncio.sleep(RTVE_PROGRAMES_ESPERA_REINTENT_S)

    logger.info(
        f"[RTVE] Cerca de programes '{terme}': després de "
        f"{RTVE_PROGRAMES_REINTENTS} intents, cap ha contingut l'IMDb "
        f"{imdb_id} — usant l'últim resultat ({len(ultims_items)} items)"
    )
    return ultims_items


def rtve_obtenir_temporada(item: dict) -> int:
    t = item.get("temporadaOrden")
    if isinstance(t, int) and t > 0:
        return t
    m = re.search(r"T(\d+)", item.get("temporadaShortTitle", "") or "")
    if m:
        return int(m.group(1))
    m = re.search(r"Temporada\s+(\d+)", item.get("temporada", "") or "", re.I)
    return int(m.group(1)) if m else -1


def rtve_obtenir_capitol(item: dict) -> int:
    ep = item.get("episode")
    return ep if isinstance(ep, int) and ep > 0 else -1


def rtve_durada_segons(item: dict) -> int:
    try:
        return int(item.get("duration") or 0) // 1000
    except (TypeError, ValueError):
        return 0


RTVE_PROGRAMES_API = "https://www.rtve.es/api/programas"
RTVE_MAX_PAGINES_PROGRAMA = 50  # sostre de seguretat: fins a 3000 episodis (size=60/pàgina)


async def rtve_obtenir_episodis_programa(
    program_id: str, temporada: int, capitol: int, ep_absolut: int | None = None,
) -> dict | None:
    """
    Recorre l'endpoint d'episodis d'un programa (ordenat, més nou primer)
    fins trobar el capítol demanat.

    RTVE NO és consistent amb el camp 'episode' entre programes:
      1) Sèries normals: 'episode' és relatiu a la temporada (1, 2, 3...).
      2) Culebrons diaris llargs (ex. "La Promesa"): 'episode' és un
         comptador ABSOLUT de capítol (895, 896...), sovint sense
         'temporadaOrden' als episodis normals (només als especials).
      3) Late nights / talk shows (ex. "La Revuelta"): 'episode' és
         SEMPRE 0 — cada programa s'identifica per data/convidat, no per
         número. Aquí l'única opció és comptar la POSICIÓ cronològica
         dins la temporada (episodi 1 = el més antic d'aquella temporada).
    Provem les tres estratègies, en aquest ordre de fiabilitat.
    """
    logger.info(
        f"[RTVE] Navegant episodis del programa {program_id} buscant "
        f"T{temporada}E{capitol} (ep_absolut={ep_absolut}) "
        f"(fins a {RTVE_MAX_PAGINES_PROGRAMA} pàgines de 60)"
    )
    temporades_vistes: set[int] = set()
    total_episodis_recorreguts = 0
    # Acumulem els items de la temporada demanada, en l'ordre en què RTVE
    # els retorna (més nou primer), per si calgués la 3a estratègia
    # (posició cronològica) quan cap episodi porta número.
    items_temporada_demanada: list[dict] = []

    try:
        async with httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": RTVE_USER_AGENT}
        ) as client:
            for pagina in range(1, RTVE_MAX_PAGINES_PROGRAMA + 1):
                r = await client.get(
                    f"{RTVE_PROGRAMES_API}/{program_id}/videos",
                    params={"type": 39816, "page": pagina, "size": 60},
                )
                r.raise_for_status()
                page_data = r.json().get("page", {})
                items = page_data.get("items", [])
                total_pagines = page_data.get("totalPages", 1)
                total_items_programa = page_data.get("total", "?")

                if not items:
                    logger.info(
                        f"[RTVE] Programa {program_id}, pàgina {pagina}/{total_pagines}: "
                        f"0 items retornats, aturant la navegació"
                    )
                    break

                logger.info(
                    f"[RTVE] Programa {program_id}, pàgina {pagina}/{total_pagines} "
                    f"({total_items_programa} episodis en total): {len(items)} items rebuts"
                )

                for item in items:
                    total_episodis_recorreguts += 1
                    temp_item = rtve_obtenir_temporada(item)
                    cap_item = rtve_obtenir_capitol(item)
                    if temp_item > 0:
                        temporades_vistes.add(temp_item)
                        if temp_item == temporada:
                            items_temporada_demanada.append(item)
                    # Alguns programes (sovint infantils) no porten temporada
                    # explícita: RTVE els llista com a episodis plans, així
                    # que en aquest cas comparem només pel número d'episodi.
                    coincideix_temporada = (
                        temp_item == temporada if temp_item > 0 else True
                    )
                    coincideix_relatiu = coincideix_temporada and cap_item == capitol
                    coincideix_absolut = (
                        ep_absolut is not None and cap_item == ep_absolut
                    )
                    if coincideix_relatiu or coincideix_absolut:
                        estrategia = "relatiu (T+E)" if coincideix_relatiu else "absolut (culebró)"
                        logger.info(
                            f"[RTVE] TROBAT a la pàgina {pagina} (estratègia={estrategia}): "
                            f"id={item.get('id')}, T{temp_item}E{cap_item}, "
                            f"longTitle={item.get('longTitle')!r}"
                        )
                        return item

                if pagina >= total_pagines:
                    logger.info(
                        f"[RTVE] Programa {program_id}: s'ha arribat a l'última "
                        f"pàgina ({total_pagines}) sense trobar T{temporada}E{capitol} "
                        f"per número explícit"
                    )
                    break
    except Exception as e:
        logger.error(
            f"[RTVE] Error recorrent episodis del programa {program_id} "
            f"(pàgina en curs, {total_episodis_recorreguts} episodis ja recorreguts): "
            f"{type(e).__name__}: {e}"
        )
        return None

    # ─── Estratègia 3: posició cronològica dins la temporada ──────────────
    # Útil per programes on 'episode' és sempre 0 (talk shows, late nights).
    # RTVE retorna més nou primer, així que invertim per tenir el més antic
    # (= episodi 1) primer.
    if items_temporada_demanada:
        items_cronologic = list(reversed(items_temporada_demanada))
        logger.info(
            f"[RTVE] Cap coincidència per número explícit. Provant estratègia "
            f"posicional: temporada {temporada} té {len(items_cronologic)} "
            f"episodis recollits (tots amb 'episode'=0, probablement talk show), "
            f"buscant posició {capitol}"
        )
        if 1 <= capitol <= len(items_cronologic):
            item = items_cronologic[capitol - 1]
            logger.info(
                f"[RTVE] TROBAT (estratègia=posicional dins temporada): "
                f"id={item.get('id')}, posició {capitol}/{len(items_cronologic)}, "
                f"longTitle={item.get('longTitle')!r}"
            )
            return item
        else:
            logger.warning(
                f"[RTVE] Posició {capitol} fora de rang: la temporada "
                f"{temporada} només té {len(items_cronologic)} episodis recollits"
            )

    logger.warning(
        f"[RTVE] No s'ha trobat T{temporada}E{capitol} al programa {program_id} "
        f"després de recórrer {total_episodis_recorreguts} episodis "
        f"(cap de les 3 estratègies ha funcionat). "
        f"Temporades trobades al recorregut: {sorted(temporades_vistes) or 'cap (numeració plana)'}"
    )
    return None


def _rtve_get_alphabet(alphabet_data: bytes) -> list[str]:
    alphabet = []
    e = 0
    d = 0
    for char in alphabet_data.decode("iso-8859-1"):
        if d == 0:
            alphabet.append(char)
            d = e = (e + 1) % 4
        else:
            d -= 1
    return alphabet


def _rtve_get_url(alphabet: list[str], url_data: bytes) -> str:
    url = ""
    f = 0
    e = 3
    b = 1
    l = 0
    for char in url_data.decode("iso-8859-1"):
        if f == 0:
            l = int(char) * 10
            f = 1
        elif e == 0:
            l += int(char)
            url += alphabet[l]
            e = (b + 3) % 4
            f = 0
            b += 1
        else:
            e -= 1
    return url


def rtve_decrypt_png(png_b64: str) -> list[tuple[str, str]]:
    """
    Desxifra el PNG que RTVE fa servir per amagar les URLs de vídeo.
    Reimplementació de https://js2.rtve.es/pages/app-player/3.5.1/js/pf_video.js
    Retorna una llista de (qualitat, url).
    """
    resultats = []
    try:
        raw = base64.b64decode(png_b64)
        buf = io.BytesIO(raw[8:])
        while True:
            length_data = buf.read(4)
            if len(length_data) < 4:
                break
            length = struct.unpack("!I", length_data)[0]
            chunk_type = buf.read(4)
            if chunk_type == b"IEND":
                break
            data = buf.read(length)
            if chunk_type == b"tEXt":
                data = bytes(filter(None, data))
                alphabet_data, _, url_data = data.partition(b"#")
                quality_str, _, url_data = url_data.rpartition(b"%%")
                alphabet = _rtve_get_alphabet(alphabet_data)
                url = _rtve_get_url(alphabet, url_data)
                if url:
                    resultats.append((quality_str.decode(errors="ignore"), url))
            buf.read(4)  # CRC
    except Exception as e:
        logger.error(
            f"[RTVE] Error desxifrant PNG: {type(e).__name__}: {e} "
            f"(mida base64 rebuda: {len(png_b64) if png_b64 else 0} caràcters)"
        )
    if not resultats:
        logger.warning(
            f"[RTVE] El PNG desxifrat no conté cap URL de vídeo vàlida "
            f"(cap chunk 'tEXt' amb dades útils, o dades corruptes)"
        )
    return resultats


async def rtve_obtenir_url_video(video_id: str) -> str | None:
    logger.info(f"[RTVE] Resolent URL de vídeo per id={video_id}...")
    for manager in ("rtveplayw", "default"):
        url = f"{RTVE_THUMBNAIL_BASE}/{manager}/videos/{video_id}.png"
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True,
                headers={"User-Agent": RTVE_USER_AGENT},
            ) as client:
                r = await client.get(url, params={"q": "v2"})
                r.raise_for_status()
                png_b64 = r.text
        except Exception as e:
            logger.error(
                f"[RTVE] Error HTTP obtenint PNG ({manager}) per id={video_id}: "
                f"{type(e).__name__}: {e} (url={url})"
            )
            continue

        if not png_b64:
            logger.warning(
                f"[RTVE] Manager '{manager}' per id={video_id}: resposta buida "
                f"(el vídeo pot no existir, estar geo-bloquejat, o requerir un "
                f"altre manager)"
            )
            continue

        candidats = rtve_decrypt_png(png_b64)
        if not candidats:
            logger.warning(
                f"[RTVE] Manager '{manager}' per id={video_id}: PNG rebut però "
                f"sense URLs desxifrables, provant següent manager..."
            )
            continue

        qualitats_disponibles = [q for q, _ in candidats]
        logger.info(
            f"[RTVE] Manager '{manager}' per id={video_id}: "
            f"qualitats disponibles={qualitats_disponibles}"
        )

        for qualitat_preferida in RTVE_QUALITY_ORDRE:
            for qualitat, url_video in candidats:
                if qualitat == qualitat_preferida:
                    logger.info(
                        f"[RTVE] URL vídeo obtinguda (manager='{manager}', "
                        f"qualitat='{qualitat}') per id={video_id}: {url_video[:100]}..."
                    )
                    return url_video

        qualitat, url_video = candidats[0]
        logger.info(
            f"[RTVE] Cap qualitat preferida disponible, usant la primera "
            f"(manager='{manager}', qualitat='{qualitat}') per id={video_id}: "
            f"{url_video[:100]}..."
        )
        return url_video

    logger.warning(
        f"[RTVE] No s'ha pogut obtenir cap URL de vídeo per id={video_id} "
        f"després de provar els managers ('rtveplayw', 'default'). "
        f"Possibles causes: el vídeo ja no existeix a RTVE, requereix "
        f"registre/login, o està geo-restringit."
    )
    return None


def rtve_avaluar_candidat(
    item: dict, terme: str, imdb_id: str | None, llindar: float = 0.6,
) -> tuple[bool, bool]:
    """
    Decideix si un programa/item de RTVE és un candidat vàlid.
    Retorna (accepta, confirmat_per_imdb).

    RTVE sovint inclou l'IMDb ID directament a les metadades ('idImdb').
    Quan hi és, és AUTORITATIU:
      - Si coincideix amb el nostre imdb_id → acceptem sempre (encara que
        el títol no coincideixi textualment).
      - Si NO coincideix → rebutgem SEMPRE, encara que el títol coincideixi
        per fuzzy match — és la prova que són programes diferents (ex. hi
        ha diversos "El Ángel" a RTVE: la pel·lícula de 2018 i un
        documental de flamenc sense relació amb el mateix nom).
    Si l'item no porta idImdb, caiem al fuzzy match de sempre.
    """
    idimdb_item = (item.get("idImdb") or "").strip().lower()
    if idimdb_item:
        if imdb_id and idimdb_item == imdb_id.strip().lower():
            return True, True
        return False, False

    titol = item.get("title", "") or ""
    return textos_coincideixen(terme, titol, llindar=llindar), False


async def cercar_rtve_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None, nom_bonic: str,
    ep_absolut: int | None = None, imdb_id: str | None = None,
) -> dict | None:
    if tipus == "series":
        logger.info(
            f"[RTVE] Iniciant cerca: tipus=series, noms={noms}, "
            f"S{temporada}E{capitol}, ep_absolut={ep_absolut}, imdb_id={imdb_id}"
        )
    else:
        logger.info(f"[RTVE] Iniciant cerca: tipus={tipus}, noms={noms}, imdb_id={imdb_id}")

    if tipus == "series":
        programes_provats: set[str] = set()
        cap_programa_trobat = False
        for nom in noms:
            for terme in generar_termes_cerca(nom):
                programes = await rtve_cercar_programes_fiable(terme, imdb_id)
                if not programes:
                    logger.info(f"[RTVE] Terme '{terme}': cap programa trobat")
                    continue

                for prog in programes:
                    program_id = str(prog.get("id") or "")
                    titol_prog = prog.get("title", "") or ""
                    if not program_id:
                        logger.info(f"[RTVE] Programa '{titol_prog}' sense id, ignorat")
                        continue
                    if program_id in programes_provats:
                        logger.info(
                            f"[RTVE] Programa '{titol_prog}' (id={program_id}) "
                            f"ja provat abans amb un altre terme, salt"
                        )
                        continue

                    accepta, confirmat_imdb = rtve_avaluar_candidat(prog, terme, imdb_id)
                    if not accepta:
                        logger.info(
                            f"[RTVE] Programa '{titol_prog}' (id={program_id}, "
                            f"idImdb={prog.get('idImdb')!r}) NO coincideix amb el "
                            f"terme '{terme}' ni amb l'IMDb {imdb_id} "
                            f"(descartat{' — idImdb apunta a un altre títol' if prog.get('idImdb') else ''})"
                        )
                        continue
                    programes_provats.add(program_id)
                    cap_programa_trobat = True
                    if confirmat_imdb:
                        logger.info(
                            f"[RTVE] Programa '{titol_prog}' (id={program_id}) "
                            f"CONFIRMAT per IMDb ID exacte ({imdb_id})"
                        )

                    logger.info(
                        f"[RTVE] Programa vàlid '{titol_prog}' (id={program_id}), "
                        f"cercant T{temporada}E{capitol}..."
                    )
                    item_triat = await rtve_obtenir_episodis_programa(
                        program_id, temporada, capitol, ep_absolut
                    )
                    if not item_triat:
                        logger.info(
                            f"[RTVE] Programa '{titol_prog}' (id={program_id}): "
                            f"T{temporada}E{capitol} no trobat, provant altres programes/termes..."
                        )
                        continue

                    video_id = item_triat.get("id")
                    if not video_id:
                        logger.warning(
                            f"[RTVE] Episodi trobat però sense camp 'id': {item_triat.get('longTitle')!r}"
                        )
                        continue

                    video_url = await rtve_obtenir_url_video(str(video_id))
                    if not video_url:
                        logger.warning(
                            f"[RTVE] Episodi trobat (id={video_id}, "
                            f"{item_triat.get('longTitle')!r}) però NO s'ha pogut "
                            f"resoldre cap URL de vídeo reproduïble"
                        )
                        continue

                    logger.info(f"[RTVE] TROBAT I RESOLT: '{item_triat.get('longTitle') or item_triat.get('title')}'")
                    return {
                        "url": video_url,
                        "name": "🎬 En castellà · 📡 RTVE",
                        "description": (
                            f"{nom_bonic}\nT{temporada} · Episodi {capitol}\n📡 RTVE"
                        ),
                        "behaviorHints": {"notWebReady": False},
                    }

        if not cap_programa_trobat:
            logger.warning(
                f"[RTVE] Cap programa a RTVE ha coincidit amb noms={noms} — "
                f"RTVE probablement no té aquesta sèrie al catàleg"
            )
        else:
            logger.warning(
                f"[RTVE] S'han trobat programes coincidents amb noms={noms} però "
                f"cap tenia l'episodi S{temporada}E{capitol} disponible/resoluble"
            )
        return None

    # ─── Pel·lícules: primer com a "programa" dedicat (ex. "Somos Cine") ───
    # Moltes pel·lícules a RTVE viuen com un "programa" d'un sol ítem, on
    # l'id del programa coincideix amb l'id del propi vídeo. La cerca
    # genèrica de contingut sovint no les troba (queden ofegades per altres
    # resultats més populars), així que ho provem primer.
    programes_pelicula_provats: set[str] = set()
    for nom in noms:
        for terme in generar_termes_cerca(nom):
            programes = await rtve_cercar_programes_fiable(terme, imdb_id)
            if not programes:
                logger.info(f"[RTVE] Cerca de programa per pel·lícula '{terme}': cap resultat")
                continue

            for prog in programes:
                program_id = str(prog.get("id") or "")
                titol_prog = prog.get("title", "") or ""
                if not program_id or program_id in programes_pelicula_provats:
                    continue
                accepta, confirmat_imdb = rtve_avaluar_candidat(prog, terme, imdb_id)
                if not accepta:
                    logger.info(
                        f"[RTVE] Programa '{titol_prog}' (id={program_id}, "
                        f"idImdb={prog.get('idImdb')!r}) NO coincideix amb '{terme}' "
                        f"ni amb l'IMDb {imdb_id} "
                        f"(descartat{' — idImdb apunta a un altre títol' if prog.get('idImdb') else ''})"
                    )
                    continue
                programes_pelicula_provats.add(program_id)
                if confirmat_imdb:
                    logger.info(
                        f"[RTVE] Programa '{titol_prog}' (id={program_id}) "
                        f"CONFIRMAT per IMDb ID exacte ({imdb_id})"
                    )

                logger.info(
                    f"[RTVE] Provant programa '{titol_prog}' (id={program_id}) "
                    f"com a pel·lícula d'un sol ítem..."
                )
                video_url = await rtve_obtenir_url_video(program_id)
                if not video_url:
                    logger.info(
                        f"[RTVE] Programa '{titol_prog}' (id={program_id}) no és "
                        f"un ítem de vídeo directe, descartat com a pel·lícula"
                    )
                    continue

                logger.info(f"[RTVE] TROBAT I RESOLT (com a programa): '{titol_prog}'")
                return {
                    "url": video_url,
                    "name": "🎬 En castellà · 📡 RTVE",
                    "description": f"{nom_bonic}\n📡 RTVE",
                    "behaviorHints": {"notWebReady": False},
                }

    # ─── Pel·lícules: cerca genèrica de contingut (fallback) ───────────────
    cap_terme_amb_items = False
    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await rtve_cercar(terme)
            if not items:
                logger.info(f"[RTVE] Terme '{terme}': 0 items retornats per la cerca")
                continue
            cap_terme_amb_items = True

            candidats = []
            candidats_confirmats_imdb = []
            rebutjats_titol = 0
            rebutjats_durada = 0
            for item in items:
                accepta, confirmat_imdb = rtve_avaluar_candidat(item, terme, imdb_id, llindar=0.5)
                if not accepta:
                    # Fallback addicional: si idImdb no hi és, encara provem
                    # el nom del programa pare (alguns items només el porten allà).
                    titol_prog = ((item.get("programInfo") or {}).get("title", "")) or ""
                    if not (item.get("idImdb")) and bool(titol_prog) and textos_coincideixen(terme, titol_prog, llindar=0.6):
                        accepta = True
                    else:
                        rebutjats_titol += 1
                        continue
                if rtve_durada_segons(item) < DURADA_MIN_PELICULA:
                    rebutjats_durada += 1
                    continue
                candidats.append(item)
                if confirmat_imdb:
                    candidats_confirmats_imdb.append(item)

            logger.info(
                f"[RTVE] Terme '{terme}': {len(candidats)}/{len(items)} candidats vàlids "
                f"({len(candidats_confirmats_imdb)} confirmats per IMDb) "
                f"(rebutjats: {rebutjats_titol} per no coincidir el títol, "
                f"{rebutjats_durada} per durada < {DURADA_MIN_PELICULA}s)"
            )

            if not candidats:
                continue

            # Prioritzem sempre un candidat confirmat per IMDb ID si n'hi ha.
            item_triat = candidats_confirmats_imdb[0] if candidats_confirmats_imdb else candidats[0]

            video_id = item_triat.get("id")
            if not video_id:
                logger.warning(f"[RTVE] Candidat triat sense camp 'id': {item_triat.get('title')!r}")
                continue

            video_url = await rtve_obtenir_url_video(str(video_id))
            if not video_url:
                logger.warning(
                    f"[RTVE] Candidat triat (id={video_id}, {item_triat.get('title')!r}) "
                    f"però NO s'ha pogut resoldre cap URL de vídeo reproduïble"
                )
                continue

            logger.info(f"[RTVE] TROBAT I RESOLT: '{item_triat.get('title')}'")
            return {
                "url": video_url,
                "name": "🎬 En castellà · 📡 RTVE",
                "description": f"{nom_bonic}\n📡 RTVE",
                "behaviorHints": {"notWebReady": False},
            }

    if not cap_terme_amb_items:
        logger.warning(
            f"[RTVE] Cap terme de cerca ha retornat resultats per noms={noms} "
            f"— RTVE probablement no té aquesta pel·lícula al catàleg"
        )
    else:
        logger.warning(
            f"[RTVE] S'han trobat items però cap ha superat els filtres de "
            f"coincidència/durada, o cap URL de vídeo s'ha pogut resoldre, "
            f"per noms={noms}"
        )
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  PLUTO TV — CATÀLEG AVOD GRATUÏT (PEL·LÍCULES I SÈRIES)
# ═══════════════════════════════════════════════════════════════════════════════

PLUTO_BOOT_API = "https://boot.pluto.tv/v4/start"
PLUTO_HUBS_GRAPHQL = "https://pluto.tv/api/tn/hubs/graphql/"
PLUTO_VIDEO_GRAPHQL = "https://pluto.tv/api/tn/video/graphql/"
PLUTO_SEARCH_HASH = "253b3511aee80a466c36eaf000eec46e846c841a1db216b9189a2c416e477303"
PLUTO_STREAMINGURL_HASH = "d2210e84a51ed4a4382c15720a7366ee955d6354ee76039f58a34ecf274134b7"
PLUTO_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)
DURADA_MIN_PLUTO_MOVIE = 40 * 60  # pel·lícules gaire curtes no compten

_pluto_boot_cache: dict | None = None
_pluto_boot_lock = asyncio.Lock()
_pluto_boot_cache_timestamp: float = 0.0
PLUTO_BOOT_TTL_SEGONS = 20 * 60


async def pluto_obtenir_boot() -> dict | None:
    """
    Sessió anònima de Pluto TV (sense compte necessari). Retorna
    {'sessionToken', 'stitcherParams', 'stitcherBase'}, cachejat uns minuts.
    """
    global _pluto_boot_cache, _pluto_boot_cache_timestamp
    ara = time.time()
    if _pluto_boot_cache and (ara - _pluto_boot_cache_timestamp) < PLUTO_BOOT_TTL_SEGONS:
        return _pluto_boot_cache

    async with _pluto_boot_lock:
        if _pluto_boot_cache and (time.time() - _pluto_boot_cache_timestamp) < PLUTO_BOOT_TTL_SEGONS:
            return _pluto_boot_cache

        device_id = str(uuid.uuid4())
        params = {
            "appName": "web",
            "appVersion": "8.0.0",
            "deviceVersion": "122.0.0",
            "deviceModel": "web",
            "deviceMake": "chrome",
            "deviceType": "web",
            "clientID": device_id,
            "clientModelNumber": "1.0.0",
        }
        try:
            async with httpx.AsyncClient(
                timeout=15.0, headers={"User-Agent": PLUTO_USER_AGENT}
            ) as client:
                r = await client.get(PLUTO_BOOT_API, params=params)
                r.raise_for_status()
                data = r.json()

            if "sessionToken" not in data:
                logger.error(f"[PLUTOTV] Boot sense sessionToken: {str(data)[:200]}")
                return None

            resultat = {
                "sessionToken": data["sessionToken"],
                "stitcherParams": data.get("stitcherParams", ""),
                "stitcherBase": data.get("servers", {}).get(
                    "stitcher", "https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv"
                ),
            }
            _pluto_boot_cache = resultat
            _pluto_boot_cache_timestamp = time.time()
            logger.info(
                f"[PLUTOTV] Sessió boot obtinguda (deviceId={device_id[:8]}...), "
                f"cachejada {PLUTO_BOOT_TTL_SEGONS}s"
            )
            return resultat
        except Exception as e:
            logger.error(f"[PLUTOTV] Error obtenint sessió boot: {type(e).__name__}: {e}")
            return None


async def pluto_cercar(terme: str) -> list[dict]:
    extensions = json.dumps({"tnPersistedDocumentHash": PLUTO_SEARCH_HASH})
    variables = json.dumps({"query": terme})
    params = {"extensions": extensions, "variables": variables, "operationName": "GetVODContent"}
    headers = {
        "User-Agent": PLUTO_USER_AGENT,
        "Referer": "https://pluto.tv/es/search/",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.get(PLUTO_HUBS_GRAPHQL, params=params)
            r.raise_for_status()
            items = r.json().get("data", {}).get("searchVODContent", {}).get("data", []) or []
    except Exception as e:
        logger.error(f"[PLUTOTV] Error cercant '{terme}': {type(e).__name__}: {e}")
        return []

    if items:
        logger.info(f"[PLUTOTV] Cerca '{terme}' -> {len(items)} items")
    else:
        logger.info(f"[PLUTOTV] Cerca '{terme}' -> 0 items")
    return items


async def pluto_obtenir_url_video(content_id: str) -> str | None:
    """
    Resol la URL HLS reproduïble d'un contingut (pel·lícula o episodi) pel
    seu contentId. Cal una sessió amb cookies (per passar la comprovació
    CSRF/regió del GraphQL) + la sessió boot (per construir la URL final
    de l'stitcher amb el JWT).
    """
    boot = await pluto_obtenir_boot()
    if not boot:
        logger.warning(f"[PLUTOTV] Sense sessió boot, no es pot resoldre {content_id}")
        return None

    extensions = json.dumps({"tnPersistedDocumentHash": PLUTO_STREAMINGURL_HASH})
    variables = json.dumps({
        "params": {
            "appVersion": "10.7.18",
            "contentId": content_id,
            "contentType": "vod",
            "drm": "",
            "drmLevel": "",
            "streamType": "stitcher",
        }
    })
    params = {"extensions": extensions, "variables": variables, "operationName": "StreamingUrl"}

    try:
        async with httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": PLUTO_USER_AGENT}, follow_redirects=True,
        ) as client:
            # Cal cookies de sessió (ptv_device_id, ptv_session_id...), que
            # s'obtenen simplement visitant qualsevol pàgina de pluto.tv.
            await client.get("https://pluto.tv/")
            r = await client.get(
                PLUTO_VIDEO_GRAPHQL, params=params,
                headers={"Referer": "https://pluto.tv/", "Content-Type": "application/json"},
            )
            r.raise_for_status()
            data = r.json().get("data", {}).get("streamingUrl", {})
    except Exception as e:
        logger.error(f"[PLUTOTV] Error resolent contentId={content_id}: {type(e).__name__}: {e}")
        return None

    if not data.get("success"):
        logger.warning(
            f"[PLUTOTV] StreamingUrl sense èxit per contentId={content_id}: {str(data)[:200]}"
        )
        return None

    stitcher_paths = data.get("stitcherPaths") or []
    hls_path = next((p["path"] for p in stitcher_paths if p.get("type") == "hls"), None)
    if not hls_path:
        logger.warning(f"[PLUTOTV] Sense path HLS per contentId={content_id}: {stitcher_paths}")
        return None

    url = (
        f"{boot['stitcherBase']}{hls_path}?{boot['stitcherParams']}"
        f"&jwt={boot['sessionToken']}&masterJWTPassthrough=true&includeExtendedEvents=true"
    )
    logger.info(f"[PLUTOTV] URL resolta per contentId={content_id}")
    return url


async def pluto_obtenir_episodis_temporada(slug: str, temporada: int) -> list[dict] | None:
    url = f"https://pluto.tv/es/shows/{slug}/season/{temporada}/"
    try:
        async with httpx.AsyncClient(
            timeout=15.0, headers={"User-Agent": PLUTO_USER_AGENT}, follow_redirects=True,
        ) as client:
            r = await client.get(url)
            r.raise_for_status()
            text = r.text
    except Exception as e:
        logger.error(f"[PLUTOTV] Error carregant temporada {temporada} de '{slug}': {type(e).__name__}: {e}")
        return None

    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', text, re.DOTALL)
    if not m:
        logger.warning(f"[PLUTOTV] No s'ha trobat __NEXT_DATA__ per '{slug}' T{temporada}")
        return None

    try:
        data = json.loads(m.group(1))
        episodis = data["props"]["pageProps"].get("initialEpisodes") or []
    except Exception as e:
        logger.error(f"[PLUTOTV] Error parsejant __NEXT_DATA__ de '{slug}' T{temporada}: {e}")
        return None

    logger.info(f"[PLUTOTV] '{slug}' T{temporada}: {len(episodis)} episodis trobats")
    return episodis


async def cercar_pluto_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None, nom_bonic: str,
    durada_min: int | None = None,
) -> dict | None:
    logger.info(f"[PLUTOTV] Iniciant cerca: tipus={tipus}, noms={noms}, S{temporada}E{capitol}, durada≈{durada_min}min")

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await pluto_cercar(terme)
            if not items:
                continue

            tipus_pluto_desitjat = "movie" if tipus == "movie" else "show"
            candidats = [
                it for it in items
                if it.get("contentType") == tipus_pluto_desitjat
                and textos_coincideixen(terme, it.get("title", "") or "", llindar=0.7)
            ]
            logger.info(
                f"[PLUTOTV] Terme '{terme}': {len(candidats)}/{len(items)} candidats "
                f"vàlids (tipus={tipus_pluto_desitjat})"
            )
            if not candidats:
                continue

            if tipus == "movie":
                for cand in candidats:
                    # Pluto TV no exposa cap camp d'any de producció als
                    # resultats de cerca, així que fem servir la durada real
                    # (TMDB/Cinemeta) contra "movieDuration" com a substitut
                    # per no confondre pel·lícules diferents amb el mateix
                    # títol (ex. "Obsession" té diverses pel·lícules amb
                    # aquest nom exacte al catàleg de Pluto).
                    durada_cand_seg = cand.get("movieDuration")
                    if isinstance(durada_cand_seg, (int, float)) and not durada_coincideix(
                        durada_min, durada_cand_seg / 60,
                        tolerancia_alta=DURADA_TOLERANCIA_ALTA_PLUTO_MIN,
                    ):
                        logger.info(
                            f"[PLUTOTV] '{cand.get('title')}' descartada: durada "
                            f"{durada_cand_seg/60:.0f}min no coincideix amb la "
                            f"durada real esperada ({durada_min}min)"
                        )
                        continue
                    content_id = cand.get("id")
                    if not content_id:
                        continue
                    video_url = await pluto_obtenir_url_video(content_id)
                    if not video_url:
                        continue
                    logger.info(f"[PLUTOTV] TROBAT I RESOLT (pel·lícula): '{cand.get('title')}'")
                    return {
                        "url": video_url,
                        "name": "🎬 En castellà · 📡 Pluto TV",
                        "description": f"{nom_bonic}\n📡 Pluto TV",
                        "behaviorHints": {"notWebReady": False},
                    }
                continue

            # ─── Sèries ─────────────────────────────────────────────────
            for cand in candidats:
                href = cand.get("href", "") or ""
                slug = href.strip("/").split("/")[-1] if href else ""
                num_seasons = cand.get("numSeasons") or 0
                if not slug:
                    continue
                if num_seasons and temporada and temporada > num_seasons:
                    logger.info(
                        f"[PLUTOTV] '{cand.get('title')}' només té {num_seasons} "
                        f"temporades, es demana T{temporada} — descartat"
                    )
                    continue

                episodis = await pluto_obtenir_episodis_temporada(slug, temporada or 1)
                if not episodis:
                    continue

                idx = (capitol or 1) - 1
                if not (0 <= idx < len(episodis)):
                    logger.info(
                        f"[PLUTOTV] '{cand.get('title')}' T{temporada}: "
                        f"posició {capitol} fora de rang ({len(episodis)} episodis)"
                    )
                    continue

                episodi = episodis[idx]
                content_id = episodi.get("contentId")
                if not content_id:
                    continue

                video_url = await pluto_obtenir_url_video(content_id)
                if not video_url:
                    continue

                logger.info(
                    f"[PLUTOTV] TROBAT I RESOLT (sèrie): '{cand.get('title')}' "
                    f"T{temporada}E{capitol} -> '{episodi.get('title')}'"
                )
                return {
                    "url": video_url,
                    "name": "🎬 En castellà · 📡 Pluto TV",
                    "description": (
                        f"{nom_bonic}\nT{temporada} · Episodi {capitol}: "
                        f"{episodi.get('title', '')}\n📡 Pluto TV"
                    ),
                    "behaviorHints": {"notWebReady": False},
                }

    logger.warning(f"[PLUTOTV] Cap resultat per noms={noms} S{temporada}E{capitol}")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  PLEX — CATÀLEG "WATCH FREE" (PEL·LÍCULES I SÈRIES, NOMÉS TÍTOLS SENSE DRM)
# ═══════════════════════════════════════════════════════════════════════════════
# El catàleg gratuït de Plex barreja títols de grans estudis (protegits amb DRM
# FairPlay/Widevine, que no podem reproduir) amb títols de distribuïdores més
# petites que serveixen HLS/DASH en clar. Resolem sempre i descartem els que
# venen marcats com a drm=true o que la pròpia API rebutja (playQueue buit =
# títol no disponible gratuïtament per aquest compte anònim).

PLEX_ANON_AUTH_API = "https://plex.tv/api/v2/users/anonymous"
PLEX_DISCOVER_API = "https://discover.provider.plex.tv"
PLEX_VOD_API = "https://vod.provider.plex.tv"
PLEX_CLIENT_HEADERS_BASE = {
    "Accept": "application/json",
    "X-Plex-Product": "Plex Web",
    "X-Plex-Version": "4.145.1",
    "X-Plex-Platform": "Chrome",
    "X-Plex-Device": "Windows",
    "X-Plex-Language": "es",
}

_plex_token_cache: str | None = None
_plex_token_lock = asyncio.Lock()
_plex_token_cache_timestamp: float = 0.0
PLEX_TOKEN_TTL_SEGONS = 60 * 60

async def plex_obtenir_token() -> str | None:
    """Compte anònim de Plex (sense necessitat de login). Cal un token nou per
    dispositiu (X-Plex-Client-Identifier), cachejat per no crear-ne un a cada
    petició."""
    global _plex_token_cache, _plex_token_cache_timestamp
    ara = time.time()
    if _plex_token_cache and (ara - _plex_token_cache_timestamp) < PLEX_TOKEN_TTL_SEGONS:
        return _plex_token_cache

    async with _plex_token_lock:
        if _plex_token_cache and (time.time() - _plex_token_cache_timestamp) < PLEX_TOKEN_TTL_SEGONS:
            return _plex_token_cache

        client_id = str(uuid.uuid4())
        headers = {**PLEX_CLIENT_HEADERS_BASE, "X-Plex-Client-Identifier": client_id}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(PLEX_ANON_AUTH_API, headers=headers)
                r.raise_for_status()
                data = r.json()
            token = data.get("authToken")
            if not token:
                logger.error(f"[PLEX] Compte anònim sense authToken: {str(data)[:200]}")
                return None
            _plex_token_cache = token
            _plex_token_cache_timestamp = time.time()
            logger.info(f"[PLEX] Token anònim obtingut (clientId={client_id[:8]}...), cachejat {PLEX_TOKEN_TTL_SEGONS}s")
            return token
        except Exception as e:
            logger.error(f"[PLEX] Error creant compte anònim: {type(e).__name__}: {e}")
            return None

async def plex_cercar(terme: str) -> list[dict]:
    token = await plex_obtenir_token()
    if not token:
        return []
    params = {
        "query": terme,
        "searchTypes": "movies,tv",
        "searchProviders": "discover",
        "includeMetadata": "1",
        "limit": "15",
    }
    headers = {**PLEX_CLIENT_HEADERS_BASE, "X-Plex-Token": token}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.get(f"{PLEX_DISCOVER_API}/library/search", params=params)
            r.raise_for_status()
            grups = r.json().get("MediaContainer", {}).get("SearchResults", []) or []
    except Exception as e:
        logger.error(f"[PLEX] Error cercant '{terme}': {type(e).__name__}: {e}")
        return []

    resultats = []
    for grup in grups:
        for item in grup.get("SearchResult", []) or []:
            meta = item.get("Metadata")
            if meta:
                resultats.append(meta)

    if resultats:
        logger.info(f"[PLEX] Cerca '{terme}' -> {len(resultats)} items")
    else:
        logger.info(f"[PLEX] Cerca '{terme}' -> 0 items")
    return resultats

async def plex_obtenir_fills(rating_key: str) -> list[dict] | None:
    """Retorna les temporades d'una sèrie, o els episodis d'una temporada
    (mateix endpoint, segons de quin ratingKey pengem)."""
    token = await plex_obtenir_token()
    if not token:
        return None
    headers = {**PLEX_CLIENT_HEADERS_BASE, "X-Plex-Token": token}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.get(f"{PLEX_DISCOVER_API}/library/metadata/{rating_key}/children")
            r.raise_for_status()
            return r.json().get("MediaContainer", {}).get("Metadata", []) or []
    except Exception as e:
        logger.error(f"[PLEX] Error obtenint fills de {rating_key}: {type(e).__name__}: {e}")
        return None

async def plex_resoldre_stream(rating_key: str) -> dict | None:
    """Crea un playQueue per aquest ratingKey i retorna la URL HLS si existeix
    una variant sense DRM. Un playQueue buit (400) vol dir que el títol no
    forma part del catàleg gratuït per a comptes anònims."""
    token = await plex_obtenir_token()
    if not token:
        return None
    headers = {**PLEX_CLIENT_HEADERS_BASE, "X-Plex-Token": token}
    uri = f"provider://tv.plex.provider.vod/library/metadata/{rating_key}"
    params = {"uri": uri, "type": "video", "continuous": "1"}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            r = await client.post(f"{PLEX_VOD_API}/playQueues", params=params)
            if r.status_code != 200:
                logger.info(f"[PLEX] {rating_key}: no disponible gratis per compte anònim (HTTP {r.status_code})")
                return None
            meta_list = r.json().get("MediaContainer", {}).get("Metadata", []) or []
    except Exception as e:
        logger.error(f"[PLEX] Error creant playQueue per {rating_key}: {type(e).__name__}: {e}")
        return None

    if not meta_list:
        return None
    media_list = meta_list[0].get("Media", []) or []
    hls = next((m for m in media_list if m.get("protocol") == "hls" and not m.get("drm")), None)
    if not hls:
        if any(m.get("drm") for m in media_list):
            logger.info(f"[PLEX] {rating_key}: només disponible amb DRM (FairPlay/Widevine) — descartat")
        return None

    part = (hls.get("Part") or [{}])[0]
    key = part.get("key")
    if not key:
        return None

    idioma = "castellà"
    audio = next((s for s in part.get("Stream", []) if s.get("streamType") == 2), None)
    if audio and audio.get("languageCode") not in ("es", "spa"):
        idioma = audio.get("language", "V.O.")

    url = f"{PLEX_VOD_API}{key}?X-Plex-Token={token}"
    return {"url": url, "idioma": idioma}

async def cercar_plex_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None,
    nom_bonic: str, any_estrena: int | None,
) -> dict | None:
    logger.info(f"[PLEX] Iniciant cerca: tipus={tipus}, noms={noms}, S{temporada}E{capitol}")
    tipus_plex_desitjat = "movie" if tipus == "movie" else "show"

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await plex_cercar(terme)
            if not items:
                continue
            candidats = [
                it for it in items
                if it.get("type") == tipus_plex_desitjat
                and textos_coincideixen(terme, it.get("title", "") or "", llindar=0.7)
            ]
            # Descartem sempre (no només quan hi ha >1 candidat) qualsevol
            # item l'any del qual no coincideixi amb l'esperat — un sol
            # candidat amb títol igual però any diferent és exactament el
            # cas que produeix falsos positius (ex. "Obsession").
            if any_estrena:
                candidats_filtrats = []
                for c in candidats:
                    try:
                        any_item = int(c.get("year"))
                    except (TypeError, ValueError):
                        any_item = None
                    if any_coincideix(any_estrena, any_item):
                        candidats_filtrats.append(c)
                candidats = candidats_filtrats
            logger.info(f"[PLEX] Terme '{terme}': {len(candidats)}/{len(items)} candidats vàlids (tipus={tipus_plex_desitjat})")
            if not candidats:
                continue

            if tipus == "movie":
                for cand in candidats:
                    rating_key = cand.get("ratingKey")
                    if not rating_key:
                        continue
                    resolt = await plex_resoldre_stream(rating_key)
                    if not resolt:
                        continue
                    logger.info(f"[PLEX] TROBAT I RESOLT (pel·lícula): '{cand.get('title')}' ({cand.get('year')})")
                    return {
                        "url": resolt["url"],
                        "name": f"🎬 En {resolt['idioma']} · 📡 Plex",
                        "description": f"{nom_bonic}\n📡 Plex",
                        "behaviorHints": {"notWebReady": False},
                    }
                continue

            # ─── Sèries ─────────────────────────────────────────────────
            for cand in candidats:
                show_rk = cand.get("ratingKey")
                if not show_rk:
                    continue
                temporades = await plex_obtenir_fills(show_rk)
                if not temporades:
                    continue
                temp = next((t for t in temporades if t.get("index") == (temporada or 1)), None)
                if not temp:
                    logger.info(f"[PLEX] '{cand.get('title')}' no té temporada {temporada}")
                    continue
                season_rk = temp.get("ratingKey")
                episodis = await plex_obtenir_fills(season_rk)
                if not episodis:
                    continue
                ep = next((e for e in episodis if e.get("index") == (capitol or 1)), None)
                if not ep:
                    logger.info(f"[PLEX] '{cand.get('title')}' T{temporada}: no té episodi {capitol}")
                    continue
                ep_rk = ep.get("ratingKey")
                if not ep_rk:
                    continue
                resolt = await plex_resoldre_stream(ep_rk)
                if not resolt:
                    continue
                logger.info(
                    f"[PLEX] TROBAT I RESOLT (sèrie): '{cand.get('title')}' "
                    f"T{temporada}E{capitol} -> '{ep.get('title')}'"
                )
                return {
                    "url": resolt["url"],
                    "name": f"🎬 En {resolt['idioma']} · 📡 Plex",
                    "description": (
                        f"{nom_bonic}\nT{temporada} · Episodi {capitol}: "
                        f"{ep.get('title', '')}\n📡 Plex"
                    ),
                    "behaviorHints": {"notWebReady": False},
                }

    logger.warning(f"[PLEX] Cap resultat per noms={noms} S{temporada}E{capitol}")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  RUNTIME — CATÀLEG AVOD GRATUÏT (PEL·LÍCULES I SÈRIES)
# ═══════════════════════════════════════════════════════════════════════════════
# API interna "OTTera" (api-ott.runtime.tv), protegida per una capçalera
# ottera-cs-auth amb un secret estàtic per lloc (embegut en clar al
# drupalSettings de runtime.tv, igual que el hash de consulta persistent de
# Pluto TV). El catàleg exposa directament un camp `drm` per objecte: si és
# "true" descartem sense ni intentar resoldre l'stream.

RUNTIME_API = "https://api-ott.runtime.tv"
RUNTIME_CS_AUTH = os.environ.get("RUNTIME_CS_AUTH", "")
RUNTIME_HEADERS = {
    "ottera-referrer": "runtime.tv",
    "ottera-cs-auth": RUNTIME_CS_AUTH,
}
RUNTIME_PARAMS_BASE = {
    "version": "13",
    "device_type": "desktop",
    "platform": "web",
    "partner": "internal",
    "language": "en",
    "connection": "wifi",
    "timezone": "0200",
}
_RUNTIME_HLS_REGEX = re.compile(r"playerSources\.hls\s*=\s*\[\{\s*url:\s*'([^']+)'")

async def runtime_cercar(terme: str, tipus_ott: str) -> list[dict]:
    """tipus_ott: 'video' (pel·lícules) o 'show' (sèries)."""
    body = {
        **RUNTIME_PARAMS_BASE,
        "image_width": "366",
        "image_format": "widescreen",
        "object_type": tipus_ott,
        "key": terme,
        "timestamp": str(int(time.time())),
    }
    if tipus_ott == "video":
        body["video_type"] = "non_episode"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=RUNTIME_HEADERS) as client:
            r = await client.post(f"{RUNTIME_API}/search", data=body)
            r.raise_for_status()
            items = r.json().get("objects", []) or []
    except Exception as e:
        logger.error(f"[RUNTIME] Error cercant '{terme}' ({tipus_ott}): {type(e).__name__}: {e}")
        return []

    if items:
        logger.info(f"[RUNTIME] Cerca '{terme}' ({tipus_ott}) -> {len(items)} items")
    else:
        logger.info(f"[RUNTIME] Cerca '{terme}' ({tipus_ott}) -> 0 items")
    return items

async def runtime_obtenir_episodis(show_id: str) -> list[dict]:
    params = {
        **RUNTIME_PARAMS_BASE,
        "image_width": "366",
        "image_format": "widescreen",
        "object_type": "video",
        "video_type": "episode",
        "parent_id": show_id,
        "parent_type": "show",
        "max": "200",
        "timestamp": str(int(time.time())),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=RUNTIME_HEADERS) as client:
            r = await client.get(f"{RUNTIME_API}/getreferencedobjects", params=params)
            r.raise_for_status()
            return r.json().get("objects", []) or []
    except Exception as e:
        logger.error(f"[RUNTIME] Error obtenint episodis de show={show_id}: {type(e).__name__}: {e}")
        return []

async def runtime_resoldre_stream(video_id: str) -> str | None:
    params = {
        **RUNTIME_PARAMS_BASE,
        "div_id": "video_player",
        "content_page_url": "https://www.runtime.tv/",
        "image_width": "1280",
        "max_bitrate": "10000",
        "h265": "1",
        "max_res": "2160",
        "id": video_id,
        "timestamp": str(int(time.time())),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=RUNTIME_HEADERS) as client:
            r = await client.get(f"{RUNTIME_API}/embeddedVideoPlayer", params=params)
            r.raise_for_status()
            text = r.text
    except Exception as e:
        logger.error(f"[RUNTIME] Error resolent id={video_id}: {type(e).__name__}: {e}")
        return None

    m = _RUNTIME_HLS_REGEX.search(text)
    if not m:
        logger.warning(f"[RUNTIME] Cap URL HLS trobada per id={video_id} (contingut no disponible o format inesperat)")
        return None
    url = m.group(1).encode().decode("unicode_escape")
    return url

async def cercar_runtime_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None,
    nom_bonic: str, any_estrena: int | None,
) -> dict | None:
    logger.info(f"[RUNTIME] Iniciant cerca: tipus={tipus}, noms={noms}, S{temporada}E{capitol}")
    tipus_ott = "video" if tipus == "movie" else "show"

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await runtime_cercar(terme, tipus_ott)
            if not items:
                continue
            candidats = [
                it for it in items
                if textos_coincideixen(terme, it.get("name", "") or "", llindar=0.7)
                or textos_coincideixen(terme, it.get("admin_name", "") or "", llindar=0.7)
            ]
            # Descartem sempre (no només quan hi ha >1 candidat) qualsevol
            # item l'any del qual no coincideixi amb l'esperat — un sol
            # candidat amb títol igual però any diferent és exactament el
            # cas que produeix falsos positius (ex. "Obsession").
            if any_estrena:
                candidats_filtrats = []
                for c in candidats:
                    try:
                        any_item = int(c.get("year"))
                    except (TypeError, ValueError):
                        any_item = None
                    if any_coincideix(any_estrena, any_item):
                        candidats_filtrats.append(c)
                candidats = candidats_filtrats
            candidats = [c for c in candidats if str(c.get("drm")).lower() != "true"]
            logger.info(f"[RUNTIME] Terme '{terme}': {len(candidats)}/{len(items)} candidats vàlids (sense DRM)")
            if not candidats:
                continue

            if tipus == "movie":
                for cand in candidats:
                    video_id = cand.get("id")
                    if not video_id:
                        continue
                    url = await runtime_resoldre_stream(video_id)
                    if not url:
                        continue
                    logger.info(f"[RUNTIME] TROBAT I RESOLT (pel·lícula): '{cand.get('name')}' ({cand.get('year')})")
                    return {
                        "url": url,
                        "name": "🎬 En castellà · 📡 Runtime",
                        "description": f"{nom_bonic}\n📡 Runtime",
                        "behaviorHints": {"notWebReady": False},
                    }
                continue

            # ─── Sèries ─────────────────────────────────────────────────
            for cand in candidats:
                show_id = cand.get("id")
                if not show_id:
                    continue
                episodis = await runtime_obtenir_episodis(show_id)
                if not episodis:
                    continue
                ep = next(
                    (
                        e for e in episodis
                        if str(e.get("show_info", {}).get("season_num")) == str(temporada or 1)
                        and str(e.get("show_info", {}).get("episode_num")) == str(capitol or 1)
                    ),
                    None,
                )
                if not ep:
                    logger.info(f"[RUNTIME] '{cand.get('name')}' no té S{temporada}E{capitol}")
                    continue
                if str(ep.get("drm")).lower() == "true":
                    logger.info(f"[RUNTIME] '{cand.get('name')}' S{temporada}E{capitol}: només amb DRM — descartat")
                    continue
                video_id = ep.get("id")
                if not video_id:
                    continue
                url = await runtime_resoldre_stream(video_id)
                if not url:
                    continue
                logger.info(
                    f"[RUNTIME] TROBAT I RESOLT (sèrie): '{cand.get('name')}' "
                    f"S{temporada}E{capitol} -> '{ep.get('name')}'"
                )
                return {
                    "url": url,
                    "name": "🎬 En castellà · 📡 Runtime",
                    "description": (
                        f"{nom_bonic}\nT{temporada} · Episodi {capitol}: "
                        f"{ep.get('name', '')}\n📡 Runtime"
                    ),
                    "behaviorHints": {"notWebReady": False},
                }

    logger.warning(f"[RUNTIME] Cap resultat per noms={noms} S{temporada}E{capitol}")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  ATRESPLAYER — CATÀLEG "EN ABIERTO" (PEL·LÍCULES I SÈRIES, SENSE DRM)
# ═══════════════════════════════════════════════════════════════════════════════
# API pública d'Atresmedia. El paràmetre NODRM=true a l'endpoint del player
# demana explícitament la variant HLS en clar (sense DRM) — és la mateixa
# petició que fa el seu propi reproductor web quan detecta que el dispositiu
# no suporta EME. El catàleg "en abierto" (format.open=true) és gratuït i
# sense login.

ATRESPLAYER_API = "https://api.atresplayer.com"
_ATRESPLAYER_SEASON_NUM_REGEX = re.compile(r"^T(\d+)\b")
_ATRESPLAYER_EP_NUM_REGEX = re.compile(r"(\d+)\s*$")

async def atresplayer_cercar(terme: str) -> list[dict]:
    params = {"entityType": "ATPFormat", "text": terme, "size": "15", "page": "0"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{ATRESPLAYER_API}/client/v1/row/search", params=params)
            r.raise_for_status()
            items = r.json().get("itemRows", []) or []
    except Exception as e:
        logger.error(f"[ATRESPLAYER] Error cercant '{terme}': {type(e).__name__}: {e}")
        return []

    if items:
        logger.info(f"[ATRESPLAYER] Cerca '{terme}' -> {len(items)} items")
    else:
        logger.info(f"[ATRESPLAYER] Cerca '{terme}' -> 0 items")
    return items

async def atresplayer_obtenir_format(format_id: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{ATRESPLAYER_API}/client/v1/page/format/{format_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"[ATRESPLAYER] Error obtenint format {format_id}: {type(e).__name__}: {e}")
        return None

ATRESPLAYER_MIDA_PAGINA_MAX = 100  # límit dur de l'API — size>100 dona HTTP 400
ATRESPLAYER_MAX_PAGINES_EPISODIS = 20  # fins a 2000 episodis (sèries diàries llargues)

async def atresplayer_obtenir_episodis(format_id: str, season_id: str | None) -> list[dict]:
    episodis: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            for pagina in range(ATRESPLAYER_MAX_PAGINES_EPISODIS):
                params = {
                    "entityType": "ATPEpisode", "formatId": format_id,
                    "size": str(ATRESPLAYER_MIDA_PAGINA_MAX), "page": str(pagina),
                }
                if season_id:
                    params["seasonId"] = season_id
                r = await client.get(f"{ATRESPLAYER_API}/client/v1/row/search", params=params)
                r.raise_for_status()
                data = r.json()
                episodis.extend(data.get("itemRows", []) or [])
                if not data.get("pageInfo", {}).get("hasNext"):
                    break
    except Exception as e:
        logger.error(f"[ATRESPLAYER] Error obtenint episodis de format={format_id} season={season_id}: {type(e).__name__}: {e}")
        return episodis
    return episodis

async def atresplayer_resoldre_stream(content_id: str) -> str | None:
    params = {"device": "desktop", "NODRM": "true"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{ATRESPLAYER_API}/player/v1/episode/{content_id}", params=params)
            if r.status_code != 200:
                logger.info(f"[ATRESPLAYER] {content_id}: no disponible (HTTP {r.status_code})")
                return None
            sources = r.json().get("sources", []) or []
    except Exception as e:
        logger.error(f"[ATRESPLAYER] Error resolent {content_id}: {type(e).__name__}: {e}")
        return None

    hls = next((s for s in sources if s.get("type") == "application/vnd.apple.mpegurl"), None)
    if not hls or not hls.get("src"):
        logger.warning(f"[ATRESPLAYER] Cap font HLS per {content_id}")
        return None
    return hls["src"]

async def cercar_atresplayer_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None,
    nom_bonic: str, any_estrena: int | None,
) -> dict | None:
    logger.info(f"[ATRESPLAYER] Iniciant cerca: tipus={tipus}, noms={noms}, S{temporada}E{capitol}")

    for nom in noms:
        for terme in generar_termes_cerca(nom):
            items = await atresplayer_cercar(terme)
            if not items:
                continue
            candidats = [
                it for it in items
                if it.get("monoChapter") == (tipus == "movie")
                and textos_coincideixen(terme, it.get("title", "") or "", llindar=0.7)
            ]
            logger.info(f"[ATRESPLAYER] Terme '{terme}': {len(candidats)}/{len(items)} candidats vàlids")
            if not candidats:
                continue

            for cand in candidats:
                format_id = cand.get("contentId")
                if not format_id:
                    continue
                format_info = await atresplayer_obtenir_format(format_id)
                if not format_info or not format_info.get("open"):
                    logger.info(f"[ATRESPLAYER] '{cand.get('title')}': no és contingut obert/gratuït — descartat")
                    continue

                # Descartem sempre (no només quan hi ha >1 candidat) qualsevol
                # item l'any del qual no coincideixi amb l'esperat — un sol
                # candidat amb títol igual però any diferent és exactament
                # el cas que produeix falsos positius (ex. "Obsession").
                try:
                    any_item = int(format_info.get("productionYear"))
                except (TypeError, ValueError):
                    any_item = None
                if any_estrena and not any_coincideix(any_estrena, any_item):
                    logger.info(
                        f"[ATRESPLAYER] '{cand.get('title')}' ({format_info.get('productionYear')}) "
                        f"no coincideix amb l'any esperat ({any_estrena}) — descartat"
                    )
                    continue

                if tipus == "movie":
                    episodis = await atresplayer_obtenir_episodis(format_id, None)
                    if not episodis:
                        continue
                    ep = episodis[0]
                    content_id = ep.get("contentId")
                    if not content_id:
                        continue
                    url = await atresplayer_resoldre_stream(content_id)
                    if not url:
                        continue
                    logger.info(f"[ATRESPLAYER] TROBAT I RESOLT (pel·lícula): '{cand.get('title')}' ({format_info.get('productionYear')})")
                    return {
                        "url": url,
                        "name": "🎬 En castellà · 📡 Atresplayer",
                        "description": f"{nom_bonic}\n📡 Atresplayer",
                        "behaviorHints": {"notWebReady": False},
                    }

                # ─── Sèries ─────────────────────────────────────────────────
                seasons = format_info.get("seasons", []) or []
                season_id = None
                for idx, s in enumerate(seasons, start=1):
                    m = _ATRESPLAYER_SEASON_NUM_REGEX.match(s.get("title", "") or "")
                    num = int(m.group(1)) if m else idx
                    if num == (temporada or 1):
                        href = s.get("link", {}).get("href", "") or ""
                        parsed = urllib.parse.urlparse(href)
                        season_id = urllib.parse.parse_qs(parsed.query).get("seasonId", [None])[0]
                        break
                if not season_id:
                    logger.info(f"[ATRESPLAYER] '{cand.get('title')}' no té temporada {temporada}")
                    continue

                episodis = await atresplayer_obtenir_episodis(format_id, season_id)
                if not episodis:
                    continue
                ep = None
                for idx, e in enumerate(episodis, start=1):
                    m = _ATRESPLAYER_EP_NUM_REGEX.search(e.get("title", "") or "")
                    num = int(m.group(1)) if m else idx
                    if num == (capitol or 1):
                        ep = e
                        break
                if not ep:
                    logger.info(f"[ATRESPLAYER] '{cand.get('title')}' T{temporada}: no té episodi {capitol}")
                    continue
                content_id = ep.get("contentId")
                if not content_id:
                    continue
                url = await atresplayer_resoldre_stream(content_id)
                if not url:
                    continue
                logger.info(
                    f"[ATRESPLAYER] TROBAT I RESOLT (sèrie): '{cand.get('title')}' "
                    f"T{temporada}E{capitol} -> '{ep.get('title')}'"
                )
                return {
                    "url": url,
                    "name": "🎬 En castellà · 📡 Atresplayer",
                    "description": (
                        f"{nom_bonic}\nT{temporada} · Episodi {capitol}: "
                        f"{ep.get('title', '')}\n📡 Atresplayer"
                    ),
                    "behaviorHints": {"notWebReady": False},
                }

    logger.warning(f"[ATRESPLAYER] Cap resultat per noms={noms} S{temporada}E{capitol}")
    return None

# ═══════════════════════════════════════════════════════════════════════════════
#  ENDPOINTS STREMIO
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/hls/{video_id}.m3u8")
async def hls_master(video_id: int):
    """Master HLS de 3Cat amb només la millor qualitat disponible (sempre la màxima)."""
    master = await obtenir_master_hls(video_id)
    if not master:
        return Response(status_code=404)
    cos, _ = reescriure_master_hls(master[1], master[0], nomes_millor=True)
    return Response(content=cos, media_type="application/vnd.apple.mpegurl", headers={"Cache-Control": "no-cache"})


@app.get("/manifest.json")
async def manifest():
    return MANIFEST

CACHE_TTL_SEGONS = 5 * 60
_cache_streams: dict[str, tuple[float, list]] = {}

def cache_obtenir(clau: str) -> list | None:
    entrada = _cache_streams.get(clau)
    if not entrada:
        return None
    timestamp, streams = entrada
    if time.time() - timestamp > CACHE_TTL_SEGONS:
        del _cache_streams[clau]
        return None
    return streams

def cache_guardar(clau: str, streams: list) -> None:
    _cache_streams[clau] = (time.time(), streams)


async def cercar_3cat_stream(
    tipus: str, noms: list[str], any_estrena: int | None, durada_min: int | None,
    temporada: int | None, capitol: int | None, nom_bonic: str,
) -> dict | None:
    item_3cat = None
    if tipus == "movie":
        item_3cat = await cercar_pelicula(noms, any_estrena, durada_min)
    elif tipus == "series" and temporada is not None and capitol is not None:
        item_3cat = await cercar_episodi(noms, temporada, capitol, any_estrena)

    if not item_3cat:
        return None

    video_id = item_3cat.get("id")
    master = await obtenir_master_hls(video_id) if PUBLIC_BASE_URL else None
    millor = reescriure_master_hls(master[1], master[0], nomes_millor=True)[1] if master else None

    stream_url = None
    if not millor:
        stream_url = await obtenir_stream_url(video_id)
        if not stream_url:
            logger.warning(f"[3CAT] No s'ha pogut obtenir l'stream (video_id={video_id})")
            return None

    linia_episodi = ""
    if tipus == "series":
        temp_item = obtenir_temporada(item_3cat)
        cap_item = obtenir_capitol(item_3cat)
        if temp_item > 0 and cap_item > 0:
            linia_episodi = f"\nT{temp_item} · Episodi {cap_item}"

    logger.info(f"[3CAT] Stream trobat (HLS fins a {millor}p)" if millor else "[3CAT] Stream trobat")
    if millor:  # un sol stream, sempre a la màxima qualitat disponible
        return {
            "url": f"{PUBLIC_BASE_URL}/hls/{video_id}.m3u8",
            "name": f"🎬 En català · {millor}p",
            "description": f"{nom_bonic}{linia_episodi}\n📡 3Cat · {millor}p",
            "behaviorHints": {"notWebReady": False},
        }
    return {
        "url": stream_url,
        "name": "🎬 En català",
        "description": f"{nom_bonic}{linia_episodi}\n📡 3Cat",
        "behaviorHints": {"notWebReady": False},
    }


async def cercar_anidd_stream(
    tipus: str, noms: list[str], temporada: int | None, capitol: int | None, nom_bonic: str,
) -> dict | None:
    item_anidd = await anidd_cercar_contingut(noms, tipus, temporada, capitol)
    if not item_anidd:
        return None

    linia_episodi = f"\nT{temporada} · Episodi {capitol}" if tipus == "series" else ""
    logger.info("[ANIDD] Stream trobat")
    return {
        "url": item_anidd["url"],
        "name": "🎬 En català",
        "description": f"{nom_bonic}{linia_episodi}\n🎌 AniDD",
        "behaviorHints": {"notWebReady": False},
    }


_computacions: dict[str, dict] = {}  # clau -> {"tasca": Task, "parcials": {idx: resultat}}: peticions en curs, compartides


def _aplanar(parcials: dict) -> list[dict]:
    streams: list[dict] = []
    for idx in sorted(parcials):  # ordre d'inserció de les fonts (català abans que castellà)
        r = parcials[idx]
        if r is None:
            continue
        if isinstance(r, list):
            streams.extend(r)
        else:
            streams.append(r)
    return streams


@app.get("/stream/{tipus}/{imdb_id}.json")
async def stream(tipus: str, imdb_id: str):
    clau = f"{tipus}/{imdb_id}"
    streams_cache = cache_obtenir(clau)
    if streams_cache is not None:
        logger.info(f"[CACHE] Resposta servida des de cache ({len(streams_cache)} stream(s))")
        return {"streams": streams_cache}

    comp = _computacions.get(clau)
    if comp is None:  # la primera petició calcula; les simultànies del mateix títol s'hi enganxen
        parcials: dict = {}
        tasca = asyncio.create_task(_calcular_streams(tipus, imdb_id, parcials))
        comp = {"tasca": tasca, "parcials": parcials}
        _computacions[clau] = comp

        def _final(t, k=clau):
            _computacions.pop(k, None)
            if not t.cancelled() and t.exception():
                logger.error(f"[STREAM] Error calculant {k}: {t.exception()!r}")

        tasca.add_done_callback(_final)

    # asyncio.wait no cancel·la la tasca en vèncer el termini: les fonts lentes acaben en segon pla i omplen la cache
    fet, _ = await asyncio.wait({comp["tasca"]}, timeout=DEADLINE_RESPOSTA_SEGONS)
    if comp["tasca"] in fet and not comp["tasca"].exception():
        return comp["tasca"].result()
    parcial = _aplanar(comp["parcials"])
    motiu = "error" if comp["tasca"] in fet else f"termini de {DEADLINE_RESPOSTA_SEGONS}s"
    logger.warning(f"[RESPOSTA PARCIAL] {clau}: {len(parcial)} stream(s) ({motiu}); la resta continua en segon pla")
    return {"streams": parcial}


async def _calcular_streams(tipus: str, imdb_id: str, parcials: dict):
    logger.info(f"{'='*60}")
    logger.info(f"PETICIÓ: {tipus}/{imdb_id}")
    logger.info(f"{'='*60}")

    clau_cache = f"{tipus}/{imdb_id}"
    streams_cache = cache_obtenir(clau_cache)
    if streams_cache is not None:
        logger.info(f"[CACHE] Resposta servida des de cache ({len(streams_cache)} stream(s))")
        return {"streams": streams_cache}

    parts = imdb_id.split(":")
    imdb_puro = parts[0]
    temporada = int(parts[1]) if len(parts) > 1 else None
    capitol = int(parts[2]) if len(parts) > 2 else None

    logger.info(f"[PARSE] IMDB={imdb_puro}, temporada={temporada}, capitol={capitol}")

    # ─── Resolució de noms (TMDB → Cinemeta fallback) ──────────────────────
    noms, any_estrena, es_animacio, durada_min = await obtenir_noms_tmdb(imdb_puro, tipus)
    if not noms:
        noms, any_estrena, es_animacio, durada_min = await obtenir_noms_cinemeta(imdb_puro, tipus)

    if not noms:
        if imdb_puro == XARXACAT_IMDB:
            noms = ["One Piece"]
            es_animacio = True
        else:
            logger.info(f"Cap nom trobat per {imdb_puro}")
            cache_guardar(clau_cache, [])
            return {"streams": []}

    logger.info(f"[NOMS] {noms} (any≈{any_estrena}, animació={es_animacio})")
    nom_bonic = noms[0]

    # ─── Calcular episodi absolut (Cinemeta → TMDB fallback) ───────────────
    ep_absolut = None
    if tipus == "series" and temporada is not None and capitol is not None:
        logger.info(f"[MAPATGE] Consultant Cinemeta per estructura temporades de {imdb_puro}...")
        estructura = await obtenir_estructura_temporades_cinemeta(imdb_puro)

        if not estructura:
            logger.info(
                f"[MAPATGE] Cinemeta no té dades per {imdb_puro}. "
                f"Provant TMDB com a fallback..."
            )
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r_find = await client.get(
                        f"https://api.themoviedb.org/3/find/{imdb_puro}",
                        params={"api_key": TMDB_API_KEY, "external_source": "imdb_id"},
                    )
                    r_find.raise_for_status()
                    tv_results = r_find.json().get("tv_results", [])
                    if tv_results:
                        tmdb_id = tv_results[0]["id"]
                        estructura = await obtenir_temporades_tmdb(tmdb_id)
                        logger.warning(
                            f"[MAPATGE] Usant TMDB (id={tmdb_id}) com a fallback. "
                            f"ATENCIÓ: el mapatge pot ser incorrecte si el teu "
                            f"addon de metadades usa una estructura diferent."
                        )
            except Exception as e:
                logger.error(f"[MAPATGE] Error obtenint TMDB fallback: {e}")

        if estructura:
            ep_absolut = convertir_a_episodi_absolut(temporada, capitol, estructura)
        else:
            ep_absolut = capitol
            logger.warning(
                f"[MAPATGE] No s'ha pogut obtenir estructura de temporades! "
                f"Usant capitol={capitol} directament com a absolut"
            )

    logger.info(f"[MAPATGE] Episodi absolut final: {ep_absolut}")

    # ─── Llançar TOTES les fonts EN PARAL·LEL ──────────────────────────────
    tasques = []

    # Ordre d'inserció important: gather() retorna els resultats en el
    # mateix ordre en què s'han afegit les tasques (no per ordre d'arribada),
    # i volem que el català aparegui sempre per sobre del castellà (RTVE) a
    # la llista final. Per això RTVE s'afegeix sempre l'últim.
    tasques.append(cercar_3cat_stream(tipus, noms, any_estrena, durada_min, temporada, capitol, nom_bonic))

    if es_animacio:
        if ANIDD_PASSWORD:
            tasques.append(cercar_anidd_stream(tipus, noms, temporada, capitol, nom_bonic))
        tasques.append(fansubscat_cercar_streams(
            noms, tipus, temporada, capitol, nom_bonic, ep_absolut,
        ))

    if imdb_puro == XARXACAT_IMDB:
        tasques.append(cercar_xarxacat_stream(
            imdb_puro, tipus, temporada, capitol, nom_bonic, ep_absolut,
        ))

    tasques.append(cercar_rtve_stream(tipus, noms, temporada, capitol, nom_bonic, ep_absolut, imdb_puro))
    tasques.append(cercar_pluto_stream(tipus, noms, temporada, capitol, nom_bonic, durada_min))
    tasques.append(cercar_plex_stream(tipus, noms, temporada, capitol, nom_bonic, any_estrena))
    if RUNTIME_CS_AUTH:
        tasques.append(cercar_runtime_stream(tipus, noms, temporada, capitol, nom_bonic, any_estrena))
    tasques.append(cercar_atresplayer_stream(tipus, noms, temporada, capitol, nom_bonic, any_estrena))

    logger.info(f"[FONTS] Llançant {len(tasques)} fonts en paral·lel...")

    async def _amb_timeout_font(idx, tasca):
        try:
            r = await asyncio.wait_for(tasca, timeout=FONT_TIMEOUT_SEGONS)
        except asyncio.TimeoutError:
            logger.warning(
                f"[FONTS] Una font ha superat el timeout de {FONT_TIMEOUT_SEGONS}s "
                f"— descartada perquè no bloquegi la resta"
            )
            r = None
        parcials[idx] = r  # visible per a la resposta de termini mentre les altres fonts continuen
        return r

    resultats = await asyncio.gather(*(_amb_timeout_font(i, t) for i, t in enumerate(tasques)))

    streams: list[dict] = []
    for r in resultats:
        if r is None:
            continue
        if isinstance(r, list):
            streams.extend(r)
        else:
            streams.append(r)

    logger.info(f"[RESULTAT] {len(streams)} stream(s) trobat(s) per {imdb_id}")
    if not streams:
        logger.info(f"[RESULTAT] Cap font ha trobat contingut per {imdb_id}")

    cache_guardar(clau_cache, streams)
    return {"streams": streams}


@app.on_event("startup")
async def _avisos_configuracio():
    for nom, valor, efecte in (
        ("TMDB_API_KEY", TMDB_API_KEY, "sense clau TMDB fallen alguns mapatges de temporades i noms"),
        ("ANIDD_PASSWORD", ANIDD_PASSWORD, "font AniDD desactivada"),
        ("RUNTIME_CS_AUTH", RUNTIME_CS_AUTH, "font Runtime desactivada"),
        ("PUBLIC_BASE_URL", PUBLIC_BASE_URL, "3Cat sense qualitat màxima forçada (es fa servir el stream de l'API tal qual)"),
    ):
        if not valor:
            logger.warning(f"[CONFIG] {nom} no definit: {efecte}")

# ─── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
