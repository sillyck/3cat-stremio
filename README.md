# 📺 3Cat Stremio Addon

[![Stremio](https://img.shields.io/badge/Stremio-Addon-purple?style=for-the-badge&logo=stremio)](https://www.stremio.com/)
[![Python](https://img.shields.io/badge/Python-3.9+-blue?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)

Un complement (Addon) no oficial per a [Stremio](https://www.stremio.com/) que et permet buscar i reproduir directament tot el catàleg gratuït de sèries, programes, documentals i animes de **3Cat** des de qualsevol dispositiu.

---

## ✨ Característiques

- 🔍 **Cerca Híbrida Avançada:** Tradueix automàticament els títols internacionals d'IMDb/TMDB als títols locals de 3Cat.
- 🎯 **Detecció Intel·ligent:** Suport per a sèries regulars (temporades i capítols), documentals sense temporada (com *Crims*) gràcies a l'extracció del nom del cas, i animes mítics del *SX3*.
- 🛡️ **Filtre Anti-Clips:** Algoritme exclusiu que descarta automàticament vídeos promocionals, tràilers i resums de la web oficial.
- ⚡ **Extracció Directa:** Utilitza `yt-dlp` en temps real per obtenir l'enllaç `.mp4` i `.mpd` de màxima qualitat per oferir un *streaming* sense talls.

---

## 🛠️ Com funciona l'arquitectura?

L'Addon actua com un pont intel·ligent entre l'ecosistema internacional de Stremio i la base de dades local de 3Cat:
1. Rep l'ID (IMDb o TMDB) des de Stremio.
2. Consulta múltiples APIs (`Cinemeta`, `TMDB Addon`, `TVMaze`) per obtenir el títol original de la sèrie i el nom específic de l'episodi.
3. Utilitza un diccionari intern per traduir sagues conegudes (ex: *Dragon Ball Z* -> *Bola de drac Z*).
4. Connecta amb l'API interna de 3Cat realitzant peticions massives de fins a 300 ítems (per esquivar problemes de paginació en sèries llargues).
5. Filtra i pondera els resultats mitjançant metadades o coincidències de títol per retornar exclusivament el capítol correcte a l'usuari.

---

## 💻 Instal·lació Local (Per a Desenvolupadors)

Si vols fer córrer aquest Addon en el teu propi servidor o ordinador, segueix aquests passos:

1. **Clona el repositori:**
   ```bash
   git clone [https://github.com/TEU_USUARI/3cat-stremio-addon.git](https://github.com/TEU_USUARI/3cat-stremio-addon.git)
   cd 3cat-stremio-addon
   
2. **Instal·la les dependències:**
	```bash
	pip install -r requirements.txt
	
3. **Inicia el servidor:**
	```bash
	python -m uvicorn main:app --reload
	
4. **Afegeix-lo a Stremio:**
	Obre l'aplicació de Stremio, ves a la barra de cerca d'Addons i enganxa la següent URL:
	```bash
	http://127.0.0.1:8000/manifest.json
