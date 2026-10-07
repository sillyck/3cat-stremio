"""Utilitats per als masters HLS de 3Cat: URIs absolutes i variants ordenades per qualitat."""
import re
from urllib.parse import urljoin

_RESOLUCIO = re.compile(r"RESOLUTION=([0-9]+)x([0-9]+)")
_AMPLADA = re.compile(r"[:,]BANDWIDTH=([0-9]+)")
_URI = re.compile(r'URI="([^"]+)"')


def reescriure_master_hls(text: str, url_base: str, nomes_millor: bool = False) -> tuple[str, int | None]:
    """Retorna (master reescrit, alçada de la millor variant).

    - Totes les URIs (variants, àudio, subtítols) passen a ser absolutes: el master
      es serveix des d'un altre host i les relatives deixarien de funcionar.
    - Les variants queden ordenades de més a menys resolució, perquè un reproductor
      que comença per la primera variant arrenqui ja en la millor qualitat.
    - nomes_millor=True deixa només la millor variant (qualitat fixa).
    """
    etiquetes: list[str] = []
    variants: list[tuple[int, int, str, str]] = []
    linies = [l.strip() for l in text.splitlines() if l.strip()]
    i = 0
    while i < len(linies):
        l = linies[i]
        if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(linies):
            res, amp = _RESOLUCIO.search(l), _AMPLADA.search(l)
            variants.append((
                int(res.group(2)) if res else 0,
                int(amp.group(1)) if amp else 0,
                l,
                urljoin(url_base, linies[i + 1]),
            ))
            i += 2
            continue
        if l.startswith("#"):
            etiquetes.append(_URI.sub(lambda m: 'URI="' + urljoin(url_base, m.group(1)) + '"', l))
        i += 1
    if not variants:
        return text, None
    variants.sort(key=lambda v: (v[0], v[1]), reverse=True)
    if nomes_millor:
        variants = variants[:1]
    sortida = etiquetes + [x for v in variants for x in (v[2], v[3])]
    return "\n".join(sortida) + "\n", (variants[0][0] or None)
