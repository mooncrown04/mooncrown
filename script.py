import asyncio
import logging
import aiohttp
import re
from dataclasses import dataclass
from typing import List

# --- LOG AYARLARI ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR ---
TIMEOUT = 7 
MAX_CONCURRENT_REQUESTS = 30 
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

M3U_SOURCES = [
    'https://raw.githubusercontent.com/smartgmr/cdn/refs/heads/main/Perfect.m3u',
    'https://raw.githubusercontent.com/Mertcantv/Mertcan/refs/heads/main/%C4%B0zle2.m3u',
    'https://raw.githubusercontent.com/primatzeka/kurbaga/main/NeonSpor/NeonSpor.m3u',
    'https://tinyurl.com/TVCANLI'
]

# --- SIRALAMA ÖNCELİĞİ ---
# Buradaki sıraya göre M3U dosyasında en üstte görünecekler.
# İstediğin zaman buraya yeni gruplar ekleyebilirsin.
PRIORITY_GROUPS = [
    "Ulusal Kanallar",
    "Haberler",
    "Spor"
]

CATEGORY_MAPPING = {
    "haber": "Haberler",
    "ulusal": "Ulusal Kanallar",
    "sport": "Spor",
    "spor": "Spor",
    "movie": "Sinema",
    "film": "Sinema",
    "belgesel": "Belgesel",
    "cocuk": "Çocuk & Aile",
    "kids": "Çocuk & Aile"
}

@dataclass
class Channel:
    name: str
    category: str
    url: str
    logo: str = ""

def clean_category(raw_cat: str) -> str:
    if not raw_cat: return "Genel"
    clean = re.sub(r'[|\[\(].*?[|\]\)]', '', raw_cat) 
    clean = clean.replace(':', '').strip().lower()
    clean = clean.replace('ı', 'i').replace('ü', 'u').replace('ö', 'o').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')
    for key, target in CATEGORY_MAPPING.items():
        if key in clean:
            return target
    return clean.title() if clean else "Genel"

def normalize_channel_identity(name: str):
    name = re.sub(r'[\[\(].*?[\]\)]', '', name)
    patterns = [r'\bHD\b', r'\bSD\b', r'\bFHD\b', r'\b4K\b', r'\bYedek\b', r'\bBackup\b', r'\bHEVC\b']
    for p in patterns:
        name = re.sub(p, '', name, flags=re.IGNORECASE)
    return ' '.join(name.split()).strip().upper()

def parse_m3u(m3u_content: str) -> List[Channel]:
    channels = []
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
        if std_name and url:
            channels.append(Channel(name=std_name, category=std_category, url=url, logo=logo_url.strip()))
            seen_urls.add(url)
    return channels

async def check_url(sem, session, ch):
    async with sem:
        try:
            async with session.get(ch.url, timeout=TIMEOUT, allow_redirects=True) as response:
                if response.status == 200:
                    logging.info(f"OK: {ch.name}")
                    return ch
        except:
            pass
        return None

def get_group_priority(category_name: str) -> int:
    """Kategorinin öncelik sırasını döndürür."""
    try:
        # Eğer kategori PRIORITY_GROUPS içindeyse index numarasını döner (0, 1, 2...)
        return PRIORITY_GROUPS.index(category_name)
    except ValueError:
        # Eğer listede yoksa çok büyük bir numara döner ki en sona kalsın
        return 999

async def main():
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}) as session:
        all_channels = []
        global_seen_urls = set()
        logo_map = {} 
        
        for url in M3U_SOURCES:
            logging.info(f"İndiriliyor: {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = parse_m3u(text)
                        for ch in found:
                            if ch.url not in global_seen_urls:
                                if ch.name not in logo_map and ch.logo:
                                    logo_map[ch.name] = ch.logo
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
            except Exception as e:
                logging.error(f"Hata: {e}")

        if not all_channels: return

        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        alive_channels = [c for c in results if c]

        if alive_channels:
            # --- ÖZEL SIRALAMA MANTIĞI ---
            # 1. Önce get_group_priority ile kategori sırasına bakılır.
            # 2. Kategoriler aynıysa isme göre alfabetik dizilir.
            alive_channels.sort(key=lambda x: (get_group_priority(x.category), x.category, x.name))
            
            with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for ch in alive_channels:
                    final_logo = logo_map.get(ch.name, ch.logo)
                    f.write(f'#EXTINF:-1 group-title="{ch.category}" tvg-logo="{final_logo}",{ch.name}\n')
                    f.write(f"{ch.url}\n")
            
            logging.info(f"BİTTİ! {len(alive_channels)} kanal sıralı şekilde kaydedildi.")

if __name__ == "__main__":
    asyncio.run(main())
