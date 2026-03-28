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

# Kaynaklar
M3U_SOURCES = [
    'https://raw.githubusercontent.com/Mertcantv/Mertcan/refs/heads/main/%C4%B0zle2.m3u',
    'https://raw.githubusercontent.com/primatzeka/kurbaga/main/NeonSpor/NeonSpor.m3u',
    'https://tinyurl.com/TVCANLI'
]

# Kategori Dönüştürme Sözlüğü
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
    """Kategori temizleme ve standartlaştırma."""
    if not raw_cat: return "Genel"
    clean = re.sub(r'[|\[\(].*?[|\]\)]', '', raw_cat) 
    clean = clean.replace(':', '').strip().lower()
    clean = clean.replace('ı', 'i').replace('ü', 'u').replace('ö', 'o').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')

    for key, target in CATEGORY_MAPPING.items():
        if key in clean:
            return target
    return clean.title() if clean else "Genel"

def parse_m3u(m3u_content: str) -> List[Channel]:
    """M3U içeriğinden Logo, Grup, İsim ve URL bilgilerini çeker."""
    channels = []
    # Regex: grup, logo, isim ve url'yi yakalar
    pattern = re.compile(
        r'#EXTINF:.*?(?:group-title|tvg-group)="([^"]*)".*?(?:tvg-logo)="([^"]*)".*?,([^\n\r]+)[\s\n\r]+(http[^\s\n\r]+)', 
        re.IGNORECASE | re.DOTALL
    )
    
    matches = pattern.findall(m3u_content)
    seen_urls = set()  # Aynı liste içindeki mükerrerleri engellemek için

    for match in matches:
        raw_group, logo_url, raw_name, url = match
        url = url.strip()
        
        # Eğer bu URL daha önce eklendiyse atla
        if url in seen_urls:
            continue
            
        final_cat = clean_category(raw_group)
        name = raw_name.strip()
        logo = logo_url.strip()
        
        if name and url:
            channels.append(Channel(name=name, category=final_cat, url=url, logo=logo))
            seen_urls.add(url)
            
    return channels

async def check_url(sem, session, ch):
    """Linklerin çalışıp çalışmadığını doğrular."""
    async with sem:
        try:
            async with session.get(ch.url, timeout=TIMEOUT, allow_redirects=True) as response:
                if response.status == 200:
                    logging.info(f"OK: {ch.name}")
                    return ch
        except:
            pass
        return None

async def main():
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}) as session:
        all_channels = []
        global_seen_urls = set() # Tüm kaynaklar arasında benzersiz URL kontrolü
        
        # 1. Verileri Çek ve Birleştir
        for url in M3U_SOURCES:
            logging.info(f"Liste indiriliyor: {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = parse_m3u(text)
                        
                        for ch in found:
                            if ch.url not in global_seen_urls:
                                all_channels.append(ch)
                                global_seen_urls.add(ch.url)
                        
                        logging.info(f"Kaynaktan {len(found)} kanal işlendi.")
            except Exception as e:
                logging.error(f"Hata oluştu: {url} -> {e}")

        if not all_channels:
            logging.error("Hiç kanal ayrıştırılamadı.")
            return

        # 2. Canlılık Kontrolü
        logging.info(f"Toplam {len(all_channels)} benzersiz kanal kontrol ediliyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        
        alive_channels = [c for c in results if c]

        # 3. M3U Olarak Kaydet
        if alive_channels:
            with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for ch in alive_channels:
                    # Sade ve logolu çıktı
                    f.write(f'#EXTINF:-1 group-title="{ch.category}" tvg-logo="{ch.logo}",{ch.name}\n')
                    f.write(f"{ch.url}\n")
            
            logging.info(f"BİTTİ! {len(alive_channels)} benzersiz ve aktif kanal kaydedildi.")
        else:
            logging.warning("Hiç çalışan kanal bulunamadı.")

if __name__ == "__main__":
    asyncio.run(main())
