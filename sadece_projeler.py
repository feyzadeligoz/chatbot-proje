"""
sadece_projeler.py

Bu script, scraper.py'deki fonksiyonlari kullanarak SADECE projeleri ceker.
Digerlerine (duyuru/haber/etkinlik/tesis) dokunmuyor, hepsi zaten cekilmis durumda.

Calistirmak icin: python sadece_projeler.py
"""

from scraper import projeleri_isle

if __name__ == "__main__":
    projeleri_isle()
    print("\nBitti! Sadece projeler cekildi, digerlerine dokunulmadi.")