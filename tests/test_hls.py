"""Prova mínima del reescriptor de masters HLS (sense dependències): python tests/test_hls.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from hls import reescriure_master_hls

MASTER = """#EXTM3U
#EXT-X-INDEPENDENT-SEGMENTS
#EXT-X-VERSION:6

#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Català",LANGUAGE="ca",AUTOSELECT=YES,DEFAULT=YES,URI="audioca.m3u8"

#EXT-X-STREAM-INF:BANDWIDTH=3639296,AVERAGE-BANDWIDTH=1728633,CODECS="avc1.4D401F,mp4a.40.2",RESOLUTION=1024x576,AUDIO="audio"
video576p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=4585460,AVERAGE-BANDWIDTH=2128690,CODECS="avc1.4D401F,mp4a.40.2",RESOLUTION=1280x720,AUDIO="audio"
video720p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=8191320,AVERAGE-BANDWIDTH=4123885,CODECS="avc1.640028,mp4a.40.2",RESOLUTION=1920x1080,AUDIO="audio"
video1080p.m3u8
"""
BASE = "https://ott-vod.example/0/1/42/1/stream.m3u8"

auto, alt = reescriure_master_hls(MASTER, BASE)
assert alt == 1080, alt
uris = [l for l in auto.splitlines() if not l.startswith("#")]
assert uris == [BASE.rsplit("/", 1)[0] + "/video" + q + "p.m3u8" for q in ("1080", "720", "576")], uris
assert 'URI="https://ott-vod.example/0/1/42/1/audioca.m3u8"' in auto

fix, alt = reescriure_master_hls(MASTER, BASE, nomes_millor=True)
assert alt == 1080 and fix.count("#EXT-X-STREAM-INF") == 1 and "video1080p.m3u8" in fix and "video576p" not in fix

assert reescriure_master_hls("#EXTM3U\n#EXT-X-TARGETDURATION:6\n", BASE) == ("#EXTM3U\n#EXT-X-TARGETDURATION:6\n", None)
print("OK: reescriptor de master HLS")
