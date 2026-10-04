"""
sadece_etkinlikler.py

Bu script, scraper.py'deki fonksiyonlari kullanarak SADECE etkinlikleri ceker
(artik gorsel/afis URL'sini de kaydediyor). Digerlerine dokunmuyor.

Calistirmak icin: python sadece_etkinlikler.py
"""

from scraper import etkinlikleri_isle

if __name__ == "__main__":
    etkinlikleri_isle()
    print("\nBitti! Sadece etkinlikler cekildi, digerlerine dokunulmadi.")