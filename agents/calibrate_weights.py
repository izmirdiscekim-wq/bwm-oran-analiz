# -*- coding: utf-8 -*-
"""
CALIBRATE_WEIGHTS - multi_sport_analyzer.py'nin VARSAYIM-temelli (CALIBRATED=False) B2B / Tempo /
Kaleci / Tenis Zemin katsayilarini, biriken sinyal_log.jsonl kayitlarini gerceklesen kapanis
oran degeriyle (CLV) eslestirip SciPy ile (Ridge regresyon, kapali-form) kalibre eder.

VERI DURUMU (durustce belirtilir): multi_sport_analyzer._logla() bayrakli sinyalleri DOGRUDAN
history_log.sinyal_coklu_yaz() ile sinyal_log.jsonl'e yazar; clv.py'nin iz_kaydet() -> kapanis_yakala()
-> clv_hesapla() zinciri (5 dakikalik dongude calisir) SADECE futbol taktik/balina sinyalleri icindir
(Nesine futbol pazar kodlari/MK yapisini kullanir, basketbol/hokey/tenis'i taniMAZ). Yani basketbol/
hokey/tenis sinyalleri icin CLV su an OTOMATIK URETILMIYOR - bu modul o boru hattini KURMAZ (kapsam
disi, ayri bir basketbol/hokey/tenis kapanis-yakalama hatti gerektirir); sadece ESLESTIRME YAPISINI
(kaynak+esd_ms+takimlar anahtari, clv.py'nin CLV_DOSYA="clv_log.jsonl" ciktisiyla AYNI sema) kurar ve
veri birikince calisir. Bugun n=0 oldugu icin egit() "YETERSIZ VERI" doner - uydurma katsayi URETILMEZ
(xgboost_model.py'deki ayni durustluk ilkesi).

YONTEM: her spor kendi ozellik(ler)ini tasir (basketbol: B2B+Tempo birlikte; hokey: B2B+Kaleci
birlikte; tenis: yalniz Zemin) - AYNI sinyalde birlikte varyasyon gosterdikleri icin TEK regresyonda
(confounding'den kacinmak icin). Ozellik degerleri, COEF'teki GUNCEL katsayiyla ZATEN HESAPLANMIS
uygulanan sinyal (orn. dinlenme_puan = BK_B2B ya da BK_REST3). Ridge regresyonun kattigi EGIM (slope),
"su anki katsayiyla uretilen 1 birim sinyal, gercekte kac birim CLV getiriyor" sorusunun cevabidir:
egim ~ 1.0 ise kalibrasyon dogru, egim > 1 ise sinyal GUCLENDIRILMELI (COEF'i egimle carp), egim < 1
ise HAFIFLETILMELI. calibrated_weights.json SADECE bu carpani ("olcek") tasir; multi_sport_analyzer.py
CALIBRATED=True oldugunda bu olcegi karsilik gelen COEF degerine CARPAR (coef'i DEGISTIRMEZ, kopyasini
kalibre eder) - eksik/yetersiz veri olan olcekler 1.0 (no-op) kalir.

SciPy SADECE YEREL EGITIMDE kullanilir (requirements.txt'e EKLENMEDI - xgboost_model.py'deki ayni
"model egitimi yerelde, bulut yalniz JSON okur" ilkesiyle; Render build/bellek butcesini zorlamaz).

    python calibrate_weights.py durum             # kac sinyal/eslesme var, esik asildi mi (spor basina)
    python calibrate_weights.py egit              # n>=50 (spor basina) ise Ridge ile kalibre eder, JSON yazar
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import history_log as HL

MIN_N = 50          # spor basina asgari eslesen sinyal sayisi (xgboost_model.MIN_TRAIN ile ayni esik)
ALPHA = 1.0          # Ridge L2 ceza katsayisi (intercept cezalandirilmaz)
SINYAL_DOSYA = "sinyal_log.jsonl"
CLV_DOSYA = "clv_log.jsonl"          # clv.py ile AYNI dosya/sema
CALIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "calibrated_weights.json")

# spor -> (ozellik_adlari, hedef COEF anahtar(lar)i) - sadece dokumantasyon/raporlama icin
HEDEF_COEF = {
    "basketbol": {"B2B": "BK_B2B", "Tempo": "BK_PACE_W"},
    "hokey": {"B2B": ["HK_B2B_OWN", "HK_B2B_OPP"], "Kaleci": "HK_GK_K"},
    "tenis": {"Tenis Zemin": "TN_BETA"},
}


def _logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1.0 - p))


# ---------------------------------------------------------------- ozellik cikarma (f sozlugunden)
def _ozellikler_basketbol(f):
    """-> [dinlenme_puan, tempo_sapmasi] | None (eksikse)."""
    dp = f.get("dinlenme_puan")
    tp = f.get("tempo")
    if dp is None or tp is None:
        return None
    return [dp, tp - 1.0]


def _ozellikler_hokey(f):
    """-> [b2b_farki, kaleci_farki] | None (eksikse)."""
    b2b = f.get("b2b") or [None, None]
    kk = f.get("kaleci_kats") or [None, None]
    bh, ba = b2b
    gh, ga = kk
    if bh is None or ba is None or gh is None or ga is None:
        return None
    return [(1.0 if bh else 0.0) - (1.0 if ba else 0.0), gh - ga]


def _ozellikler_tenis(f):
    """-> [zemin_logit_farki] | None (eksikse)."""
    wr = f.get("zemin_wr") or [None, None]
    wh, wa = wr
    if wh is None or wa is None:
        return None
    return [_logit(wh) - _logit(wa)]


_OZELLIK_FN = {"basketbol": _ozellikler_basketbol, "hokey": _ozellikler_hokey, "tenis": _ozellikler_tenis}
_ISIMLER = {"basketbol": ["B2B", "Tempo"], "hokey": ["B2B", "Kaleci"], "tenis": ["Tenis Zemin"]}


# ---------------------------------------------------------------- eslestirme (sinyal <-> CLV)
def _mac_parcala(ev):
    ev = ev or ""
    if " - " not in ev:
        return ev.strip(), ""
    h, _, a = ev.partition(" - ")
    return h.strip(), a.strip()


def _clv_anahtarlari():
    """clv_log.jsonl -> {(kaynak, esd_ms, ev_lower, dep_lower): clv_pct}."""
    d = {}
    for k in HL.sinyal_oku(dosya=CLV_DOSYA):
        anah = (k.get("kaynak"), k.get("esd_ms"), str(k.get("ev", "")).lower(), str(k.get("dep", "")).lower())
        if k.get("clv_pct") is not None:
            d[anah] = k["clv_pct"]
    return d


def esle(spor=None):
    """sinyal_log.jsonl'deki 'multi:<spor>' sinyallerini clv_log.jsonl ile eslestirir.
    -> {spor: [(ozellik_vektoru, clv_pct), ...]}  (CLV'si olmayan sinyaller elenir)."""
    clv = _clv_anahtarlari()
    out = {sp: [] for sp in _OZELLIK_FN}
    for k in HL.sinyal_oku(dosya=SINYAL_DOSYA):
        kaynak = k.get("kaynak") or ""
        if not kaynak.startswith("multi:"):
            continue
        sp = kaynak.split(":", 1)[1]
        if sp not in _OZELLIK_FN or (spor and sp != spor):
            continue
        f = k.get("f") or {}
        oz = _OZELLIK_FN[sp](f)
        if oz is None:
            continue
        h, a = _mac_parcala(k.get("ev", ""))
        anah = (kaynak, k.get("esd_ms"), h.lower(), a.lower())
        clv_pct = clv.get(anah)
        if clv_pct is None:
            continue
        out[sp].append((oz, clv_pct))
    return out


# ---------------------------------------------------------------- Ridge regresyon (kapali-form, SciPy)
def _ridge_fit(X, y, alpha=ALPHA):
    """X: n x p ozellik matrisi, y: n uzunlugunda hedef. -> (intercept, [egim...]).
    Kapali-form Ridge: beta = (X'X + alpha*I)^-1 X'y, intercept CEZALANDIRILMAZ (ortalaması
    cikarilip ayrica eklenir)."""
    import numpy as np
    from scipy import linalg

    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    x_ort = X.mean(axis=0)
    y_ort = y.mean()
    Xc, yc = X - x_ort, y - y_ort
    p = Xc.shape[1]
    A = Xc.T @ Xc + alpha * np.eye(p)
    b = Xc.T @ yc
    egim = linalg.solve(A, b, assume_a="pos")
    intercept = y_ort - x_ort @ egim
    return float(intercept), [float(e) for e in egim]


def _olcek_hesapla(egim_degeri):
    """Ridge egimi -> COEF carpan katsayisi. Egim negatif/0 ise (sinyal yon DEGISTIRMIS ya da
    etkisiz) olcek guvenlik icin 0.0'a (sinyal devre disi, uydurma yon degistirme yok) sabitlenir;
    > 3.0 ise (asiri az veri gurultusu) 3.0 ile sinirlanir (STAKE_CAP mantigiyla ayni, asiri
    agresif kalibrasyona karsi)."""
    if egim_degeri is None:
        return 1.0
    return max(0.0, min(3.0, egim_degeri))


# ---------------------------------------------------------------- CLI
def durum():
    eslesen = esle()
    print("| Spor | Eslesen (CLV'li) n | Esik | Durum |")
    print("|---|---:|---:|---|")
    for sp, kayitlar in eslesen.items():
        n = len(kayitlar)
        d = "KALIBRE EDILEBILIR" if n >= MIN_N else "YETERSIZ VERI (%d/%d)" % (n, MIN_N)
        print("| %s | %d | %d | %s |" % (sp, n, MIN_N, d))
    toplam = sum(len(v) for v in eslesen.values())
    if toplam == 0:
        print("\nNOT: 0 eslesme. Sebep: multi_sport_analyzer sinyalleri clv.py'nin kapanis-yakalama "
              "zincirinden GECMIYOR (futbol-ozel Nesine pazar kodlarina bagli) - basketbol/hokey/tenis "
              "icin CLV henuz otomatik uretilmiyor (ayri hat gerekir, kapsam disi).")


def egit():
    eslesen = esle()
    sonuc = {"guncelleme_ts": time.time(), "esik": MIN_N, "spor": {}}
    herhangi = False
    for sp, kayitlar in eslesen.items():
        isimler = _ISIMLER[sp]
        n = len(kayitlar)
        if n < MIN_N:
            sonuc["spor"][sp] = {"n": n, "durum": "YETERSIZ VERI (%d/%d)" % (n, MIN_N),
                                  "olcekler": {ad: 1.0 for ad in isimler}}
            continue
        X = [oz for oz, _ in kayitlar]
        y = [clv for _, clv in kayitlar]
        intercept, egimler = _ridge_fit(X, y)
        olcekler = {ad: round(_olcek_hesapla(e), 4) for ad, e in zip(isimler, egimler)}
        sonuc["spor"][sp] = {"n": n, "durum": "KALIBRE EDILDI", "intercept": round(intercept, 4),
                              "olcekler": olcekler, "hedef_coef": HEDEF_COEF[sp]}
        herhangi = True
    os.makedirs(os.path.dirname(CALIB_PATH), exist_ok=True)
    with open(CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(sonuc, f, ensure_ascii=False, indent=2)
    if herhangi:
        print("Kalibrasyon yazildi -> %s" % CALIB_PATH)
    else:
        print("HICBIR SPOR icin esik asilmadi (n>=%d yok) - %s dosyasi yine de yazildi, "
              "tum olcekler 1.0 (no-op / kalibrasyonsuz varsayim davranisi korunur)." % (MIN_N, CALIB_PATH))
    for sp, d in sonuc["spor"].items():
        print("  %-9s n=%-3d %s" % (sp, d["n"], d["durum"]))
    return sonuc


def oku_olcek(spor, isim):
    """multi_sport_analyzer.py'nin calistirma-zamaninda cagirdigi okuma fonksiyonu.
    -> float olcek (dosya/kayit yoksa 1.0, no-op)."""
    try:
        with open(CALIB_PATH, encoding="utf-8") as f:
            veri = json.load(f)
        return float(veri.get("spor", {}).get(spor, {}).get("olcekler", {}).get(isim, 1.0))
    except (OSError, ValueError, KeyError, TypeError):
        return 1.0


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="calibrate_weights.py")
    p.add_argument("komut", choices=["durum", "egit"])
    a = p.parse_args(argv)
    (durum if a.komut == "durum" else egit)()


if __name__ == "__main__":
    main()
