"""
Bağcılar Chatbot - Flask Web Sunucusu (server.py)

Bu dosya chatbot_logic.py'deki soru_cevap fonksiyonunu web'e açıyor.
Artık KONUŞMA HAFIZASI da var: Flask'ın "session" özelliğiyle, her tarayıcı
için ayrı bir konuşma geçmişi tutuyoruz (çerez/cookie üzerinden).

YENİ: Kullanıcı sohbeti sonlandırırken verdiği 1-5 puan + yorum, /degerlendirme
endpoint'i üzerinden "degerlendirmeler.jsonl" dosyasına kaydediliyor (her satır
bir değerlendirme, JSON formatında - ileride analiz etmek/okumak kolay olsun diye).

YENİ: Kullanıcı bir PDF yükleyebiliyor (/dosya_yukle). PDF'in metni pypdf ile
çıkarılıp session'a kaydediliyor - bundan sonraki sorular, normal Bağcılar bilgi
tabanı yerine SADECE bu yüklenen belgeye dayanarak cevaplanıyor (/dosya_kaldir
ile bu "belge modu" kapatılıp normale donulebiliyor).
"""

import io
import json
from datetime import datetime

from flask import Flask, request, jsonify, render_template, session
from pypdf import PdfReader
from chatbot_logic import soru_cevap  # gercek soru-cevap mantigi burada, chatbot_logic.py icinde

app = Flask(__name__)

# Session (oturum) verilerini şifrelemek için gereken gizli anahtar.
# Bunu kimseyle paylaşma, ama şimdilik sabit bir değer olması sorun değil (yerel/test kullanımı için).
app.secret_key = "bagcilar-chatbot-gizli-anahtar-degistir-istersen"

DEGERLENDIRME_DOSYASI = "degerlendirmeler.jsonl"

# PDF yukleme icin guvenlik sinirlari
IZIN_VERILEN_UZANTILAR = {"pdf"}
MAKS_DOSYA_BOYUTU = 10 * 1024 * 1024  # 10 MB
MAKS_BELGE_KARAKTERI = 20000  # Gemini'ye asiri uzun prompt gondermemek icin


def uzanti_izinli_mi(dosya_adi):
    return "." in dosya_adi and dosya_adi.rsplit(".", 1)[1].lower() in IZIN_VERILEN_UZANTILAR


@app.route("/")
def anasayfa():
    # Yeni bir ziyaretçi geldiğinde konuşma geçmişini VE yuklu belge varsa onu da sıfırla
    session["gecmis"] = []
    session.pop("yuklenen_belge_adi", None)
    session.pop("yuklenen_belge_metni", None)
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    soru = data.get("question", "").strip()

    if not soru:
        return jsonify({"answer": "Bir soru yazmalısın."}), 400

    # Bu tarayıcının önceki konuşma geçmişini al (yoksa boş liste)
    gecmis = session.get("gecmis", [])

    # Bu tarayicida yuklu bir PDF varsa, onu da soru_cevap'a iletiyoruz - varsa
    # chatbot_logic.py normal bilgi tabani yerine SADECE bu belgeyi kullanacak.
    belge_metni = session.get("yuklenen_belge_metni")
    belge_adi = session.get("yuklenen_belge_adi")

    try:
        cevap = soru_cevap(
            soru,
            gecmis=gecmis,
            yuklenen_belge_metni=belge_metni,
            yuklenen_belge_adi=belge_adi,
        )
    except Exception as e:
        cevap = f"Bir hata oldu: {e}"

    # Yeni soru-cevabı geçmişe ekle ve session'a geri kaydet
    gecmis.append({"soru": soru, "cevap": cevap})

    # ONEMLI: Session (cookie) boyutu sinirli (~4KB). Gecmisi SONSUZA KADAR biriktirirsek
    # cookie tasar, tarayici sessizce cookie'yi yok sayabilir (hafiza bozulur).
    # Bu yuzden SADECE SON 5 konusmayi saklıyoruz (zaten prompt'a da sadece bu kadari gidiyordu).
    gecmis = gecmis[-5:]
    session["gecmis"] = gecmis

    return jsonify({"answer": cevap})


@app.route("/dosya_yukle", methods=["POST"])
def dosya_yukle():
    """
    Kullanicinin yukledigi PDF'i alir, pypdf ile metnini cikarir ve session'a
    kaydeder. Bundan sonraki /ask istekleri, bu belge session'da durdugu surece
    SADECE bu belgeye dayanarak cevaplanacak (chatbot_logic.py tarafinda).
    """
    if "dosya" not in request.files:
        return jsonify({"status": "hata", "mesaj": "Dosya bulunamadı."}), 400

    dosya = request.files["dosya"]

    if dosya.filename == "":
        return jsonify({"status": "hata", "mesaj": "Dosya seçilmedi."}), 400

    if not uzanti_izinli_mi(dosya.filename):
        return jsonify({"status": "hata", "mesaj": "Sadece PDF dosyaları desteklenmektedir."}), 400

    dosya_bytes = dosya.read()

    if len(dosya_bytes) > MAKS_DOSYA_BOYUTU:
        return jsonify({"status": "hata", "mesaj": "Dosya çok büyük (10 MB üst sınırı aşıyor)."}), 400

    try:
        reader = PdfReader(io.BytesIO(dosya_bytes))
        sayfa_metinleri = [(sayfa.extract_text() or "") for sayfa in reader.pages]
        tam_metin = "\n".join(sayfa_metinleri).strip()
    except Exception as e:
        return jsonify({"status": "hata", "mesaj": f"PDF okunamadı: {e}"}), 400

    if not tam_metin:
        return jsonify({
            "status": "hata",
            "mesaj": "Bu PDF'den metin çıkarılamadı (taranmış/görsel bir PDF olabilir).",
        }), 400

    # Cok uzun PDF'lerde, Gemini'ye asiri uzun bir prompt gondermemek icin metni sinirliyoruz
    if len(tam_metin) > MAKS_BELGE_KARAKTERI:
        tam_metin = tam_metin[:MAKS_BELGE_KARAKTERI] + "\n\n[Belgenin geri kalanı, uzunluk sınırı nedeniyle kesildi.]"

    dosya_adi_temiz = dosya.filename

    session["yuklenen_belge_adi"] = dosya_adi_temiz
    session["yuklenen_belge_metni"] = tam_metin

    return jsonify({
        "status": "ok",
        "dosya_adi": dosya_adi_temiz,
        "sayfa_sayisi": len(reader.pages),
    })


@app.route("/dosya_kaldir", methods=["POST"])
def dosya_kaldir():
    """Yuklu belgeyi session'dan temizler - bundan sonraki sorular tekrar normal
    Bagcilar bilgi tabanina gore cevaplanir."""
    session.pop("yuklenen_belge_adi", None)
    session.pop("yuklenen_belge_metni", None)
    return jsonify({"status": "ok"})


@app.route("/degerlendirme", methods=["POST"])
def degerlendirme():
    """
    Kullanıcı sohbeti sonlandırırken verdiği 1-5 puan ve (varsa) yorum metnini
    "degerlendirmeler.jsonl" dosyasına EKLER (append) - her satır ayrı bir
    JSON kaydı, boylece dosya buyudukce eski kayitlar bozulmaz, ve ileride
    (Excel/pandas ile) kolayca okunup analiz edilebilir.
    """
    data = request.get_json() or {}
    puan = data.get("puan")
    yorum = (data.get("yorum") or "").strip()

    # Puan gercekten 1-5 arasinda bir sayi mi, kontrol ediyoruz (kotu/bozuk veri
    # dosyaya yazilmasin diye).
    if not isinstance(puan, int) or not (1 <= puan <= 5):
        return jsonify({"status": "hata", "mesaj": "Puan 1-5 arasında bir tam sayı olmalı."}), 400

    kayit = {
        "tarih": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "puan": puan,
        "yorum": yorum,
    }

    try:
        with open(DEGERLENDIRME_DOSYASI, "a", encoding="utf-8") as f:
            f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[HATA] Degerlendirme kaydedilemedi: {e}")
        return jsonify({"status": "hata", "mesaj": "Değerlendirme kaydedilemedi."}), 500

    return jsonify({"status": "ok"})


@app.route("/reset", methods=["POST"])
def reset():
    """İstersen kullanıcı 'konuşmayı sıfırla' derse bu endpoint'i çağırırsın."""
    session["gecmis"] = []
    session.pop("yuklenen_belge_adi", None)
    session.pop("yuklenen_belge_metni", None)
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # debug=True -> kod değiştirince sunucu otomatik yeniden başlar (geliştirme için ideal)
    app.run(debug=True, port=5000)