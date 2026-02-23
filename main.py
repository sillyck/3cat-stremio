from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import yt_dlp
import httpx
import unicodedata

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# 1. EL MANIFEST
# ---------------------------------------------------------
@app.get("/manifest.json")
def obtenir_manifest():
    return {
        "id": "cat.stremio.3cat",
        "version": "1.3.0",
        "name": "3Cat Addon [Sense Límits]",
        "description": "Reprodueix sèries, programes i documentals de 3Cat.",
        "resources": ["stream"],
        "types": ["movie", "series", "tv", "channel"], # Obrim a tots els tipus de contingut
        "idPrefixes": ["tt", "tmdb:", "tvdb:"], # Ara acceptem TOTS els formats d'ID!
        "catalogs": []
    }

# ---------------------------------------------------------
# 2. EL TRADUCTOR (Cinemeta + TMDB + TVMaze + Diccionaris)
# ---------------------------------------------------------
DICCIONARI_3CAT = {
    "dragon ball": "bola de drac",
    "dragon ball z": "bola de drac z",
    "dragon ball gt": "bola de drac gt",
    "dragon ball super": "bola de drac súper",
    "detective conan": "el detectiu conan",
    "cardcaptor sakura": "sakura, la caçadora de cartes",
    "dr. slump": "el doctor slump",
    "totally spies!": "tres espies de veritat",
    "the powerpuff girls": "les supernenes",
    "magical doremi": "la màgica doremi",
    "sailor moon": "sailor moon",
    "inuyasha": "inuyasha",
    "my brilliant friend": "l'amiga genial",
    "the collapse": "el col·lapse",
    "the paradise": "el paradís",
    "the responders": "els primers a arribar",
    "borgen": "borgen",
    "mr. bean": "mr. bean"
}

# Traduccions específiques per a noms d'episodis de Crims que venen en anglès
DICCIONARI_EPISODIS = {
    "grandma anita": "avia anita",
    "the girl from portbou": "noia de portbou",
    "the caretaker of olot": "zelador d'olot",
    "the putxet killer": "putxet",
    "granny killer": "assassi de iaies",
    "the librarian helena jubany": "helena jubany",
    "why do we kill?": "per que matem",
    "the crime of the guardia urbana": "guardia urbana",
    "the robbery": "atracament"
}

async def obtenir_info_stremio(tipus: str, imdb_id: str, temporada: str = None, capitol: str = None):
    titol_serie = None
    titol_episodi = None

    apis_stremio = [
        f"https://v3-cinemeta.strem.io/meta/{tipus}/{imdb_id}.json",
        f"https://94c8cb9f702d-tmdb-addon.baby-beamup.club/meta/{tipus}/{imdb_id}.json"
    ]

    async with httpx.AsyncClient() as client:
        for url in apis_stremio:
            try:
                resposta = await client.get(url)
                if resposta.status_code == 200:
                    dades = resposta.json()
                    meta = dades.get("meta", {})
                    if not meta: continue
                        
                    if not titol_serie:
                        titol_serie = meta.get("name")
                    
                    if tipus == "series" and temporada and capitol and not titol_episodi:
                        videos = meta.get("videos", [])
                        for v in videos:
                            if str(v.get("season")) == str(temporada) and str(v.get("episode")) == str(capitol):
                                t_ep = v.get("name")
                                if t_ep and not t_ep.lower().startswith("episode") and not t_ep.lower().startswith("capítulo"):
                                    titol_episodi = t_ep
                                break
                    if titol_serie: break
            except Exception:
                continue

        if not titol_serie and tipus == "series":
            try:
                url_tvmaze = f"https://api.tvmaze.com/lookup/shows?imdb={imdb_id}"
                resposta_tvmaze = await client.get(url_tvmaze)
                if resposta_tvmaze.status_code == 200:
                    titol_serie = resposta_tvmaze.json().get("name")
            except Exception:
                pass

    if titol_serie and titol_serie.lower() in DICCIONARI_3CAT:
        titol_serie = DICCIONARI_3CAT[titol_serie.lower()]

    # TRADUCCIÓ D'EPISODIS (Neteja el "(1)", "(2)" i tradueix)
    if titol_episodi:
        nom_net = titol_episodi.split("(")[0].strip().lower()
        if nom_net in DICCIONARI_EPISODIS:
            print(f"🔄 Traduint episodi '{nom_net}' a '{DICCIONARI_EPISODIS[nom_net]}'!")
            titol_episodi = DICCIONARI_EPISODIS[nom_net]

    return titol_serie, titol_episodi

# ---------------------------------------------------------
# 3. EL CERCADOR DE 3CAT (La Lògica Estricta)
# ---------------------------------------------------------
def normalitzar(text):
    if not text: return ""
    text = str(text).lower()
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

async def cercar_url_3cat(titol: str, temporada: str = None, capitol: str = None, titol_episodi: str = None):
    print(f"\n🔍 Buscant a l'API de 3Cat: {titol} - T{temporada} C{capitol}")
    if titol_episodi:
        print(f"🧠 Stremio diu que l'episodi es diu: '{titol_episodi}'")
        
    titol_buscat_norm = normalitzar(titol)
    titol_ep_norm = normalitzar(titol_episodi) if titol_episodi else None
    
    cerques_a_provar = []
    if titol_episodi:
        nom_net = titol_episodi.split("(")[0].strip()
        cerques_a_provar.append(f"{titol} {nom_net}")
        
    if capitol and temporada:
        cerques_a_provar.append(f"{titol} temporada {temporada}")
        cerques_a_provar.append(f"{titol} {capitol}")
        
    cerques_a_provar.append(titol)
    
    url_api = "https://api.3cat.cat/cercador/tot"
    
    async with httpx.AsyncClient() as client:
        for terme in cerques_a_provar:
            print(f"📡 Provant terme: '{terme}'")
            params = {
                "version": "2.0", "_format": "json", "text": terme,
                "tipologia": "DTY_VIDEO_MM", "items_pagina": 300, 
                "pagina": 1, "master": "yes"
            }
            
            try:
                resposta = await client.get(url_api, params=params)
                if resposta.status_code != 200: continue
                    
                dades = resposta.json()
                items_base = dades.get("resposta", {}).get("items", {})
                elements = items_base if isinstance(items_base, list) else items_base.get("item", []) if isinstance(items_base, dict) else []

                if not elements: continue
                    
                for item in elements:
                    durada = str(item.get("durada", "00:00:00"))
                    if durada < "00:10:00": continue
                        
                    cap_api = str(item.get("capitol", ""))
                    temp_api = str(item.get("capitol_temporada", ""))
                    titol_api = item.get("titol", "")
                    titol_api_norm = normalitzar(titol_api)
                    
                    programes = item.get("programes_tv", [])
                    nom_programa = normalitzar(programes[0].get("titol", "")) if programes else titol_api_norm

                    # FILTRE ESTRICTE: El vídeo ha de pertànyer al programa "Crims", no al "Telenotícies"
                    if programes and titol_buscat_norm not in nom_programa and nom_programa not in titol_buscat_norm:
                        continue

                    if not capitol:
                        return extreure_enllac_3cat(item)

                    # --- 0. Coincidència Nom Episodi ---
                    if titol_ep_norm:
                        t_net = titol_ep_norm.split("(")[0].strip()
                        if len(t_net) > 3 and t_net in titol_api_norm:
                            print(f"✅ EXACTE (Nom de l'Episodi!): {titol_api}")
                            return extreure_enllac_3cat(item)

                    # --- 1. Metadades Flexibles ---
                    temporada_api_flexible = (temp_api == str(temporada) or temp_api in ["", "-1", "0", "None"])
                    if cap_api == str(capitol) and temporada_api_flexible:
                        print(f"✅ EXACTE (Meta-Dades): {titol_api}")
                        return extreure_enllac_3cat(item)
                        
                    # --- 2. Títol Regular ---
                    if f"t{temporada}xc{capitol} " in titol_api_norm + " " or f"t{temporada}xc{capitol}-" in titol_api_norm:
                        print(f"✅ EXACTE (Títol TxC): {titol_api}")
                        return extreure_enllac_3cat(item)
                        
                    # --- 3. Paraula Capítol ---
                    if f"capitol {capitol} " in titol_api_norm + " " or f"capitol {capitol}:" in titol_api_norm:
                        es_d_una_altra = False
                        for i in range(1, 30):
                            if str(i) != str(temporada) and (f"t{i}x" in titol_api_norm or f"temporada {i}" in titol_api_norm):
                                es_d_una_altra = True
                                break
                        if not temporada_api_flexible and temp_api != "":
                            es_d_una_altra = True

                        if not es_d_una_altra:
                            print(f"✅ EXACTE (Paraula Capítol): {titol_api}")
                            return extreure_enllac_3cat(item)

            except Exception as e:
                continue
                
    print(f"❌ No s'ha trobat a 3Cat.")
    return None

def extreure_enllac_3cat(item):
    url_video = item.get("seo_url")
    id_video = item.get("id")
    if url_video:
        return "https://www.3cat.cat" + url_video if not url_video.startswith("http") else url_video
    elif id_video:
        return f"https://www.3cat.cat/video/{id_video}/"
    return None

# ---------------------------------------------------------
# 4. L'EXTRACTOR MÀGIC (yt-dlp)
# ---------------------------------------------------------
def extreure_video_3cat(url_web: str):
    opcions_yt = {'format': 'best', 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(opcions_yt) as ydl:
            info = ydl.extract_info(url_web, download=False)
            return info.get('url')
    except Exception as e:
        print(f"Error a yt-dlp: {e}")
        return None

# ---------------------------------------------------------
# 5. EL PONT (Endpoint principal)
# ---------------------------------------------------------
@app.get("/stream/{tipus}/{id_video_sencer}.json")
async def obtenir_stream(tipus: str, id_video_sencer: str):
    parts_id = id_video_sencer.split(":")
    imdb_id = parts_id[0]
    temporada = parts_id[1] if len(parts_id) > 1 else None
    capitol = parts_id[2] if len(parts_id) > 2 else None

    print(f"\n--- NOVA PETICIÓ DE STREMIO ---")
    print(f"ID Brut: {id_video_sencer}")
    
    titol_real, titol_episodi = await obtenir_info_stremio(tipus, imdb_id, temporada, capitol)
    
    if not titol_real:
        print("❌ No s'ha pogut traduir l'ID.")
        return {"streams": []}
        
    print(f"🎬 Títol traduït: {titol_real}")
    
    url_web_3cat = await cercar_url_3cat(titol_real, temporada, capitol, titol_episodi)
    if not url_web_3cat:
        print("❌ Aquest contingut no s'ha trobat a 3Cat.")
        return {"streams": []}
        
    print(f"⚡ Extraient vídeo de: {url_web_3cat}")
    url_directa = extreure_video_3cat(url_web_3cat)
    
    if url_directa:
        nom_capitol = f"T{temporada} C{capitol}" if temporada else "Pel·lícula"
        if titol_episodi:
            nom_capitol += f" - {titol_episodi}"
            
        return {
            "streams": [
                {
                    "name": "󠁥LOCAL 3Cat",
                    "title": f"{titol_real}\n{nom_capitol}",
                    "url": url_directa
                }
            ]
        }
    return {"streams": []}