# -*- coding: utf-8 -*-
"""SONUC KAYNAGI: basketbol / buz hokeyi / tenis (Flashscore herkese acik veri akisi).

NEDEN: 12.10.2026 denetiminde bu uc bransta hicbir sinyal sonucla eslestirilemiyordu -
`data/arsiv/basketbol_*.json` 42 macta `sonuc` alani YOK, hokey/tenis arsivi yok,
`settled_log.jsonl`in 4562 kaydinin tamami mackolik_arsiv (yalniz futbol). Dolayisiyla
"balina/kirilim sinyali %X tuttu" sorusu bu bransarda HESAPLANAMIYORDU.

Bu modul yalniz SONUC getirir ve sinyalleri kapatir; analiz/tahmin uretmez.
Kaynak fetch_flash ile ayni akis (global.flashscore.ninja); spor kimligi besleme adinda:
  f_<spor>_<gun>_3_en_1   -> 1 futbol | 2 tenis | 3 basketbol | 4 hokey
AB=3 = mac bitti. AG/AH = ev/deplasman nihai skoru (basketbolda sayi, hokeyde gol,
teniste KAZANILAN SET). BA..BH = ceyrek/periyot/set detayi (bu modulde kullanilmaz).

Kullanim:
  python sonuc_multi.py arsivle [--gun-geri 7]    gunluk sonuclari diske yazar
  python sonuc_multi.py kapat                     sinyal_log multi satirlarini kapatir
  python sonuc_multi.py rapor                     brans basina isabet / basabas / ROI
"""
import argparse
import difflib
import glob
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import msa_kaynak as K

ARSIV = os.path.join(SCRIPT_DIR, "data", "arsiv")
KAPALI = os.path.join(SCRIPT_DIR, "data", "multi", "settled_multi.jsonl")
SINYAL = os.path.join(SCRIPT_DIR, "data", "history", "sinyal_log.jsonl")

SPOR_ID = {"tenis": 2, "basketbol": 3, "hokey": 4}
BITTI = "3"                 # Flashscore AB kodu
AD_BENZERLIK = 0.70         # takim/oyuncu adi esleme tabani
TS_PENCERE = 3 * 3600       # kickoff +- bu kadar saniye icinde eslesir


# ---------------------------------------------------------------- kaynak
def gun_sonuclari(session, spor, gun_farki=-1, feed_fn=None, parse_fn=None):
    """-> [{"id","ts","ev","dep","sh","sa"}] yalniz BITMIS maclar. Ag hatasinda []."""
    if spor not in SPOR_ID:
        return []
    if feed_fn is None or parse_fn is None:
        import fetch_flash as FF
        feed_fn, parse_fn = FF.feed, FF.parse
    ad = "f_%d_%d_3_en_1" % (SPOR_ID[spor], gun_farki)
    out = []
    for d in parse_fn(feed_fn(session, ad)):
        if d.get("AB") != BITTI or "AA" not in d or "AD" not in d:
            continue
        try:
            sh, sa = int(d["AG"]), int(d["AH"])
            ts = int(d["AD"])
        except (KeyError, TypeError, ValueError):
            continue            # skoru okunamayan mac SAYILMAZ (uydurma yok)
        out.append({"id": d["AA"], "ts": ts, "ev": (d.get("AE") or "").strip(),
                    "dep": (d.get("AF") or "").strip(), "sh": sh, "sa": sa})
    return out


def _yol(spor, gun):
    return os.path.join(ARSIV, "sonucmulti_%s_%s.json" % (spor, gun))


def arsivle(gun_geri=7, sporlar=None, session=None, gun_fn=None):
    """Son gun_geri gunun sonuclarini diske yazar (gun basina tek dosya). -> {spor: yazilan}."""
    sporlar = sporlar or list(SPOR_ID)
    if session is None and gun_fn is None:
        import fetch_flash as FF
        session = FF.make_session()
    gun_fn = gun_fn or gun_sonuclari
    os.makedirs(ARSIV, exist_ok=True)
    ozet = {}
    for spor in sporlar:
        n = 0
        for g in range(-gun_geri, 1):
            try:
                mac = gun_fn(session, spor, g)
            except Exception:
                continue
            if not mac:
                continue
            gun = (datetime.now(K.TR) + timedelta(days=g)).strftime("%Y%m%d")
            yol = _yol(spor, gun)
            eski = {}
            if os.path.exists(yol):
                try:
                    eski = json.load(io.open(yol, encoding="utf-8")).get("maclar", {})
                except Exception:
                    eski = {}
            eski.update({m["id"]: m for m in mac})
            with io.open(yol, "w", encoding="utf-8") as f:
                json.dump({"spor": spor, "gun": gun, "cekim": time.time(), "maclar": eski},
                          f, ensure_ascii=False)
            n += len(mac)
        ozet[spor] = n
    return ozet


def arsiv_oku(spor=None):
    """Diske yazilmis tum sonuclar -> {spor: [mac, ...]}"""
    out = {}
    for y in sorted(glob.glob(os.path.join(ARSIV, "sonucmulti_*.json"))):
        try:
            d = json.load(io.open(y, encoding="utf-8"))
        except Exception:
            continue
        sp = d.get("spor")
        if spor and sp != spor:
            continue
        out.setdefault(sp, []).extend((d.get("maclar") or {}).values())
    return out


# ---------------------------------------------------------------- esleme
def _ad(s):
    """Takim/oyuncu adini esleme icin normalize eder: 'Mannarino,A.' ~ 'Mannarino A.'
    Kadin isareti birlestirilir: iddaa '(K)' ~ Flashscore 'W' -> tek 'w' jetonu."""
    t = re.sub(r"[.,()]", " ", K.fold(s))
    t = re.sub(r"\b(k|w)\b", "w", t)
    return re.sub(r"\s+", " ", t).strip()


def _jeton(s):
    return [x for x in _ad(s).split(" ") if x]


KAPSAMA_SKORU = 0.92        # tam esitlikten dusuk: gercek tam eslesme her zaman kazanir


def _benzer(a, b):
    """Dizge benzerligi VE jeton kapsamasinin en iyisi.

    Neden kapsama gerekti (12.10.2026 olcumu): iddaa basketbolda SEHIR adi kullaniyor
    ("Indiana", "Oklahoma"), Flashscore tam kulup adi ("Indiana Pacers", "Oklahoma City
    Thunder"). Duz SequenceMatcher orani 0.62-0.65 veriyordu, yani 0.70 tabaninin altinda:
    288 basketbol sinyalinin 144'u (yarisi) bu yuzden eslesemiyordu.

    Kadin/erkek karismasini onlemek icin 'w' jetonu iki tarafta da ayni olmali - aksi halde
    "New York" hem Knicks hem Liberty W ile eslesebilirdi."""
    ja, jb = _jeton(a), _jeton(b)
    if not ja or not jb:
        return 0.0
    oran = difflib.SequenceMatcher(None, " ".join(ja), " ".join(jb)).ratio()
    kisa, uzun = (ja, jb) if len(ja) <= len(jb) else (jb, ja)
    if ("w" in ja) != ("w" in jb):
        # Biri kadin biri erkek takimi: ayni sehrin iki takimi yuksek dizge orani verir
        # ("New York (K)" vs "New York Knicks" = 0.80) - esleme TABANININ ALTINA cekilir.
        return min(oran, AD_BENZERLIK - 0.01)
    if set(kisa) <= set(uzun):
        return max(oran, KAPSAMA_SKORU)
    return oran


def eslestir(ev, dep, ts, sonuclar, taban=AD_BENZERLIK, pencere=TS_PENCERE):
    """Sinyalin maci -> sonuc kaydi | None. Hem duz hem TERS (ev/dep yer degismis) dener."""
    en, en_skor = None, taban
    for m in sonuclar:
        if abs((m.get("ts") or 0) - ts) > pencere:
            continue
        duz = (_benzer(ev, m["ev"]) + _benzer(dep, m["dep"])) / 2.0
        ters = (_benzer(ev, m["dep"]) + _benzer(dep, m["ev"])) / 2.0
        skor, cevir = (duz, False) if duz >= ters else (ters, True)
        if skor > en_skor:
            en, en_skor = (m, cevir), skor
    if not en:
        return None
    m, cevir = en
    return {"id": m["id"], "ts": m["ts"], "sh": m["sa"] if cevir else m["sh"],
            "sa": m["sh"] if cevir else m["sa"], "cevrildi": cevir, "benzerlik": round(en_skor, 3)}


# ---------------------------------------------------------------- pazar cozumu
_SAYI = r"([-+]?\d+(?:[.,]\d+)?)"


def _f(x):
    return float(str(x).replace(",", "."))


def coz(pazar, ev_ad, dep_ad, sh, sa):
    """Pazar metni + nihai skor -> True (tuttu) | False | None (cozulemedi).

    None DONEN pazar elle sayilmaz: olculmeyen sinyal 'tutmadi' sayilirsa isabet
    yapay olarak duser. Desteklenen aileler sinyal_log'da fiilen bulunanlardir."""
    if sh is None or sa is None:
        return None
    p = (pazar or "").strip()
    sol, _, sag = p.rpartition(":")
    sol, sec = sol.strip(), sag.strip()
    if not sol:
        return None
    dusuk = K.fold(sol)
    sec_f = K.fold(sec)

    # 1) Mac Sonucu [(Uzt.Dahil) | (Uz. Dahil)] -> secim bir TAKIM adi
    if re.fullmatch(r"ma[cç] sonucu(\s*\((uzt\.?dahil|uz\.? dahil)\))?", dusuk):
        if sh == sa:
            return False                       # beraberlik: iki taraf da kaybeder
        kazanan = ev_ad if sh > sa else dep_ad
        return _benzer(sec, kazanan) >= AD_BENZERLIK

    # 2) Handikapli Mac Sonucu [(Uzt.Dahil) | (Set)] <hat> -> secim TAKIM adi
    m = re.match(r"handikapl[iı] ma[cç] sonucu(\s*\((uzt\.?dahil|set)\))?\s*" + _SAYI + r"\s*$", dusuk)
    if m:
        hat = _f(m.group(3))
        ev_mi = _benzer(sec, ev_ad) >= _benzer(sec, dep_ad)
        # iddaa konvansiyonu: hat SECILEN tarafa eklenir
        fark = (sh - sa) if ev_mi else (sa - sh)
        return (fark + hat) > 0

    # 3) Toplam (mac geneli): "Toplam Sayi Alt/Ust (Uzt.Dahil) <hat>" | "Alti/Ustu <hat>"
    m = re.match(r"(toplam say[iı] )?alt[iı]?/[uü]st[uü]?(\s*\(uzt\.?dahil\))?\s*" + _SAYI + r"\s*$", dusuk)
    if m:
        return _alt_ust(sec_f, sh + sa, _f(m.group(3)))

    # 4) Takim toplami: "Ev Sahibi Alt/Ust [(Uzt.Dahil)] <hat>" / "Deplasman Alti/Ustu <hat>"
    m = re.match(r"(ev sahibi|deplasman) alt[iı]?/[uü]st[uü]?(\s*\(uzt\.?dahil\))?\s*" + _SAYI + r"\s*$", dusuk)
    if m:
        taraf = sh if m.group(1) == "ev sahibi" else sa
        return _alt_ust(sec_f, taraf, _f(m.group(3)))

    return None


def _alt_ust(sec_f, deger, hat):
    if sec_f.startswith("alt"):
        return deger < hat
    if sec_f.startswith("ust") or sec_f.startswith("üst"):
        return deger > hat
    return None


# ---------------------------------------------------------------- kapatma
def _jsonl(yol):
    out = []
    if not os.path.exists(yol):
        return out
    for ln in io.open(yol, encoding="utf-8"):
        ln = ln.strip()
        if ln:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    return out


def _anahtar(s):
    return "%s|%s|%s|%s" % (s.get("kaynak"), s.get("esd_ms"), s.get("ev"), s.get("pazar"))


def kapat(sonuclar=None, sinyaller=None, yaz=True):
    """sinyal_log'un multi satirlarini sonuclarla kapatir. TEKILDIR: daha once kapanmis
    sinyal yeniden yazilmaz (kayitli 'anahtar' alani okunur - settle_arsiv'deki ayni tuzak).
    -> ozet dict."""
    sinyaller = sinyaller if sinyaller is not None else [
        s for s in _jsonl(SINYAL) if str(s.get("kaynak", "")).startswith("multi:")]
    sonuclar = sonuclar if sonuclar is not None else arsiv_oku()
    var = {k.get("anahtar") for k in _jsonl(KAPALI)}
    yeni, ozet = [], {"aday": 0, "eslesmedi": 0, "pazar_cozulmedi": 0, "yeni": 0, "zaten": 0}
    for s in sinyaller:
        ozet["aday"] += 1
        ak = _anahtar(s)
        if ak in var:
            ozet["zaten"] += 1
            continue
        spor = str(s.get("kaynak", "")).split(":")[-1]
        mac = str(s.get("ev") or "")
        ev_ad, _, dep_ad = mac.partition(" - ")
        ts = (s.get("esd_ms") or 0) / 1000.0
        es = eslestir(ev_ad, dep_ad, ts, sonuclar.get(spor) or [])
        if not es:
            ozet["eslesmedi"] += 1
            continue
        tuttu = coz(s.get("pazar"), ev_ad, dep_ad, es["sh"], es["sa"])
        if tuttu is None:
            ozet["pazar_cozulmedi"] += 1
            continue
        var.add(ak)
        yeni.append({"anahtar": ak, "spor": spor, "mac": mac, "pazar": s.get("pazar"),
                     "oran": s.get("oran"), "tip": s.get("tip"), "karar": s.get("karar"),
                     "dusus": s.get("dusus"), "guven": s.get("guven"),
                     "skor": [es["sh"], es["sa"]], "tuttu": bool(tuttu),
                     "benzerlik": es["benzerlik"], "kapanis_ts": time.time()})
    if yaz and yeni:
        os.makedirs(os.path.dirname(KAPALI), exist_ok=True)
        with io.open(KAPALI, "a", encoding="utf-8") as f:
            for k in yeni:
                f.write(json.dumps(k, ensure_ascii=False) + "\n")
    ozet["yeni"] = len(yeni)
    return ozet


def _oz(rows):
    n = len(rows)
    if not n:
        return None
    kaz = sum(1 for r in rows if r["tuttu"])
    o = [r["oran"] for r in rows if isinstance(r.get("oran"), (int, float))]
    ort = sum(o) / len(o) if o else 0.0
    kar = sum((r["oran"] - 1.0) if r["tuttu"] else -1.0 for r in rows
              if isinstance(r.get("oran"), (int, float)))
    return {"n": n, "isabet": 100.0 * kaz / n, "oran": ort,
            "basabas": (100.0 / ort) if ort else 0.0,
            "roi": (100.0 * kar / len(o)) if o else 0.0}


def rapor(yazdir=True):
    rows = _jsonl(KAPALI)
    if not rows:
        if yazdir:
            print("settled_multi.jsonl bos. Once: python sonuc_multi.py arsivle && ... kapat")
        return {}
    out = {}
    if yazdir:
        print("%-12s %5s %8s %8s %9s %9s %s" % ("brans", "n", "isabet", "ort.or", "basabas", "ROI", "karar"))
    for spor in ("basketbol", "hokey", "tenis"):
        o = _oz([r for r in rows if r["spor"] == spor])
        if not o:
            continue
        out[spor] = o
        if yazdir:
            kar = o["isabet"] > o["basabas"]
            karar = ("KARLI" if kar else "ZARARLI") if o["n"] >= 30 else "n<30"
            print("%-12s %5d %7.1f%% %8.2f %8.1f%% %+8.1f%% %s"
                  % (spor, o["n"], o["isabet"], o["oran"], o["basabas"], o["roi"], karar))
    tum = _oz(rows)
    if yazdir and tum:
        print("%-12s %5d %7.1f%% %8.2f %8.1f%% %+8.1f%%" % ("TOPLAM", tum["n"], tum["isabet"],
                                                             tum["oran"], tum["basabas"], tum["roi"]))
    out["toplam"] = tum
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="sonuc_multi")
    p.add_argument("komut", choices=["arsivle", "kapat", "rapor"])
    p.add_argument("--gun-geri", type=int, default=7)
    a = p.parse_args(argv)
    if a.komut == "arsivle":
        print("arsivlendi:", arsivle(a.gun_geri))
    elif a.komut == "kapat":
        print("kapatma:", kapat())
    else:
        rapor()


if __name__ == "__main__":
    main()
