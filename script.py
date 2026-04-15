import asyncio
import logging
import aiohttp
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR ---
TIMEOUT = 10 
MAX_CONCURRENT_REQUESTS = 30 
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
EPG_URL = "https://iptv-epg.org/files/epg-tr.xml"

M3U_SOURCES = [
    'https://raw.githubusercontent.com/smartgmr/cdn/refs/heads/main/Perfect.m3u',
    'https://raw.githubusercontent.com/Mertcantv/Mertcan/refs/heads/main/%C4%B0zle2.m3u',
    'https://raw.githubusercontent.com/primatzeka/kurbaga/main/NeonSpor/NeonSpor.m3u',
    'https://tinyurl.com/TVCANLI'
]

PRIORITY_GROUPS = ["Ulusal Kanallar", "Haberler", "Spor"]

def get_norm_variants(name: str):
    """Kanal ismini eşleşme için en yalın haline getirir."""
    if not name: return []
    name = name.upper()
    
    # 1. Gereksiz ekleri temizle (TV kelimesi dahil)
    # Örn: "TR - STAR TV" -> "STAR"
    name = re.sub(r'TR\s?-\s?|TR:|HD|SD|FHD|4K|BACKUP|YEDEK|HEVC|\bTV\b', '', name)
    
    # 2. Özel karakterleri sil ve boşlukları düzenle
    clean = re.sub(r'[^A-Z0-9]', ' ', name)
    spaced = " ".join(clean.split()).strip() 
    compact = spaced.replace(" ", "")        
    
    # Hem "STAR" hem "STAR" (zaten aynı ama farklı kanallarda işe yarar) dönüyoruz
    return list(set([spaced, compact]))

async def fetch_epg_data(session: aiohttp.ClientSession) -> Dict[str, str]:
    logging.info("EPG verileri çekiliyor...")
    try:
        async with session.get(EPG_URL, timeout=30) as resp:
            if resp.status == 200:
                content = await resp.read()
                root = ET.fromstring(content)
                epg_map = {}
                for channel in root.findall('channel'):
                    channel_id = channel.get('id')
                    display_node = channel.find('display-name')
                    if channel_id and display_node is not None:
                        variants = get_norm_variants(display_node.text)
                        for v in variants:
                            if v: epg_map[v] = channel_id
                logging.info(f"EPG yüklendi. {len(epg_map)} anahtar oluşturuldu.")
                return epg_map
    except Exception as e:
        logging.error(f"EPG hatası: {e}")
    return {}

def extract_attribute(line: str, attr: str) -> str:
    match = re.search(f'{attr}="([^"]*)"', line, re.IGNORECASE)
    return match.group(1).strip() if match else ""

def parse_m3u(m3u_content: str, epg_map: Dict[str, str]) -> List[Channel]:
    channels = []
    lines = m3u_content.splitlines()
    current_inf = None
    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            current_inf = line
        elif line.startswith("http") and current_inf:
            raw_name = current_inf.split(',')[-1].strip()
            group = extract_attribute(current_inf, "group-title") or extract_attribute(current_inf, "tvg-group")
            logo = extract_attribute(current_inf, "tvg-logo")
            
            # Parantez içi temizliği (Örn: STAR (Yedek) -> STAR)
            clean_name = re.sub(r'[\[\(].*?[\]\)]', '', raw_name).strip()
            variants = get_norm_variants(clean_name)
            
            # Eşleşme ara
            tvg_id = ""
            for v in variants:
                if v in epg_map:
                    tvg_id = epg_map[v]
                    break
            
            channels.append(Channel(
                name=clean_name,
                category=clean_category(group),
                url=line,
                logo=logo,
                tvg_id=tvg_id
            ))
            current_inf = None
    return channels

def clean_category(raw_group: str) -> str:
    if not raw_group: return "Genel"
    clean = raw_group.lower()
    for key, target in {"haber": "Haberler", "ulusal": "Ulusal Kanallar", "spor": "Spor", "movie": "Sinema"}.items():
        if key in clean: return target
    return raw_group.title()

async def check_url(sem, session, ch: Channel) -> Optional[Channel]:
    async with sem:
        try:
            async with session.get(ch.url, timeout=TIMEOUT, allow_redirects=True) as resp:
                if resp.status == 200:
                    ctype = resp.headers.get('Content-Type', '').lower()
                    if 'text/html' in ctype: return None
                    return ch
        except: pass
        return None

async def main():
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}) as session:
        epg_map = await fetch_epg_data(session)
        all_channels = []
        global_seen_urls = set()
        
        for url in M3U_SOURCES:
            logging.info(f"Kaynak işleniyor: {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = parse_m3u(text, epg_map)
                        for ch in found:
                            if ch.url not in global_seen_urls:
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
            except Exception as e: logging.error(f"Hata: {e}")

        if not all_channels: return

        logging.info(f"{len(all_channels)} kanal kontrol ediliyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        alive_channels = [c for c in results if c]

        alive_channels.sort(key=lambda x: (
            PRIORITY_GROUPS.index(x.category) if x.category in PRIORITY_GROUPS else 999,
            x.category, x.name
        ))

        with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
            f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
            for ch in alive_channels:
                tvg_part = f'tvg-id="{ch.tvg_id}" tvg-name="{ch.name}" ' if ch.tvg_id else ''
                f.write(f'#EXTINF:-1 {tvg_part}group-title="{ch.category}" tvg-logo="{ch.logo}",{ch.name}\n')
                f.write(f"{ch.url}\n")
        
        logging.info(f"Tamamlandı: {len(alive_channels)} kanal.")

@dataclass
class Channel:
    name: str
    category: str
    url: str
    logo: str = ""
    tvg_id: str = ""

if __name__ == "__main__":
    asyncio.run(main())
