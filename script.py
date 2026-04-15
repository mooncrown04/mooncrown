import asyncio
import logging
import aiohttp
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Dict, Optional

# Log ayarları
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

# ÖZEL SIRALAMA VE STANDART İSİM LİSTELERİ
ULUSAL_ORDER = ["TRT 1", "SHOW TV", "ATV", "KANAL D", "STAR", "NOW", "KANAL 7", "TV 8", "TV 8.5", "BEYAZ", "TEVE 2"]
HABER_ORDER = ["HALK TV", "SÖZCÜ TV", "CNN TÜRK", "TV 100", "NTV", "Flash Haber TV", "HABER GLOBAL", "TGRT HABER", "TV 24", "ÜLKE TV"]

# KARA LİSTE (Hatalı eşleşen yerel/yabancı kanallar)
BLACKLIST = ["ALANYA", "AZ", "AZER", "KIBRIS", "AVRUPA", "EURO", "ALMANYA", "MAGAZIN"]

@dataclass
class Channel:
    name: str
    category: str
    url: str
    logo: str = ""
    tvg_id: str = ""

def clean_display_name(name: str) -> str:
    """Kanal ismindeki teknik ekleri (HD, FHD, 1080p vb.) tamamen temizler."""
    # Parantez içindeki her şeyi siler: [HD] veya (SD) gibi
    name = re.sub(r'[\[\(].*?[\]\)]', '', name)
    # Teknik terimleri temizle (Büyük/Küçük harf duyarsız)
    patterns = [
        r'\bHD\b', r'\bSD\b', r'\bFHD\b', r'\b4K\b', r'\bUHD\b', 
        r'\b1080P?\b', r'\b720P?\b', r'\bHEVC\b', r'\bBACKUP\b', r'\bYEDEK\b'
    ]
    for p in patterns:
        name = re.compile(p, re.IGNORECASE).sub('', name)
    
    # Fazla boşlukları temizle
    name = " ".join(name.split()).strip()
    return name

def is_strict_match(target_name: str, candidate_name: str) -> bool:
    candidate_upper = candidate_name.upper()
    target_upper = target_name.upper()
    
    if any(word in candidate_upper for word in BLACKLIST):
        return False
        
    if target_upper in candidate_upper:
        suffix = candidate_upper.replace(target_upper, "").strip()
        valid_suffixes = ["", "HD", "FHD", "SD", "4K", "1080", "1080P", "720P", "HEVC", "TV"]
        if suffix in valid_suffixes or all(word in valid_suffixes for word in suffix.split()):
            return True
            
    return False

def get_norm_variants(name: str):
    if not name: return []
    name = name.upper()
    name = re.sub(r'TR\s?-\s?|TR:|HD|SD|FHD|4K|BACKUP|YEDEK|HEVC|\bTV\b', '', name)
    clean = re.sub(r'[^A-Z0-9]', ' ', name)
    spaced = " ".join(clean.split()).strip() 
    return list(set([spaced, spaced.replace(" ", "")]))

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
            
            # İsmi temizle (Teknik eklerden arındır)
            temp_name = clean_display_name(raw_name)
            
            final_category = "Diğer"
            is_priority = False
            final_name = temp_name 
            
            # Ulusal Kanal Kontrolü ve İsim Sabitleme
            for target in ULUSAL_ORDER:
                if is_strict_match(target, temp_name):
                    final_category = "Ulusal Kanallar"
                    final_name = target 
                    is_priority = True
                    break
            
            # Haber Kanalı Kontrolü
            if not is_priority:
                for target in HABER_ORDER:
                    if is_strict_match(target, temp_name):
                        final_category = "Haberler"
                        final_name = target
                        is_priority = True
                        break
            
            # Standart Kategori Belirleme
            if not is_priority:
                group_match = re.search(r'group-title="([^"]*)"', current_inf, re.I)
                clean_raw = group_match.group(1).lower() if group_match else ""
                if "spor" in clean_raw: final_category = "Spor"
                elif "sinema" in clean_raw or "film" in clean_raw: final_category = "Sinema"
                elif "belgesel" in clean_raw: final_category = "Belgesel"

            # EPG ID Atama
            variants = get_norm_variants(final_name)
            tvg_id = ""
            for v in variants:
                if v in epg_map:
                    tvg_id = epg_map[v]
                    break
            
            channels.append(Channel(name=final_name, category=final_category, url=line, logo=logo, tvg_id=tvg_id))
            current_inf = None
    return channels

async def check_url(sem, session, ch: Channel):
    async with sem:
        try:
            async with session.get(ch.url, timeout=TIMEOUT, allow_redirects=True) as resp:
                if resp.status == 200:
                    if 'text/html' not in resp.headers.get('Content-Type', '').lower():
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
                        for ch in parse_m3u(text, epg_map):
                            if ch.url not in global_seen_urls:
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
            except: pass

        if not all_channels: return

        logging.info(f"{len(all_channels)} kanal taranıyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        alive_channels = [c for c in results if c]

        # Sıralama Mantığı
        def sorting_key(ch: Channel):
            cat_rank = 1 if ch.category == "Ulusal Kanallar" else (2 if ch.category == "Haberler" else 3)
            order_list = ULUSAL_ORDER if ch.category == "Ulusal Kanallar" else (HABER_ORDER if ch.category == "Haberler" else [])
            try:
                name_rank = 999
                for i, target in enumerate(order_list):
                    if target == ch.name: 
                        name_rank = i
                        break
            except: name_rank = 999
            return (cat_rank, name_rank, ch.name)

        alive_channels.sort(key=sorting_key)

        # Yazma işlemi
        with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
            f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
            for ch in alive_channels:
                tvg_id_part = f'tvg-id="{ch.tvg_id}" ' if ch.tvg_id else ''
                tvg_name_part = f'tvg-name="{ch.name}" '
                logo_part = f'tvg-logo="{ch.logo}" ' if ch.logo else ''
                group_part = f'group-title="{ch.category}"'
                
                # Tek virgül kuralına uygun yazım
                f.write(f'#EXTINF:-1 {tvg_id_part}{tvg_name_part}{logo_part}{group_part},{ch.name}\n')
                f.write(f"{ch.url}\n")
        
        logging.info(f"Bitti! {len(alive_channels)} kanal aktif.")

if __name__ == "__main__":
    asyncio.run(main())
