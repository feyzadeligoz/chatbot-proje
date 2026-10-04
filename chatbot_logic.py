"""
chatbot_logic.py

app_v2.py'deki gercek is mantigi, Flask'in cagirabilecegi bir fonksiyona donusturuldu.
Graph ve duyuru verileri sunucu ACILIRKEN bir kere yuklenir (her soru icin degil).
"""

import hashlib
import json
import os
import re
import time
import calendar
import difflib
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings

# ---- Ayarlar ----
client = genai.Client(
    http_options=types.HttpOptions(timeout=30_000)
)

DUYURU_KLASORU = "duyuru_verileri"

TURKCE_AYLAR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
}


def kaynak_dosya_icerigini_oku(dosya_adi, klasor=DUYURU_KLASORU):
    """
    graph.json'daki bir node'un 'source_file' alaninda yazan gercek .txt dosyasini
    duyuru_verileri klasorunden okur ve icerigini (Baslik/Tarih disindaki gercek metni) doner.
    Dosya bulunamazsa None doner.
    """
    if not dosya_adi:
        return None

    yol = os.path.join(klasor, dosya_adi)
    if not os.path.isfile(yol):
        return None

    try:
        with open(yol, "r", encoding="utf-8") as f:
            icerik = f.read()
        return icerik.strip()
    except Exception:
        return None


def link_alanlarini_cikart(icerik):
    """
    Dosya icerigindeki TUM link turlerini cikarir:
    Link (ana sayfa), BasvuruLink, BagkartLink, SSSLink
    Sadece dosyada GERCEKTEN var olanlari doner (yoksa o anahtar hic olusmaz).
    """
    sonuc = {}
    if not icerik:
        return sonuc

    alan_isimleri = {
        "Link": "Ayrıntılı bilgi için tıklayın",
        "BasvuruLink": "Başvuru için tıklayın",
        "BagkartLink": "Bağkart başvurusu için tıklayın",
        "SSSLink": "Sıkça sorulan sorular için tıklayın",
    }

    for alan, etiket in alan_isimleri.items():
        match = re.search(rf"^{alan}:\s*(.+)$", icerik, re.MULTILINE)
        if match:
            sonuc[etiket] = match.group(1).strip()

    # ---- GENISLETILEBILIR EK LINKLER: ExtraLink1/ExtraLabel1, ExtraLink2/ExtraLabel2, ... ----
    # Bazi konularda (ornek: "Nasil Giderim") 2'den fazla farkli link gerekebiliyor
    # (Mobilliyet, IETT web sitesi gibi). Sabit isim yerine, ExtraLinkN + ExtraLabelN
    # ciftini kullanarak ISTEDIGIN KADAR ek link ekleyebilirsin (N=1,2,3...).
    for n in range(1, 6):  # en fazla 5 ek link destekleniyor, yeterli olmali
        link_match = re.search(rf"^ExtraLink{n}:\s*(.+)$", icerik, re.MULTILINE)
        label_match = re.search(rf"^ExtraLabel{n}:\s*(.+)$", icerik, re.MULTILINE)
        if link_match and label_match:
            sonuc[label_match.group(1).strip()] = link_match.group(1).strip()

    return sonuc


# Her link etiketinin, cevap metninde hangi ANAHTAR KELIMELERI icerne satira
# eklenmesi gerektigini belirtir. Bu sayede link, Gemini'nin insafina kalmadan,
# BIZIM kodumuz tarafindan, ilgili cumlenin/satirin TAM SONUNA yerlestirilir.
ETIKET_ANAHTAR_KELIMELER = {
    "Bağkart başvurusu için tıklayın": ["bağkart", "bagkart", "bağ kart"],
    "Başvuru için tıklayın": ["başvuru", "basvuru", "kayıt", "kayit"],
    "Sıkça sorulan sorular için tıklayın": ["sıkça sorulan", "sikca sorulan", "sss"],
    # "Ayrıntılı bilgi için tıklayın" icin ozel anahtar kelime yok - genel/tanim
    # cumlesine (ilk anlamli satira) eklenir, asagida ozel olarak ele alinir.
}


def linkleri_cevaba_yerlestir(bot_cevabi, bulunan_linkler):
    """
    Gemini'nin urettigi cevaba, linkleri BIZ, ilgili CUMLENIN TAM SONUNA ekleriz.

    ONEMLI TASARIM KARARI: Satirin TAMAMINA degil, sadece o satirdaki ILK CUMLENIN
    (nokta/unlem/soru isaretine kadar olan kismin) sonuna ekliyoruz. Satirda cumleden
    sonra baska bir seyler DAHA varsa (ornek: Gemini bir basligi ayni satira, bold
    olsun ya da olmasin, satir atlamadan yapistirmis olabilir), o kismi AYRI BIR SATIRA
    itiyoruz. Boylece link asla "cumle + baslik" karisimina yapismaz, baslik bold olsun
    ya da olmasin fark etmez - cunku biz satirin ICERIGINE degil, CUMLE YAPISINA bakiyoruz.
    """
    if not bulunan_linkler:
        return bot_cevabi

    kalan_linkler = dict(bulunan_linkler)  # kullanildikca buradan silinecek
    orijinal_satirlar = bot_cevabi.split("\n")

    def baslik_satiri_mi(satir):
        """Bu satir SADECE bir kalin baslik mi (ornek: '**Başvuru:**' veya '**Başvuru**:')?"""
        return bool(re.match(r"^(\*\*[^*]+:\*\*|\*\*[^*]+\*\*:)$", satir.strip()))

    def satira_link_ekle(satir, marker):
        """Satiri, ILK CUMLENIN sonuna link ekleyip, varsa GERI KALANI ayri bir
        satir olarak dondurur. Boylece cumle sonrasi baska metin (baslik vs.) varsa,
        link ile o metin arasina otomatik satir atlamasi girmis olur."""
        m = re.match(r"^(.*?[.!?])(\s+)(.*)$", satir, re.DOTALL)
        if m and m.group(3).strip():
            return [m.group(1) + " " + marker, m.group(3)]
        return [satir.rstrip() + " " + marker]

    # ---- 1. GECIS: anahtar kelimesi olan linkleri, baslik OLMAYAN satirlarda ara ----
    sonuc = []
    for satir in orijinal_satirlar:
        eklenecek = None
        for etiket, anahtarlar in ETIKET_ANAHTAR_KELIMELER.items():
            if etiket in kalan_linkler and satir.strip() and not baslik_satiri_mi(satir):
                if any(a in satir.lower() for a in anahtarlar):
                    eklenecek = etiket
                    break
        if eklenecek:
            marker = f"||LINK:{eklenecek}||{kalan_linkler[eklenecek]}"
            sonuc.extend(satira_link_ekle(satir, marker))
            del kalan_linkler[eklenecek]
        else:
            sonuc.append(satir)

    # ---- 2. GECIS: hala yerlesmemis anahtar kelimeli linkler icin, baslik satirlari
    # dahil tekrar ara (belki sadece basligin icinde geciyordur) ----
    if kalan_linkler:
        yeni_sonuc = []
        for satir in sonuc:
            eklenecek = None
            if satir.strip():
                for etiket, anahtarlar in ETIKET_ANAHTAR_KELIMELER.items():
                    if etiket in kalan_linkler and any(a in satir.lower() for a in anahtarlar):
                        eklenecek = etiket
                        break
            if eklenecek:
                marker = f"||LINK:{eklenecek}||{kalan_linkler[eklenecek]}"
                yeni_sonuc.extend(satira_link_ekle(satir, marker))
                del kalan_linkler[eklenecek]
            else:
                yeni_sonuc.append(satir)
        sonuc = yeni_sonuc

    # ---- 3. GECIS: anahtar kelimesiz (genel, ornek "Ayrıntılı bilgi için tıklayın")
    # linkleri ARTIK CUMLE ICINE SOKMUYORUZ - garanti olsun diye direkt EN SONA,
    # kendi satirinda ekliyoruz. CIFT satir atlamasi (\n\n) kullaniyoruz - boylece
    # onceki icerikten VE birbirinden GORSEL OLARAK KESIN ayrisirlar.
    for etiket, url in kalan_linkler.items():
        sonuc.append("")  # bos satir - onceki icerikten ayirmak icin
        sonuc.append(f"||LINK:{etiket}||{url}")

    return "\n".join(sonuc)


# Kullanicilarin gunluk dilde kullandigi ama node basliklariyla BIREBIR ORTUSMEYEN
# kelimeler icin kucuk bir "es anlamli" sozlugu. Bir soruda soldaki kelime gecerse,
# arama sirasinda sagdaki kelime(ler) de otomatik olarak eklenir.
# ZAMANLA GENISLETILEBILIR: yanlis eslesen yeni bir kelime fark edilince buraya eklenir.
ES_ANLAMLILAR = {
    "havuz": ["yüzme"],
    "yüzme havuzu": ["yüzme"],
    "diyet": ["diyetisyen"],
    "beslenme": ["diyetisyen"],
    "dövüş": ["karate", "taekwondo", "kick", "boks", "wushu"],
    "kayıt": ["kurs", "başvuru"],
    "kağıt": ["ambalaj"],
    "karton": ["ambalaj"],
    "plastik": ["ambalaj"],
    "cam şişe": ["ambalaj"],
    "metal kutu": ["ambalaj"],
    "kıyafet": ["giysi"],
    "elbise": ["giysi"],
    "lastik": ["ömrünü tamamlamış"],
    "başkan": ["başkanı"],  # YENİ: "başkan" sorusunda "Belediye Başkanı" basligi da eslessin
    "konumu": ["konumumuz"],  # YENİ: "coğrafi konumu" sorusunda "Coğrafi Konumumuz" basligi da eslessin
}


def en_iyi_node_bul(soru, nodes):
    """
    Gemini'YE SORMADAN, kullanicinin sorusuyla EN ALAKALI node'u kendi kodumuzla bulur.
    Bu sayede her soru icin harcanan Gemini istegi 2'den 1'e duser (kota 2 kat artmis gibi olur).

    Mantik: sorunun icindeki kelimelerle, her node'un basligindaki (norm_label) kelimeleri
    karsilastirir, en cok ortusen / en benzer olani secer.
    """
    soru_kucuk = soru.lower().strip()

    # YENİ: Soru SADECE "bağcılar" kelimesinden ibaretse (baska hicbir kelime yoksa),
    # bu COK GENEL bir soru oldugu icin (SGK, muhtarlar, tarih gibi rastgele bir node'a
    # dusmek yerine) dogrudan "İlçemizi Tanıyalım" genel tanitim node'una yonlendiriyoruz.
    if soru_kucuk in ("bağcılar", "bagcilar", "bağcılar?", "bagcilar?"):
        for node in nodes:
            if node.get("source_file") == "KURUMSAL_Ilcemizi_Taniyalim.txt":
                return node, "yuksek"

    # YENİ: "bağcılar belediyesi" gibi kisa, genel bir soru sorulduysa (adres/nasil
    # giderim gibi spesifik bir ek olmadan), genel tanitim node'una yonlendiriyoruz.
    if soru_kucuk in (
        "bağcılar belediyesi", "bagcilar belediyesi",
        "bağcılar belediyesi hakkında", "bagcilar belediyesi hakkında",
        "bağcılar belediyesi hakkında her şey", "bağcılar belediyesi hakkında herşey",
    ):
        for node in nodes:
            if node.get("source_file") == "KURUMSAL_Bagcilar_Belediyesi_Hakkinda.txt":
                return node, "yuksek"

    # Es anlamli kelimeleri sessizce sorunun sonuna ekliyoruz (kullaniciya gorunmez)
    for anahtar_kelime, esanlamlilar in ES_ANLAMLILAR.items():
        if anahtar_kelime in soru_kucuk:
            soru_kucuk += " " + " ".join(esanlamlilar)

    soru_kelimeleri = set(re.findall(r"\w+", soru_kucuk))

    # YENİ - ONEMLI DUZELTME: "e", "nedir", "mi", "ve", "ile" gibi cok GENEL/
    # ANLAMSIZ kelimeler, iki alakasiz konu arasinda bile TESADUFEN ortak
    # cikabiliyor (ornek: "e-imar NEDİR" ile "e-belediye NEDİR" arasinda "e" ve
    # "nedir" ortak ama bu GERCEK bir konu eslesmesi degil). Bu kelimeleri "ortak
    # kelime sayisi" hesabina KATMIYORUZ, yoksa yanlislikla "2 ortak kelime var,
    # yeterince eminim" diyip yanlis node'a guvenebiliyorduk.
    TR_DURAK_KELIMELERI = {
        "e", "ne", "nedir", "mi", "mı", "mu", "mü", "midir", "mıdır",
        "ve", "veya", "ile", "için", "bir", "bu", "şu", "o", "da", "de", "ki", "gibi",
        "var", "yok", "nasıl", "hangi", "kim", "kimdir", "nerede", "nerde", "ne zaman",
    }

    # Soruda "branş/kurs/başvuru/kayıt" gibi ISLEMSEL kelimeler geciyorsa, bu genelde
    # kullanicinin bir TESIS/binadan degil, bir KURS/BRANS sayfasindan bahsettigini gosterir
    # (ornek: "havuz KAYIT" -> Yuzme kursu sayfasi, "havuz" tek basina -> Havuz tesisi/binasi).
    KURS_ONCELIK_KELIMELERI = {"branş", "brans", "kurs", "başvuru", "basvuru", "kayıt", "kayit"}
    kurs_oncelikli_mi = bool(soru_kelimeleri & KURS_ONCELIK_KELIMELERI)

    # YENİ: "başkan" sorularinda, "yardımcı" kelimesi GECIP GECMEDIGINE gore dogru
    # tarafa (Baskan'in kendisi mi, yoksa Baskan Yardimcilari mi) oncelik ver.
    BASKAN_YARDIMCISI_KELIMELERI = {"yardımcı", "yardimci", "yardımcısı", "yardimcisi"}
    soru_baskan_iceriyor_mu = bool(soru_kelimeleri & {"başkan", "başkanı", "baskan", "baskani"})
    soru_yardimci_iceriyor_mu = bool(soru_kelimeleri & BASKAN_YARDIMCISI_KELIMELERI)

    en_iyi_node = None
    en_iyi_skor = 0.0
    en_iyi_ortak_kelime_sayisi = 0
    en_iyi_substring_eslesme = False

    for node in nodes:
        norm_label = (node.get("norm_label") or node.get("label") or "").lower()
        if not norm_label:
            continue

        # 1. Dogrudan kelime ortusmesi (kac ortak kelime var)
        node_kelimeleri = set(re.findall(r"\w+", norm_label))
        ortak_kelime_sayisi = len((soru_kelimeleri & node_kelimeleri) - TR_DURAK_KELIMELERI)

        # 2. Genel metin benzerligi (difflib ile, kucuk yazim farklarini da yakalar)
        benzerlik = difflib.SequenceMatcher(None, soru_kucuk, norm_label).ratio()

        # Iki puani birlestiriyoruz: ortak kelime sayisi agirlikli, benzerlik destekleyici
        toplam_skor = (ortak_kelime_sayisi * 2.0) + benzerlik

        # Basligin sorunun icinde gecen bir alt-metin olmasi cok guclu bir isaret, ekstra puan
        substring_eslesme_mi = norm_label in soru_kucuk or soru_kucuk in norm_label
        if substring_eslesme_mi:
            toplam_skor += 3.0

        # Islemsel kelime varsa VE bu node bir Kurs ise, oncelik puani ver (Tesis'e karsi)
        if kurs_oncelikli_mi and node.get("community_name") == "Kurs":
            toplam_skor += 2.5

        # YENİ: "baskan" sorusunda, yardimci kelimesi yoksa Baskan'in KENDI node'unu
        # kayir; yardimci kelimesi varsa Baskan Yardimcilari node'larini kayir.
        if soru_baskan_iceriyor_mu and node.get("community_name") == "Kurumsal":
            node_yardimci_mi = "yardımcı" in norm_label or "yardimci" in norm_label
            if soru_yardimci_iceriyor_mu and node_yardimci_mi:
                toplam_skor += 2.5
            elif not soru_yardimci_iceriyor_mu and not node_yardimci_mi:
                toplam_skor += 2.5

        if toplam_skor > en_iyi_skor:
            en_iyi_skor = toplam_skor
            en_iyi_node = node
            en_iyi_ortak_kelime_sayisi = ortak_kelime_sayisi
            en_iyi_substring_eslesme = substring_eslesme_mi

    # Cok dusuk skorlu (alakasiz) eslesmeleri eleme - bir esik koyuyoruz
    if en_iyi_skor < 0.9:
        return None, "yok"

    # YENİ - ONEMLI DUZELTME: Sadece TEK bir ortak kelime (ozellikle genel/sik gecen
    # bir kelime, ornek: "geri", "kontrol") yuzunden YANLIS bir node'a yanlislikla
    # guvenmeyelim. Bir eslesmeyi KABUL ETMEK icin ya en az 2 ortak ANLAMLI kelime
    # olmali, ya da basligin sorunun icinde (veya tam tersi) GECTIGI bir substring
    # eslesmesi olmali (bu, "muhtarlar" gibi TEK kelimelik ama TAM eslesen basliklari
    # hala kabul etmemizi saglar). Bu iki sart da saglanmiyorsa, kelime eslestirmesi
    # YETERINCE EMIN DEGIL demektir - None donup, cagiran fonksiyonun (soru_cevap)
    # bunun yerine VECTOR DATABASE'e (anlam bazli arama) basvurmasini sagliyoruz.
    yeterince_emin_mi = (en_iyi_ortak_kelime_sayisi >= 2) or en_iyi_substring_eslesme
    if not yeterince_emin_mi:
        return None, "yok"

    # YENİ - HIBRIT SKORLAMA ICIN: eslesmenin ne kadar GUCLU oldugunu ikiye ayiriyoruz.
    # "yuksek" guven -> tam substring eslesmesi VAR ya da 3+ ortak anlamli kelime var.
    # Bu durumda vector database'e HIC BASVURMUYORUZ (hizli+ucretsiz, zaten guvenilir).
    # "orta" guven -> tam olarak esik degerini karsiliyor (2 ortak kelime, substring
    # YOK). Bu SINIRDA/belirsiz durumlarda, cagiran fonksiyon (soru_cevap) vector
    # database'e de bakip, o daha netse SONUCU DEGISTIREBILIR (capraz kontrol).
    if en_iyi_substring_eslesme or en_iyi_ortak_kelime_sayisi >= 3:
        guven_seviyesi = "yuksek"
    else:
        guven_seviyesi = "orta"

    return en_iyi_node, guven_seviyesi


# NOT: Istanbul Eczaci Odasi'nin JSON API'si denendi, ama istekte JS tarafinda
# dinamik uretilen bir dogrulama tokeni ("h" parametresi) gerektirdigi icin
# guvenilir sekilde taklit edilemedi. Bu yuzden Bagcilar Belediyesi'nin kendi
# rapor sayfasina GERI DONULDU. Bu kaynagin bilinen bir sinirlamasi var: gunun
# BASINDA henuz o gunun listesine guncellenmemis olabiliyor - bunu asagida
# DURUSTCE bir uyari ile kullaniciya bildiriyoruz (veriyi gizlemeden).
ECZANE_URL = "https://bbgis.bagcilar.bel.tr/belnet/genericclass/rapor.aspx?raporadi=eczane_nobet.eczane_nobet_rapor&nomaster=true&guestlogin=true"


def eczane_tarihini_parse_et(tarih_metni):
    """
    Nobetci eczane sitesinden gelen tarih metnini (ornek: '2.08.2026' - gunun
    basinda sifir OLMAYABILIR) bir date nesnesine cevirir. Parse edilemezse
    None doner.
    """
    m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", tarih_metni or "")
    if not m:
        return None
    gun, ay, yil = map(int, m.groups())
    try:
        return datetime(yil, ay, gun).date()
    except ValueError:
        return None


def eczane_sorusu_mu(soru):
    """Soru nobetci eczane ile mi ilgili, basit anahtar kelime kontrolu"""
    soru_kucuk = soru.lower()
    return "eczane" in soru_kucuk


def nobetci_eczaneleri_getir():
    """
    Nobetci eczane listesini CANLI olarak (her seferinde taze) ceker - hicbir dosyada
    SAKLAMIYORUZ, cunku bu bilgi HER GUN degisiyor. Gemini/API kullanmiyor, tamamen
    ucretsiz ve aninda calisir.

    Cache-busting: URL'ye HER SEFERINDE farkli bir zaman damgasi ekliyoruz, ayrica
    "bu sayfayi onbellekten verme" diyen HTTP basliklari gonderiyoruz - ara
    sunucularin (varsa) eski bir kopyayi donmesini engellemek icin.
    """
    cache_atlatma_url = f"{ECZANE_URL}&_ts={int(time.time())}"
    istek_basliklari = {
        "User-Agent": "Mozilla/5.0",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
    }

    try:
        response = requests.get(cache_atlatma_url, headers=istek_basliklari, timeout=15)
    except Exception:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    tablo = soup.find("table")
    if not tablo:
        return None

    # Hucrelerin icinde, mobil gorunum icin eklenmis GIZLI baslik etiketleri var
    # (ornek: "Eczane AdıBEŞİKCİ ECZANESİ" - basinda "Eczane Adı" yapisik geliyor).
    # Bunlari temizliyoruz.
    GIZLI_BASLIKLAR = ["Eczane Adı", "Nöbet Tarihi", "Telefon No", "Adres"]

    def hucre_temizle(hucre, sutun_no):
        metin = hucre.get_text(strip=True)
        baslik = GIZLI_BASLIKLAR[sutun_no]
        if metin.startswith(baslik):
            metin = metin[len(baslik):].strip()
        return metin

    eczaneler = []
    for satir in tablo.find_all("tr")[1:]:  # ilk satir baslik, atla
        hucreler = satir.find_all("td")
        if len(hucreler) < 4:
            continue
        eczaneler.append({
            "isim": hucre_temizle(hucreler[0], 0),
            "tarih": hucre_temizle(hucreler[1], 1),
            "telefon": hucre_temizle(hucreler[2], 2),
            "adres": hucre_temizle(hucreler[3], 3),
        })

    return eczaneler if eczaneler else None



AFET_URL = "https://bbgis.bagcilar.bel.tr/BELNET/GenericClass/Rapor.aspx?RaporAdi=afad_toplanma.toplanma_alanlari&nomaster=true&guestlogin=true"

# Bagcilar'daki bilinen mahalle isimleri - soruda hangi mahallenin gectigini
# tespit etmek icin kullaniliyor.
BAGCILAR_MAHALLELERI = [
    "15 temmuz", "100. yıl", "100 yıl", "bağlar", "barbaros", "çınar", "demirkapı",
    "fatih", "fevzi çakmak", "fevziçakmak", "göztepe", "güneşli", "hürriyet",
    "inönü", "kazımkarabekir", "kazım karabekir", "kemalpaşa", "kirazlı",
    "mahmutbey", "merkez", "sancaktepe", "yavuzselim", "yavuz selim",
    "yenigün", "yenimahalle", "yıldıztepe",
]


def afet_sorusu_mu(soru):
    """Soru afet toplanma alanlari ile mi ilgili"""
    soru_kucuk = soru.lower()
    return "toplanma" in soru_kucuk or ("afet" in soru_kucuk and "alan" in soru_kucuk)


def mahalle_bul(soru):
    """Soruda gecen bilinen bir mahalle ismini bulur (yoksa None doner)"""
    soru_kucuk = soru.lower()
    for mahalle in BAGCILAR_MAHALLELERI:
        if mahalle in soru_kucuk:
            return mahalle
    return None


def afet_alanlarini_getir():
    """
    Afet toplanma alanlari listesini CANLI olarak ceker (dosyada saklamiyoruz,
    Gemini/API kullanmiyor). Her satirda: mahalle, alan adi, google maps linki.
    """
    try:
        response = requests.get(AFET_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    except Exception:
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    satirlar = soup.find_all("tr", class_="netdatarow")
    if not satirlar:
        return None

    sonuc = []
    for satir in satirlar:
        veri = {}
        for cc in satir.find_all("div", class_="cellContainer"):
            lbl = cc.find("div", class_="lbldisplay")
            val = cc.find("div", class_="viewercont")
            if not lbl or not val:
                continue
            etiket = lbl.get_text(strip=True)
            if etiket == "Mahalle Adı":
                veri["mahalle"] = val.get_text(strip=True)
            elif etiket == "Toplanma Alanı":
                veri["alan"] = val.get_text(strip=True)
            elif etiket == "Nasıl Giderim ?":
                a = val.find("a")
                if a and a.get("href"):
                    veri["harita_url"] = a["href"]

        if "mahalle" in veri and "alan" in veri and "harita_url" in veri:
            sonuc.append(veri)

    return sonuc if sonuc else None


# Bir kategori kelimesi gecen "liste" sorularinda, TEK bir node yerine O KATEGORIDEKI
# TUM node'lari bulup listelemek icin kullanilan anahtar kelimeler.
# ZAMANLA GENISLETILEBILIR: yeni bir kategori (park, kutuphane, kafe vb.) icin buraya eklenir.
def yayin_listesi_sorusu_mu(soru):
    """Soru yayinlar/bultenler ile mi ilgili, basit anahtar kelime kontrolu"""
    soru_kucuk = soru.lower()
    liste_ifadesi_var = any(k in soru_kucuk for k in ["liste", "listesi", "listele", "hangi", "neler var", "nelerdir", "tüm", "tümü", "tum", "tumu"])
    yayin_kelimesi_var = any(k in soru_kucuk for k in ["yayın", "yayin", "bülten", "bulten", "sempozyum"])
    # Soru TEK BASINA sadece "yayınlar" / "sempozyumlar" gibi ise (liste ifadesi
    # olmasa bile) direkt liste gostermek en dogal davranis.
    tek_kelime_soru_mu = soru_kucuk.strip() in ("yayınlar", "yayinlar", "yayın", "yayin", "sempozyumlar", "sempozyum")
    return (liste_ifadesi_var and yayin_kelimesi_var) or tek_kelime_soru_mu


def yayin_listesini_olustur(nodes):
    """TUM Yayin turundeki node'lari (baslikta 'yayin' kelimesi GECMESE bile,
    Tur alanindan) bulup Gemini'ye SORMADAN direkt listeler."""
    eslesenler = [node.get("label") for node in nodes if node.get("community_name") == "Yayin"]

    if not eslesenler:
        return "Şu an sistemimde kayıtlı bir yayın/bülten bulunmuyor."

    liste_metni = "\n".join(f"- {isim}" for isim in eslesenler)
    return (
        f"Bağcılar Belediyesi'nin güncel yayınları/bültenleri şunlardır:\n\n{liste_metni}\n\n"
        f"Bunlardan biri hakkında detaylı bilgi (PDF linki gibi) almak istersen, adını yazman yeterli."
    )


def proje_listesi_sorusu_mu(soru):
    """Soru projeler ile mi ilgili, basit anahtar kelime kontrolu"""
    soru_kucuk = soru.lower().strip()
    liste_ifadesi_var = any(k in soru_kucuk for k in ["liste", "listesi", "listele", "hangi", "neler var", "nelerdir", "tüm", "tümü", "tum", "tumu"])
    proje_kelimesi_var = "proje" in soru_kucuk or "projeler" in soru_kucuk
    # Soru TEK BASINA sadece "projeler" / "proje" / "projelerimiz" ise de (liste ifadesi
    # olmasa bile) direkt liste gostermek en dogal davranis.
    tek_kelime_soru_mu = soru_kucuk in ("projeler", "proje", "projelerimiz")
    return (liste_ifadesi_var and proje_kelimesi_var) or tek_kelime_soru_mu


def proje_listesini_olustur(nodes):
    """TUM Proje turundeki node'lari bulup Gemini'ye SORMADAN direkt listeler."""
    eslesenler = [node.get("label") for node in nodes if node.get("community_name") == "Proje"]

    if not eslesenler:
        return "Şu an sistemimde kayıtlı bir proje bulunmuyor."

    liste_metni = "\n".join(f"- {isim}" for isim in eslesenler)
    return (
        f"Bağcılar Belediyesi'nin tamamlanan/devam eden projeleri şunlardır:\n\n{liste_metni}\n\n"
        f"Bunlardan biri hakkında detaylı bilgi almak istersen, adını yazman yeterli."
    )


def mudurler_sorusu_mu(soru):
    """Soru TUM mudurlukleri listelemek ile mi ilgili, basit anahtar kelime kontrolu.
    NOT: 'müdürler'/'müdürlükler' (COGUL) arandigi icin, 'bilgi işlem müdürlüğü'
    gibi TEKIL sorular (sadece 'müdürlüğü' iceren) buraya YANLISLIKLA dusmez."""
    soru_kucuk = soru.lower().strip()
    liste_ifadesi_var = any(k in soru_kucuk for k in ["liste", "listesi", "listele", "hangi", "neler var", "nelerdir", "tüm", "tümü", "tum", "tumu"])
    mudur_kelimesi_var = "müdürler" in soru_kucuk or "müdürlükler" in soru_kucuk
    tek_kelime_soru_mu = soru_kucuk in ("müdürler", "müdürlükler", "müdürlüklerimiz", "tüm müdürler", "tüm müdürlükler")
    return (liste_ifadesi_var and mudur_kelimesi_var) or tek_kelime_soru_mu


def mudurler_listesini_olustur(nodes):
    """
    TUM Mudurluk node'larini bulup, her birinin KAYNAK DOSYASINI okuyarak
    icindeki "Müdürlüğün başında ... bulunmaktadır" cumlesinden MUDURUN ADINI
    cikarir ve "Mudurluk: Isim" seklinde listeler (sadece birim adini degil,
    KISI isimlerini gostermek icin - kullanicinin asil sordugu bu).
    """
    mudurluk_nodeleri = [
        node for node in nodes
        if node.get("community_name") == "Kurumsal" and "müdürlüğü" in (node.get("label") or "").lower()
    ]

    if not mudurluk_nodeleri:
        return "Şu an sistemimde kayıtlı bir müdürlük bulunmuyor."

    satirlar = []
    for node in sorted(mudurluk_nodeleri, key=lambda n: n.get("label") or ""):
        baslik = node.get("label")
        kaynak_dosya = node.get("source_file")
        icerik = kaynak_dosya_icerigini_oku(kaynak_dosya) if kaynak_dosya else None

        isim = None
        if icerik:
            # ONEMLI DUZELTME: Dosyalarda uzun isimler bazen SATIR SONUNDA
            # bolunmus olabiliyor (ornek: "başında Ali İhsan\nÖztürk bulunmaktadır").
            # re.DOTALL kullanarak "." karakterinin satir sonlarini da (\n) kapsamasini
            # sagliyoruz, yoksa bolunmus isimler eksik/yanlis yakalaniyordu.
            m = re.search(r"başında\s+(.+?)\s+bulunmaktadır", icerik, re.IGNORECASE | re.DOTALL)
            if m:
                isim = re.sub(r"\s+", " ", m.group(1)).strip()

        if isim:
            satirlar.append(f"- {baslik}: {isim}")
        else:
            satirlar.append(f"- {baslik}")

    liste_metni = "\n".join(satirlar)
    return (
        f"Bağcılar Belediyesi'nin müdürleri şunlardır:\n\n{liste_metni}\n\n"
        f"Bunlardan biri hakkında detaylı bilgi (hangi başkan yardımcısına bağlı "
        f"olduğu gibi) almak istersen, müdürlüğün adını yazman yeterli."
    )


def baskan_yardimcilari_sorusu_mu(soru):
    """Soru TUM baskan yardimcilarini listelemek ile mi ilgili, basit anahtar
    kelime kontrolu. NOT: 'yardımcıları' (COGUL/iyelik) arandigi icin, 'imar
    müdürlüğü hangi başkan yardımcısına bağlı' gibi TEKIL sorular buraya
    YANLISLIKLA dusmez."""
    soru_kucuk = soru.lower().strip()
    liste_ifadesi_var = any(k in soru_kucuk for k in ["liste", "listesi", "listele", "hangi", "neler var", "nelerdir", "tüm", "tümü", "tum", "tumu", "kimlerdir", "kimler"])
    kelime_var = "başkan yardımcıları" in soru_kucuk or "başkan yardımcılarımız" in soru_kucuk
    tek_kelime_soru_mu = soru_kucuk in ("başkan yardımcıları", "başkan yardımcılarımız", "tüm başkan yardımcıları")
    return (liste_ifadesi_var and kelime_var) or tek_kelime_soru_mu


def baskan_yardimcilari_listesini_olustur(nodes):
    """
    TUM Baskan Yardimcisi (kisi) node'larini bulup, her birinin kaynak dosyasindan
    kendisine bagli mudurlukleri ("... kendisine bağlıdır.") cikararak listeler.
    """
    yardimci_nodeleri = [
        node for node in nodes
        if node.get("community_name") == "Kurumsal" and "başkan yardımcısı" in (node.get("label") or "").lower()
    ]

    if not yardimci_nodeleri:
        return "Şu an sistemimde kayıtlı bir başkan yardımcısı bulunmuyor."

    satirlar = []
    for node in sorted(yardimci_nodeleri, key=lambda n: n.get("label") or ""):
        baslik = node.get("label")
        isim = baslik.split(" - ")[0].strip() if baslik else baslik
        kaynak_dosya = node.get("source_file")
        icerik = kaynak_dosya_icerigini_oku(kaynak_dosya) if kaynak_dosya else None

        mudurlukler_cumlesi = None
        if icerik:
            m = re.search(r"([^.]*?kendisine bağlıdır\.)", icerik, re.IGNORECASE | re.DOTALL)
            if m:
                mudurlukler_cumlesi = re.sub(r"\s+", " ", m.group(1)).strip()

        if mudurlukler_cumlesi:
            satirlar.append(f"- {isim}: {mudurlukler_cumlesi}")
        else:
            satirlar.append(f"- {isim}")

    liste_metni = "\n".join(satirlar)
    return (
        f"Bağcılar Belediyesi'nin başkan yardımcıları şunlardır:\n\n{liste_metni}\n\n"
        f"Bunlardan biri hakkında detaylı bilgi almak istersen, adını yazman yeterli."
    )


KATEGORI_ANAHTAR_KELIMELERI = ["havuz", "kütüphane", "kutuphane", "bilgi evi", "gençlik merkezi", "genclik merkezi"]


def liste_sorusu_mu(soru):
    """Soru 'liste/listesi/hangi...var' + bir kategori kelimesi iceriyor mu, kontrol eder.
    Iceriyorsa, o kategori kelimesini doner (yoksa None doner)."""
    soru_kucuk = soru.lower()
    liste_ifadesi_var = any(k in soru_kucuk for k in ["liste", "listesi", "listele", "hangi", "neler var", "nelerdir", "tüm", "tümü", "tum", "tumu"])
    if not liste_ifadesi_var:
        return None

    for kategori in KATEGORI_ANAHTAR_KELIMELERI:
        if kategori in soru_kucuk:
            return kategori
    return None


def kategori_listesini_olustur(kategori, nodes):
    """Verilen kategori kelimesini basliginda gecen TUM node'lari bulup, Gemini'ye SORMADAN
    direkt bir liste metni olusturur (hem hizli hem ucretsiz).
    Once SADECE Tesis turundekilere bakar (daha temiz sonuc icin), bulamazsa herkese bakar."""
    def eslesenleri_bul(sadece_tesis):
        return [
            node.get("label") for node in nodes
            if kategori in (node.get("norm_label") or "").lower()
            and (not sadece_tesis or node.get("community_name") == "Tesis")
        ]

    eslesenler = eslesenleri_bul(sadece_tesis=True)
    if not eslesenler:
        eslesenler = eslesenleri_bul(sadece_tesis=False)

    if not eslesenler:
        return f"'{kategori}' ile ilgili kayıtlı bir tesis/bilgi bulamadım."

    liste_metni = "\n".join(f"- {isim}" for isim in eslesenler)
    return (
        f"Bağcılar'daki '{kategori}' ile ilgili tesisler şunlardır:\n\n{liste_metni}\n\n"
        f"Bunlardan biri hakkında detaylı bilgi (adres gibi) almak istersen, adını yazman yeterli."
    )


def gemini_sor(prompt, max_deneme=4):
    """Gemini'ye sorar, gecici sunucu hatalarinda (503 gibi) veya zaman asiminda otomatik tekrar dener.
    ONEMLI: Kota dolmasi (429) hatasinda TEKRAR DENEMEZ - cunku bu GUNLUK bir limit,
    birkac saniye/dakika sonra tekrar denemek sonucu degistirmez, sadece bosuna istek harcar."""
    for deneme in range(1, max_deneme + 1):
        try:
            response = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt
            )

            # YENİ - ONEMLI DUZELTME: Gemini bazen response.text = None donuyor
            # (ornek: guvenlik filtresi icerigi engellediginde, ya da baska bir
            # sebeple bos yanit dondugunde). Bu GECICI bir hata DEGILDIR, yani
            # tekrar tekrar denemek soruna COZUM OLMAZ (her seferinde ayni sekilde
            # bos donebilir) - bu yuzden hemen tekrar denemek yerine, SEBEBINI
            # logluyoruz ve kullaniciya NAZIK bir fallback cevap donuyoruz.
            if response.text is None:
                sebep = None
                try:
                    sebep = response.candidates[0].finish_reason
                except Exception:
                    pass
                print(f"  [Uyari] Gemini bos/None yanit dondurdu (sebep: {sebep}). Fallback cevap donuluyor.")
                return (
                    "Bu konu hakkında şu anda net bir cevap oluşturamadım. Lütfen "
                    "sorunuzu biraz daha farklı bir şekilde ifade etmeyi deneyin, "
                    "ya da 0212 410 06 00 numaralı çağrı merkezimizi arayabilirsiniz."
                )

            return response.text.strip()
        except genai_errors.ClientError as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                print(f"  [HATA] Gunluk Gemini kotasi dolmus (429). Tekrar denenmiyor.")
                raise Exception(
                    "Bugünkü ücretsiz Gemini kotamız (günlük istek limiti) dolmuş durumda. "
                    "Bu, birkaç saniye beklemekle çözülmez - kota günlük olarak sıfırlanır. "
                    "Lütfen daha sonra tekrar deneyin."
                )
            print(f"  [Uyari] Beklenmeyen istemci hatasi (deneme {deneme}/{max_deneme}): {e}")
            time.sleep(5)
        except genai_errors.ServerError:
            bekleme = deneme * 5
            print(f"  [Uyari] Sunucu mesgul (deneme {deneme}/{max_deneme}), {bekleme} saniye bekleniyor...")
            time.sleep(bekleme)
        except Exception as e:
            print(f"  [Uyari] Beklenmeyen hata (deneme {deneme}/{max_deneme}): {type(e).__name__}: {e}")
            time.sleep(5)
    raise Exception("Gemini'ye birkac denemeden sonra ulasilamadi, lutfen birkac dakika sonra tekrar deneyin.")


def duyuru_tarihlerini_yukle(klasor):
    """duyuru_verileri klasorundeki her .txt dosyasindan baslik ve tarihi okur"""
    kayitlar = []
    if not os.path.isdir(klasor):
        return kayitlar

    for dosya_adi in os.listdir(klasor):
        if not dosya_adi.endswith(".txt"):
            continue
        yol = os.path.join(klasor, dosya_adi)
        try:
            with open(yol, "r", encoding="utf-8") as f:
                icerik = f.read()
        except Exception:
            continue

        baslik_match = re.search(r"^Baslik:\s*(.+)$", icerik, re.MULTILINE)
        # Iki farkli tarih formatini destekliyoruz:
        # 1. Noktali (Duyuru/Haber icin): "31.07.2026" -> gun.ay.yil
        # 2. Egik cizgili (Etkinlik kartlarinda site boyle veriyor): "7/31/2026 8:30:00 PM" -> ay/gun/yil
        tarih_match_nokta = re.search(r"^Tarih:\s*(\d{1,2})\.(\d{1,2})\.(\d{4})", icerik, re.MULTILINE)
        tarih_match_egik = re.search(r"^Tarih:\s*(\d{1,2})/(\d{1,2})/(\d{4})", icerik, re.MULTILINE)
        tur_match = re.search(r"^Tur:\s*(.+)$", icerik, re.MULTILINE)
        resim_match = re.search(r"^Resim:\s*(.+)$", icerik, re.MULTILINE)
        link_match = re.search(r"^Link:\s*(.+)$", icerik, re.MULTILINE)

        if not baslik_match:
            continue

        baslik = baslik_match.group(1).strip()
        tur = tur_match.group(1).strip() if tur_match else "Duyuru"
        resim = resim_match.group(1).strip() if resim_match else None
        link = link_match.group(1).strip() if link_match else None

        gun = ay = yil = None
        if tarih_match_nokta:
            gun, ay, yil = map(int, tarih_match_nokta.groups())
        elif tarih_match_egik:
            ay, gun, yil = map(int, tarih_match_egik.groups())  # egik format: AY/GUN/YIL

        tarih = None
        if gun is not None:
            try:
                tarih = datetime(yil, ay, gun)
            except ValueError:
                tarih = None

        # ONEMLI: Duyuru/Haber icin tarih ZORUNLU (tarihsizse anlamli sekilde
        # siralanamaz/filtrelenemez, atla). Ama ETKINLIK icin tarih bulunamasa BILE
        # kaydi ATLAMIYORUZ - bazi etkinlikler (cok gunlu seriler gibi) sayfada tek bir
        # tarih icermeyebiliyor, yine de "guncel/aktif" oldugu icin gosterilmeli.
        if tarih is None and tur != "Etkinlik":
            continue

        kayitlar.append({
            "baslik": baslik, "tarih": tarih, "tur": tur,
            "resim": resim, "link": link,
        })

    return kayitlar


def zaman_ifadesi_var_mi(soru):
    """Soruda 'bu ay', 'guncel', 'su an' gibi zaman ifadeleri VEYA etkinlik/haber/duyuru
    kelimelerinden herhangi biri var mi, basit anahtar kelime kontrolu"""
    soru_kucuk = soru.lower()
    tum_kelimeler = (
        GENEL_ZAMAN_KELIMELERI
        + KATEGORI_KELIMELERI["etkinlik"]
        + KATEGORI_KELIMELERI["haber"]
        + KATEGORI_KELIMELERI["duyuru"]
    )
    return any(kelime in soru_kucuk for kelime in tum_kelimeler)


def soruda_tam_node_eslesmesi_var_mi(soru, nodes):
    """
    Soru, herhangi bir node'un TAM basligini (norm_label) icinde barindiriyor mu?

    NEDEN GEREKLI: Bazi tesis/kurum isimlerinin ICINDE, tesadufen "etkinlik", "haber"
    veya "duyuru" gibi kelimeler geciyor olabilir (ornek: "... Cocuk Etkinlik Merkezi").
    Boyle bir ozel isim arandiginda, zaman_ifadesi_var_mi() bu kelimeyi yakalayip
    yanlislikla "guncel etkinlikleri listele" kisayoluna dusebiliyordu. Bu fonksiyon,
    sorunun aslinda UZUN ve OZEL bir node basligi oldugunu tespit edip, bu durumda
    zaman kisayolunun ATLANMASINI saglar - boylece doğru tesis/node bulunabilir.

    Sadece yeterince UZUN basliklari (>= 4 kelime) dikkate aliyoruz - kisa/genel
    basliklar (ornek: "Haberler", "Etkinlikler") yanlis pozitif tetiklemesin diye.
    """
    soru_kucuk = soru.lower().strip()
    for node in nodes:
        norm_label = (node.get("norm_label") or node.get("label") or "").lower()
        if not norm_label:
            continue
        if len(norm_label.split()) >= 4 and norm_label in soru_kucuk:
            return True
    return False


# Genel zaman ifadeleri - bunlar gecince TUM kategoriler (etkinlik+haber+duyuru) gosterilir.
GENEL_ZAMAN_KELIMELERI = ["bu ay", "güncel", "gundem", "gündem", "şu an", "su an",
                          "yakın zaman", "yakin zaman", "bu haftaki", "son duyuru"]

# Spesifik kategori kelimeleri - bunlardan SADECE biri gecerse, SADECE o kategori gosterilir.
KATEGORI_KELIMELERI = {
    "etkinlik": ["etkinlik", "etkinlikler"],
    "haber": ["haberler", "haber"],
    "duyuru": ["duyurular", "duyuru"],
}


def find_node_id(name, nodes):
    """Verilen isme gore node'un id'sini bulur (esnek arama)"""
    for node in nodes:
        label = node.get("label") or node.get("name")
        if label and label.strip().lower() == name.strip().lower():
            return node.get("id"), node
    return None, None


# ---- SUNUCU ACILIRKEN BIR KERE YUKLENEN VERILER ----
# (Flask uygulamasi acilinca bu dosya import edilir, asagidaki kod BIR KERE calisir)

with open("graph.json", "r", encoding="utf-8") as f:
    _graph = json.load(f)

_nodes = _graph.get("nodes", [])
_edges = _graph.get("links", [])
_node_names = [node.get("label") or node.get("name") or node.get("id") for node in _nodes]

print(f"[Sistem] Graph yuklendi: {len(_nodes)} node bulundu.")
print("[Sistem] chatbot_logic.py VERSIYON: 2026-07-25-v29 (eczane uyarisindaki link ||LINK|| isaretleyicisine cevrildi)")


# ---- VECTOR DATABASE (ChromaDB + Gemini embedding) ----
# NOT: Bu, mevcut Graphify/graph.json kelime eslestirme sistemini SILMIYOR/DEGISTIRMIYOR,
# ona EK bir katman olarak calisiyor. Mantik: once hizli/ucretsiz kelime eslestirmesi
# (en_iyi_node_bul) denenir; o BULAMAZSA (skor esigin altinda kalirsa), vector database
# devreye girip ANLAM bazli arama yapar. Boylece hem Graphify hem Vector DB bir arada
# kullanilmis olur.

_VECTOR_DB_KLASORU = "./chroma_veritabani"
_VECTOR_KOLEKSIYON_ADI = "bagcilar_bilgileri"
_OLASI_EMBEDDING_MODELLERI = [
    "models/gemini-embedding-001",
    "models/text-embedding-004",
]
_calisan_embedding_modeli = None
_vector_koleksiyon = None


def _dogru_embedding_modelini_bul():
    global _calisan_embedding_modeli
    if _calisan_embedding_modeli:
        return _calisan_embedding_modeli
    for model_adi in _OLASI_EMBEDDING_MODELLERI:
        try:
            client.models.embed_content(
                model=model_adi,
                contents=["test"],
                config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
            )
            _calisan_embedding_modeli = model_adi
            return model_adi
        except Exception:
            continue
    raise Exception("Hicbir embedding modeli calismadi.")


class _GeminiEmbeddingFonksiyonu(EmbeddingFunction):
    """
    NOT: Bu fonksiyon SADECE koleksiyonu ACARKEN ChromaDB'nin istedigi icin
    tanimlaniyor, GERCEKTE arama sirasinda KULLANILMIYOR - cunku arama
    sorgusunun embedding'ini vector_ile_node_bul() icinde KENDIMIZ,
    task_type="RETRIEVAL_QUERY" ile manuel hesaplayip dogrudan
    query_embeddings olarak veriyoruz (belge tarafinda kullanilan
    "RETRIEVAL_DOCUMENT" ile ESLESSIN diye - bu ikisi FARKLI vektor
    uretir, birbirine karistirilmamali).
    """

    def __call__(self, input: Documents) -> Embeddings:
        model_adi = _dogru_embedding_modelini_bul()
        sonuc = client.models.embed_content(
            model=model_adi,
            contents=input,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        return [e.values for e in sonuc.embeddings]


def _vector_koleksiyonunu_yukle():
    """Vector database baglantisini SUNUCU ACILIRKEN bir kere kurar (her soru icin degil)"""
    global _vector_koleksiyon
    if _vector_koleksiyon is not None:
        return _vector_koleksiyon

    if not os.path.isdir(_VECTOR_DB_KLASORU):
        print("[Sistem] UYARI: Vector veritabani klasoru bulunamadi, vector arama devre disi kalacak.")
        return None

    try:
        vc_client = chromadb.PersistentClient(path=_VECTOR_DB_KLASORU)
        _vector_koleksiyon = vc_client.get_or_create_collection(
            name=_VECTOR_KOLEKSIYON_ADI,
            embedding_function=_GeminiEmbeddingFonksiyonu(),
            metadata={"hnsw:space": "cosine"},  # ONEMLI: varsayilan L2 yerine kosinus benzerligi kullaniyoruz
        )
        print(f"[Sistem] Vector veritabani yuklendi ({_vector_koleksiyon.count()} kayit).")
        return _vector_koleksiyon
    except Exception as e:
        print(f"[Sistem] UYARI: Vector veritabani yuklenemedi ({type(e).__name__}: {e}), devre disi kalacak.")
        return None


# Sunucu acilirken vector database baglantisini bir kere kuruyoruz (Gemini istegi
# harcamadan, sadece ChromaDB dosyasini aciyor - embedding modeli sadece GERCEKTEN
# bir arama yapildiginda cagrilir).
_vector_koleksiyon = _vector_koleksiyonunu_yukle()


# Vector database'den donen sonucun GERCEKTEN alakali sayilmasi icin bir esik.
# ChromaDB "mesafe" (distance) doner - KUCUK deger DAHA alakali demektir.
# Bu esigin uzerindeki (yani cok alakasiz) sonuclar reddedilir.
_VECTOR_MESAFE_ESIGI = 0.4


def vector_ile_node_bul(soru):
    """
    Vector database'de ANLAM bazli arama yapar, en alakali node'u _nodes listesi
    icinden bulup doner (kelime eslestirme BULAMADIGINDA yedek/tamamlayici olarak
    kullanilir). Vector veritabani yuklenemediyse veya sonuc yeterince alakali
    degilse None doner.

    ONEMLI: Sorunun embedding'ini task_type="RETRIEVAL_QUERY" ile KENDIMIZ
    hesaplayip query_embeddings olarak veriyoruz (query_texts KULLANMIYORUZ,
    cunku o, koleksiyonun kendi embedding_function'ini cagirir ve o da
    "RETRIEVAL_DOCUMENT" tipini kullanir - ikisi FARKLI vektor uretir,
    soru ile belge arasinda dogru eslesme icin bu ayrim sart).
    """
    if _vector_koleksiyon is None:
        return None

    try:
        model_adi = _dogru_embedding_modelini_bul()
        soru_embedding_sonucu = client.models.embed_content(
            model=model_adi,
            contents=[soru],
            config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
        )
        soru_vektoru = soru_embedding_sonucu.embeddings[0].values

        sonuclar = _vector_koleksiyon.query(query_embeddings=[soru_vektoru], n_results=1)
    except Exception as e:
        print(f"[Vector arama hatasi] {type(e).__name__}: {e}")
        return None

    if not sonuclar.get("ids") or not sonuclar["ids"][0]:
        return None

    en_iyi_id = sonuclar["ids"][0][0]  # bu, dosya adi (source_file) ile ayni
    en_iyi_mesafe = sonuclar["distances"][0][0]

    # DEBUG: Vector aramanin ne buldugunu terminalde gorebilmek icin (ince ayar
    # yaparken faydali - istersen bu satiri sonradan silebilirsin)
    print(f"[Vector arama] Soru: '{soru}' -> En yakin: '{en_iyi_id}' (mesafe: {en_iyi_mesafe:.4f})")

    if en_iyi_mesafe > _VECTOR_MESAFE_ESIGI:
        return None  # yeterince alakali degil, reddet

    # _nodes listesi icinden, source_file'i bu id'ye esit olan node'u bul
    for node in _nodes:
        if node.get("source_file") == en_iyi_id:
            return node

    return None


# Bir onerinin "yeterince alakali" sayilmasi icin bir esik (ayni mesafe mantigi,
# kucuk deger DAHA alakali demek). Bu, secilen_node ile GERCEKTEN yakin olmayan
# node'larin oneri olarak gorunmesini engeller.
_ONERI_MESAFE_ESIGI = 0.4


def ilgili_onerileri_bul(secilen_node_obj, en_fazla=3):
    """
    Kullaniciya "Bunlar da ilginizi çekebilir" seklinde 2-3 ilgili konu onermek
    icin, secilen_node_obj'nin KENDI vektorunu ChromaDB'den (TEKRAR HESAPLAMADAN,
    yani ekstra Gemini embedding istegi harcamadan) cekip, ona EN YAKIN diger
    node'lari buluyoruz.

    Neden bu sekilde (kendi vektorunu cekip kullanma): Iki dokuman arasindaki
    benzerligi olcmenin en dogru yolu, ikisini de AYNI turde (RETRIEVAL_DOCUMENT)
    vektorlerle karsilastirmaktir - biz zaten her dosyayi bu sekilde vektorlemistik,
    o yuzden yeniden bir "sorgu" vektoru hesaplamaya GEREK YOK, doğrudan ChromaDB'nin
    sakladigi vektoru kullanabiliyoruz.
    """
    if _vector_koleksiyon is None or secilen_node_obj is None:
        return []

    dosya_id = secilen_node_obj.get("source_file")
    if not dosya_id:
        return []

    try:
        kayit = _vector_koleksiyon.get(ids=[dosya_id], include=["embeddings"])
    except Exception as e:
        print(f"[Oneri hatasi] Kendi vektoru cekilemedi: {type(e).__name__}: {e}")
        return []

    if kayit is None or kayit.get("embeddings") is None or len(kayit["embeddings"]) == 0:
        return []

    kendi_vektoru = kayit["embeddings"][0]

    try:
        # en_fazla+1 istiyoruz cunku ilk sonuc genelde node'un KENDISI olacak
        # (kendi vektorunu kendine soruyoruz), onu disariya eleyecegiz.
        sonuclar = _vector_koleksiyon.query(query_embeddings=[kendi_vektoru], n_results=en_fazla + 1)
    except Exception as e:
        print(f"[Oneri hatasi] Benzer node aramasi basarisiz: {type(e).__name__}: {e}")
        return []

    if not sonuclar.get("ids") or not sonuclar["ids"][0]:
        return []

    oneri_basliklari = []
    for id_, mesafe in zip(sonuclar["ids"][0], sonuclar["distances"][0]):
        if id_ == dosya_id:
            continue  # kendisini oneri olarak gosterme
        if mesafe > _ONERI_MESAFE_ESIGI:
            continue  # yeterince alakali degil, atla

        for node in _nodes:
            if node.get("source_file") == id_:
                baslik = node.get("label")
                if baslik and baslik not in oneri_basliklari:
                    oneri_basliklari.append(baslik)
                break

        if len(oneri_basliklari) >= en_fazla:
            break

    return oneri_basliklari


def gecmisi_metne_cevir(gecmis):
    """
    gecmis: [{"soru": "...", "cevap": "..."}, {"soru": "...", "cevap": "..."}] seklinde bir liste.
    Bunu Gemini'nin anlayacagi duz bir metne cevirir.
    Hafiza cok uzamasin diye sadece SON 5 konusmayi kullaniyoruz.
    """
    if not gecmis:
        return "(henuz konusma gecmisi yok, bu ilk soru)"

    son_gecmis = gecmis[-5:]
    satirlar = []
    for tur in son_gecmis:
        satirlar.append(f"Kullanici: {tur['soru']}")
        satirlar.append(f"Bot: {tur['cevap']}")
    return "\n".join(satirlar)


def yuklenen_belge_hakkinda_cevap_uret(soru, belge_metni, belge_adi, gecmis):
    """
    Kullanici bir PDF yukledigi zaman cagrilir. Normal Bagcilar bilgi tabanina
    (kelime eslestirme/vector database) HIC BAKMADAN, SADECE yuklenen belgenin
    icerigine dayanarak Gemini'ye cevap urettirir - klasik "kullanicinin kendi
    belgesiyle anlik RAG" (ad-hoc document Q&A) yontemi.
    """
    gecmis_metni = gecmisi_metne_cevir(gecmis)

    prompt = f"""Kullanıcı "{belge_adi}" adlı bir PDF belgesi yükledi. Belgenin tam metni aşağıdadır:

--- BELGE METNİ BAŞLANGICI ---
{belge_metni}
--- BELGE METNİ SONU ---

Önceki konuşma geçmişi:
{gecmis_metni}

Kullanıcı şunu sordu: "{soru}"

SADECE yukarıdaki belge içeriğine dayanarak cevap ver. Belgede sorunun cevabı
YOKSA, bunu dürüstçe belirt ("bu belgede bu bilgiye rastlamadım" gibi) - UYDURMA.
Kısa, net ve dürüst bir Türkçe ile yaz. Link, URL veya özel işaretleyici (||...||) YAZMA."""

    return gemini_sor(prompt)


def soru_cevap(soru: str, gecmis=None, yuklenen_belge_metni=None, yuklenen_belge_adi=None) -> str:
    """
    Flask'in cagiracagi ana fonksiyon.
    Bir soru alir, cevabi (string olarak) doner.

    gecmis: onceki soru-cevaplarin listesi (konusma hafizasi icin).
            Ornek: [{"soru": "hangi mahalleler var?", "cevap": "..."}]
            Verilmezse (None), hafizasiz calisir (eski davranis).

    yuklenen_belge_metni: Kullanici bir PDF yuklediyse, o PDF'den cikarilan
            metin buraya gelir. Doluysa, fonksiyon NORMAL Bagcilar bilgi
            tabanini (kisayollar, kelime eslestirme, vector database) HIC
            KULLANMAZ - SADECE bu belgeye dayanarak cevap uretir.
    yuklenen_belge_adi: Yuklenen PDF'in dosya adi (cevapta/promtta referans
            olarak kullanilir).
    """
    if gecmis is None:
        gecmis = []

    # ---- YENI: Kullanici bir PDF yuklediyse, TUM normal akisi atlayip SADECE
    # o belgeye dayali bir cevap uretiyoruz. ----
    if yuklenen_belge_metni:
        return yuklenen_belge_hakkinda_cevap_uret(soru, yuklenen_belge_metni, yuklenen_belge_adi, gecmis)

    gecmis_metni = gecmisi_metne_cevir(gecmis)

    # Her soruda tarihi TAZE hesapla (sunucu gunler boyu acik kalabilir, tarih degisebilir)
    bugun = datetime.now()
    bugun_metni = f"{bugun.day} {TURKCE_AYLAR[bugun.month]} {bugun.year}"

    tum_duyurular = duyuru_tarihlerini_yukle(DUYURU_KLASORU)

    # Ayin son gunu (ornek: Temmuz 2026 -> 31) - SADECE etkinlikler icin kullanilir
    # (yaklasan etkinlikleri "bu ay sonuna kadar" gostermek mantikli).
    ayin_son_gunu = calendar.monthrange(bugun.year, bugun.month)[1]
    ay_sonu = bugun.replace(day=ayin_son_gunu, hour=23, minute=59, second=59)
    bugun_baslangic = bugun.replace(hour=0, minute=0, second=0, microsecond=0)
    bugun_sonu = bugun.replace(hour=23, minute=59, second=59)

    # YENİ - ONEMLI DUZELTME: Haber/Duyuru icin "bu takvim ayi" (1'inden bugune
    # kadar) yerine, KAYAN SON 45 GUN penceresi kullaniyoruz. Eskiden ay basinda
    # (ornek: 3 Agustos gibi) sorulan bir soru, Temmuz'daki haberleri GORMEZDEN
    # GELIYORDU (cunku "ay baslangici" hep ayin 1'iydi) - oysa eski_verileri_temizle.py
    # zaten 60 gunluk veriyi sakliyor, filtreleme bunu YANSITMIYORDU. Simdi "son 30
    # gun" dedigimizde GERCEKTEN bugunden geriye 45 gunu kastediyoruz.
    kirk_bes_gun_once = bugun - timedelta(days=45)

    # ETKINLIK: Tarihi olan VE bugunden ay sonuna kadar olanlar ONCE gelsin; tarihi
    # HIC BULUNAMAYAN etkinlikler (cok gunlu seriler gibi) de yine de gosterilsin,
    # sadece sona eklensin (cunku sitenin kendi API'si zaten sadece guncel/aktif
    # etkinlikleri veriyor - tarih bulunamasa da "gecmis" oldugu anlamina gelmez).
    guncel_etkinlikler = sorted(
        [d for d in tum_duyurular if d["tur"] == "Etkinlik" and d["tarih"] is not None
         and bugun_baslangic <= d["tarih"] <= ay_sonu],
        key=lambda d: d["tarih"]
    ) + [
        d for d in tum_duyurular if d["tur"] == "Etkinlik" and d["tarih"] is None
    ]

    # HABER: KAYAN SON 45 GUN (takvim ayi degil). En YENI haber en basta olsun diye siraliyoruz.
    guncel_haberler = sorted(
        [d for d in tum_duyurular if d["tur"] == "Haber" and kirk_bes_gun_once <= d["tarih"] <= bugun_sonu],
        key=lambda d: d["tarih"], reverse=True
    )

    # DUYURU: KAYAN SON 45 GUN (takvim ayi degil - bir duyuru eski olsa da bilgi
    # olarak hala gecerli, ama "son 45 gun" penceresinde gosteriyoruz).
    # En YENI duyuru en basta olsun diye siraliyoruz.
    guncel_duyurular = sorted(
        [d for d in tum_duyurular if d["tur"] == "Duyuru" and kirk_bes_gun_once <= d["tarih"] <= bugun_sonu],
        key=lambda d: d["tarih"], reverse=True
    )

    guncel_donem_duyurulari = [d["baslik"] for d in guncel_etkinlikler + guncel_haberler + guncel_duyurular]

    # ---- KISAYOL 0: nobetci eczane sorusu ise, CANLI veri cek, Gemini'ye hic sormadan cevapla ----
    if eczane_sorusu_mu(soru):
        eczaneler = nobetci_eczaneleri_getir()
        if eczaneler:
            tarih = eczaneler[0]["tarih"]
            liste = "\n".join(f"- **{e['isim']}** — {e['adres']}" for e in eczaneler)

            # ONEMLI: Belediyenin canli eczane sitesi bazen GUNUN BASINDA henuz o
            # gunun listesine GUNCELLENMEMIS olabiliyor (onceki gunun listesini
            # gosterebiliyor). Bunu SESSIZCE "bugun" diye sunmak yaniltici olur -
            # bu yuzden sitenin dondurdugu tarihi GERCEK bugunle karsilastirip,
            # uyusmuyorsa DURUSTCE bir uyari ekliyoruz.
            site_tarihi = eczane_tarihini_parse_et(tarih)
            uyari = ""
            if site_tarihi is not None and site_tarihi != bugun.date():
                uyari = (
                    f"\n\n(Not: Belediyenin nöbetçi eczane sistemi şu anda {tarih} tarihli "
                    f"listeyi gösteriyor, bugünün ({bugun_metni}) listesi henüz "
                    f"güncellenmemiş olabilir.)\n\n"
                    f"||LINK:İstanbul Eczacı Odası güncel nöbetçi eczane sayfası||"
                    f"https://www.istanbuleczaciodasi.org.tr/nobetci-eczane/"
                )

            return (
                f"Bugün ({tarih}) Bağcılar'da nöbetçi olan eczaneler şunlardır:\n\n{liste}\n\n"
                f"Bunlardan birinin telefon numarasını öğrenmek istersen, adını söylemen yeterli."
                f"{uyari}"
            )
        else:
            return (
                "Nöbetçi eczane bilgisine şu anda ulaşamıyorum, sistem geçici olarak "
                "erişilemiyor olabilir. Lütfen birkaç dakika sonra tekrar deneyin."
            )

    # ---- KISAYOL 0b: afet toplanma alani sorusu ise, CANLI veri cek, Gemini'ye hic sormadan cevapla ----
    # Mahalle adi ya bu soruda, ya da (soru sadece "merkez mah" gibi kisaysa) BIR ONCEKI
    # bot cevabinda toplanma alani sorusu soruldugunda gecerli olur (Bagbi gibi takip sorusu destegi).
    onceki_cevap_toplanma_sordu_mu = bool(gecmis) and "toplanma alan" in gecmis[-1].get("cevap", "").lower()
    mahalle_adi = mahalle_bul(soru)

    if afet_sorusu_mu(soru) or (mahalle_adi and onceki_cevap_toplanma_sordu_mu):
        if not mahalle_adi:
            return (
                "Bağcılar'da doğal afetler sırasında toplanma alanlarını öğrenmek için lütfen "
                "yaşadığınız mahalleyi belirtir misiniz? Örneğin: \"Göztepe Mahallesi'ndeki "
                "toplanma alanlarını öğrenmek istiyorum.\" şeklinde yazmanız yeterlidir."
            )

        alanlar = afet_alanlarini_getir()
        if not alanlar:
            return (
                "Toplanma alanları bilgisine şu anda ulaşamıyorum, sistem geçici olarak "
                "erişilemiyor olabilir. Lütfen birkaç dakika sonra tekrar deneyin."
            )

        filtrelenmis = [a for a in alanlar if mahalle_adi in a["mahalle"].lower()]
        if not filtrelenmis:
            return f"'{mahalle_adi.title()}' mahallesi için kayıtlı bir toplanma alanı bulamadım."

        liste = "\n".join(
            f"- **{a['mahalle']} - {a['alan']}**  ||LINK:Yol tarifi||{a['harita_url']}"
            for a in filtrelenmis
        )
        return f"{filtrelenmis[0]['mahalle'].title()} Mahallesi'ndeki afet toplanma alanları:\n\n{liste}"

    # ---- KISAYOL 1: kategori liste sorusu ise (ornek: "havuzlar listesi"), Gemini'ye hic sormadan direkt listele ----
    if yayin_listesi_sorusu_mu(soru):
        return yayin_listesini_olustur(_nodes)

    if proje_listesi_sorusu_mu(soru):
        return proje_listesini_olustur(_nodes)

    if mudurler_sorusu_mu(soru):
        return mudurler_listesini_olustur(_nodes)

    if baskan_yardimcilari_sorusu_mu(soru):
        return baskan_yardimcilari_listesini_olustur(_nodes)

    kategori = liste_sorusu_mu(soru)
    if kategori:
        return kategori_listesini_olustur(kategori, _nodes)

    # ---- KISAYOL 2: zaman ifadesi varsa Gemini'ye hic sormadan direkt cevap ----
    # ONEMLI: Eger soru, aslinda UZUN/OZEL bir node basligiysa (ornek: "... Cocuk
    # Etkinlik Merkezi" gibi isminde "etkinlik" gecen bir tesis), bu kisayolu
    # ATLIYORUZ - yoksa yanlislikla "guncel etkinlikleri listele"ye dusup, asil
    # aranan tesis/kurumu hic bulamadan yanlis bir cevap donebiliyorduk.
    if zaman_ifadesi_var_mi(soru) and not soruda_tam_node_eslesmesi_var_mi(soru, _nodes):
        soru_kucuk = soru.lower()

        # Kullanici hangi SPESIFIK kategori(ler)i sordu? (ornek: sadece "etkinlik" -> sadece etkinlik)
        istenen_kategoriler = [
            kat for kat, kelimeler in KATEGORI_KELIMELERI.items()
            if any(k in soru_kucuk for k in kelimeler)
        ]

        # Hicbir spesifik kategori belirtilmediyse (ornek: "bu ay ne var", "güncel bilgiler"),
        # TUM kategorileri goster.
        gosterilecek_kategoriler = istenen_kategoriler or ["etkinlik", "haber", "duyuru"]

        SINIR = 15
        TUM_HABERLER_SAYFASI = "https://www.bagcilar.bel.tr/haberler"
        TUM_DUYURULAR_SAYFASI = "https://www.bagcilar.bel.tr/duyurular"

        parcalar = []

        if "etkinlik" in gosterilecek_kategoriler:
            if guncel_etkinlikler:
                # SADECE en yakin/ilk etkinligi gosteriyoruz (hepsini degil)
                e = guncel_etkinlikler[0]
                blok = f"**{e['baslik']}**"
                if e["tarih"] is not None:
                    tarih_metni = f"{e['tarih'].day} {TURKCE_AYLAR[e['tarih'].month]} {e['tarih'].year}"
                    blok += f"\n{tarih_metni}"
                if e.get("resim"):
                    blok += f"\n||IMG||{e['resim']}"
                if e.get("link"):
                    blok += f"\n||LINK:Etkinlik detayı için tıklayın||{e['link']}"
                parcalar.append(blok)
            else:
                parcalar.append("Bugünden ay sonuna kadar planlanmış bir etkinlik bulunmuyor.")

        if "haber" in gosterilecek_kategoriler:
            if guncel_haberler:
                liste = "\n".join(f"  - {d['baslik']}" for d in guncel_haberler[:SINIR])
                blok = f"En son haberler:\n{liste}"
                blok += f"\n||LINK:Devamını görmek için tıklayın||{TUM_HABERLER_SAYFASI}"
                parcalar.append(blok)
            else:
                parcalar.append("Son 45 gün içinde yayınlanmış bir haber bulunmuyor.")

        if "duyuru" in gosterilecek_kategoriler:
            if guncel_duyurular:
                liste = "\n".join(f"  - {d['baslik']}" for d in guncel_duyurular[:SINIR])
                blok = f"En son duyurular:\n{liste}"
                blok += f"\n||LINK:Devamını görmek için tıklayın||{TUM_DUYURULAR_SAYFASI}"
                parcalar.append(blok)
            else:
                parcalar.append("Son 45 gün içinde yayınlanmış bir duyuru bulunmuyor.")

        return f"Bugün {bugun_metni}.\n\n" + "\n\n".join(parcalar)

    bu_ay_metni = ("\n".join(f"- {b}" for b in guncel_donem_duyurulari)
                   if guncel_donem_duyurulari else "(bu donem icin kayitli duyuru/etkinlik bulunamadi)")

    # ---- HIBRIT SKORLAMA: kelime eslestirme + vector database birlikte karar veriyor ----
    # 1. Once HIZLI/UCRETSIZ kelime eslestirmesi (Graphify tarzi) deneniyor, bu bize
    #    hem bir node (varsa) hem de o eslesmenin GUVEN SEVIYESINI ("yuksek"/"orta"/"yok") verir.
    secilen_node_obj, kelime_guven_seviyesi = en_iyi_node_bul(soru, _nodes)

    # 2. Guven seviyesi "yuksek" DEGILSE (yani ya hic bulunamadiysa, ya da SINIRDA/
    #    belirsiz bir eslesmeyse), vector database'e de bakip CAPRAZ KONTROL yapiyoruz.
    #    Vector bir sonuc bulursa, onu tercih ediyoruz (anlam bazli arama, sinirda kalan
    #    kelime eslestirmesinden daha guvenilir kabul edilir). Vector de bulamazsa,
    #    elimizdeki "orta" seviyeli kelime sonucu (varsa) fallback olarak kullanilmaya
    #    devam eder - hicbir sey donmemekten iyidir.
    if kelime_guven_seviyesi != "yuksek":
        vector_sonucu = vector_ile_node_bul(soru)
        if vector_sonucu is not None:
            secilen_node_obj = vector_sonucu

    if secilen_node_obj:
        secilen_node = secilen_node_obj.get("label") or secilen_node_obj.get("norm_label")
        secilen_id = secilen_node_obj.get("id")
    else:
        secilen_node = None
        secilen_id = None

    ilgili_bilgiler = []
    bulunan_linkler = {}
    kaynak_resim = None
    if secilen_node_obj:
        ilgili_bilgiler.append(f"Konu: {secilen_node}")

        # ---- YENI: node'un gercek kaynak dosyasini oku, sadece basligi degil tam icerigi kullan ----
        kaynak_dosya = secilen_node_obj.get("source_file")
        gercek_metin = kaynak_dosya_icerigini_oku(kaynak_dosya)
        if gercek_metin:
            ilgili_bilgiler.append(f"Kaynak duyurunun tam metni:\n{gercek_metin}")
            bulunan_linkler = link_alanlarini_cikart(gercek_metin)
            resim_match = re.search(r"^Resim:\s*(.+)$", gercek_metin, re.MULTILINE)
            if resim_match:
                kaynak_resim = resim_match.group(1).strip()

    for edge in _edges:
        src = edge.get("source") or edge.get("src") or edge.get("from")
        dst = edge.get("target") or edge.get("dst") or edge.get("to")
        rel = edge.get("type") or edge.get("relation") or edge.get("label") or "ilişkili"

        if src == secilen_id or dst == secilen_id:
            src_name = next((n.get("label") or n.get("name") for n in _nodes if n.get("id") == src), src)
            dst_name = next((n.get("label") or n.get("name") for n in _nodes if n.get("id") == dst), dst)
            ilgili_bilgiler.append(f"{src_name} --{rel}--> {dst_name}")

    baglam = "\n".join(ilgili_bilgiler) if ilgili_bilgiler else "Bu konu hakkında ek bilgi bulunamadı."

    link_talimati = (
        "COK ONEMLI: Cevabinin HICBIR YERINDE link, URL, http/https adresi, markdown link "
        "formati ([yazi](link) gibi) veya '||' iceren herhangi bir isaretleyici YAZMA. "
        "Sadece bilgiyi anlat, linkleri biz ayrica, dogru yerlere kendimiz yerlestirecegiz."
    )

    # ---- Bu bilgiyi Gemini'ye verip dogal cevap urettir ----
    prompt2 = f"""Sen bir belediye chatbot asistanısın. Bugünün tarihi: {bugun_metni}.

Önceki konuşma geçmişi:
{gecmis_metni}

Kullanıcı şimdi şunu sordu: "{soru}"

Elindeki veriler (kaynak metinden):
{baglam}

Cevabini olustururken, ELINDEKI VERININ TURUNE gore en mantikli yapiyi SEN sec:
- Icerik bir kurs/basvuru/kayit ile ilgiliyse: sartlar, nasil basvurulur, tarihler gibi
  pratik/islemsel bilgileri one cikar.
- Icerik bir tesis/mekan ile ilgiliyse: ne oldugu ve adres bilgisini one cikar.
- Icerik genel bir haber/duyuru ise: konuyu kisa ve net ozetle, gereksiz detaya bogma.
- Veride GERCEKTEN yer alan bilgileri kullan, olmayan bir basligi/kategoriyi UYDURMA
  veya zorla EKLEME - hangi bilgiler o icerikte varsa sadece onlari aktar.

Kisa, dogal ve durust bir Turkce ile yaz. Nazik ve yardimsever ol ama ABARTMA -
"cok guzel", "harika", "muhtesem", "yakisacagina inaniyoruz" gibi pazarlama/reklam
dili KULLANMA, ozel bir goruslu/coskulu yorum ekleme. Sadece bilgiyi, sade ve net
bir sekilde aktar - bir belediye gorevlisinin sakin ve profesyonel ama sicak
tonuyla konus, asiri hevesli/pazarlamaci bir satis dili kullanma.

Onceki konusmayla tutarli ol (kullanici "peki oradaki..." gibi bir sey sorduysa, gecmise bakip
neyi kastettigini anla).
Eger soru guncellik/zaman ile ilgiliyse, bugunun tarihini referans al.
Eger veri yetersizse, bunu durustce belirt.

BICIMLENDIRME: Onemli alt basliklari (ornegin "Konum:", "Kapasite:", "Sartlar:", "Adres:" gibi)
**iki yildiz arasina alarak** kalin yaz (markdown bold formati). Bu, cevabin okunmasini kolaylastirir.
AYRICA: madde isaretli (•) bir listede her madde "Isim: Deger" formatindaysa (ornek:
"Vakıflar Bankası Güneşli Şubesi: TR87 0001...") SADECE ISIM kismini kalin yap
(ornek: "**Vakıflar Bankası Güneşli Şubesi:** TR87 0001..."), deger kismini kalin YAPMA.

MADDE ISARETI KURALI: SADECE gercekten BIRDEN FAZLA (2 veya daha fazla) madde varsa
madde isareti (•) kullan. Tek bir cumle/bilgi varsa, madde isareti KOYMA, duz cumle olarak yaz.
Ornegin sadece "8-14 yas arasi" gibi TEK bir bilgi varsa, bunu "• 8-14 yas arasi" diye
madde yapma, direkt "8-14 yas arasindaki ogrencileri kapsamaktadir." diye duz cumle yaz.

GEREKSIZ BASLIK ACMA: Eger elinde SADECE TEK bir basit bilgi varsa (ornegin sadece bir
telefon numarasi), bunun icin ayri bir "**İletişim:**" gibi baslik ACMA - bu bilgiyi
ilgili cumlenin icine dogal sekilde katistir. Baslik acmak, ancak o baslik altinda
gercekten birden fazla/detayli bilgi varsa mantiklidir.

{link_talimati}"""

    bot_cevabi = gemini_sor(prompt2)

    # ---- KOKTEN DUZELTME: Gemini bazen **Baslik:** (nokta ICERIDE) ya da **Baslik**: (nokta
    # DISARIDA) seklindeki kalin basliklarin onunde HIC bosluk birakmiyor (yapisik yaziyor),
    # bazen SADECE tek satir atliyor (bosluk yok). Ikisini de tutarli hale getirmek icin,
    # basligin onundeki NE KADAR '\n' varsa (sifir, bir, iki...) hepsini siliyoruz ve
    # HER ZAMAN tam olarak bir bos satir (\n\n) birakiyoruz - boylece gorunum HER ZAMAN ayni.
    bot_cevabi = re.sub(r"\n*(\*\*[^*\n]+:\*\*|\*\*[^*\n]+\*\*:)", r"\n\n\1", bot_cevabi)

    # ---- TEMIZLIK 1b: "1. **Başlık:**" gibi numarali madde + kalin baslik ayni satirdaysa,
    # yukaridaki duzeltme numarayi yalniz birakip basligi ayri satira atabiliyor
    # (ornek: "1.\n\n**Başvuru:** ..."). Bunu tekrar birlestiriyoruz: "1. **Başvuru:** ..."
    bot_cevabi = re.sub(r"(\d+\.)\s*\n+(\*\*)", r"\1 \2", bot_cevabi)

    # ---- TEMIZLIK 2: Baslik ayirma islemi bazen "• **Baslik:**" seklindeki bir maddeyi
    # ikiye bolup, "•" isaretini YALNIZ birakiyor (icerigi olmayan). Bu yalniz/bos madde
    # satirlarini temizliyoruz.
    bot_cevabi = re.sub(r"(?m)^[•\-\*]\s*$\n?", "", bot_cevabi)

    # ---- TEMIZLIK: Gemini talimata uymayip yine de link/markdown link yazmis olabilir. ----
    # Ihtimal disi birakmamak icin, cevaptaki OLASI markdown link kaliplarini ([yazi](url))
    # ve eski isaretleyici kaliplarini temizliyoruz - boylece cift link gorunmesin.
    bot_cevabi = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", bot_cevabi)
    bot_cevabi = re.sub(r"\|\|LINK:[^|]+\|\|\S+", "", bot_cevabi)
    # Gemini yine de ham bir http(s) linki yazip birakmis olabilir, onu da temizleyelim
    bot_cevabi = re.sub(r"https?://\S+", "", bot_cevabi)
    bot_cevabi = bot_cevabi.strip()

    # ---- LINKLERI, ILGILI SATIRIN SONUNA, BIZ KENDIMIZ YERLESTIRIYORUZ ----
    # (Gemini'nin kendi biçimlendirmesine guvenmek yerine, garantili/tutarli sonuc icin)
    # Once: ayni URL'ye giden BIRDEN FAZLA etiket varsa (ornek: Link ve SSSLink ayni
    # adrese gidiyorsa), tekrari onlemek icin sadece BIRINI tutuyoruz.
    gorulen_urller = set()
    tekil_linkler = {}
    for etiket, url in bulunan_linkler.items():
        if url in gorulen_urller:
            continue
        gorulen_urller.add(url)
        tekil_linkler[etiket] = url

    bot_cevabi = linkleri_cevaba_yerlestir(bot_cevabi, tekil_linkler)

    # Kaynak dosyada bir gorsel (Resim:) varsa, cevabin EN BASINA ekliyoruz
    if kaynak_resim:
        bot_cevabi = f"||IMG||{kaynak_resim}\n\n{bot_cevabi}"

    # ---- YENI: "Bunlar da ilginizi çekebilir" onerileri ----
    # SADECE gercek bir node bulunduysa (kisayol cevaplarinda degil, bu kod
    # zaten sadece normal Gemini-uretim yolunda calisiyor) ve vector database'de
    # yeterince alakali baska node'lar varsa, cevabin SONUNA ozel bir isaretleyici
    # ekliyoruz. Frontend (index.html) bu isaretleyiciyi yakalayip TIKLANABILIR
    # oneri butonlarina cevirecek (||LINK|| mantiginin ayni sekilde calisan bir versiyonu).
    oneriler = ilgili_onerileri_bul(secilen_node_obj)
    if oneriler:
        bot_cevabi = bot_cevabi + "\n\n||ONERI||" + "||".join(oneriler)

    return bot_cevabi