@echo off
REM Bu dosya, Gorev Zamanlayici tarafindan cagrilip:
REM 1) scraper.py'yi calistirir (yeni duyuru/haber/etkinlik/tesis/proje/kurs ceker,
REM    kurslar artik OTOMATIK KESFEDILIYOR - yeni brans eklenirse kendisi bulur)
REM 2) eski_verileri_temizle.py'yi calistirir (30 gunden eski duyuru/haber siler)
REM 3) graph_guncelle.py'yi calistirir (yeni dosyalari graph.json'a ekler,
REM    silinmis dosyalarin node'larini da temizler)
REM Cikti, hem ekrana hem de scraper_log.txt dosyasina yazilir.

cd /d "%~dp0"
echo ============================== >> scraper_log.txt
echo Calisma zamani: %date% %time% >> scraper_log.txt
python scraper.py >> scraper_log.txt 2>&1
echo --- Eski veri temizligi basliyor --- >> scraper_log.txt
python eski_verileri_temizle.py >> scraper_log.txt 2>&1
echo --- Graph guncelleniyor --- >> scraper_log.txt
python graph_guncelle.py >> scraper_log.txt 2>&1
echo Bitti. >> scraper_log.txt