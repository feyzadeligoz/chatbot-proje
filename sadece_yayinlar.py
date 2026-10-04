"""
sadece_yayinlar.py

Bu script, scraper.py'deki fonksiyonlari kullanarak SADECE yayinlari
(sadece 2026 yili) ceker. Digerlerine dokunmuyor.

Calistirmak icin: python sadece_yayinlar.py
"""

from scraper import yayinlari_isle

if __name__ == "__main__":
    yayinlari_isle()
    print("\nBitti! Sadece yayinlar cekildi, digerlerine dokunulmadi.")