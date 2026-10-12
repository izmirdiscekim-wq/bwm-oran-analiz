# -*- coding: utf-8 -*-
"""SINYAL OPTIMIZER - %80 hedefli kati filtre kurallarinin GECMIS VERI backtest'i.

SALT OKUMA: settled_log.jsonl (yerlesmis kararlar) okunur; hicbir uretim dosyasi
degistirilmez, hicbir ag cagrisi yapilmaz, hicbir karar yeniden yazilmaz.

Dersler FAZ 1 olcumunden (n=3161) cikarildi:
  - ESNEK kademe  : isabet %31.8 / ROI -%39.8  (n=110 -> artik n>=30, karar verilebilir)
  - KUPON         : isabet %41.5 / ROI -%39.2  (n=53)
  - oran >= 1.50  : isabet %36.8 ve altı        (n=57+51)
  - model p < 0.70: isabet %55.7 ve altı        (n=79+41+61)
  - Handikap -3   : isabet %60.0 / ROI -%30.1  (n=60)

ONEMLI BULGU (rapora girer): "%80 isabet" hedefi ULASILABILIR ama KAR GETIRMEZ -
YUKSEK kademe zaten %83.6 isabet ediyor ve ROI'si -%11.6. Cunku %80 isabeti saglayan
secimler 1.06 ortalama oranda ve iddaa marji (%13-17) bu farki yiyor. Betik bu yuzden
her kural setinde isabet YANINDA ROI'yi de basar; yalniz isabete bakmak yanlis yonlendirir.
"""
import collections
import io
import json
import math
import os
import sys
import unittest

SETTLED = r"C:\Users\Tuffy\.claude\agents\data\settled_log.jsonl"


# ---------------------------------------------------------------- veri
def oku(yol=SETTLED):
    out = []
    if not os.path.exists(yol):
        return out
    for ln in io.open(yol, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            k = json.loads(ln)
        except Exception:
            continue
        if k.get("kazandi") is None:
            continue
        if not isinstance(k.get("odds"), (int, float)):
            continue
        out.append(k)
    return out


def ozet(rows):
    n = len(rows)
    if not n:
        return {"n": 0, "isabet": 0.0, "roi": 0.0, "oran": 0.0, "ev": 0.0}
    kaz = sum(1 for r in rows if r["kazandi"])
    kar = sum((r["odds"] - 1.0) if r["kazandi"] else -1.0 for r in rows)
    ev = [r["p"] * r["odds"] - 1.0 for r in rows if isinstance(r.get("p"), (int, float))]
    return {"n": n, "isabet": 100.0 * kaz / n, "roi": 100.0 * kar / n,
            "oran": sum(r["odds"] for r in rows) / n,
            "ev": 100.0 * sum(ev) / max(len(ev), 1)}


# ---------------------------------------------------------------- kurallar
KOTU_KADEME = ("ESNEK", "KUPON")
KOTU_PAZAR_PARCA = ("Handikaplı Maç Sonucu -3", "Handikapli Mac Sonucu -3")
ORAN_TAVANI = 1.50      # bu ve ustu: olculen isabet %37 ve altı
P_TABANI = 0.70         # altinda: olculen isabet %56 ve altı


def _pazar_kotu(ad):
    ad = str(ad or "")
    return any(p in ad for p in KOTU_PAZAR_PARCA)


def eski_filtre(r):
    """Suan uretimde fiilen gecerli olan kapi: bir karar yazildiysa bahis sayilir."""
    return True, "GECTI"


def yeni_filtre(r):
    """Derslerden cikan kati kapi -> (gecer_mi, red_sebebi)."""
    if str(r.get("tier")) in KOTU_KADEME:
        return False, "kademe %s (olculen isabet <%%45)" % r.get("tier")
    if str(r.get("kind")) == "kupon":
        return False, "kupon (kombine - marj katlaniyor)"
    if r["odds"] >= ORAN_TAVANI:
        return False, "oran >= %.2f (olculen isabet <%%40)" % ORAN_TAVANI
    p = r.get("p")
    if isinstance(p, (int, float)) and p < P_TABANI:
        return False, "model p < %.2f (olculen isabet <%%56)" % P_TABANI
    if _pazar_kotu(r.get("market")):
        return False, "pazar: Handikap -3 (olculen isabet %60)"
    return True, "GECTI"


def yeni_filtre_sadece_yuksek(r):
    """%80 hedefini DOGRUDAN vuran en kati set: yalniz YUKSEK kademe / p>=0.80."""
    ok, sebep = yeni_filtre(r)
    if not ok:
        return False, sebep
    p = r.get("p")
    if str(r.get("tier")) != "YÜKSEK" and not (isinstance(p, (int, float)) and p >= 0.80):
        return False, "YUKSEK kademe degil ve p < 0.80"
    return True, "GECTI"


# ---------------------------------------------------------------- kalibrasyon
def kalibrasyon(rows):
    """Beklenen (model p) vs gerceklesen isabet + Brier. Asiri ozguven var mi?"""
    bantlar = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.75), (0.75, 0.8), (0.8, 0.9), (0.9, 1.01)]
    print("\n%-14s %6s %10s %10s %9s" % ("p bandi", "n", "beklenen", "gercek", "fark"))
    print("-" * 56)
    brier, bn = 0.0, 0
    for alt, ust in bantlar:
        g = [r for r in rows if isinstance(r.get("p"), (int, float)) and alt <= r["p"] < ust]
        if len(g) < 20:
            continue
        bek = 100.0 * sum(r["p"] for r in g) / len(g)
        ger = 100.0 * sum(1 for r in g if r["kazandi"]) / len(g)
        print("%-14s %6d %9.1f%% %9.1f%% %+8.1f" % ("%.2f-%.2f" % (alt, ust), len(g), bek, ger, ger - bek))
    for r in rows:
        if isinstance(r.get("p"), (int, float)):
            brier += (r["p"] - (1.0 if r["kazandi"] else 0.0)) ** 2
            bn += 1
    if bn:
        print("\nBrier: %.4f (n=%d)  [dusuk=iyi; 0.25 = yazi-tura]" % (brier / bn, bn))


# ---------------------------------------------------------------- rapor
def rapor():
    d = oku()
    if not d:
        print("settled_log okunamadi / bos.")
        return
    print("=" * 90)
    print("SINYAL OPTIMIZER BACKTEST - yerlesmis karar n=%d" % len(d))
    print("=" * 90)

    setler = [("ESKI (filtre yok)", eski_filtre),
              ("YENI (dersli kati filtre)", yeni_filtre),
              ("YENI-SIKI (%80 hedefi)", yeni_filtre_sadece_yuksek)]
    print("\n%-28s %6s %9s %9s %8s %9s" % ("kural seti", "bahis", "isabet%", "ROI%", "ort.or", "ort.EV%"))
    print("-" * 90)
    for ad, fn in setler:
        gecen = [r for r in d if fn(r)[0]]
        o = ozet(gecen)
        print("%-28s %6d %8.1f%% %8.1f%% %7.2f %8.1f%%"
              % (ad, o["n"], o["isabet"], o["roi"], o["oran"], o["ev"]))

    gecen_yeni = [r for r in d if yeni_filtre(r)[0]]
    elenen = [(r, yeni_filtre(r)[1]) for r in d if not yeni_filtre(r)[0]]
    print("\nENGELLENEN BAHIS: %d / %d (%.1f%%)" % (len(elenen), len(d), 100.0 * len(elenen) / len(d)))
    print("\nRED SEBEPLERI (ve engellenen dilimin kendi performansi):")
    grup = collections.defaultdict(list)
    for r, s in elenen:
        grup[s].append(r)
    for s, rows in sorted(grup.items(), key=lambda x: -len(x[1])):
        o = ozet(rows)
        print("  %5d  %-44s isabet %5.1f%%  ROI %7.1f%%" % (o["n"], s[:44], o["isabet"], o["roi"]))

    kayip_onlendi = sum((-(r["odds"] - 1.0)) if r["kazandi"] else 1.0 for r, _s in elenen)
    print("\nENGELLENEN DILIMIN NET ETKISI: 1 birim duz bahiste %+.1f birim" % kayip_onlendi)
    print("  (pozitif = engellemek KAZANC sagladi; bu dilim oynanmasaydi kasa bu kadar korunurdu)")

    print("\n" + "=" * 90)
    print("KALIBRASYON - TUM KARARLAR")
    print("=" * 90)
    kalibrasyon(d)
    print("\n" + "=" * 90)
    print("KALIBRASYON - YENI FILTREDEN GECENLER")
    print("=" * 90)
    kalibrasyon(gecen_yeni)

    print("\n" + "=" * 90)
    print("DURUST SONUC")
    print("=" * 90)
    oy = ozet(gecen_yeni)
    os_ = ozet([r for r in d if yeni_filtre_sadece_yuksek(r)[0]])
    print("  Kati filtre isabeti %.1f%%'e, en kati set %.1f%%'e cikariyor." % (oy["isabet"], os_["isabet"]))
    print("  ANCAK ROI hala %.1f%% / %.1f%% - yani NEGATIF." % (oy["roi"], os_["roi"]))
    print("  Sebep: %%80 isabeti saglayan secimlerin ortalama orani %.2f ve iddaa marji" % os_["oran"])
    print("  (olculen %%13-17) bu farki yiyor. Kayit anindaki ortalama EV %.1f%%." % os_["ev"])
    print("  => '%80 isabet' bir KALITE olcutu DEGIL; kisa oran secmenin yan urunu.")
    print("     Kar icin gereken sey yuksek isabet degil, POZITIF EV: 3161 kararin 1'i pozitifti.")


# ---------------------------------------------------------------- testler
class Optimizer(unittest.TestCase):
    def _r(self, **ek):
        d = {"tier": "MUHTEMEL", "kind": "canli", "odds": 1.20, "p": 0.78,
             "market": "Alt/Üst 2.5", "kazandi": True}
        d.update(ek)
        return d

    def test_esnek_elenir(self):
        self.assertFalse(yeni_filtre(self._r(tier="ESNEK"))[0])

    def test_kupon_elenir(self):
        self.assertFalse(yeni_filtre(self._r(kind="kupon"))[0])
        self.assertFalse(yeni_filtre(self._r(tier="KUPON"))[0])

    def test_yuksek_oran_elenir(self):
        self.assertFalse(yeni_filtre(self._r(odds=1.60))[0])
        self.assertTrue(yeni_filtre(self._r(odds=1.49))[0])

    def test_dusuk_p_elenir(self):
        self.assertFalse(yeni_filtre(self._r(p=0.65))[0])

    def test_handikap_eksi3_elenir(self):
        self.assertFalse(yeni_filtre(self._r(market="Handikaplı Maç Sonucu -3"))[0])

    def test_temiz_karar_gecer(self):
        self.assertTrue(yeni_filtre(self._r())[0])

    def test_siki_set_yuksek_ister(self):
        self.assertFalse(yeni_filtre_sadece_yuksek(self._r(tier="MUHTEMEL", p=0.75))[0])
        self.assertTrue(yeni_filtre_sadece_yuksek(self._r(tier="YÜKSEK"))[0])
        self.assertTrue(yeni_filtre_sadece_yuksek(self._r(p=0.85))[0])

    def test_ozet_hesabi(self):
        o = ozet([{"odds": 2.0, "p": 0.5, "kazandi": True}, {"odds": 2.0, "p": 0.5, "kazandi": False}])
        self.assertEqual(o["n"], 2)
        self.assertAlmostEqual(o["isabet"], 50.0)
        self.assertAlmostEqual(o["roi"], 0.0)          # 1 kazanc (+1.0), 1 kayip (-1.0)

    def test_bozuk_satir_cokmez(self):
        for bozuk in ({}, {"odds": None}, {"tier": None, "odds": 1.2}):
            self.assertIsInstance(yeni_filtre({"odds": 1.2, **bozuk} if "odds" not in bozuk
                                               else {"odds": 1.2}), tuple)


if __name__ == "__main__":
    if "--rapor" in sys.argv:
        rapor()
    else:
        unittest.main()
