import asyncio
import logging
import aiohttp
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Dict, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR VE ÖZEL SIRALAMA ---
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

# SENİN İSTEDİĞİN SIRALAMA VE KATEGORİLER
ULUSAL_ORDER = ["TRT 1", "SHOW TV", "ATV", "KANAL D", "STAR", "NOW", "KANAL 7", "TV 8", "TV 8.5", "BEYAZ", "TEVE 2"]
HABER_ORDER = ["HALK TV", "SÖZCÜ TV", "CNN TÜRK", "TV 100", "NTV", "Flash Haber TV", "HABER GLOBAL", "TGRT HABER", "TV 24", "ÜLKE TV"]

# Öncelik sözlüğü (Sıralama için)
PRIORITY_MAP = {name.upper(): i for i, name in enumerate(ULUSAL_ORDER + HABER_ORDER)}

@dataclass
class Channel:
    name: str
    category: str
    url: str
    logo: str = ""
    tvg_id: str = ""

def get_norm_variants(name: str):
    if not name: return []
    name = name.upper()
    name = re.sub(r'TR\s?-\s?|TR:|HD|SD|FHD|4K|BACKUP|YEDEK|HEVC|\bTV\b', '', name)
    clean = re.sub(r'[^A-Z0-9]', ' ', name)
    spaced = " ".join(clean.split()).strip() 
    compact = spaced.replace(" ", "")        
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
                return epg_map
    except Exception as e: logging.error(f"EPG hatası: {e}")
    return {}

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
            logo = re.search(r'tvg-logo="([^"]*)"', current_inf, re.I).group(1) if 'tvg-logo="' in current_inf else ""
            
            clean_name = re.sub(r'[\[\(].*?[\]\)]', '', raw_name).strip()
            upper_name = clean_name.upper()
            
            # --- KATEGORİ ZORLAMASI ---
            final_category = "Diğer"
            if any(u_name == upper_name or u_name in upper_name for u_name in [n.upper() for n in ULUSAL_ORDER]):
                final_category = "Ulusal Kanallar"
            elif any(h_name == upper_name or h_name in upper_name for h_name in [n.upper() for n in HABER_ORDER]):
                final_category = "Haberler"
            else:
                # Eğer senin listende yoksa eski mantıkla kategori ata
                raw_group = re.search(r'group-title="([^"]*)"', current_inf, re.I).group(1) if 'group-title="' in current_inf else ""
                final_category = clean_category_manual(raw_group)

            # EPG Eşleşme
            variants = get_norm_variants(clean_name)
            tvg_id = ""
            for v in variants:
                if v in epg_map:
                    tvg_id = epg_map[v]
                    break
            
            channels.append(Channel(name=clean_name, category=final_category, url=line, logo=logo, tvg_id=tvg_id))
            current_inf = None
    return channels

def clean_category_manual(raw_group: str) -> str:
    if not raw_group: return "Genel"
    clean = raw_group.lower()
    if "spor" in clean or "sport" in clean: return "Spor"
    if "sinema" in clean or "movie" in clean or "film" in clean: return "Sinema"
    return "Belgesel" if "belgesel" in clean else "Genel"

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
            logging.info(f"İndiriliyor: {url}")
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        for ch in parse_m3u(text, epg_map):
                            if ch.url not in global_seen_urls:
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
            except: pass

        if not all_channels: return

        logging.info(f"{len(all_channels)} kanal doğrulanıyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        alive_channels = [c for c in results if c]

        # --- ÖZEL SIRALAMA MANTIĞI ---
        def sorting_key(ch: Channel):
            # 1. Kategori Önceliği (Ulusal > Haber > Diğer)
            cat_priority = 0
            if ch.category == "Ulusal Kanallar": cat_priority = 1
            elif ch.category == "Haberler": cat_priority = 2
            else: cat_priority = 3
            
            # 2. Kanalın kendi sırası (Listede varsa sırası, yoksa alfabetik)
            name_upper = ch.name.upper()
            # Tam eşleşme veya içerme kontrolü
            order_val = 999
            for listed_name, pos in PRIORITY_MAP.items():
                if listed_name == name_upper or listed_name in name_upper:
                    order_val = pos
                    break
            
            return (cat_priority, order_val, ch.name)

        alive_channels.sort(key=sorting_key)

        with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
            f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
            for ch in alive_channels:
                tvg_part = f'tvg-id="{ch.tvg_id}" tvg-name="{ch.name}" ' if ch.tvg_id else ''
                f.write(f'#EXTINF:-1 {tvg_part}group-title="{ch.category}" tvg-logo="{ch.logo}",{ch.name}\n')
                f.write(f"{ch.url}\n")
        
        logging.info(f"Bitti! {len(alive_channels)} kanal listelendi.")

if __name__ == "__main__":
    asyncio.run(main())
