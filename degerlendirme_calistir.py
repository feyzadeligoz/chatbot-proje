"""
degerlendirme_calistir.py

RAG sisteminin (kelime eslestirme + vector database) DOGRULUGUNU olcen test scripti.

NASIL CALISIR:
- degerlendirme_seti.json icindeki her soru icin, sistemin (chatbot_logic.py'daki
  ayni fonksiyonlari kullanarak) HANGI dosyayi buldugunu kontrol eder.
- Bulunan dosya, test setindeki "beklenen_dosya" ile ESLESIYORSA basarili sayilir.
- ONEMLI: Bu test SADECE "dogru node'u bulma" (retrieval) kismini olcer,
  Gemini'nin CEVAP URETME kismini (gemini_sor) HIC CAGIRMAZ - boylece ne
  gereksiz Gemini kotasi harcanir, ne de test sonucu Gemini'nin o anki
  yazim tarzina bagli olarak degisir. Sadece "doğru kaynagi bulduk mu"
  sorusuna net, tekrarlanabilir bir cevap verir.

Ayrica hangi YONTEMLE bulundugunu da (kelime eslestirme mi, yoksa vector
database mi) ayri ayri raporlar - boylece hangi katmanin ne kadar ise
yaradigini gorebilirsin.

Calistirmak icin: python degerlendirme_calistir.py

NOT: Test setine (degerlendirme_seti.json) yeni .txt dosyalari ekledikce
yeni satirlar eklemeyi unutma - boylece sistem buyudukce test seti de
onunla birlikte buyur.
"""

import json
import sys

import chatbot_logic as cl

TEST_SETI_DOSYASI = "degerlendirme_seti.json"


def test_setini_yukle():
    try:
        with open(TEST_SETI_DOSYASI, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"HATA: {TEST_SETI_DOSYASI} bulunamadi, ayni klasorde oldugundan emin ol.")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"HATA: {TEST_SETI_DOSYASI} gecerli bir JSON degil: {e}")
        sys.exit(1)


def main():
    test_seti = test_setini_yukle()
    print(f"Toplam {len(test_seti)} test sorusu yuklendi.\n")
    print("=" * 70)

    basarili_sayisi = 0
    yontem_dogru_sayaci = {}
    yontem_yanlis_sayaci = {}
    hicbiri_bulamadi = 0

    basarisiz_detaylari = []

    for i, test in enumerate(test_seti, 1):
        soru = test["soru"]
        beklenen = test["beklenen_dosya"]

        # 1. ONCE kelime eslestirmeyi dene (Graphify tarzi, Gemini kullanmiyor)
        # ONEMLI: en_iyi_node_bul artik (node, guven_seviyesi) tuple donduruyor -
        # HIBRIT SKORLAMA sayesinde "yuksek" guvenli eslesmeler vector'e hic
        # basvurmadan kullanilir, "orta"/"yok" olanlarda ise vector'e de bakilip
        # capraz kontrol yapilir (chatbot_logic.py'daki soru_cevap ile AYNI mantik).
        secilen_node, kelime_guven = cl.en_iyi_node_bul(soru, cl._nodes)

        if kelime_guven == "yuksek":
            yontem = "kelime-yuksek"
        else:
            # 2. Guven "yuksek" degilse (yani "orta" ya da "yok"), vector database'e
            # de basvurup CAPRAZ KONTROL yapiyoruz - tipki gercek sistemin yaptigi gibi.
            vector_node = cl.vector_ile_node_bul(soru)
            if vector_node is not None:
                secilen_node = vector_node
                yontem = "vector"
            elif secilen_node is not None:
                yontem = "kelime-orta(fallback)"
            else:
                yontem = "yok"

        bulunan_dosya = secilen_node.get("source_file") if secilen_node else None
        dogru_mu = bulunan_dosya == beklenen

        if dogru_mu:
            basarili_sayisi += 1
            yontem_dogru_sayaci[yontem] = yontem_dogru_sayaci.get(yontem, 0) + 1
            durum = "OK"
        else:
            if bulunan_dosya is None:
                hicbiri_bulamadi += 1
            yontem_yanlis_sayaci[yontem] = yontem_yanlis_sayaci.get(yontem, 0) + 1
            durum = "YANLIS" if bulunan_dosya is not None else "BULUNAMADI"
            basarisiz_detaylari.append({
                "soru": soru,
                "beklenen": beklenen,
                "bulunan": bulunan_dosya,
                "yontem": yontem,
            })

        print(f"[{i:2}/{len(test_seti)}] {durum:11} ({yontem:20}) - \"{soru}\"")

    print("=" * 70)
    print(f"\nSONUC OZETI")
    print(f"Toplam soru: {len(test_seti)}")
    print(f"Dogru bulunan: {basarili_sayisi} ({basarili_sayisi / len(test_seti) * 100:.1f}%)")
    for yontem_adi in sorted(set(list(yontem_dogru_sayaci.keys()) + list(yontem_yanlis_sayaci.keys()))):
        dogru = yontem_dogru_sayaci.get(yontem_adi, 0)
        yanlis = yontem_yanlis_sayaci.get(yontem_adi, 0)
        print(f"  - {yontem_adi}: {dogru} dogru, {yanlis} yanlis")
    print(f"Hic bulunamayan: {hicbiri_bulamadi}")

    if basarisiz_detaylari:
        print(f"\n--- BASARISIZ OLAN SORULAR (incelemen gereken kisimlar) ---")
        for b in basarisiz_detaylari:
            print(f"\nSoru: \"{b['soru']}\"")
            print(f"  Beklenen: {b['beklenen']}")
            print(f"  Bulunan : {b['bulunan']} (yontem: {b['yontem']})")


if __name__ == "__main__":
    main()