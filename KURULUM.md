# Bağcılar Chatbot - Kurulum Talimatı

Bu klasördeki dosyaları bilgisayarınızda (örneğin masaüstünde) bir klasöre
kopyaladıktan sonra aşağıdaki adımları sırayla uygulayın.

## 1. Python kurulu olduğundan emin olun
Python 3.10 veya üzeri önerilir. Terminalde şu komutla kontrol edebilirsiniz:
```
python --version
```

## 2. Gerekli kütüphaneleri kurun
Proje klasöründeyken terminalde:
```
pip install -r requirements.txt
```

## 3. Gemini API anahtarını tanımlayın
Bu proje Google Gemini API'sini kullanıyor, bu yüzden bir API anahtarı
tanımlamanız gerekiyor:

1. https://aistudio.google.com/apikey adresinden ücretsiz bir API anahtarı
   oluşturun (yeni bir Google Cloud projesiyle).
2. Windows PowerShell'de şu komutla anahtarı tanımlayın (kendi anahtarınızı
   yapıştırın):
   ```
   $env:GEMINI_API_KEY="BURAYA_KENDI_ANAHTARINIZ"
   ```
   Bu komut, o terminal penceresi kapanana kadar geçerlidir - her yeni
   terminal açtığınızda tekrar çalıştırmanız gerekir.

## 4. Sunucuyu başlatın
Aynı terminalde (API anahtarını tanımladığınız pencerede):
```
python server.py
```

Terminalde şu satırları görmeniz gerekir:
```
[Sistem] Graph yuklendi: XXX node bulundu.
[Sistem] Vector veritabani yuklendi (XXX kayit).
 * Running on http://127.0.0.1:5000
```

## 5. Tarayıcıda açın
Tarayıcınızda şu adrese gidin:
```
http://127.0.0.1:5000
```

## Notlar
- `chroma_veritabani/` klasörü, vektör tabanlı arama (RAG) için önceden
  hazırlanmış veritabanını içerir - bu klasör olduğu sürece yeniden
  oluşturmanıza gerek yoktur.
- Sistemi güncel tutmak için `scraper.py`, `graph_guncelle.py` ve
  `vector_db_olustur.py` script'lerini sırayla çalıştırabilirsiniz (bu
  adım proje değerlendirmesi için ZORUNLU DEĞİLDİR, mevcut veriler
  yeterlidir).
- Retrieval (bilgi bulma) doğruluğunu test etmek için:
  ```
  python degerlendirme_calistir.py
  ```