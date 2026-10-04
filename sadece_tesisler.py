"""
sadece_tesisler.py

Bu script, scraper.py'deki fonksiyonlari kullanarak SADECE tesisleri ceker.
Duyuru/haber/etkinlik kismini tekrar calistirmiyoruz (zaten cekilmis, beklemeye gerek yok).

Calistirmak icin: python sadece_tesisler.py
"""

from scraper import tesisleri_isle

if __name__ == "__main__":
    tesisleri_isle()
    print("\nBitti! Sadece tesisler cekildi, digerlerine dokunulmadi.")