import asyncio
import logging
import aiohttp
import re
from dataclasses import dataclass, field
from typing import List, Dict

# --- AYARLAR ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

TIMEOUT = 7  # Kanal kontrolü için zamanaşımı
MAX_CONCURRENT_REQUESTS = 50 # Aynı anda yapılacak kontrol sayısı
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

M3U_SOURCES = [
    'https://raw.githubusercontent.com/Mertcantv/Mertcan/refs/heads/main/%C4%B0zle2.m3u',
    'https://raw.githubusercontent.com/primatzeka/kurbaga/main/NeonSpor/NeonSpor.m3u',
    'https://tinyurl.com/TVCANLI'
]

# Kategori Standartlaştırma Sözlüğü
# Soldaki kelime yakalanırsa, sağdaki kelimeye dönüştürülür.
CATEGORY_MAPPING = {
    "Haber": "Haberler",
    "News": "Haberler",
    "Sport": "Spor",
    "Sporlar": "Spor",
    "Sports": "Spor",
    "Movie": "Sinema",
    "Film": "Sinema",
    "Cinema": "Sinema",
    "Belgesel": "Belgesel",
    "Documentary": "Belgesel",
    "Çocuk": "Çocuk & Aile",
    "Kids": "Çocuk & Aile",
    "Cartoon": "Çocuk & Aile",
    "Müzik": "Müzik",
    "Music": "Müzik",
    "Eğlence": "Eğlence",
    "Entertainment": "Eğlence"
}

FILTER_CATEGORIES = ["Adult", "XXX", "Erotic", "Cinsel"]

@dataclass
class Channel:
    name: str
    category: str
    url: str
    metadata: dict = field(default_factory=dict)

# --- FONKSİYONLAR ---

def clean_category_string(cat_name: str) -> str:
    """Regex ile gereksiz karakterleri (|Tr|, [EN], ---) temizler."""
    if not cat_name:
        return "Various"
    
    # 1. Adım: |TR|, [EN], (4K) gibi yapıları temizle
    cat_name = re.sub(r'^[|\[\(].*?[|\]\)]\s*', '', cat_name)
    # 2. Adım: Özel karakterleri (-, *, _, =) temizle
    cat_name = re.sub(r'[-*_=]+', '', cat_name)
    # 3. Adım: Baş ve sondaki boşlukları al ve kelimeleri düzelt
    cat_name = cat_name.strip().title()
    
    # 4. Adım: Mapping sözlüğüyle eşleştir
    for key, value in CATEGORY_MAPPING.items():
        if key.lower() in cat_name.lower():
            return value
            
    return cat_name

async def check_url(sem: asyncio.Semaphore, session: aiohttp.ClientSession, channel: Channel) -> Channel | None:
    """Kanal linkinin aktif olup olmadığını kontrol eder."""
    async with sem:
        try:
            async with session.get(channel.url, timeout=TIMEOUT, allow_redirects=True) as response:
                if response.status == 200:
                    logging.info(f"AKTİF: {channel.name}")
                    return channel
        except Exception:
            pass # Hatalı linkleri sessizce geç
        return None

def parse_m3u(m3u_content: str) -> List[Channel]:
    """M3U içeriğini ayrıştırır ve kategorileri temizler."""
    channels = []
    lines = m3u_content.splitlines()
    
    for i in range(len(lines)):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            # tvg-name veya tırnak içindeki ismi yakala
            name_match = re.search(r'tvg-name="([^"]*)"', line)
            if not name_match:
                name_match = re.search(r',([^,]*)$', line)
            
            # Kategori bilgisi (group-title veya tvg-group)
            group_match = re.search(r'group-title="([^"]*)"|tvg-group="([^"]*)"', line)
            
            raw_name = name_match.group(1).strip() if name_match else "Bilinmeyen Kanal"
            raw_category = (group_match.group(1) or group_match.group(2)) if group_match else "Genel"
            
            # Kategori temizleme ve birleştirme
            clean_cat = clean_category_string(raw_category)
            
            # Filtre kontrolü
            if any(f.lower() in clean_cat.lower() for f in FILTER_CATEGORIES):
                continue

            # URL bir sonraki satırda olmalı
            if i + 1 < len(lines):
                url = lines[i+1].strip()
                if url and not url.startswith("#"):
                    channels.append(Channel(name=raw_name, category=clean_cat, url=url))
    return channels

async def download_m3u(session: aiohttp.ClientSession, url: str) -> str:
    """M3U listesini indirir."""
    try:
        async with session.get(url, timeout=15) as response:
            if response.status == 200:
                return await response.text()
    except Exception as e:
        logging.error(f"İndirme Hatası ({url}): {e}")
    return ""

def generate_m3u_output(channels: List[Channel]) -> str:
    """Final M3U içeriğini oluşturur."""
    output = "#EXTM3U\n"
    for ch in channels:
        output += f'#EXTINF:-1 tvg-name="{ch.name}" group-title="{ch.category}",{ch.name}\n'
        output += f'{ch.url}\n'
    return output

async def main():
    headers = {'User-Agent': USER_AGENT}
    all_extracted_channels = []

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Kaynakları İndir ve Ayrıştır
        for source_url in M3U_SOURCES:
            logging.info(f"Kaynak işleniyor: {source_url}")
            content = await download_m3u(session, source_url)
            if content:
                found = parse_m3u(content)
                all_extracted_channels.extend(found)
                logging.info(f"{len(found)} kanal bulundu.")

        if not all_extracted_channels:
            logging.error("Hiç kanal bulunamadı. Program sonlandırılıyor.")
            return

        # 2. Ölü Linkleri Temizle (Asenkron)
        logging.info(f"Toplam {len(all_extracted_channels)} kanal kontrol ediliyor (Link kontrolü)...")
        sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        tasks = [check_url(sem, session, ch) for ch in all_extracted_channels]
        
        results = await asyncio.gather(*tasks)
        alive_channels = [ch for ch in results if ch is not None]

        logging.info(f"Kontrol tamamlandı. Aktif kanal sayısı: {len(alive_channels)}")

        # 3. Dosyaya Yaz
        final_m3u = generate_m3u_output(alive_channels)
        with open("temiz_liste.m3u", "w", encoding="utf-8") as f:
            f.write(final_m3u)
        
        # Kategori istatistiklerini göster
        stats = {}
        for ch in alive_channels:
            stats[ch.category] = stats.get(ch.category, 0) + 1
        
        logging.info("--- İŞLEM TAMAMLANDI ---")
        logging.info(f"Oluşturulan Gruplar: {stats}")
        logging.info("Dosya: temiz_liste.m3u olarak kaydedildi.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
