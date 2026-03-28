import asyncio
import logging
import aiohttp
import re
from dataclasses import dataclass
from typing import List

# --- LOG AYARLARI ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- AYARLAR ---
TIMEOUT = 5
MAX_CONCURRENT_REQUESTS = 30 # Hız ve banlanma dengesi için
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'

# Kaynaklar (Kendi linklerini buraya ekleyebilirsin)
M3U_SOURCES = [
    'https://raw.githubusercontent.com/Mertcantv/Mertcan/refs/heads/main/%C4%B0zle2.m3u',
    'https://raw.githubusercontent.com/primatzeka/kurbaga/main/NeonSpor/NeonSpor.m3u',
    'https://tinyurl.com/TVCANLI'
]

# Kategori Dönüştürme Sözlüğü
# Küçük harfle kontrol edilir, sağdaki değere dönüştürülür.
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

def clean_category(raw_cat: str) -> str:
    """'|Tr| Haber' -> 'Haberler' dönüşümünü yapar."""
    # 1. Adım: Sembolleri ve ülke kodlarını temizle (|Tr|, [Tr], TR: vb.)
    clean = re.sub(r'[|\[\(].*?[|\]\)]', '', raw_cat) 
    clean = clean.replace(':', '').strip().lower()
    
    # Türkçe karakter normalizasyonu (Arama kolaylığı için)
    clean = clean.replace('ı', 'i').replace('ü', 'u').replace('ö', 'o').replace('ş', 's').replace('ç', 'c').replace('ğ', 'g')

    # 2. Adım: Mapping sözlüğünde ara
    for key, target in CATEGORY_MAPPING.items():
        if key in clean:
            return target
    
    # 3. Adım: Eşleşme yoksa temizlenmiş metni güzelleştirip döndür
    return clean.title() if clean else "Genel"

def parse_m3u(m3u_content: str) -> List[Channel]:
    """Senin paylaştığın özel formata göre ayrıştırma yapar."""
    channels = []
    # #EXTINF ile başlayan blokları bul
    pattern = re.compile(r'#EXTINF:.*?(?:group-title|tvg-group)="([^"]+)".*?,([^\n\r]+)[\s\n\r]+(http[^\s\n\r]+)', re.IGNORECASE)
    
    matches = pattern.findall(m3u_content)
    
    for match in matches:
        raw_group, raw_name, url = match
        
        final_cat = clean_category(raw_group)
        name = raw_name.strip()
        
        # Sadece adı ve URL'si olanları ekle
        if name and url:
            channels.append(Channel(name=name, category=final_cat, url=url))
            
    return channels

async def check_url(sem, session, ch):
    """Linklerin çalışıp çalışmadığını doğrular."""
    async with sem:
        try:
            # allow_redirects=True önemli çünkü bazı linkler yönlendirme yapar
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
        
        # 1. Verileri Çek
        for url in M3U_SOURCES:
            logging.info(f"Liste indiriliyor: {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        found = parse_m3u(text)
                        all_channels.extend(found)
                        logging.info(f"Bulunan kanal: {len(found)}")
            except Exception as e:
                logging.error(f"Hata oluştu: {url} -> {e}")

        if not all_channels:
            logging.error("Hiç kanal ayrıştırılamadı. Regex desenini kontrol edin.")
            return

        # 2. Canlılık Kontrolü
        logging.info(f"Toplam {len(all_channels)} kanal kontrol ediliyor...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_channels]
        results = await asyncio.gather(*tasks)
        
        alive_channels = [c for c in results if c]

        # 3. M3U Olarak Kaydet
        if alive_channels:
            with open("guncel_liste.m3u", "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for ch in alive_channels:
                    # Hem tvg-group hem group-title ekliyoruz ki her player tanısın
                    f.write(f'#EXTINF:-1 group-title="{ch.category}" tvg-group="{ch.category}",{ch.name}\n')
                    f.write(f"{ch.url}\n")
            
            logging.info(f"Başarılı! {len(alive_channels)} kanal 'guncel_liste.m3u' dosyasına yazıldı.")
        else:
            logging.warning("Hiç çalışan kanal bulunamadı.")

if __name__ == "__main__":
    asyncio.run(main())
