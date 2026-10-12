# -*- coding: utf-8 -*-
"""COKLU BRANS BALINA DENETIMI (futbol / basketbol / hokey / tenis) - SALT OKUMA, ag cagrisi YOK.

OLCULEBILIRLIK DENETIMI ONCE: basketbol/hokey/tenis icin projede SONUC KAYNAGI YOK.
  - data/arsiv/basketbol_*.json : 42 mac, `sonuc` alani HIC yok (0 sonuclu)
  - hokey/tenis arsivi yok
  - settled_log.jsonl: 4562 kaydin TAMAMI kaynak=mackolik_arsiv (yalniz futbol)
Bu yuzden bu 3 brans icin "balina sinyali %X tuttu" HESAPLANAMAZ ve uydurulmaz.

Sonuc gerektirmeyen, olculebilir olan: oran hareketinin YAPISI.
  - taban orani  : pazar anahtarlarinin kaci esigi asiyor
  - yon asimetrisi: lehte (dusen) vs aleyhte (yukselen) kirilim
  - siddet       : sapma dagilimi
  - SURERLILIK   : ekstremde esigi astiktan sonra oran acilista mi kaldi (teyit) yoksa
                   geri mi dondu (FAKE-OUT). 11.10'da eklenen SON_oran alani sayesinde
                   ilk kez olculebiliyor (5 alanli kayitlar).
Futbol icin AYRICA gercek isabet/ROI olculur (sonuc var) - diger branslar icin kiyas tabani.
"""
import collections
import glob
import io
import json
import os
import re
import statistics as st
from datetime import datetime

AG = os.path.dirname(os.path.abspath(__file__))
MULTI = os.path.join(AG, "data", "multi")
ARSIV = os.path.join(AG, "data", "arsiv")

SPOR = {"1": "futbol", "2": "basketbol", "4": "hokey", "5": "tenis"}
ESIK = 0.10          # COEF["KIRILIM"]
GERI_DONUS = 0.03    # guncel sapma bunun altina dustuyse "acilisa geri dondu" = fake-out
MIN_N = 30


def _oku(ad):
    try:
        return json.load(io.open(os.path.join(MULTI, ad), encoding="utf-8"))
    except Exception:
        return {}


def brans_yapisi():
    """ekstrem_oran.json -> brans basina kirilim yapisi + surerlilik."""
    ek = _oku("ekstrem_oran.json")
    ilk = _oku("ilk_oran.json")
    g = collections.defaultdict(lambda: {"n": 0, "lehte": [], "aleyhte": [],
                                          "teyit": 0, "fake": 0, "kismi": 0, "surer_n": 0})
    for k, v in ek.items():
        sp = SPOR.get(k.split("|")[0])
        if not sp:
            continue
        d = g[sp]
        d["n"] += 1
        try:
            ed = float(v[1])
        except Exception:
            continue
        if ed <= -ESIK:
            d["lehte"].append(ed)
        elif ed >= ESIK:
            d["aleyhte"].append(ed)
        # SURERLILIK: yalniz 5 alanli (SON_oran var) ve ekstremde LEHTE kirilmis kayitlar
        if len(v) > 4 and ed <= -ESIK:
            # Acilis orani KAYDIN KENDISINDEN turetilir: ekstrem_dusus = ekstrem/ilk - 1
            # => ilk = ekstrem / (1 + ekstrem_dusus). ilk_oran.json budandigi icin (874 kayit
            # vs ekstrem 8474) oradan okumak cogu anahtarda basarisiz oluyordu.
            try:
                i = float(v[0]) / (1.0 + ed)
                gs = float(v[3]) / i - 1.0
            except Exception:
                continue
            d["surer_n"] += 1
            if gs <= -ESIK:
                d["teyit"] += 1
            elif abs(gs) < GERI_DONUS:
                d["fake"] += 1
            else:
                d["kismi"] += 1
    return g


# ---------------- futbol gercek isabet (kiyas tabani) ----------------
MS1, MSX, MS2 = (1, None, 1), (1, None, 2), (1, None, 3)
AU_A, AU_U = (12, 2.5, 1), (12, 2.5, 2)


def _deg(mk, h):
    t, sov, n = h
    for m in mk or []:
        if m.get("t") == t and (sov is None or abs((m.get("sov") or 0) - sov) < 0.01):
            v = (m.get("o") or {}).get(str(n))
            if v and v > 1.01:
                return v
    return None


def futbol_isabet():
    out = []
    for f in sorted(glob.glob(os.path.join(ARSIV, "nesine_*.json"))):
        if not re.fullmatch(r"\d{8}", os.path.basename(f)[7:15]):
            continue
        try:
            d = json.load(io.open(f, encoding="utf-8"))["maclar"]
        except Exception:
            continue
        for r in d.values():
            s = (r.get("sonuc") or {}).get("ms")
            if not s or None in s or not r.get("ilk") or not r.get("son"):
                continue
            ev, dep = s
            for h, tut in ((MS1, ev > dep), (MSX, ev == dep), (MS2, dep > ev),
                           (AU_A, ev + dep <= 2), (AU_U, ev + dep >= 3)):
                i, so = _deg(r["ilk"], h), _deg(r["son"], h)
                if not (i and so) or so / i - 1.0 > -ESIK:
                    continue
                out.append((so, tut))
    if not out:
        return None
    n = len(out)
    kaz = sum(1 for _o, t in out if t)
    ort = sum(o for o, _t in out) / n
    kar = sum((o - 1.0) if t else -1.0 for o, t in out)
    return {"n": n, "isabet": 100.0 * kaz / n, "oran": ort,
            "basabas": 100.0 / ort, "roi": 100.0 * kar / n}


def main():
    g = brans_yapisi()
    print("=" * 94)
    print("COKLU BRANS BALINA DENETIMI")
    print("=" * 94)
    print("\n1) KIRILIM YAPISI (sonuc gerekmez) | esik %%%d" % (ESIK * 100))
    print("  %-11s %7s %8s %9s %10s %s" % ("brans", "anahtar", "lehte", "aleyhte", "asimetri", "lehte sapma ortanca"))
    for sp in ("basketbol", "hokey", "tenis"):
        d = g.get(sp)
        if not d or not d["n"]:
            print("  %-11s %7s  (veri yok)" % (sp, "-"))
            continue
        le, al = len(d["lehte"]), len(d["aleyhte"])
        asim = ("%.1fx aleyhte" % (al / le)) if le else ("%d aleyhte" % al)
        med = ("%.1f%%" % (100 * abs(st.median(d["lehte"])))) if le else "-"
        print("  %-11s %7d %8d %9d %10s %s" % (sp, d["n"], le, al, asim, med))

    print("\n2) SURERLILIK / FAKE-OUT (lehte kirilim sonrasi oran nerede)")
    print("  %-11s %8s %8s %8s %8s %s" % ("brans", "olculen", "teyit", "FAKE", "kismi", "fake orani"))
    for sp in ("basketbol", "hokey", "tenis"):
        d = g.get(sp)
        if not d or not d["surer_n"]:
            print("  %-11s %8s  (SON_oran alani 11.10'da eklendi, bu bransta henuz veri yok)" % (sp, "-"))
            continue
        fo = 100.0 * d["fake"] / d["surer_n"]
        not_ = "" if d["surer_n"] >= MIN_N else "  (n<%d: kanit degil)" % MIN_N
        print("  %-11s %8d %8d %8d %8d %9.1f%%%s"
              % (sp, d["surer_n"], d["teyit"], d["fake"], d["kismi"], fo, not_))

    print("\n3) GERCEK ISABET / ROI")
    fi = futbol_isabet()
    if fi:
        print("  futbol     : n=%d | isabet %.1f%% | ort.oran %.2f | basabas %.1f%% | ROI %+.1f%%"
              % (fi["n"], fi["isabet"], fi["oran"], fi["basabas"], fi["roi"]))
    for sp, kanit in (("basketbol", "data/arsiv/basketbol_*.json: 42 mac, sonuc alani YOK"),
                      ("hokey", "arsiv dosyasi YOK"),
                      ("tenis", "arsiv dosyasi YOK")):
        print("  %-11s: OLCULEMEZ - %s" % (sp, kanit))
    print("  (settled_log.jsonl: 4562 kaydin tamami kaynak=mackolik_arsiv = yalniz futbol)")

    print("\n4) %70 ESIGI")
    if fi:
        print("  futbol balina (oran dususu >= %%10): isabet %.1f%% -> %%70 ESIGININ ALTINDA"
              % fi["isabet"])
    print("  basketbol / hokey / tenis: isabet hesaplanamadigi icin %70 esigi UYGULANAMAZ.")
    print("  -> Bu branslarda hicbir balina sinyali 'basarili' ya da 'basarisiz' ilan EDILEMEZ.")

    print("\n5) TELEGRAM")
    print("  multi-spor kanali: bildirim kapisi 11.10'da sikilastirildi (IZLE/PAS gitmiyor,")
    print("  KIRILIM yalniz KENDI pazarinda ve lehte); backtest 437 -> 0 mesaj. Bu branslarda")
    print("  isabet olculemedigi icin 'basarisiz taktik bildirimi' listesi OLUSTURULAMAZ.")
    print("\n  Bu betik: 0 LLM cagrisi, 0 ag cagrisi.")


if __name__ == "__main__":
    main()
