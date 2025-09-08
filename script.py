import asyncio
import logging
import aiohttp
import re
from dataclasses import dataclass, field
from typing import List, Dict

# Türkçe karakter desteği için
import locale
locale.setlocale(locale.LC_ALL, 'tr_TR.UTF-8')

# Ayarları yapılandır
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Zamanaşımı süresi (saniye)
TIMEOUT = 5

# M3U kaynak linkleri (Buraya güncel linkleri ekleyebilirsiniz)
M3U_SOURCES = [
    'https://raw.githubusercontent.com/GitLatte/patr0n/refs/heads/site/lists/iptvsevenler.m3u',
    'https://raw.githubusercontent.com/keyiflerolsun/IPTV_YenirMi/refs/heads/main/Kanallar/KekikAkademi.m3u',
    'https://tinyurl.com/TVCANLI'
]

# Kategori eşleştirme (daha iyi eşleşme için kullanılıyor)
CATEGORY_MAPPING = {
    "Sport": "Sports",
    "Movie": "Movies",
    "News": "News & Politics"
}

# Filtrelenmesi gereken kategoriler
FILTER_CATEGORIES = ["Adult", "XXX", "Erotic"]

@dataclass
class Channel:
    """Kanal verilerini saklamak için dataclass."""
    name: str
    category: str
    url: str
    metadata: dict = field(default_factory=dict)  # Ek meta bilgileri için

    def __repr__(self):
        return f"Channel(name={self.name}, category={self.category}, url={self.url})"


# URL'nin çalışıp çalışmadığını asenkron olarak kontrol et
async def check_url(sem: asyncio.Semaphore, url: str) -> str | None:
    """Asenkron olarak URL'nin çalışıp çalışmadığını kontrol eder, eşzamanlı bağlantıları sınırlar."""
    async with sem:
        try:
            # Otomatik yönlendirmeleri izle
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=TIMEOUT, allow_redirects=True) as response:
                    response.raise_for_status()  # 4xx/5xx hatalarında istisna fırlatır
                    logging.info(f"BAŞARILI: {url}")
                    return url
        except aiohttp.ClientError as e:
            logging.warning(f"URL KONTROL HATASI: {url} - {e}")
            return None
        except asyncio.TimeoutError:
            logging.warning(f"ZAMANAŞIMI HATASI: {url} - İstek {TIMEOUT} saniyede tamamlanamadı.")
            return None


async def remove_dead_links(channels: List[Channel]) -> List[Channel]:
    """Ölü linkleri temizle, eşzamanlı bağlantı sınırı koy."""
    sem = asyncio.Semaphore(50)  # Aynı anda en fazla 50 istek
    tasks = [check_url(sem, ch.url) for ch in channels]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    alive_channels = []
    for ch, result in zip(channels, results):
        if isinstance(result, str):
            alive_channels.append(ch)
    
    return alive_channels


# Kategori normalizasyonu (yazım hatası düzeltme)
def normalize_category(category: str) -> str:
    """Kategoriyi normalize et ve eşleştir."""
    category = category.strip().title()
    category = " ".join(category.split())  # Çift boşlukları temizle

    # Manuel eşleştirme
    for key, value in CATEGORY_MAPPING.items():
        if key.lower() in category.lower():
            return value
    return category


# Kanalları temizleme (Filtreleme ve Kategori Normalizasyonu)
def clean_channels(channels: List[Channel]) -> List[Channel]:
    """Filtreleme ve kategori düzenleme işlemi."""
    cleaned = []
    for ch in channels:
        normalized_category = normalize_category(ch.category)
        
        # Genişletilmiş kategori filtreleme
        if any(f.lower() in normalized_category.lower() for f in FILTER_CATEGORIES):
            logging.info(f"FİLTRELENDİ: {ch.name} - Kategori: {normalized_category}")
            continue

        ch.category = normalized_category
        cleaned.append(ch)
    return cleaned


# M3U Ayrıştırma (Boş kanal adlarını düzeltme)
def parse_m3u(m3u_content: str) -> List[Channel]:
    """M3U içeriğini daha esnek bir şekilde ayrıştırır."""
    channels: List[Channel] = []
    lines = m3u_content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            # tvg-name ve group-title'ı daha esnek yakala
            name_match = re.search(r'tvg-name="([^"]*)"', line)
            group_match = re.search(r'tvg-group="([^"]*)"|group-title="([^"]*)"', line)
            
            name = name_match.group(1) if name_match else "Unknown Channel"
            category = (group_match.group(1) or group_match.group(2)) if group_match else "Various"
            
            # Sonraki satırın bir URL olup olmadığını kontrol et
            if i + 1 < len(lines) and not lines[i+1].startswith("#"):
                url = lines[i+1].strip()
                if url:
                    channels.append(Channel(name=name, category=category, url=url))
                i += 1
        i += 1
    return channels


# Asenkron M3U indirme
async def download_m3u(url: str) -> str | None:
    """M3U listesini asenkron indir."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                return await response.text()
    except aiohttp.ClientError as e:
        logging.error(f"M3U indirilemedi: {url}, Hata: {e}")
        return None


def generate_m3u(channels: List[Channel]) -> str:
    """Temizlenmiş listeyi M3U formatına çevir."""
    m3u_content = "#EXTM3U\n"
    for ch in channels:
        m3u_content += f'#EXTINF:-1 tvg-name="{ch.name}" tvg-group="{ch.category}",{ch.name}\n'
        m3u_content += f'{ch.url}\n'
    return m3u_content

# Asenkron M3U indirme ve işleme (Birden fazla M3U kaynağı için)
async def process_m3u_sources(m3u_sources: List[str]) -> List[Channel]:
    """M3U kaynaklarını indirip işler."""
    all_channels = []
    for url in m3u_sources:
        logging.info(f"M3U kaynağı indiriliyor: {url}")
        m3u_content = await download_m3u(url)
        if m3u_content:
            channels = parse_m3u(m3u_content)
            all_channels.extend(channels)
            logging.info(f"Kaynak {url}'den {len(channels)} kanal eklendi.")
    return all_channels


# Örnek Kullanım
async def main():
    # M3U kaynaklarını işle
    all_channels = await process_m3u_sources(M3U_SOURCES)
    logging.info(f"Toplam kanal sayısı (işlenmeden önce): {len(all_channels)}")
    
    if not all_channels:
        logging.error("Hiçbir M3U kaynağından kanal verisi alınamadı.")
        return

    logging.info("--- Ölü linkler temizleniyor. Lütfen bekleyin... ---")
    channels = await remove_dead_links(all_channels)
    logging.info(f"Geçerli kanal sayısı (ölü linkler temizlendikten sonra): {len(channels)}")

    # Kategorileri düzenleme ve filtreleme
    cleaned_channels = clean_channels(channels)
    logging.info(f"Temizlenen kanal sayısı (filtreleme sonrası): {len(cleaned_channels)}")

    # Sonuçları göster
    if cleaned_channels:
        logging.info("--- Temizlenmiş ve güncel kanal listesi ---")
        for ch in cleaned_channels:
            logging.info(f"Kanal: {ch.name}, Kategori: {ch.category}")
        
    final_m3u = generate_m3u(cleaned_channels)

    with open("filtered_playlist.m3u", "w", encoding="utf-8") as f:
        f.write(final_m3u)

    logging.info("Yeni M3U listesi oluşturuldu: filtered_playlist.m3u")

# Asenkron çalıştır
if __name__ == "__main__":
    asyncio.run(main())
