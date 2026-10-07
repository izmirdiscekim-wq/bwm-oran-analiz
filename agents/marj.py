# -*- coding: utf-8 -*-
"""
MARJ - lig ve pazar bazli DINAMIK marj tabani / esik mantigi.

SORUN (denetim bulgusu): tek global MARJ_TAVANI = 0.23 ve tek HAREKET_ESIK kullaniliyordu.
Oysa olculen marj pazar/lig basina %17,8 ile %50+ arasinda degisiyor. Sabit tavan pahali
liglerde hic sinyal uretmiyor, ucuz liglerde fazla uretiyor - yani esik, sinyal kalitesini
degil ligin fiyatlama aliskanligini olcuyordu.

COZUM: her taramada gorulen marj (lig, pazar) kirilimi ile biriktirilir; tavan ve ek hareket
esigi o kirilimin MEDYANINA gore belirlenir. Ornek orneklem yetersizse (n < MIN_N) global
degere dusulur - uydurma yapilmaz.

Kurallar (belgeli, keyfi degil):
  tavan(lig, pazar)   = medyan x TAVAN_CARPANI, [TAVAN_ALT, TAVAN_UST] arasina sikistirilir
  esik_ek(lig, pazar) = (medyan - REFERANS_MARJ) x 10 puan, [0, ESIK_EK_UST] arasinda
                        -> pahali pazarda dunya fiyatinin DAHA COK kaymasi istenir
  ucuz_mu(...)        = marj <= medyan x UCUZ_CARPANI

    import marj
    marj.ogren("İtalya Serie A", "1X2", 0.148)
    marj.tavan("İtalya Serie A", "1X2")      -> (0.17, 42, "lig medyani")
    marj.esik_ek("İtalya Serie A", "1X2")    -> 0.0
    python marj.py --durum
"""
import io
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DOSYA = os.path.join(BASE, "data", "balina", "marj_taban.json")

MIN_N = 12               # bu orneklem altinda lig medyanina guvenilmez -> global deger
ORNEK_TAVAN = 400        # kirilim basina saklanan azami olcum (kayan pencere)
TAVAN_CARPANI = 1.15     # medyanin %15 ustu: ayni ligin normalinden pahali mac
TAVAN_ALT, TAVAN_UST = 0.08, 0.35
REFERANS_MARJ = 0.15     # "ucuz" sayilan taban (olculen bulten medyani ~%17,6'nin altinda)
ESIK_EK_UST = 1.5        # pahali pazarda istenen ek hareket (yuzde PUAN)
UCUZ_CARPANI = 0.90

_BELLEK = {"veri": None, "kirli": False, "son_yaz": 0.0}
YAZ_ARALIGI = 120        # saniye: her taramada diske yazmamak icin (5 dk dongu zaten sik)


def _anahtar(lig, pazar):
    return "%s|%s" % ((lig or "?").strip()[:40], pazar or "1X2")


def _yukle():
    if _BELLEK["veri"] is None:
        try:
            with io.open(DOSYA, encoding="utf-8") as f:
                _BELLEK["veri"] = json.load(f)
        except (OSError, ValueError):
            _BELLEK["veri"] = {}
    return _BELLEK["veri"]


def kaydet(zorla=False):
    """Diske yazar (efemeral diskte sessizce basarisiz olur, bellek calismaya devam eder)."""
    if not _BELLEK["kirli"]:
        return False
    if not zorla and time.time() - _BELLEK["son_yaz"] < YAZ_ARALIGI:
        return False
    try:
        os.makedirs(os.path.dirname(DOSYA), exist_ok=True)
        with io.open(DOSYA, "w", encoding="utf-8") as f:
            json.dump(_BELLEK["veri"] or {}, f, ensure_ascii=False, separators=(",", ":"))
        _BELLEK["kirli"], _BELLEK["son_yaz"] = False, time.time()
        return True
    except OSError:
        return False


def _medyan(arr):
    a = sorted(arr)
    n = len(a)
    if not n:
        return None
    return a[n // 2] if n % 2 else (a[n // 2 - 1] + a[n // 2]) / 2.0


def ogren(lig, pazar, marj_degeri):
    """Gorulen marji kirilime ekler (kayan pencere). -> orneklem sayisi."""
    if marj_degeri is None or not (0.0 < marj_degeri < 1.0):
        return 0
    veri = _yukle()
    ak = _anahtar(lig, pazar)
    kayit = veri.setdefault(ak, {"ornek": []})
    kayit["ornek"].append(round(float(marj_degeri), 4))
    if len(kayit["ornek"]) > ORNEK_TAVAN:
        kayit["ornek"] = kayit["ornek"][-ORNEK_TAVAN:]
    kayit["medyan"] = round(_medyan(kayit["ornek"]), 4)
    kayit["n"] = len(kayit["ornek"])
    _BELLEK["kirli"] = True
    kaydet()
    return kayit["n"]


def taban(lig, pazar):
    """-> (medyan, n) ya da (None, n) orneklem yetersizse."""
    k = _yukle().get(_anahtar(lig, pazar)) or {}
    n = k.get("n") or 0
    return (k.get("medyan") if n >= MIN_N else None), n


def pazar_tabani(pazar):
    """Ligden bagimsiz, pazar genelinde medyan (lig orneklemi yetersizken ikinci basamak)."""
    toplam = []
    for ak, k in (_yukle() or {}).items():
        if ak.endswith("|" + (pazar or "1X2")):
            toplam.extend(k.get("ornek") or [])
    return (_medyan(toplam) if len(toplam) >= MIN_N else None), len(toplam)


def tavan(lig, pazar, global_tavan=0.23):
    """Dinamik marj tavani. -> (tavan, n, kaynak_metni)."""
    med, n = taban(lig, pazar)
    kaynak = "lig medyani"
    if med is None:
        med, n2 = pazar_tabani(pazar)
        kaynak, n = ("pazar medyani", n2) if med is not None else ("global", n)
    if med is None:
        return global_tavan, n, kaynak
    return max(TAVAN_ALT, min(TAVAN_UST, round(med * TAVAN_CARPANI, 4))), n, kaynak


def esik_ek(lig, pazar):
    """Pahali pazarda istenen EK hareket (yuzde puan). -> 0.0 .. ESIK_EK_UST."""
    med, _ = taban(lig, pazar)
    if med is None:
        med, _ = pazar_tabani(pazar)
    if med is None:
        return 0.0
    return max(0.0, min(ESIK_EK_UST, round((med - REFERANS_MARJ) * 10, 1)))


def ucuz_mu(lig, pazar, marj_degeri):
    med, _ = taban(lig, pazar)
    if med is None or marj_degeri is None:
        return False
    return marj_degeri <= med * UCUZ_CARPANI


def durum(en=25):
    veri = _yukle()
    if not veri:
        print("marj tabani bos. balina taramasi calistikca dolar.")
        return
    satir = [(ak, k.get("medyan"), k.get("n") or 0) for ak, k in veri.items()]
    satir.sort(key=lambda x: -(x[1] or 0))
    print("| Lig \\ Pazar | n | medyan marj | dinamik tavan | ek esik | durum |")
    print("|---|---:|---:|---:|---:|---|")
    for ak, med, n in satir[:en]:
        lig, pazar = ak.split("|", 1)
        tv, _, kaynak = tavan(lig, pazar)
        print("| %s / %s | %d | %s | %.3f | +%.1f p | %s |"
              % (lig[:26], pazar, n, ("%.3f" % med) if med else "-", tv, esik_ek(lig, pazar), kaynak))
    tum = [v for k in veri.values() for v in (k.get("ornek") or [])]
    if tum:
        print("")
        print("TOPLAM olcum: %d | genel medyan %.4f | kirilim %d"
              % (len(tum), _medyan(tum), len(veri)))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if "--durum" in argv:
        durum()
    else:
        durum()


if __name__ == "__main__":
    main()
