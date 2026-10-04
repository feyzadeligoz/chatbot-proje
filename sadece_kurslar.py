"""
sadece_kurslar.py

Bu script, scraper.py'deki fonksiyonlari kullanarak SADECE
Genclik ve Spor kurslarini/branslarini ceker.
Digerlerine (duyuru/haber/etkinlik/tesis/proje) dokunmuyor.

Calistirmak icin: python sadece_kurslar.py
"""

from scraper import kurslari_isle

if __name__ == "__main__":
    kurslari_isle()
    print("\nBitti! Sadece kurslar cekildi, digerlerine dokunulmadi.")