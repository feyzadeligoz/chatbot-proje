import requests
from bs4 import BeautifulSoup
import time
import os
import re
from urllib.parse import quote

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

CIKTI_KLASORU = "duyuru_verileri"
os.makedirs(CIKTI_KLASORU, exist_ok=True)


def dosya_ismi_temizle(metin):
    """Dosya ismi olarak kullanilamayacak karakterleri temizler"""
    return re.sub(r'[^\w\-]', '_', metin)[:80]


def guvenli_istek_gonder(url, method="GET", max_deneme=3, **kwargs):
    """
    requests.get/post'u GUVENLI sekilde cagirir:
    - Zaman asimi (timeout) koyar, boylece site yanit vermezse sonsuza kadar beklemez
    - Basarisiz olursa otomatik olarak birkac kez tekrar dener
    - Hepsi basarisiz olursa None doner (script COKMEZ, sadece o kaydi atlar)
    """
    for deneme in range(1, max_deneme + 1):
        try:
            if method == "POST":
                return requests.post(url, timeout=15, **kwargs)
            else:
                return requests.get(url, timeout=15, **kwargs)
        except requests.exceptions.RequestException as e:
            print(f"    [Uyari] Istek basarisiz (deneme {deneme}/{max_deneme}): {type(e).__name__}")
            if deneme < max_deneme:
                time.sleep(3)
    print(f"    [HATA] {max_deneme} denemeden sonra basarisiz oldu, bu kayit atlaniyor: {url}")
    return None


def duyuru_listesini_cek(sayfa_no):
    """Belirli bir sayfadaki duyuru linklerini/basliklarini ceker"""
    url = f"https://www.bagcilar.bel.tr/News/GetNewsList?page={sayfa_no}&catid=196&link="
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    duyurular = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        # onclick icinden linki cikar: window.open('/duyuru/1110/irade-bizim-vatan-bizim', '_self')
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        tarih_tag = kart.find("time")

        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"
        tarih = tarih_tag.get_text(strip=True) if tarih_tag else "Tarih yok"

        img_tag = kart.find("img")
        resim_url = None
        if img_tag and img_tag.get("src"):
            resim_url = tam_url_yap(img_tag["src"])

        duyurular.append({
            "link": link,
            "baslik": baslik,
            "tarih": tarih,
            "resim": resim_url,
        })
    return duyurular


def duyuru_detayini_cek(link):
    """Bir duyurunun tam metnini ceker"""
    tam_url = "https://www.bagcilar.bel.tr" + link
    response = guvenli_istek_gonder(tam_url, headers=HEADERS)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    detay_div = soup.find("div", class_="news--desc")
    if detay_div:
        return detay_div.get_text(separator="\n", strip=True)
    return None


# ---- HABERLER (catid=174) ----
# Duyurular ile ayni API/HTML yapisini kullaniyor, sadece catid farkli.
# Toplamda 422 sayfa var (~3000+ haber), ama hepsini cekmek cok uzun surer.
# Bu yuzden sadece EN GUNCEL 41 sayfayi (duyurularla ayni miktarda) cekiyoruz.
HABER_CATID = 174
HABER_CEKILECEK_SAYFA_SAYISI = 41


def haber_listesini_cek(sayfa_no):
    """Belirli bir sayfadaki haber linklerini/basliklarini ceker (duyuru ile ayni yapida)"""
    url = f"https://www.bagcilar.bel.tr/News/GetNewsList?page={sayfa_no}&catid={HABER_CATID}&link="
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    haberler = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        # onclick icinden linki cikar: window.open('/haber/5931/bagcilarda-camilerde-...', '_self')
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        tarih_tag = kart.find("time")

        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"
        tarih = tarih_tag.get_text(strip=True) if tarih_tag else "Tarih yok"

        img_tag = kart.find("img")
        resim_url = None
        if img_tag and img_tag.get("src"):
            resim_url = tam_url_yap(img_tag["src"])

        haberler.append({
            "link": link,
            "baslik": baslik,
            "tarih": tarih,
            "resim": resim_url,
        })
    return haberler


def haber_detayini_cek(link):
    """Bir haberin tam metnini ceker (duyuru ile ayni HTML yapisi kullaniliyor)"""
    tam_url = "https://www.bagcilar.bel.tr" + link
    response = guvenli_istek_gonder(tam_url, headers=HEADERS)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    detay_div = soup.find("div", class_="news--desc")
    if detay_div:
        return detay_div.get_text(separator="\n", strip=True)
    return None


def haberleri_isle():
    """En guncel HABER_CEKILECEK_SAYFA_SAYISI sayfadaki haberleri cekip dosyaya kaydeder"""
    print(f"\n=== HABERLER CEKILIYOR (ilk {HABER_CEKILECEK_SAYFA_SAYISI} sayfa) ===\n")

    tum_haberler = []
    for sayfa in range(1, HABER_CEKILECEK_SAYFA_SAYISI + 1):
        print(f"Haber sayfasi {sayfa} cekiliyor...")
        haberler = haber_listesini_cek(sayfa)
        print(f"  -> {len(haberler)} haber bulundu")
        tum_haberler.extend(haberler)
        time.sleep(1)

    print(f"\nToplam {len(tum_haberler)} haber bulundu. Detaylari cekiliyor...\n")

    basarili = 0
    basarisiz = 0

    for i, haber in enumerate(tum_haberler, 1):
        print(f"[{i}/{len(tum_haberler)}] {haber['baslik'][:50]}...")
        metin = haber_detayini_cek(haber["link"])

        if metin:
            # HABER_ on eki ekliyoruz ki duyuru dosyalariyla ismi cakismasin
            dosya_adi = "HABER_" + dosya_ismi_temizle(haber["baslik"]) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {haber['baslik']}\n")
                f.write(f"Link: https://www.bagcilar.bel.tr{haber['link']}\n")
                if haber.get("resim"):
                    f.write(f"Resim: {haber['resim']}\n")
                f.write(f"Tarih: {haber['tarih']}\n")
                f.write("Tur: Haber\n\n")
                f.write(metin)
            basarili += 1
        else:
            print(f"  UYARI: Metin bulunamadi (muhtemelen sadece gorsel iceren haber)")
            basarisiz += 1

        time.sleep(0.5)

    print(f"\nHaberler bitti! Basarili: {basarili}, Basarisiz: {basarisiz}")


def etkinlik_listesini_cek():
    """Etkinlikler listesini ceker (POST istegi ile)"""
    url = "https://www.bagcilar.bel.tr/Activity/GetActivityList"
    etkinlik_headers = HEADERS.copy()
    etkinlik_headers["Referer"] = "https://www.bagcilar.bel.tr/activity/archive"
    etkinlik_headers["X-Requested-With"] = "XMLHttpRequest"
    etkinlik_headers["Content-Type"] = "application/json; charset=UTF-8"

    payload = {"datedto": {}, "type": 0, "page": 1}

    response = guvenli_istek_gonder(url, method="POST", headers=etkinlik_headers, json=payload)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    etkinlikler = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        tarih_tag = kart.find("time")
        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"
        tarih = tarih_tag.get_text(strip=True) if tarih_tag else "Tarih yok"

        # Afis/gorsel URL'sini de cekiyoruz (goruntulu cevap verebilmek icin)
        img_tag = kart.find("img")
        resim_url = None
        if img_tag and img_tag.get("src"):
            resim_url = tam_url_yap(img_tag["src"])

        etkinlikler.append({
            "link": link,
            "baslik": baslik,
            "tarih": tarih,
            "resim": resim_url,
        })
    return etkinlikler


def etkinlik_detayini_cek(link):
    """Bir etkinligin tam metnini ceker (duyuru ile ayni HTML yapisi kullaniliyor)"""
    tam_url = "https://www.bagcilar.bel.tr" + link
    response = guvenli_istek_gonder(tam_url, headers=HEADERS)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    detay_div = soup.find("div", class_="news--desc")
    if detay_div:
        return detay_div.get_text(separator="\n", strip=True)
    return None


def etkinlikleri_isle():
    """Tum etkinlikleri cekip dosyaya kaydeder"""
    print("\n=== ETKINLIKLER CEKILIYOR ===\n")
    etkinlikler = etkinlik_listesini_cek()
    print(f"{len(etkinlikler)} etkinlik bulundu.\n")

    basarili = 0
    basarisiz = 0

    for i, etkinlik in enumerate(etkinlikler, 1):
        print(f"[{i}/{len(etkinlikler)}] {etkinlik['baslik'][:50]}...")
        metin = etkinlik_detayini_cek(etkinlik["link"])

        # ONEMLI: Metin bulunamasa bile (sadece afis/gorsel iceren etkinlik) dosyayi
        # yine de kaydediyoruz - boylece gorsel + baslik + tarih ile hala cevap verilebilir.
        if not metin:
            metin = (
                f"{etkinlik['baslik']} etkinliği düzenlenmektedir. Detaylı bilgi için "
                f"etkinlik sayfası ziyaret edilebilir."
            )
            print(f"  [Bilgi] Aciklama metni yok (sadece gorsel), yine de kaydediliyor.")

        dosya_adi = "ETKINLIK_" + dosya_ismi_temizle(etkinlik["baslik"]) + ".txt"
        dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
        with open(dosya_yolu, "w", encoding="utf-8") as f:
            f.write(f"Baslik: {etkinlik['baslik']}\n")
            f.write(f"Link: https://www.bagcilar.bel.tr{etkinlik['link']}\n")
            f.write(f"Tarih: {etkinlik['tarih']}\n")
            if etkinlik.get("resim"):
                f.write(f"Resim: {etkinlik['resim']}\n")
            f.write("Tur: Etkinlik\n\n")
            f.write(metin)
        basarili += 1

        time.sleep(0.5)

    print(f"\nEtkinlikler bitti! Basarili: {basarili}, Basarisiz: {basarisiz}")


# ---- TESISLER ----
# Duyuru/haber ile ayni "news-card" liste yapisini kullaniyor, ama detay sayfasi
# FARKLI bir class kullaniyor: "event__bottom-left" (news--desc DEGIL).
# Toplam 14 sayfa var (~112 tesis), tamamini cekiyoruz.
TESIS_TOPLAM_SAYFA = 14


def tesis_listesini_cek(sayfa_no):
    """Belirli bir sayfadaki tesis linklerini/basliklarini ceker"""
    url = f"https://www.bagcilar.bel.tr/Facility/GetFacilityList?page={sayfa_no}&typeid=0&key="
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    tesisler = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"

        tesisler.append({
            "link": link,
            "baslik": baslik
        })
    return tesisler


def tesis_detayini_cek(link):
    """Bir tesisin tam aciklamasini ceker (FARKLI class kullaniyor: event__bottom-left)"""
    tam_url = "https://www.bagcilar.bel.tr" + link
    response = guvenli_istek_gonder(tam_url, headers=HEADERS)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    detay_div = soup.find("div", class_="event__bottom-left")
    if detay_div:
        return detay_div.get_text(separator="\n", strip=True)
    return None


def tesisleri_isle():
    """Tum tesisleri cekip dosyaya kaydeder"""
    print(f"\n=== TESISLER CEKILIYOR (toplam {TESIS_TOPLAM_SAYFA} sayfa) ===\n")

    tum_tesisler = []
    for sayfa in range(1, TESIS_TOPLAM_SAYFA + 1):
        print(f"Tesis sayfasi {sayfa} cekiliyor...")
        tesisler = tesis_listesini_cek(sayfa)
        print(f"  -> {len(tesisler)} tesis bulundu")
        tum_tesisler.extend(tesisler)
        time.sleep(1)

    print(f"\nToplam {len(tum_tesisler)} tesis bulundu. Detaylari cekiliyor...\n")

    basarili = 0
    basarisiz = 0

    for i, tesis in enumerate(tum_tesisler, 1):
        print(f"[{i}/{len(tum_tesisler)}] {tesis['baslik'][:50]}...")
        metin = tesis_detayini_cek(tesis["link"])

        if metin:
            dosya_adi = "TESIS_" + dosya_ismi_temizle(tesis["baslik"]) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {tesis['baslik']}\n")
                f.write(f"Link: https://www.bagcilar.bel.tr{tesis['link']}\n")
                f.write("Tur: Tesis\n\n")
                f.write(metin)
            basarili += 1
        else:
            print(f"  UYARI: Metin bulunamadi")
            basarisiz += 1

        time.sleep(0.5)

    print(f"\nTesisler bitti! Basarili: {basarili}, Basarisiz: {basarisiz}")


# ---- PROJELER ----
# Tesisler ile AYNI yapiyi kullaniyor: news-card liste + event__bottom-left detay.
# Toplam 5 sayfa var (~40 proje), tamamini cekiyoruz.
PROJE_TOPLAM_SAYFA = 5


def proje_listesini_cek(sayfa_no):
    """Belirli bir sayfadaki proje linklerini/basliklarini ceker"""
    url = f"https://www.bagcilar.bel.tr/Project/GetProjectList?link=&page={sayfa_no}&type=NaN&isHome=false"
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    projeler = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"

        projeler.append({
            "link": link,
            "baslik": baslik
        })
    return projeler


def proje_detayini_cek(link):
    """Bir projenin tam aciklamasini ceker (event__bottom-left class'i kullaniyor)"""
    tam_url = "https://www.bagcilar.bel.tr" + link
    response = guvenli_istek_gonder(tam_url, headers=HEADERS)
    if response is None:
        return None
    soup = BeautifulSoup(response.text, "html.parser")

    detay_div = soup.find("div", class_="event__bottom-left")
    if detay_div:
        return detay_div.get_text(separator="\n", strip=True)
    return None


def projeleri_isle():
    """Tum projeleri cekip dosyaya kaydeder"""
    print(f"\n=== PROJELER CEKILIYOR (toplam {PROJE_TOPLAM_SAYFA} sayfa) ===\n")

    tum_projeler = []
    for sayfa in range(1, PROJE_TOPLAM_SAYFA + 1):
        print(f"Proje sayfasi {sayfa} cekiliyor...")
        projeler = proje_listesini_cek(sayfa)
        print(f"  -> {len(projeler)} proje bulundu")
        tum_projeler.extend(projeler)
        time.sleep(1)

    print(f"\nToplam {len(tum_projeler)} proje bulundu. Detaylari cekiliyor...\n")

    basarili = 0
    basarisiz = 0

    for i, proje in enumerate(tum_projeler, 1):
        print(f"[{i}/{len(tum_projeler)}] {proje['baslik'][:50]}...")
        metin = proje_detayini_cek(proje["link"])

        if metin:
            dosya_adi = "PROJE_" + dosya_ismi_temizle(proje["baslik"]) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {proje['baslik']}\n")
                f.write(f"Link: https://www.bagcilar.bel.tr{proje['link']}\n")
                f.write("Tur: Proje\n\n")
                f.write(metin)
            basarili += 1
        else:
            print(f"  UYARI: Metin bulunamadi")
            basarisiz += 1

        time.sleep(0.5)

    print(f"\nProjeler bitti! Basarili: {basarili}, Basarisiz: {basarisiz}")


# ---- GENCLIK VE SPOR KURSLARI ----
# Bu bolumde LISTE API'si YOK - her brans kendi sabit sayfasinda (/icerik/<slug>).
# Ayrica bu sayfalarda BIRDEN FAZLA link turu olabiliyor:
#   - Basvuru linki (genelde ebelediye.bagcilar.bel.tr/web.talep.doms?...)
#   - Bagkart linki (baglica basvuru sarti oldugunda cikan ayri link)
#   - SSS (Sikca Sorulan Sorular) linki (bazi branslarda var, hepsinde degil)
# Bunlari otomatik ayirt edip, dosyaya AYRI satirlar olarak kaydediyoruz.

GENCLIK_SPOR_SLUGLARI = [
    "kadinlar-sabah-sporunda-bulusuyor",
    "siber-guvenlik",
    "oyun-yazilim-egitimi",
    "genclik-merkezi-branslari",
    "yuzme",
    "e-spor",
    "güreş",
    "temel-bisiklet-egitimi",
    "kick-boks",
    "boks",
    "besyo",
    "cimnastik",
    "okculuk",
    "Badminton",
    "futsal",
    "Voleybol",
    "masa-tenisi",
    "Basketbol",
    "pmyo",
    "Wushu",
    "fitness",
    "karate",
    "Taekwondo",
    "pilates",
    "pomem",
]


def genclik_spor_sluglarini_otomatik_kesfet():
    """
    /genclikvespor ana sayfasini tarar, sayfadaki TUM '/icerik/...' linklerini
    otomatik olarak bulur. Boylece belediye YENI bir brans eklerse (bizim sabit
    listemizde olmasa bile), otomatik olarak kesfedilmis olur.
    Bulamazsa (site erisilemezse), sabit listeye geri doner.
    """
    url = "https://www.bagcilar.bel.tr/genclikvespor"
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        print("  [UYARI] genclikvespor sayfasina erisilemedi, sabit slug listesi kullanilacak.")
        return list(GENCLIK_SPOR_SLUGLARI)

    soup = BeautifulSoup(response.text, "html.parser")
    bulunan_sluglar = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.match(r"^(?:https://www\.bagcilar\.bel\.tr)?/icerik/([^?]+)", href)
        if match:
            slug = match.group(1)
            bulunan_sluglar.add(slug)

    # Otomatik bulunanlarla, elimizdeki sabit listeyi BIRLESTIRIYORUZ (hicbirini kaybetmeyelim)
    tum_sluglar = set(GENCLIK_SPOR_SLUGLARI) | bulunan_sluglar
    yeni_bulunanlar = bulunan_sluglar - set(GENCLIK_SPOR_SLUGLARI)

    if yeni_bulunanlar:
        print(f"  [YENI] Otomatik olarak {len(yeni_bulunanlar)} yeni brans kesfedildi: {sorted(yeni_bulunanlar)}")

    return sorted(tum_sluglar)


# Bazi ekstra linkler (ozellikle SSS) sayfanin HTML kaynaginda hic BULUNMUYOR -
# bu yuzden otomatik taramayla yakalanamiyorlar. Bunlari burada MANUEL olarak
# ekliyoruz. Sen yeni bir tane fark edince, buraya slug: {"SSS": "url"} seklinde
# eklememizi soyleyebilirsin.
EK_LINKLER_MANUEL = {
    "yuzme": {
        "SSS": "https://www.bagcilar.bel.tr/icerik/yuzme-havuzlari-sikca-sorulan-sorular",
    },
}


def tam_url_yap(href):
    """Goreli (/icerik/...) veya tam (https://...) href'i her zaman tam URL'ye cevirir"""
    if href.startswith("http"):
        return href
    return "https://www.bagcilar.bel.tr" + href


def kurs_detayini_cek(slug):
    """
    Bir brans/kurs sayfasini ceker. Iki sey doner:
    1. Ana aciklama metni
    2. Ekstra linkler sozlugu (ornek: {'Basvuru': '...', 'Bagkart': '...', 'SSS': '...'})
    """
    url = "https://www.bagcilar.bel.tr/icerik/" + quote(slug)
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return None, {}, None

    soup = BeautifulSoup(response.text, "html.parser")

    # Basligi bulmaya calis (breadcrumb'in son elemani, yoksa slug'dan uret)
    baslik = None
    breadcrumb = soup.find("ul", class_="breadcrumb")
    if breadcrumb:
        li_listesi = breadcrumb.find_all("li")
        if li_listesi:
            baslik = li_listesi[-1].get_text(strip=True)
    if not baslik:
        baslik = slug.replace("-", " ").replace("_", " ").title()

    # Ana aciklama metni
    detay_div = soup.find("div", class_="mayor__top-right")
    metin = detay_div.get_text(separator="\n", strip=True) if detay_div else None

    # Ekstra linkleri TUM sayfada ara (SSS/Bagkart bazen ana metin disinda olabiliyor)
    ekstra_linkler = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        href_kucuk = href.lower()
        metin_kucuk = a.get_text(strip=True).lower()

        if "sikca-sorulan" in href_kucuk or "sıkça sorulan" in metin_kucuk or "sikca sorulan" in metin_kucuk:
            ekstra_linkler.setdefault("SSS", tam_url_yap(href))
        elif "bağkart" in metin_kucuk or "bagkart" in metin_kucuk:
            ekstra_linkler.setdefault("Bagkart", tam_url_yap(href))
        elif "ebelediye.bagcilar.bel.tr" in href_kucuk and ("talepid" in href_kucuk or "talep.doms" in href_kucuk):
            ekstra_linkler.setdefault("Basvuru", tam_url_yap(href))

    # Manuel eklenen linkleri de ekle (otomatik bulunanlarin UZERINE yazar, yani manuel oncelikli)
    if slug in EK_LINKLER_MANUEL:
        ekstra_linkler.update(EK_LINKLER_MANUEL[slug])

    return metin, ekstra_linkler, baslik


def kurslari_isle():
    """Tum genclik/spor kurslarini/branslarini cekip dosyaya kaydeder.
    Once ana sayfayi tarayip YENI branslarin olup olmadigini otomatik kontrol eder."""
    sluglar = genclik_spor_sluglarini_otomatik_kesfet()
    print(f"\n=== GENCLIK VE SPOR KURSLARI CEKILIYOR (toplam {len(sluglar)} sayfa) ===\n")

    basarili = 0
    basarisiz = 0

    for i, slug in enumerate(sluglar, 1):
        print(f"[{i}/{len(sluglar)}] {slug}...")
        metin, ekstra_linkler, baslik = kurs_detayini_cek(slug)

        if metin:
            dosya_adi = "KURS_" + dosya_ismi_temizle(baslik) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {baslik}\n")
                f.write(f"Link: https://www.bagcilar.bel.tr/icerik/{slug}\n")
                if "Basvuru" in ekstra_linkler:
                    f.write(f"BasvuruLink: {ekstra_linkler['Basvuru']}\n")
                if "Bagkart" in ekstra_linkler:
                    f.write(f"BagkartLink: {ekstra_linkler['Bagkart']}\n")
                if "SSS" in ekstra_linkler:
                    f.write(f"SSSLink: {ekstra_linkler['SSS']}\n")
                f.write("Tur: Kurs\n\n")
                f.write(metin)
            basarili += 1
        else:
            print(f"  UYARI: Metin bulunamadi ({slug})")
            basarisiz += 1

        time.sleep(0.5)

    print(f"\nKurslar bitti! Basarili: {basarili}, Basarisiz: {basarisiz}")


# ---- YAYINLAR (Bultenler) ----
# Diger bolumlerden FARKLI: her yayin dogrudan bir PDF dosyasina gidiyor, ayri bir
# "detay sayfasi" yok. Bu yuzden cekilecek "icerik" sadece baslik + PDF linki.
# Toplam 22 sayfa var ama SADECE 2026 yilina ait olanlari istiyoruz - bu yuzden
# sayfalari geziyoruz, 2026 iceren basliklari topluyoruz, bir sayfada HIC 2026'li
# baslik kalmazsa (daha eski yillara gecildigini varsayip) taramayi durduruyoruz.
YAYIN_MAX_SAYFA = 22


def yayin_listesini_cek(sayfa_no):
    """Belirli bir sayfadaki yayin (bulten) linklerini/basliklarini ceker"""
    url = f"https://www.bagcilar.bel.tr/Broadcast/GetBroadcastList?page={sayfa_no}&typeid=0&key="
    response = guvenli_istek_gonder(url, headers=HEADERS)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")

    yayinlar = []
    for kart in soup.find_all("div", class_="news-card"):
        onclick = kart.get("onclick", "")
        match = re.search(r"window\.open\('([^']+)'", onclick)
        if not match:
            continue
        link = match.group(1)

        baslik_tag = kart.find("h3")
        baslik = baslik_tag.get_text(strip=True) if baslik_tag else "Baslik yok"

        yayinlar.append({"link": link, "baslik": baslik})
    return yayinlar


def yayinlari_isle():
    """Sadece 2026 yilina ait yayinlari (bultenleri) cekip dosyaya kaydeder"""
    print(f"\n=== YAYINLAR CEKILIYOR (sadece 2026, en fazla {YAYIN_MAX_SAYFA} sayfa taranacak) ===\n")

    basarili = 0
    basarisiz = 0

    for sayfa in range(1, YAYIN_MAX_SAYFA + 1):
        print(f"Yayin sayfasi {sayfa} taraniyor...")
        yayinlar = yayin_listesini_cek(sayfa)

        bu_sayfada_2026_var_mi = False

        for yayin in yayinlar:
            if "2026" not in yayin["baslik"]:
                continue

            bu_sayfada_2026_var_mi = True
            dosya_adi = "YAYIN_" + dosya_ismi_temizle(yayin["baslik"]) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)

            # Link zaten dogrudan PDF - tam url'ye ceviriyoruz
            tam_link = tam_url_yap(yayin["link"])

            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {yayin['baslik']}\n")
                f.write(f"Link: {tam_link}\n")
                f.write("Tur: Yayin\n\n")
                f.write(f"{yayin['baslik']}, Bağcılar Belediyesi tarafından yayınlanan bir bültendir. "
                        f"Tam içeriğine PDF dosyasından ulaşılabilir.")

            basarili += 1

        if not bu_sayfada_2026_var_mi:
            print(f"  Bu sayfada 2026 yilina ait yayin kalmadi, tarama durduruluyor.")
            break

        time.sleep(1)

    print(f"\nYayinlar bitti! 2026 yilina ait {basarili} yayin kaydedildi.")


def main():
    # Tum duyurulari cekiyoruz (41 sayfa, sayfa basina 8 duyuru = ~328 duyuru)
    TEST_SAYFA_SAYISI = 41

    tum_duyurular = []
    for sayfa in range(1, TEST_SAYFA_SAYISI + 1):
        print(f"Sayfa {sayfa} cekiliyor...")
        duyurular = duyuru_listesini_cek(sayfa)
        print(f"  -> {len(duyurular)} duyuru bulundu")
        tum_duyurular.extend(duyurular)
        time.sleep(1)  # siteye asiri yuklenmeyelim diye kisa bir bekleme

    print(f"\nToplam {len(tum_duyurular)} duyuru bulundu. Detaylari cekiliyor...\n")

    basarili = 0
    basarisiz = 0

    for i, duyuru in enumerate(tum_duyurular, 1):
        print(f"[{i}/{len(tum_duyurular)}] {duyuru['baslik'][:50]}...")
        metin = duyuru_detayini_cek(duyuru["link"])

        if metin:
            dosya_adi = dosya_ismi_temizle(duyuru["baslik"]) + ".txt"
            dosya_yolu = os.path.join(CIKTI_KLASORU, dosya_adi)
            with open(dosya_yolu, "w", encoding="utf-8") as f:
                f.write(f"Baslik: {duyuru['baslik']}\n")
                f.write(f"Link: https://www.bagcilar.bel.tr{duyuru['link']}\n")
                if duyuru.get("resim"):
                    f.write(f"Resim: {duyuru['resim']}\n")
                f.write(f"Tarih: {duyuru['tarih']}\n\n")
                f.write(metin)
            basarili += 1
        else:
            print(f"  UYARI: Metin bulunamadi (muhtemelen sadece gorsel iceren duyuru)")
            basarisiz += 1

        time.sleep(0.5)  # her istek arasinda kisa bekleme

    print(f"\nBitti! Basarili: {basarili}, Basarisiz (metin yok): {basarisiz}")
    print(f"Dosyalar '{CIKTI_KLASORU}' klasorune kaydedildi.")

    # Simdi haberleri cekelim
    haberleri_isle()

    # Simdi etkinlikleri de cekelim
    etkinlikleri_isle()

    # Simdi tesisleri de cekelim
    tesisleri_isle()

    # Simdi projeleri de cekelim
    projeleri_isle()

    # Simdi genclik/spor kurslarini da cekelim
    kurslari_isle()

    # Simdi yayinlari da cekelim (sadece 2026)
    yayinlari_isle()

    print(f"\nTUM ISLEM TAMAMLANDI. Dosyalar '{CIKTI_KLASORU}' klasorunde.")


if __name__ == "__main__":
    main()