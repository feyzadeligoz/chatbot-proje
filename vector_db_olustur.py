"""
vector_db_olustur.py

duyuru_verileri klasorundeki TUM .txt dosyalarini okuyup, Gemini embedding
modeliyle vektore cevirip ChromaDB'ye kaydeder.

ONEMLI DUZELTME (v2): Ilk versiyonda embedding modeline "task_type" hic
belirtilmemisti. Google'in embedding modelleri, bir metnin "aranacak bir
SORU" mu yoksa "arastirilacak bir BELGE" mi oldugunu bilmek ister - bu ikisi
farkli sekilde vektorlestirilir. Bu ayrim yapilmayinca arama sonuclari neredeyse
RASTGELE cikiyordu. Simdi burada TUM belgeler "RETRIEVAL_DOCUMENT" tipiyle
isleniyor (arama sorgusu tarafinda ise chatbot_logic.py'da "RETRIEVAL_QUERY"
kullanilacak - ikisi ESLESEREK dogru calisir).

ONEMLI: Bu script'i CALISTIRMADAN ONCE, eger daha once (task_type OLMADAN)
bir kere calistirdiysen, "chroma_veritabani" klasorunu VE
"vector_db_islenen_dosyalar.json" dosyasini SIL - yoksa script butun
dosyalarin "zaten islenmis" oldugunu sanip hicbir seyi YENIDEN islemez,
eski/yanlis vektorler kalir.

ONEMLI TASARIM KARARLARI:
- Graph.json/graph_guncelle.py sistemi SILINMIYOR, bu VECTOR DATABASE ona
  EK bir katman olarak calisiyor (hem Graphify hem Vector DB kullanilmis olur).
- Zaten islenmis (vektore cevrilmis) dosyalar TEKRAR islenmez - kaydedilen
  "islenmis dosyalar" listesine bakip atlar. Bu sayede script yarida kesilse
  veya kota dolsa bile, tekrar calistirinca KALDIGI YERDEN devam eder.
- Kota (429) hatasi alinirsa script GUVENLI sekilde durur, o ana kadar
  islenenler kaybolmaz (ChromaDB'ye her dosyadan hemen sonra kaydedilir).
- Her istek arasinda kisa bir bekleme var, siteye/API'ye asiri yuklenmeyelim.

Calistirmak icin: python vector_db_olustur.py
Tekrar calistirinca sadece YENI/DEGISMIS dosyalari isler.
"""

import chromadb
from chromadb import Documents, EmbeddingFunction, Embeddings
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
import os
import re
import time
import json
import hashlib

DUYURU_KLASORU = "duyuru_verileri"
VECTOR_DB_KLASORU = "./chroma_veritabani"
KOLEKSIYON_ADI = "bagcilar_bilgileri"
ISLENEN_DOSYALAR_KAYDI = "vector_db_islenen_dosyalar.json"

_client = genai.Client()

OLASI_MODELLER = [
    "models/gemini-embedding-001",
    "models/text-embedding-004",
]
_calisan_model = None


def dogru_modeli_bul():
    global _calisan_model
    if _calisan_model:
        return _calisan_model
    for model_adi in OLASI_MODELLER:
        try:
            _client.models.embed_content(
                model=model_adi,
                contents=["test"],
                config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
            )
            print(f"[Sistem] Calisan embedding modeli: {model_adi}\n")
            _calisan_model = model_adi
            return model_adi
        except Exception:
            continue
    raise Exception("Hicbir embedding modeli calismadi.")


class GeminiEmbeddingFonksiyonu(EmbeddingFunction):
    """
    ONEMLI: task_type="RETRIEVAL_DOCUMENT" kullaniyoruz - cunku burada
    ISLEDIGIMIZ metinler, ILERIDE aranacak olan KAYNAK BELGELER (yani
    duyuru_verileri klasorundeki .txt dosyalari). Arama SORGUSU (soru) tarafinda
    ise chatbot_logic.py'da FARKLI bir task_type ("RETRIEVAL_QUERY") kullanilacak.
    """

    def __call__(self, input: Documents) -> Embeddings:
        model_adi = dogru_modeli_bul()
        sonuc = _client.models.embed_content(
            model=model_adi,
            contents=input,
            config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
        )
        return [e.values for e in sonuc.embeddings]


def dosya_icerigi_hash(icerik):
    return hashlib.md5(icerik.encode("utf-8")).hexdigest()


def islenen_dosyalari_yukle():
    """Daha once vektore cevrilmis dosyalarin listesini (id -> hash) yukler"""
    if not os.path.isfile(ISLENEN_DOSYALAR_KAYDI):
        return {}
    try:
        with open(ISLENEN_DOSYALAR_KAYDI, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def islenen_dosyalari_kaydet(islenenler):
    with open(ISLENEN_DOSYALAR_KAYDI, "w", encoding="utf-8") as f:
        json.dump(islenenler, f, ensure_ascii=False, indent=2)


def baslik_oku(icerik):
    m = re.search(r"^Baslik:\s*(.+)$", icerik, re.MULTILINE)
    return m.group(1).strip() if m else None


def main():
    if not os.path.isdir(DUYURU_KLASORU):
        print(f"HATA: {DUYURU_KLASORU} klasoru bulunamadi.")
        return

    gemini_ef = GeminiEmbeddingFonksiyonu()
    client = chromadb.PersistentClient(path=VECTOR_DB_KLASORU)
    koleksiyon = client.get_or_create_collection(
        name=KOLEKSIYON_ADI,
        embedding_function=gemini_ef,
        metadata={"hnsw:space": "cosine"},  # ONEMLI: varsayilan L2 yerine kosinus benzerligi kullaniyoruz
    )

    islenenler = islenen_dosyalari_yukle()
    tum_dosyalar = sorted(f for f in os.listdir(DUYURU_KLASORU) if f.endswith(".txt"))

    print(f"Toplam {len(tum_dosyalar)} dosya bulundu, {len(islenenler)} tanesi daha once islenmis.\n")

    yeni_sayisi = 0
    atlanan_sayisi = 0
    hata_sayisi = 0

    for i, dosya_adi in enumerate(tum_dosyalar, 1):
        dosya_yolu = os.path.join(DUYURU_KLASORU, dosya_adi)
        try:
            with open(dosya_yolu, "r", encoding="utf-8") as f:
                icerik = f.read()
        except Exception:
            continue

        yeni_hash = dosya_icerigi_hash(icerik)

        # Bu dosya daha once AYNI icerikle islenmisse atla
        if islenenler.get(dosya_adi) == yeni_hash:
            atlanan_sayisi += 1
            continue

        baslik = baslik_oku(icerik) or dosya_adi

        print(f"[{i}/{len(tum_dosyalar)}] Isleniyor: {baslik[:60]}...")

        try:
            koleksiyon.upsert(
                documents=[icerik],
                ids=[dosya_adi],
                metadatas=[{"baslik": baslik, "source_file": dosya_adi}],
            )
            islenenler[dosya_adi] = yeni_hash
            yeni_sayisi += 1

            # Her 20 dosyada bir, ilerlemeyi diske kaydediyoruz - boylece script
            # yarida kesilse bile (kota dolmasi, internetin gitmesi vb.) o ana
            # kadarki ilerleme KAYBOLMAZ.
            if yeni_sayisi % 20 == 0:
                islenen_dosyalari_kaydet(islenenler)
                print(f"  [Ilerleme kaydedildi: {yeni_sayisi} yeni dosya islendi]")

        except genai_errors.ClientError as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                print(f"\n[HATA] Gunluk embedding kotasi dolmus olabilir (429).")
                print("Su ana kadarki ilerleme kaydedildi. Yarin/daha sonra tekrar")
                print("calistirdiginda KALDIGI YERDEN devam edecek.")
                islenen_dosyalari_kaydet(islenenler)
                return
            print(f"  [Uyari] Hata: {e}")
            hata_sayisi += 1
        except Exception as e:
            print(f"  [Uyari] Beklenmeyen hata: {type(e).__name__}: {e}")
            hata_sayisi += 1

        time.sleep(1)  # API'ye asiri yuklenmeyelim diye kisa bekleme

    islenen_dosyalari_kaydet(islenenler)

    print(f"\nBitti!")
    print(f"Yeni islenen: {yeni_sayisi}")
    print(f"Zaten islenmis, atlanan: {atlanan_sayisi}")
    print(f"Hata: {hata_sayisi}")
    print(f"Vector veritabani '{VECTOR_DB_KLASORU}' klasorunde saklandi.")


if __name__ == "__main__":
    main()