"""
graph_guncelle.py

Bu script, Gemini/Graphify KULLANMADAN, duyuru_verileri klasorundeki
YENI dosyalari (Haberler, Tesisler, Projeler, Kurumsal, ve varsa yeni Duyurular)
dogrudan graph.json'a node olarak ekler.

Nasil calisir:
- Mevcut graph.json'i okur
- duyuru_verileri klasorundeki her .txt dosyasina bakar
- Eger o dosya (source_file) graph.json'da HIC YOKSA, yeni bir node olarak ekler
- Eger dosya ZATEN VARSA ama ICERIGI DEGISMISSE (ornek: Link'i guncelledin, metni
  duzelttin), var olan node'u SILMEDEN, UZERINE YAZARAK gunceller (id ayni kalir,
  boylece o node'a baska yerden referans varsa bozulmaz)
- Icerik DEGISMEDIYSE hicbir sey yapmaz (tekrar tekrar calistirsan bile guvenli)
- Hicbir Gemini/API istegi yapmaz, tamamen ucretsiz ve aninda calisir

Calistirmak icin: python graph_guncelle.py
"""

import hashlib
import json
import os
import re

GRAPH_DOSYASI = "graph.json"
DUYURU_KLASORU = "duyuru_verileri"


def dosyadan_baslik_ve_tur_oku(dosya_yolu):
    """Bir .txt dosyasindan Baslik ve Tur bilgisini okur"""
    try:
        with open(dosya_yolu, "r", encoding="utf-8") as f:
            icerik = f.read()
    except Exception:
        return None, None

    baslik_match = re.search(r"^Baslik:\s*(.+)$", icerik, re.MULTILINE)
    tur_match = re.search(r"^Tur:\s*(.+)$", icerik, re.MULTILINE)

    baslik = baslik_match.group(1).strip() if baslik_match else None
    tur = tur_match.group(1).strip() if tur_match else "Duyuru"
    return baslik, tur


def icerik_hash_hesapla(dosya_yolu):
    """Dosyanin tam icerigi degisti mi anlamak icin kucuk bir ozet (hash) hesaplar."""
    try:
        with open(dosya_yolu, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return None


def id_uret(dosya_adi):
    """Dosya adindan benzersiz, temiz bir id uretir"""
    isim = os.path.splitext(dosya_adi)[0]
    isim = isim.lower()
    isim = re.sub(r"[^a-z0-9]+", "_", isim)
    return isim.strip("_")[:100]


def main():
    # 1. Mevcut graph.json'i yukle
    if not os.path.isfile(GRAPH_DOSYASI):
        print(f"HATA: {GRAPH_DOSYASI} bulunamadi, ana proje klasorunde oldugundan emin ol.")
        return

    with open(GRAPH_DOSYASI, "r", encoding="utf-8") as f:
        graph = json.load(f)

    nodes = graph.get("nodes", [])

    # 2. Graph'ta zaten hangi source_file'lar var, HIZLI ERISIM icin bir sozluge topla
    # (dosya adi -> node objesinin KENDISI, boylece icerik degistiyse UZERINDE
    # degisiklik yapabiliriz, silip yeniden eklemek zorunda kalmayiz)
    mevcut_kaynaklar = {}
    for node in nodes:
        kaynak = node.get("source_file")
        if kaynak:
            mevcut_kaynaklar[kaynak] = node

    print(f"Mevcut graph.json'da {len(nodes)} node var, {len(mevcut_kaynaklar)} farkli kaynak dosyadan.")

    # 3. duyuru_verileri klasorundeki TUM dosyalari tara
    if not os.path.isdir(DUYURU_KLASORU):
        print(f"HATA: {DUYURU_KLASORU} klasoru bulunamadi.")
        return

    yeni_node_sayisi = 0
    guncellenen_node_sayisi = 0
    en_buyuk_community = max((n.get("community", 0) for n in nodes), default=0)

    for dosya_adi in sorted(os.listdir(DUYURU_KLASORU)):
        if not dosya_adi.endswith(".txt"):
            continue

        dosya_yolu = os.path.join(DUYURU_KLASORU, dosya_adi)
        yeni_hash = icerik_hash_hesapla(dosya_yolu)
        if yeni_hash is None:
            continue  # dosya okunamadi, atla

        var_olan_node = mevcut_kaynaklar.get(dosya_adi)

        # ---- DURUM A: Bu dosya graph'ta HENUZ YOK -> yeni node olarak ekle ----
        if var_olan_node is None:
            baslik, tur = dosyadan_baslik_ve_tur_oku(dosya_yolu)
            if not baslik:
                continue  # Baslik okunamayan dosyayi atla

            en_buyuk_community += 1

            yeni_node = {
                "label": baslik,
                "file_type": "code",
                "source_file": dosya_adi,
                "community": en_buyuk_community,
                "norm_label": baslik.lower(),
                "id": id_uret(dosya_adi),
                "community_name": tur,  # Haber / Tesis / Proje / Etkinlik / Duyuru / Kurumsal / Kurs / Hizmet
                "icerik_hash": yeni_hash,
            }

            nodes.append(yeni_node)
            yeni_node_sayisi += 1
            continue

        # ---- DURUM B: Bu dosya graph'ta ZATEN VAR -> icerik DEGISMIS Mİ kontrol et ----
        eski_hash = var_olan_node.get("icerik_hash")

        # NOT: Eger var olan node'da hic "icerik_hash" alani yoksa (eski/onceki
        # calistirmalardan kalma node'lar), bu ilk calistirmada hash'i hesaplayip
        # KAYDEDIYORUZ ama iceriği tekrar YAZMIYORUZ - cunku o node'un Baslik/Link
        # bilgisi zaten elle/onceden dogru sekilde islenmis olabilir, gereksiz yere
        # ezmeyelim. Bir sonraki gercek icerik degisikliginde normal sekilde guncellenir.
        if eski_hash is None:
            var_olan_node["icerik_hash"] = yeni_hash
            continue

        if eski_hash == yeni_hash:
            continue  # icerik degismemis, hicbir sey yapma

        # Icerik DEGISMIS -> node'u dosyadaki GUNCEL bilgiyle UZERINE YAZ (id sabit kalir)
        baslik, tur = dosyadan_baslik_ve_tur_oku(dosya_yolu)
        if not baslik:
            continue

        var_olan_node["label"] = baslik
        var_olan_node["norm_label"] = baslik.lower()
        var_olan_node["community_name"] = tur
        var_olan_node["icerik_hash"] = yeni_hash
        # id, community, source_file KASITLI olarak degistirilmiyor - node'un
        # "kimligi" sabit kalsin, sadece icerigi guncellensin.

        guncellenen_node_sayisi += 1

    # 4. TEMIZLIK: source_file'i belirtilmis ama dosyasi ARTIK MEVCUT OLMAYAN node'lari sil
    # (ornek: sen bir dosyayi elle sildiysen, ya da eski_verileri_temizle.py bir dosyayi
    # kaldirdiysa, o node graph'ta "hayalet" olarak kalmasin diye).
    # NOT: source_file alani OLMAYAN node'lara (orijinal Graphify concept node'lari gibi)
    # dokunulmuyor, sadece dosyasi belli ama kaybolmus olanlar temizleniyor.
    silinen_node_sayisi = 0
    yeni_nodes = []
    for node in nodes:
        kaynak = node.get("source_file")
        if kaynak and not os.path.isfile(os.path.join(DUYURU_KLASORU, kaynak)):
            silinen_node_sayisi += 1
            continue
        yeni_nodes.append(node)
    nodes = yeni_nodes

    # 5. Guncellenmis graph.json'i kaydet
    graph["nodes"] = nodes

    with open(GRAPH_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"\n{yeni_node_sayisi} yeni node eklendi.")
    if guncellenen_node_sayisi:
        print(f"{guncellenen_node_sayisi} node, dosya icerigi degistigi icin GUNCELLENDI.")
    if silinen_node_sayisi:
        print(f"{silinen_node_sayisi} eski/kaybolmus node temizlendi.")
    print(f"Graph.json'da artik toplam {len(nodes)} node var.")
    print("Hicbir Gemini/API istegi kullanilmadi, tamamen ucretsiz calisti.")


if __name__ == "__main__":
    main()