# -*- coding: utf-8 -*-
"""
DEVIG - vig (bahis vergisi) arindirma: SAF PYTHON, hicbir dis bagimlilik yok.

Neden ayri dosya: shin_probs() eskiden dixon_coles_model.py icindeydi; o dosya modul
duzeyinde numpy/pandas/scipy import ediyor. Render free plan'da bu paketler YOK
(requirements.txt: requests + python-dotenv) -> vig arindirma gibi saf matematik bile
bulutta calismiyordu. Artik cekirdek fonksiyonlar buradan geliyor; dixon_coles_model
ve market_coherence bu modulu kullaniyor (geriye uyumluluk icin isim korundu).

    import devig
    devig.shin_probs({"1": 1.65, "X": 3.70, "2": 5.00})   -> {"1": 0.58, ...}
    devig.oransal({"1": 1.65, "X": 3.70, "2": 5.00})      -> oransal (basit) arindirma
    devig.marj({"1": 1.65, "X": 3.70, "2": 5.00})         -> 0.0712 (overround)
"""
import math

SHIN_ITER = 60          # ikili arama adimi: 0.4/2^60 cozunurluk, fazlasi anlamsiz
SHIN_Z_TAVAN = 0.4      # bilgili bahisci orani z'nin ust siniri (Shin 1993)


def marj(oran_sozluk):
    """Overround: sum(1/oran) - 1. Negatif/bozuk oranlar atlanir."""
    gecerli = [v for v in oran_sozluk.values() if v and v > 1.0]
    if len(gecerli) < 2:
        return None
    return sum(1.0 / v for v in gecerli) - 1.0


def oransal(oran_sozluk):
    """Oransal (proportional) arindirma: en basit yontem, favori yanliligini duzeltmez."""
    pi = {k: 1.0 / v for k, v in oran_sozluk.items() if v and v > 1.0}
    s = sum(pi.values())
    if len(pi) < 2 or s <= 0:
        return {}
    return {k: v / s for k, v in pi.items()}


def shin_probs(odds_by_outcome):
    """Shin (1993) yontemi: favori-sansli yanliligini duzelten vig arindirma."""
    items = {k: v for k, v in odds_by_outcome.items() if v and v > 1.0}
    if len(items) < 2:
        return {}
    pi = {k: 1.0 / v for k, v in items.items()}
    s = sum(pi.values())

    def probs(z):
        return {k: (math.sqrt(z * z + 4 * (1 - z) * (p * p) / s) - z) / (2 * (1 - z)) for k, p in pi.items()}

    lo, hi = 0.0, SHIN_Z_TAVAN
    for _ in range(SHIN_ITER):
        mid = (lo + hi) / 2
        if sum(probs(mid).values()) > 1:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    tot = sum(p.values())
    return {k: v / tot for k, v in p.items()}


def devig(oran_sozluk, yontem="shin"):
    """Tek giris noktasi: Shin dener, basarisizsa oransala duser."""
    if yontem == "shin":
        p = shin_probs(oran_sozluk)
        if p:
            return p
    return oransal(oran_sozluk)
