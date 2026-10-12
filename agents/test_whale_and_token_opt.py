# -*- coding: utf-8 -*-
"""BALINA KALIBRASYONU + TOKEN MALIYETI - tek betik, SALT OKUMA, ag cagrisi YOK.

NEDEN ARSIVDEN: balina.py'nin kendi gunlugu (data/balina/*.jsonl, n=345) sinyal
URETMEMIS - `tip` dolu 0 kayit, `dp_w` 345/345 sifir (dunya orani cekimi lig basina
3 kredi, gunluk tavan 12 -> gunde 4 tek cekim; ayni maci 2. kez cekmek 6 kredi
gerektiriyor, olculen: 33 anahtarin yalniz 3'u iki kez cekilmis). Dolayisiyla
"balina sinyali % kac tuttu" o gunlukten HESAPLANAMAZ.

Bunun yerine ayni soru, SONUCU BILINEN gercek oran hareketi uzerinden yanitlanir:
nesine arsivi her mac icin `ilk` (ilk gorulen oran) ve `son` (son gorulen oran)
tutuyor. dusus = son/ilk - 1. Bu, "balina" tanimnin olculebilir karsiligidir ve
n buyuk (1182 sonuclu mac).

Olculen eksenler: dusus siddeti x zamanlama (son cekimin kickoff'a uzakligi) x
lig seviyesi; fake-out kalibrasyonu; cizgi (Alt/Ust hatti) kaymasi duyarliligi.
"""
import collections
import glob
import io
import json
import os
import re
import sys
from datetime import datetime

AG = os.path.dirname(os.path.abspath(__file__))
ARSIV = os.path.join(AG, "data", "arsiv")

MS1, MSX, MS2 = (1, None, 1), (1, None, 2), (1, None, 3)
AU_ALT, AU_UST = (12, 2.5, 1), (12, 2.5, 2)
ESIK = 0.10          # COEF["KIRILIM"] / "balina" esigi
MIN_N = 30           # altinda hicbir dilim hakkinda karar verilmez

# Majör lig tespiti: ad kalibi (kesin bir lig tablosu yok, bu bir YAKLASIMDIR)
MAJOR = re.compile(r"(premier lig|la liga|serie a|bundesliga|ligue 1|s[uü]per lig|"
                   r"şampiyonlar ligi|avrupa ligi|uefa|championship|eredivisie|"
                   r"primeira liga|brezilya serie a|arjantin primera)", re.I)


def _deger(mk, hedef):
    t, sov, n = hedef
    for m in mk or []:
        if m.get("t") == t and (sov is None or abs((m.get("sov") or 0) - sov) < 0.01):
            v = (m.get("o") or {}).get(str(n))
            if v and v > 1.01:
                return v
    return None


def _hat(mk, t=12):
    """Pazarin SOV (cizgi) degeri - Alt/Ust hattinin kaydigini gormek icin."""
    for m in mk or []:
        if m.get("t") == t:
            return m.get("sov")
    return None


def maclar(gun_geri=40):
    out = []
    for f in sorted(glob.glob(os.path.join(ARSIV, "nesine_*.json"))):
        g = os.path.basename(f)[7:15]
        if not re.fullmatch(r"\d{8}", g):
            continue
        try:
            d = json.load(io.open(f, encoding="utf-8"))["maclar"]
        except Exception:
            continue
        for r in d.values():
            s = r.get("sonuc") or {}
            ms = s.get("ms")
            if not ms or None in ms or not r.get("ilk") or not r.get("son"):
                continue
            try:
                ko = datetime.fromisoformat(r["kickoff"]).timestamp()
                sc = datetime.fromisoformat(r["son_cekim"]).timestamp() if r.get("son_cekim") else ko
            except Exception:
                continue
            out.append({"ev": r["home"], "dep": r["away"], "lig": r.get("league") or "?",
                        "ms": ms, "iy": s.get("iy") if s.get("iy") and None not in (s.get("iy") or [None]) else None,
                        "ilk": r["ilk"], "son": r["son"], "dk_kala": max(0.0, (ko - sc) / 60.0)})
    return out


def adaylar(ms, esik=ESIK):
    """Oran DUSEN (lehte hareket) secenekler -> balina adayi satirlari."""
    out = []
    for m in ms:
        ev, dep = m["ms"]
        for ad, hedef, tuttu in (("MS 1", MS1, ev > dep), ("MS X", MSX, ev == dep),
                                 ("MS 2", MS2, dep > ev), ("2.5 Alt", AU_ALT, ev + dep <= 2),
                                 ("2.5 Ust", AU_UST, ev + dep >= 3)):
            i, s = _deger(m["ilk"], hedef), _deger(m["son"], hedef)
            if not (i and s):
                continue
            dusus = s / i - 1.0
            if dusus > -esik:
                continue
            out.append({"pazar": ad, "ilk": i, "son": s, "dusus": dusus, "tuttu": tuttu,
                        "dk_kala": m["dk_kala"], "lig": m["lig"],
                        "major": bool(MAJOR.search(m["lig"])), "ms": m["ms"],
                        "hat_ilk": _hat(m["ilk"]), "hat_son": _hat(m["son"])})
    return out


def oz(rows):
    n = len(rows)
    if not n:
        return None
    kaz = sum(1 for r in rows if r["tuttu"])
    ort = sum(r["son"] for r in rows) / n
    kar = sum((r["son"] - 1.0) if r["tuttu"] else -1.0 for r in rows)
    return {"n": n, "isabet": 100.0 * kaz / n, "oran": ort, "basabas": 100.0 / ort,
            "roi": 100.0 * kar / n}


def tablo(baslik, gruplar):
    print("\n" + baslik)
    print("  %-26s %5s %8s %8s %8s %8s %s" % ("dilim", "n", "isabet", "ort.or", "basabas", "ROI", "karar"))
    for ad, rows in gruplar:
        o = oz(rows)
        if not o:
            continue
        kar = o["isabet"] > o["basabas"]
        karar = ("KARLI" if kar else "ZARARLI") if o["n"] >= MIN_N else "n<%d" % MIN_N
        print("  %-26s %5d %7.1f%% %8.2f %7.1f%% %+7.1f%% %s"
              % (ad[:26], o["n"], o["isabet"], o["oran"], o["basabas"], o["roi"], karar))


def main():
    ms = maclar()
    ad = adaylar(ms)
    print("=" * 86)
    print("BALINA KALIBRASYONU - arsiv: %d sonuclu mac | oran DUSUSU >= %%%d olan aday: %d"
          % (len(ms), ESIK * 100, len(ad)))
    print("=" * 86)
    g = oz(ad)
    if not g:
        print("aday yok")
        return
    print("GENEL: n=%d | isabet %.1f%% | ort.oran %.2f | basabas %.1f%% | ROI %+.1f%%"
          % (g["n"], g["isabet"], g["oran"], g["basabas"], g["roi"]))

    tablo("1) DUSUS SIDDETI", [
        ("%10-15 dusus", [r for r in ad if -0.15 < r["dusus"] <= -0.10]),
        ("%15-25 dusus", [r for r in ad if -0.25 < r["dusus"] <= -0.15]),
        ("%25+ dusus", [r for r in ad if r["dusus"] <= -0.25])])

    tablo("2) ZAMANLAMA (son cekimin kickoff'a uzakligi)", [
        ("T-20dk ve yakini", [r for r in ad if r["dk_kala"] <= 20]),
        ("T-20dk..T-2sa", [r for r in ad if 20 < r["dk_kala"] <= 120]),
        ("T-2sa..T-8sa", [r for r in ad if 120 < r["dk_kala"] <= 480]),
        ("T-8sa+", [r for r in ad if r["dk_kala"] > 480])])

    tablo("3) LIG SEVIYESI", [
        ("major lig", [r for r in ad if r["major"]]),
        ("alt lig / diger", [r for r in ad if not r["major"]])])

    tablo("4) PAZAR", [(p, [r for r in ad if r["pazar"] == p])
                       for p in ("MS 1", "MS X", "MS 2", "2.5 Alt", "2.5 Ust")])

    # ---- FAKE-OUT: kullanicinin ornegi (alt lig, MS 1/2 dususu, mac BERABERE bitiyor)
    print("\n" + "=" * 86)
    print("FAKE-OUT (TUZAK) KALIBRASYONU")
    print("=" * 86)
    ms12 = [r for r in ad if r["pazar"] in ("MS 1", "MS 2")]
    for etiket, grup in (("TUM MS 1/2 dususleri", ms12),
                         ("alt lig MS 1/2", [r for r in ms12 if not r["major"]]),
                         ("major lig MS 1/2", [r for r in ms12 if r["major"]])):
        if not grup:
            continue
        bera = sum(1 for r in grup if r["ms"][0] == r["ms"][1])
        print("  %-24s n=%4d | BERABERE biten %4d (%.1f%%) | isabet %.1f%%"
              % (etiket, len(grup), bera, 100.0 * bera / len(grup),
                 100.0 * sum(1 for r in grup if r["tuttu"]) / len(grup)))

    # ---- CIZGI DUYARLILIGI: oran duserken Alt/Ust HATTI sabit mi kaydi mi?
    au = [r for r in ad if r["pazar"] in ("2.5 Alt", "2.5 Ust")
          and r["hat_ilk"] is not None and r["hat_son"] is not None]
    sabit = [r for r in au if abs(r["hat_son"] - r["hat_ilk"]) < 0.01]
    kaymis = [r for r in au if abs(r["hat_son"] - r["hat_ilk"]) >= 0.01]
    tablo("5) CIZGI (Alt/Ust hatti) DUYARLILIGI - oran duserken hat ne yapti", [
        ("hat SABIT (supheli)", sabit), ("hat KAYMIS (teyitli)", kaymis)])

    # ---- KONTROL GRUBU: dususu olmayan secenekler ayni donemde ne yapti?
    kontrol = []
    for m in ms:
        for adn, hedef, tuttu in (("MS 1", MS1, m["ms"][0] > m["ms"][1]),
                                  ("MS 2", MS2, m["ms"][1] > m["ms"][0])):
            i, s = _deger(m["ilk"], hedef), _deger(m["son"], hedef)
            if i and s and abs(s / i - 1.0) < 0.02:          # neredeyse hic hareket yok
                kontrol.append({"son": s, "tuttu": tuttu})
    k = oz(kontrol)
    print("\n" + "=" * 86)
    print("KONTROL GRUBU (oran HAREKETSIZ, |degisim| < 2 puan): n=%d | isabet %.1f%% | basabas %.1f%% | ROI %+.1f%%"
          % (k["n"], k["isabet"], k["basabas"], k["roi"]) if k else "kontrol grubu yok")
    if k and g:
        print("  -> balina adaylari kontrol grubundan %+.1f puan isabet, %+.1f puan ROI farki"
              % (g["isabet"] - k["isabet"], g["roi"] - k["roi"]))

    # ---- TOKEN MALIYETI
    print("\n" + "=" * 86)
    print("TOKEN / API MALIYETI")
    print("=" * 86)
    print("  Bu analiz: 0 LLM cagrisi, 0 ag cagrisi (yalniz yerel arsiv okundu).")
    try:
        sys.path.insert(0, AG)
        import multi_sport_analyzer as M
        r = {"spor": "basketbol", "mac": "Los Angeles Lakers - Boston Celtics", "lig": "NBA",
             "ts": 1791750000, "pick": "Maç Sonucu: Los Angeles Lakers", "oran": 1.85,
             "ilk": 2.10, "dusus": -0.119, "ev": 0.03, "kasa": 1.5, "bayrak": "SINYAL",
             "p_mod": 0.6, "p_mkt": 0.55, "p": 0.57, "ev_ham": 0.08, "risk": "Düşük",
             "skor": "110-105", "guven": 4, "f": {"b2b": -2.0}, "eksik": [], "n": (10, 10),
             "kirilimlar": [], "kirilim_kendi": None, "konfluens": None, "karar": "OYNA",
             "risk_neden": [], "konfluens_detay": None, "konfluens_aciklama": None}
        pipe = M.to_pipe_format(r)
        js = json.dumps(M.kompakt(r), ensure_ascii=False)
        print("  PIPE serializer : %3d bayt/mac" % len(pipe.encode("utf-8")))
        print("  kompakt JSON    : %3d bayt/mac  -> %.1fx tasarruf"
              % (len(js.encode("utf-8")), len(js.encode("utf-8")) / max(len(pipe.encode("utf-8")), 1)))
    except Exception as exc:
        print("  PIPE olcumu atlandi: %s" % exc)
    print("  Balina dunya-orani kotasi: 3 kredi/lig x gunluk tavan 12 = gunde 4 lig cekimi")
    print("  (bugun harcanan: 0 - radar penceresinde mac yoktu)")


if __name__ == "__main__":
    main()
