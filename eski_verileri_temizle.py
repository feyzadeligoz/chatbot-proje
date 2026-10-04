"""
eski_verileri_temizle.py

Bu script, duyuru_verileri klasorundeki GUN_SINIRI gunden ESKI olan
Duyuru ve Haber dosyalarini otomatik SILER.

NEDEN SADECE Duyuru/Haber: Bunlar zaman-hassas icerikler (bir haber/duyuru
eskiyince onemini kaybeder). Tesis ve Proje dosyalari SILINMEZ (onlar
"surekli/kalici" bilgiler - bir kafenin adresi zamanla eskimez).
Etkinlik dosyalari da SILINMEZ (zaten cok az sayida oluyorlar, tarih
filtrelemesi chatbot tarafinda zaten yapiliyor).

Bir dosya silindiginde, graph.json'daki KARSILIK GELEN node da temizlenir
(yoksa graph'ta "hayalet" / kirik referanslar kalir).

Calistirmak icin: python eski_verileri_temizle.py
"""

import json
import os
import re
from datetime import datetime, timedelta

DUYURU_KLASORU = "duyuru_verileri"
GRAPH_DOSYASI = "graph.json"
GUN_SINIRI = 60  # Bu gunden eski Duyuru/Haber dosyalari silinir (~2 ay)


def dosyadan_bilgi_oku(dosya_yolu):
    """Bir .txt dosyasindan Tarih ve Tur bilgisini okur"""
    try:
        with open(dosya_yolu, "r", encoding="utf-8") as f:
            icerik = f.read()
    except Exception:
        return None, None

    tarih_match = re.search(r"^Tarih:\s*(\d{2})\.(\d{2})\.(\d{4})", icerik, re.MULTILINE)
    tur_match = re.search(r"^Tur:\s*(.+)$", icerik, re.MULTILINE)

    tur = tur_match.group(1).strip() if tur_match else "Duyuru"

    if not tarih_match:
        return None, tur

    gun, ay, yil = map(int, tarih_match.groups())
    try:
        tarih = datetime(yil, ay, gun)
    except ValueError:
        return None, tur

    return tarih, tur


def main():
    if not os.path.isdir(DUYURU_KLASORU):
        print(f"HATA: {DUYURU_KLASORU} klasoru bulunamadi.")
        return

    sinir_tarihi = datetime.now() - timedelta(days=GUN_SINIRI)
    silinen_dosyalar = []

    for dosya_adi in sorted(os.listdir(DUYURU_KLASORU)):
        if not dosya_adi.endswith(".txt"):
            continue

        dosya_yolu = os.path.join(DUYURU_KLASORU, dosya_adi)
        tarih, tur = dosyadan_bilgi_oku(dosya_yolu)

        # SADECE Duyuru ve Haber turundekileri kontrol ediyoruz (Tesis/Proje/Etkinlik'e dokunma)
        if tur not in ("Duyuru", "Haber"):
            continue

        if tarih is None:
            continue  # Tarihi okunamayan dosyaya dokunma (guvenli tarafta kal)

        if tarih < sinir_tarihi:
            os.remove(dosya_yolu)
            silinen_dosyalar.append(dosya_adi)

    print(f"{len(silinen_dosyalar)} eski Duyuru/Haber dosyasi silindi ({GUN_SINIRI} gunden eski).")

    if not silinen_dosyalar:
        print("Graph.json'da temizlenecek bir sey yok, bitti.")
        return

    # Graph.json'daki karsilik gelen node'lari da temizle
    if not os.path.isfile(GRAPH_DOSYASI):
        print(f"UYARI: {GRAPH_DOSYASI} bulunamadi, node temizligi atlandi.")
        return

    with open(GRAPH_DOSYASI, "r", encoding="utf-8") as f:
        graph = json.load(f)

    nodes = graph.get("nodes", [])
    silinen_dosyalar_seti = set(silinen_dosyalar)

    yeni_nodes = [n for n in nodes if n.get("source_file") not in silinen_dosyalar_seti]
    silinen_node_sayisi = len(nodes) - len(yeni_nodes)

    graph["nodes"] = yeni_nodes

    with open(GRAPH_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"Graph.json'dan da {silinen_node_sayisi} node temizlendi.")
    print(f"Graph.json'da artik toplam {len(yeni_nodes)} node var.")


if __name__ == "__main__":
    main()