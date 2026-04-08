import asyncio
import logging
import aiohttp
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Dict

# --- LOG AYARLARI ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR ---
TIMEOUT = 7 
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

CATEGORY_MAPPING = {
    "haber": "Haberler", "ulusal": "Ulusal Kanallar", "sport": "Spor", 
    "spor": "Spor", "movie": "Sinema", "film": "Sinema", 
    "belgesel": "Belgesel", "cocuk": "Çocuk & Aile", "kids": "Çocuk & Aile"
}

@dataclass
class Channel:
    name: str
    category: str
    url: str
    logo: str = ""
    tvg_id: str = ""
    tvg_name: str = ""

def normalize_name_for_matching(name: str) -> str:
    """Kanal isimlerini karşılaştırma için temizler (örn: 'TR - SHOW TV' -> 'SHOWTV')"""
    if not name: return ""
    name = name.upper()
    # 'TR - ', 'TR:', 'HD', 'SD' gibi ekleri ve boşlukları temizle
    name = re.sub(r'TR\s?-\s?|TR:|HD|SD|FHD|4K|BACKUP|YEDEK', '', name)
    name = re.sub(r'[^A-Z0-9]', '', name) # Sadece harf ve rakam bırak
    return name.strip()

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
                        # Hem orijinal ismi hem de normalize edilmiş ismi haritala
                        raw_name = display_node.text.strip()
                        norm_name = normalize_name_for_matching(raw_name)
                        epg_map[norm_name] = channel_id
                logging.info(f"EPG'den {len(epg_map)} benzersiz kanal eşleşmesi yüklendi.")
                return epg_map
    except Exception as e:
        logging.error(f"EPG çekme hatası: {e}")
    return {}

def clean_category(raw_cat: str) -> str:
    if not raw_cat: return "Genel"
    clean = re.sub(r'[|\[\(].*?[|\]\)]', '', raw_cat).replace(':', '').strip().lower()
    clean = clean.replace('ı', 'i').replace('ü', 'u').replace('ö', 'o').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')
    for key, target in CATEGORY_MAPPING.items():
        if key in clean: return target
    return clean.title() if clean else "Genel"

def normalize_channel_identity(name: str):
    name = re.sub(r'[\[\(].*?[\]\)]', '', name)
    patterns = [r'\bHD\b', r'\bSD\b', r'\bFHD\b', r'\b4K\b', r'\bYedek\b', r'\bBackup\b', r'\bHEVC\b']
    for p in patterns:
        name = re.sub(p, '', name, flags=re.IGNORECASE)
    return ' '.join(name.split()).strip().upper()

def parse_m3u(m3u_content: str, epg_map: Dict[str, str]) -> List[Channel]:
    channels = []
    # Regex'i tvg-id ve tvg-name olsa da olmasa da çalışacak şekilde esnettik
    pattern = re.compile(
        r'#EXTINF:.*?(?:group-title|tvg-group)="([^"]*)".*?(?:tvg-logo)="([^"]*)".*?,([^\n\r]+)[\s\n\r]+(http[^\s\n\r]+)', 
        re.IGNORECASE | re.DOTALL
    )
    matches = pattern.findall(m3u_content)
    seen_urls = set()
    
    for match in matches:
        raw_group, logo_url, raw_name, url = match
        url = url.strip()
        if url in seen_urls: continue
        
        std_name = normalize_channel_identity(raw_name)
        std_category = clean_category(raw_group)
        
        # --- EPG EŞLEŞTİRME MANTIĞI ---
        norm_name = normalize_name_for_matching(std_name)
        tvg_id = epg_map.get(norm_name, "")
        
        if std_name and url:
            channels.append(Channel(
                name=std_name, category=std_category, url=url, 
                logo=logo_url.strip(), tvg_id=tvg_id, tvg_name=std_name if tvg_id else ""
            ))
            seen_urls.add(url)
    return channels

async def check_url(sem, session, ch):
    async with sem:
        try:
            async with session.get(ch.url, timeout=TIMEOUT, allow_redirects=True) as response:
                if response.status == 200:
                    return ch
        except: pass
        return None

def get_group_priority(category_name: str) -> int:
    try: return PRIORITY_GROUPS.index(category_name)
    except ValueError: return 999

async def main():
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}) as session:
        epg_map = await fetch_epg_data(session)
        
        all_channels = []
        global_seen_urls = set()
        logo_map = {} 
        
        for url in M3U_SOURCES:
            logging.info(f"İndiriliyor: {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = parse_m3u(text, epg_map)
                        for ch in found:
                            if ch.url not in global_seen_urls:
                                if ch.name not in logo_map and ch.logo:
                                    logo_map[ch.name] = ch.logo
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
            except Exception as e: logging.error(f"Hata: {e}")

        if not all_channels: 
            logging.warning("Hiç kanal bulunamadı!")
            return

        logging.info(f"Toplam {len(all_channels)} kanal kontrol ediliyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        alive_channels = [c for c in results if c]

        if alive_channels:
            alive_channels.sort(key=lambda x: (get_group_priority(x.category), x.category, x.name))
            
            with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
                f.write(f'#EXTM3U x-tvg-url="{EPG_URL}"\n')
                for ch in alive_channels:
                    final_logo = logo_map.get(ch.name, ch.logo)
                    # ID varsa ekle, yoksa boş bırakma (oynatıcılar için daha sağlıklı)
                    tvg_id_str = f'tvg-id="{ch.tvg_id}" ' if ch.tvg_id else ''
                    tvg_name_str = f'tvg-name="{ch.name}" ' if ch.tvg_id else ''
                    
                    f.write(f'#EXTINF:-1 {tvg_id_str}{tvg_name_str}group-title="{ch.category}" tvg-logo="{final_logo}",{ch.name}\n')
                    f.write(f"{ch.url}\n")
            
            logging.info(f"BİTTİ! {len(alive_channels)} kanal 'guncel_liste.m3u' dosyasına kaydedildi.")

if __name__ == "__main__":
    async main()
