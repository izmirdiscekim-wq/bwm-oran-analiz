# -*- coding: utf-8 -*-
"""
BACKTEST_TAKTIK - oran taktiklerinin KAR KARNESI: N, isabet, ROI, CLV + aralik esnetme taramasi.

NEDEN AYRI ARAC: `bwm.py taktik --arsiv` yalnizca isabet SAYISI veriyor. Isabet orani tek
basina yanilticidir (1.30 oranda %70 isabet zarardir); karar icin ROI ve CLV gerekir.

VERI: data/arsiv/nesine_*.json - her mac kaydi hem `ilk` (ilk gorulen oran) hem `son`
(kickoff'a en yakin = kapanis) anlik goruntusunu ve `sonuc`u tutuyor. Bu ikili sayesinde
GERCEK CLV geriye donuk hesaplanabiliyor: acilista tetiklenen sinyalin oynadigi secenek
kapanisa kadar kisaldi mi (pozitif CLV) yoksa acildi mi (negatif CLV).

SONUCLANDIRMA: `basari` ifadesine GUVENILMIYOR, oynanan kodun kendi kurali kullaniliyor.
Olculen hata (07.10.2026): 3 taktikte `basari` oynanan pazarla uyusmuyordu ->
  - "İY-MS Korelasyon A": oyna=kgvar ama basari=top>=3  (3-0 maci "tuttu" sayiyordu)
  - "İY-MS B1/B2": oyna=u35 (3.5 Ust = 4+ gol) ama basari=top>=3 (2-1 maci "tuttu" sayiyordu)
Bu yuzden isabet oranlari sisirilmis gorunuyordu.

    python backtest_taktik.py                  # tum aktif taktikler, tam karne
    python backtest_taktik.py --esnet          # aralik esnetme taramasi (1.0 / 1.25 / 1.5 / 2.0)
    python backtest_taktik.py --taktik "4,5"   # ad filtresi
"""
import argparse
import glob
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import taktik as T

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ARSIV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "arsiv")
MIN_N_KARAR = 30            # bu orneklem altinda ROI/isabet KANIT sayilmaz (Buchdahl)
ROI_KORU = 3.0              # yuzde: bu ustu KORU adayi
ESNETME = (1.0, 1.25, 1.5, 2.0)
ACIK_UC = 99.0              # bu ust sinirdan itibaren bant "acik uclu" sayilir (genisletilmez)

# Oynanan kodun KENDI sonuclandirma kurali (ev, dep, iy=(iyev,iydep)) -> True/False/None
SONUC_KURALI = {
    "u45": ("4,5 Üst (5+ gol)", lambda e, d, iy: (e + d) >= 5),
    "a45": ("4,5 Alt (<=4 gol)", lambda e, d, iy: (e + d) <= 4),
    "u35": ("3,5 Üst (4+ gol)", lambda e, d, iy: (e + d) >= 4),
    "a35": ("3,5 Alt (<=3 gol)", lambda e, d, iy: (e + d) <= 3),
    "u25": ("2,5 Üst (3+ gol)", lambda e, d, iy: (e + d) >= 3),
    "a25": ("2,5 Alt (<=2 gol)", lambda e, d, iy: (e + d) <= 2),
    "u15": ("1,5 Üst (2+ gol)", lambda e, d, iy: (e + d) >= 2),
    "a15": ("1,5 Alt (<=1 gol)", lambda e, d, iy: (e + d) <= 1),
    "tg6": ("6+ gol", lambda e, d, iy: (e + d) >= 6),
    "tg01": ("0-1 gol", lambda e, d, iy: (e + d) <= 1),
    "tg23": ("2-3 gol", lambda e, d, iy: 2 <= (e + d) <= 3),
    "tg45": ("4-5 gol", lambda e, d, iy: 4 <= (e + d) <= 5),
    "kgvar": ("KG Var", lambda e, d, iy: e > 0 and d > 0),
    "kgyok": ("KG Yok", lambda e, d, iy: not (e > 0 and d > 0)),
    "ms1": ("MS 1", lambda e, d, iy: e > d),
    "msx": ("MS X", lambda e, d, iy: e == d),
    "ms2": ("MS 2", lambda e, d, iy: d > e),
    "iyskor11": ("İY 1-1", lambda e, d, iy: iy is not None and iy == (1, 1)),
    "iyskor00": ("İY 0-0", lambda e, d, iy: iy is not None and iy == (0, 0)),
    "iyu05": ("İY 0,5 Üst (İY'de 1+ gol)", lambda e, d, iy: iy is not None and sum(iy) >= 1),
    "iyu15": ("İY 1,5 Üst (İY'de 2+ gol)", lambda e, d, iy: iy is not None and sum(iy) >= 2),
    "iya15": ("İY 1,5 Alt", lambda e, d, iy: iy is not None and sum(iy) <= 1),
    "iykg": ("İY KG Var", lambda e, d, iy: iy is not None and iy[0] > 0 and iy[1] > 0),
}


# ---------------------------------------------------------------- arsiv
def maclar(gun_geri=None):
    """-> [{id, ev, dep, lig, kickoff, ilk, son, sonuc}] (yalniz sonuclanmis maclar)."""
    out = []
    for yol in sorted(glob.glob(os.path.join(ARSIV, "nesine_*.json"))):
        try:
            d = json.load(io.open(yol, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for mid, v in (d.get("maclar") or {}).items():
            s = v.get("sonuc") or {}
            if not s.get("ms") or None in s["ms"]:
                continue
            out.append({"id": mid, "ev": v.get("home"), "dep": v.get("away"),
                        "lig": v.get("league"), "kickoff": v.get("kickoff"),
                        "ilk": v.get("ilk") or [], "son": v.get("son") or v.get("ilk") or [],
                        "sonuc": s})
    return out


# ---------------------------------------------------------------- kural degerlendirme
def _deger(mk, t, sov, n):
    return T._deger(mk, t, sov, n)


def _kural_degeri(k, mk, mk_ilk):
    """Kuralin o anki degeri (None = pazar kapali). taktik._mk_icin ile ayni mantik."""
    hedef_mk = mk_ilk if k.ilk else mk
    if k.fark:
        v1 = _deger(hedef_mk, k.t, k.sov, k.n)
        v2 = _deger(hedef_mk, k.t2, k.sov2, k.n2)
        return abs(v1 - v2) if (v1 is not None and v2 is not None) else None
    if k.fav:
        fav = T._favori(hedef_mk)
        if fav is None:
            return None
        kod = (k.fav_ev if fav == "ev" else k.fav_dep)
        return _deger(hedef_mk, *T._kod_coz(kod))
    return _deger(hedef_mk, k.t, k.sov, k.n)


def _genis(araliklar, faktor, taban=1.01):
    """Araligi faktor kadar genislet (merkez sabit). >=lo (sonsuz ust) aralikta yalniz lo duser.

    taban: alt sinir. Oran kurallarinda 1.01 (oran 1'in altina inemez) ama KORELASYON
    (fark) kurallarinda deger iki oranin FARKI'dir, 0'a yakin olabilir -> taban 0.0.
    (07.10.2026: taban her kuralda 1.01 oldugu icin "2:2/+6 Gol" taktigi esnetildiginde
    0 tetige dusuyordu - aralik (0.0,0.6) iken alt sinir 1.01'e sicriyordu.)"""
    if faktor == 1.0:
        return araliklar
    out = []
    for lo, hi in araliklar:
        # ACIK UCLU bant (>=lo ya da 10.00-99.00 gibi): genisletmek bandi degil KOSULU kaldirir.
        # Yalniz alt sinir oransal olarak indirilir, ust sinira dokunulmaz.
        if hi == float("inf") or hi >= ACIK_UC:
            out.append((max(taban, lo - (lo * (faktor - 1) / 2.0)), hi))
        else:
            orta, yari = (lo + hi) / 2.0, (hi - lo) / 2.0 * faktor
            out.append((max(taban, orta - yari), orta + yari))
    return out


def gecer(k, v, faktor=1.0):
    if k.kapali:
        return v is None
    if v is None:
        return False
    for lo, hi in _genis(k.araliklar, faktor, taban=(0.0 if k.fark else 1.01)):
        if lo - 1e-9 <= v <= hi + 1e-9:
            return True
    return False


# ---------------------------------------------------------------- sonuclandirma
def _oyna_kodlari(t, mk):
    """Taktigin oynadigi kodlar (favori-bagimli kodlar cozulur)."""
    out = []
    for o in (t.get("oyna") or []):
        kod = o.lower()
        if kod in T.FAV_KODLAR:
            fav = T._favori(mk)
            if fav is None:
                continue
            kod = T.FAV_KODLAR[kod][0 if fav == "ev" else 1]
        out.append(kod)
    return out


def _tuttu(kod, sonuc):
    kural = SONUC_KURALI.get(kod)
    if not kural:
        return None
    e, d = sonuc["ms"]
    iy = tuple(sonuc["iy"]) if (sonuc.get("iy") and None not in sonuc["iy"]) else None
    try:
        return bool(kural[1](e, d, iy))
    except Exception:
        return None


# ---------------------------------------------------------------- tek taktik karnesi
def karne(t, mac_listesi, faktor=1.0):
    """-> {n, n_sonuclu, tuttu, isabet, roi, clv, clv_n, bahis} (anlik goruntu: 'ilk' tetikleme)."""
    bahisler = []
    for m in mac_listesi:
        mk_ilk, mk_son = m["ilk"], m["son"]
        if not mk_ilk:
            continue
        degerler = [_kural_degeri(k, mk_ilk, mk_ilk) for k in t["kurallar"]]
        if not all(gecer(k, v, faktor) for k, v in zip(t["kurallar"], degerler)):
            continue
        for kod in _oyna_kodlari(t, mk_ilk):
            oran_ilk = _deger(mk_ilk, *T._kod_coz(kod))
            oran_son = _deger(mk_son, *T._kod_coz(kod))
            if not oran_ilk:
                continue
            hit = _tuttu(kod, m["sonuc"])
            bahisler.append({"kod": kod, "oran": oran_ilk, "kapanis": oran_son, "hit": hit,
                             "mac": "%s - %s" % (m["ev"], m["dep"]), "lig": m["lig"],
                             "skor": m["sonuc"]["ms"]})
    sonuclu = [b for b in bahisler if b["hit"] is not None]
    tuttu = sum(1 for b in sonuclu if b["hit"])
    kar = sum((b["oran"] - 1.0) if b["hit"] else -1.0 for b in sonuclu)
    clv = [100.0 * (b["oran"] / b["kapanis"] - 1.0) for b in bahisler
           if b["kapanis"] and b["oran"]]
    return {"n": len(bahisler), "n_sonuclu": len(sonuclu), "tuttu": tuttu,
            "isabet": (100.0 * tuttu / len(sonuclu)) if sonuclu else None,
            "roi": (100.0 * kar / len(sonuclu)) if sonuclu else None,
            "ort_oran": (sum(b["oran"] for b in sonuclu) / len(sonuclu)) if sonuclu else None,
            "clv": (sum(clv) / len(clv)) if clv else None, "clv_n": len(clv),
            "bahis": bahisler}


def kategori(k):
    """🟢 KORU / 🟡 REVİZE / 🔴 ELE - orneklem ve ROI'ye gore."""
    if not k["n_sonuclu"]:
        return "🔴 ELE", "hiç tetiklenmedi (sonuçlu örnek 0)"
    if k["n_sonuclu"] < MIN_N_KARAR:
        if (k["roi"] or -99) > ROI_KORU:
            return "🟡 REVİZE", "ROI +%.1f%% ama örneklem n=%d < %d (kanıt değil)" % (k["roi"], k["n_sonuclu"], MIN_N_KARAR)
        return "🔴 ELE", "örneklem n=%d < %d ve ROI %+.1f%%" % (k["n_sonuclu"], MIN_N_KARAR, k["roi"] or 0)
    if (k["roi"] or -99) > ROI_KORU:
        return "🟢 KORU", "ROI +%.1f%%, n=%d" % (k["roi"], k["n_sonuclu"])
    return "🔴 ELE", "ROI %+.1f%% <= %%%g, n=%d" % (k["roi"] or 0, ROI_KORU, k["n_sonuclu"])


def _satir(ad, k, kat=None, sebep=None, ek=""):
    return "| %s | %d | %d | %s | %s | %s | %s | %s |" % (
        ad[:40] + ek, k["n"], k["n_sonuclu"],
        ("%.0f%%" % k["isabet"]) if k["isabet"] is not None else "-",
        ("%.2f" % k["ort_oran"]) if k["ort_oran"] else "-",
        ("%+.1f%%" % k["roi"]) if k["roi"] is not None else "-",
        ("%+.2f%% (n=%d)" % (k["clv"], k["clv_n"])) if k["clv"] is not None else "-",
        (kat or ""))


def rapor(ad_filtre=None, esnet=False):
    ms = maclar()
    _, tum = T.dosya_oku()
    akt = [t for t in tum if t["aktif"] and t.get("spor", "futbol") == "futbol" and t["kurallar"]]
    if ad_filtre:
        akt = [t for t in akt if ad_filtre.lower() in t["ad"].lower()]
    print("Arşiv: %d sonuçlanmış maç | aktif futbol taktiği: %d | sonuçlandırma: oynanan kodun "
          "kendi kuralı (basari ifadesi kullanılmadı)" % (len(ms), len(akt)))
    print("")
    print("| Taktik | Tetik | Sonuçlu | İsabet | Ort.oran | ROI | CLV (açılış→kapanış) | Karar |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    kararlar = []
    for t in akt:
        k = karne(t, ms)
        kat, sebep = kategori(k)
        kararlar.append((t, k, kat, sebep))
        print(_satir(t["ad"], k, kat))
    print("")
    for t, k, kat, sebep in kararlar:
        print("  %-42s %s - %s" % (t["ad"][:42], kat, sebep))
    if esnet:
        print("")
        print("## Aralık esnetme taraması (merkez sabit, genişlik x faktör)")
        print("")
        print("| Taktik | " + " | ".join("x%.2f  n/ROI" % f for f in ESNETME) + " |")
        print("|---" * (len(ESNETME) + 1) + "|")
        for t in akt:
            huc = []
            for f in ESNETME:
                k = karne(t, ms, f)
                huc.append("%d / %s" % (k["n_sonuclu"],
                                        ("%+.0f%%" % k["roi"]) if k["roi"] is not None else "-"))
            print("| %s | %s |" % (t["ad"][:36], " | ".join(huc)))
    return kararlar


def main(argv=None):
    p = argparse.ArgumentParser(prog="backtest_taktik.py")
    p.add_argument("--taktik", help="ad filtresi")
    p.add_argument("--esnet", action="store_true", help="aralık esnetme taraması")
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    rapor(a.taktik, a.esnet)


if __name__ == "__main__":
    main()
