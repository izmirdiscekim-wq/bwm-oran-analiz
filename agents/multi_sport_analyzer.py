# -*- coding: utf-8 -*-
"""
MULTI_SPORT_ANALYZER - Basketbol / Buz Hokeyi / Tenis icin BWM sinyal motoru.

TASARIM (minimum token, maksimum mantik):
  1. Ham veri (recent-matches, pazar listesi) LLM'e GIDERMEZ. Power rating, tempo, dinlenme,
     kaleci, zemin, Shin de-vig, EV, Kelly - hepsi burada, SAF PYTHON (numpy/scipy yok -> Render'da da calisir).
  2. LLM'e yalniz bayrakli (flagged) maclar, mac basina ~250 byte'lik kompakt JSON olarak gider.
  3. Cikti: karar + tahmini skor + Kelly kasa yuzdesi. Dolgu yok.

KATSAYI DURUSTLUGU: COEF tablosundaki degerlerin cogu literatur-temelli VARSAYIMDIR, bu veriyle
KALIBRE EDILMEDI (CALIBRATED=False). Her sinyal uygulanan katsayilari "f" alaninda tasir; sinyal
gunlugu (history_log) birikince CLV ile kalibre edilir. Kanitsiz katsayiyla "banko" denmez.

    python multi_sport_analyzer.py tara --spor hepsi --saat 12          # tablo
    python multi_sport_analyzer.py tara --spor hokey --json             # LLM'e gidecek kompakt JSON
    python multi_sport_analyzer.py kaleci "Toronto Maple Leafs" onayli 0.918
    python multi_sport_analyzer.py durum                                # veri kaynaklari saglik
"""
import argparse
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import devig
import elo_tracker as ET
import fetch_iddaa as F
import msa_kaynak as K
import pynum

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

TR = K.TR
CALIBRATED = False

# ---------------------------------------------------------------- katsayilar (VARSAYIM - kalibre degil)
COEF = {
    # ortak (multi_sport.py ile ayni, orada olculmus)
    "W_MKT": 0.70,           # nihai olasilikta piyasa agirligi (model~piyasa: kanit tabani)
    "MIN_PROB": 0.10,        # cok dusuk olasilikli sonuclarda model hatasi EV'yi sisirir
    "MIN_ORAN": 1.30,        # proje kurali (CLAUDE.md / BOA): 1.30 alti oranda marj EV'yi yer
    "MAX_REL_DIV": 0.30,     # model piyasadan >%30 uzaksa model hatasi sayilir, aday degil
    "MIN_N": 5,              # takim basina asgari son-mac (altinda model yok)
    "K_SHRINK": 4.0,         # kucuk orneklemde havuza cekme
    "MIN_EV_HAM": 0.05,      # IZLE: ham model EV esigi
    "EV_SINYAL": 0.02,       # SINYAL: harmanlanmis EV esigi (iddaa marji yuzunden nadir olmasi BEKLENIR)
    "KIRILIM": 0.10,         # oran kirilimi esigi (odds_watcher ile ayni)
    "STAKE_CAP": 2.0,        # tek bahis tavani (% kasa) - decision.stake ile ayni
    "KELLY": {"Düşük": 0.25, "Orta": 0.25, "Yüksek": 0.125},
    # basketbol
    "BK_HA": 2.5, "BK_SD_M": 13.0, "BK_SD_T": 18.0,    # multi_sport.SPORTS ile ayni
    "BK_B2B": -2.0,          # varsayim: back-to-back takim puan farki cezasi
    "BK_REST3": 0.5,         # varsayim: >=3 gun dinlenme ufak bonus
    "BK_PACE_W": 0.25,       # varsayim: tempo etkilesiminin toplam puana agirligi
    "BK_PACE_CLAMP": 0.05,   # tempo katsayisi 1 +- bu kadar
    "BK_HCP_RISK": 3.0,      # (|handikap| - |ort. fark|) > 3 -> YUKSEK RISK (CLAUDE.md kurali)
    "BK_ELO_W": 0.5,         # varsayim: lokal ELO SoS farkinin puan-farkina agirligi (elo_tracker)
    # buz hokeyi
    "HK_HA": 0.04, "HK_B2B_OWN": 0.97, "HK_B2B_OPP": 1.03,   # varsayim: yorgun takim 3% az atar, 3% fazla yer
    "HK_SV_LIG": 0.905, "HK_SV_YEDEK": 0.895, "HK_GK_K": 15.0, "HK_GK_CLAMP": (0.85, 1.15),
    "HK_ELO_W": 0.5,         # varsayim: lokal ELO SoS farkinin gol-oranina agirligi (elo_tracker)
    # tenis
    "TN_BETA": 0.80,         # varsayim: ham galibiyet-orani logit farkini kisma (rakip gucu duzeltilmedi)
    "TN_ZEMIN_K": 3.0,       # zemin ornegini genel galibiyet oranina cekme agirligi
    "TN_GAMMA": 20.0,        # Barnett-Clarke yakinsamasi: ~%1 nokta avantaji ~ %9-10 mac olasiligi
    "TN_ELO_W": 0.60,        # varsayim: servis verisi YOKKEN zemin-ELO farkinin logit'e agirligi (fallback)
    "TN_ELO_MIN_N": 3,       # zemin-ELO fallback'i yalniz iki tarafta da >= bu kadar zemin orneginde
    "TN_P_CLAMP": (0.08, 0.92),
}
if CALIBRATED:
    # calibrate_weights.py: sinyal_log.jsonl + CLV eslesmesinden Ridge ile olculmus olcek
    # (dosya/kayit yoksa 1.0 = no-op). HK_B2B_* gibi 1.0-merkezli carpanlarda 1.0'dan SAPMA
    # olceklenir (0 olcek -> coef=1.0, etkisiz; coef'in kendisi degil sapmasi buyur/kuculur).
    try:
        import calibrate_weights as _CW
        COEF["BK_B2B"] *= _CW.oku_olcek("basketbol", "B2B")
        COEF["BK_PACE_W"] *= _CW.oku_olcek("basketbol", "Tempo")
        _hk_b2b = _CW.oku_olcek("hokey", "B2B")
        COEF["HK_B2B_OWN"] = 1.0 + (COEF["HK_B2B_OWN"] - 1.0) * _hk_b2b
        COEF["HK_B2B_OPP"] = 1.0 + (COEF["HK_B2B_OPP"] - 1.0) * _hk_b2b
        COEF["HK_GK_K"] *= _CW.oku_olcek("hokey", "Kaleci")
        COEF["TN_BETA"] *= _CW.oku_olcek("tenis", "Tenis Zemin")
    except Exception:
        pass  # kalibrasyon dosyasi yoksa/bozuksa VARSAYIM katsayilarla calismaya devam eder
SPORLAR = {
    "basketbol": {"st": 2, "yol": "basketball", "model": "normal", "cap": 5},
    "hokey": {"st": 4, "yol": "icehockey", "model": "poisson", "cap": 5},
    "tenis": {"st": 5, "yol": "tennis", "model": "tennis", "cap": 3},
}
TAKMA = {"basket": "basketbol", "basketbol": "basketbol", "buz": "hokey", "hokey": "hokey",
         "tenis": "tenis", "hepsi": "hepsi", "tum": "hepsi"}


# ---------------------------------------------------------------- matematik
def ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def shrunk(vals, pool, k=None):
    k = COEF["K_SHRINK"] if k is None else k
    n = len(vals)
    return (sum(vals) + k * pool) / (n + k) if n else pool


def ort(v):
    return sum(v) / len(v) if v else None


def std(v):
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def kelly_pct(p, oran, risk):
    """Kesirli Kelly, % kasa. EV <= EV_SINYAL ise 0 (taban/zorlama yok). Tavan STAKE_CAP."""
    if not p or not oran or oran <= 1.0:
        return 0.0
    ev = p * oran - 1.0
    if ev < COEF["EV_SINYAL"]:
        return 0.0
    k = ev / (oran - 1.0)
    return round(min(COEF["KELLY"][risk] * k * 100.0, COEF["STAKE_CAP"]), 2)


# ---------------------------------------------------------------- dinlenme katsayilari
def b2b_mi(gun):
    return gun is not None and gun <= 1.5


def dinlenme_puan_basket(gun):
    """Basketbol: takimin puan-farki duzeltmesi (varsayim)."""
    if gun is None:
        return 0.0
    if gun <= 1.5:
        return COEF["BK_B2B"]
    return COEF["BK_REST3"] if gun >= 3.0 else 0.0


def gk_faktor(kaleci):
    """Rakibe karsi atilacak gol carpani. kaleci: {'durum','sv','gp'} | None.
    Yalniz durum 'onayli' ya da 'yedek' ise uygulanir; 'belirsiz' -> 1.0 (uydurma yok)."""
    if not kaleci or kaleci.get("durum") not in ("onayli", "yedek"):
        return 1.0
    sv = kaleci.get("sv")
    if kaleci["durum"] == "yedek" and not sv:
        sv = COEF["HK_SV_YEDEK"]
    if not sv:
        return 1.0
    gp = kaleci.get("gp") or 20
    sv_eff = (sv * gp + COEF["HK_SV_LIG"] * COEF["HK_GK_K"]) / (gp + COEF["HK_GK_K"])
    lo, hi = COEF["HK_GK_CLAMP"]
    return max(lo, min(hi, (1.0 - sv_eff) / (1.0 - COEF["HK_SV_LIG"])))


# ---------------------------------------------------------------- pazar siniflandirma
def siniflandir(ad, sov):
    """Pazar adi -> (tur, taraf, cizgi, uzatma_dahil) | None.
    tur: win | hcp | sethcp | total | team. Tanimadigi (ceyrek, yari, periyot...) pazar None."""
    import re
    n = K.fold(ad)
    taban = re.sub(r"\s*-?\d+(?:[.,]\d+)?$", "", n)
    uz = bool(re.search(r"\(uz", taban))
    t = re.sub(r"\s*\(uz[^)]*\)", "", taban).strip()
    try:
        cizgi = float(str(sov).replace(",", ".")) if sov not in (None, "") else None
    except ValueError:
        cizgi = None
    if cizgi is None:
        m = re.search(r"-?\d+(?:\.\d+)?$", n)
        cizgi = float(m.group(0)) if m else None
    if t == "mac sonucu":
        return ("win", None, None, uz)
    if t == "handikapli mac sonucu" and cizgi is not None:
        return ("hcp", None, cizgi, uz)
    if t == "handikapli mac sonucu (set)" and cizgi is not None:
        return ("sethcp", None, cizgi, False)
    if t in ("alti/ustu", "alt/ust", "toplam sayi alti/ustu", "toplam sayi alt/ust") and cizgi is not None:
        return ("total", None, cizgi, uz)
    m = re.fullmatch(r"(ev sahibi|deplasman) (alti/ustu|alt/ust)", t)
    if m and cizgi is not None:
        return ("team", "h" if m.group(1) == "ev sahibi" else "a", cizgi, uz)
    return None


def yarim_mi(x):
    return x is not None and abs((x * 2) - round(x * 2)) < 1e-9 and abs(x - round(x)) > 1e-9


# ---------------------------------------------------------------- BASKETBOL modeli
def model_basket(gh, ga, ko_ts, havuz_tot=None, elo_sos=None):
    """Son-10 maca dayali guc/tempo/dinlenme modeli. elo_sos: (sos_h, sos_a) - elo_tracker.sos()'tan
    ELO puaninda rakip-gucu farki (SoS); None ise duzeltme uygulanmaz (eski davranis). -> model dict | None."""
    if min(len(gh), len(ga)) < COEF["MIN_N"]:
        return None
    pool = (sum(g["pf"] + g["pa"] for g in gh + ga)) / (2.0 * len(gh + ga))
    eh = (shrunk([g["pf"] for g in gh], pool) + shrunk([g["pa"] for g in ga], pool)) / 2.0
    ea = (shrunk([g["pf"] for g in ga], pool) + shrunk([g["pa"] for g in gh], pool)) / 2.0
    # tempo: iki takimin mac toplamlari / lig havuzu (yoksa nötr)
    tot_h, tot_a = ort([g["pf"] + g["pa"] for g in gh]), ort([g["pf"] + g["pa"] for g in ga])
    pace_idx = (((tot_h + tot_a) / 2.0) / havuz_tot) if havuz_tot else None
    c = COEF["BK_PACE_CLAMP"]
    tadj = 1.0 if pace_idx is None else max(1 - c, min(1 + c, 1.0 + COEF["BK_PACE_W"] * (pace_idx - 1.0)))
    rh, ra = K.dinlenme_gun(gh, ko_ts), K.dinlenme_gun(ga, ko_ts)
    rest_adj = dinlenme_puan_basket(rh) - dinlenme_puan_basket(ra)
    sos_h, sos_a = elo_sos if elo_sos else (None, None)
    # SoS duzeltmesi: takim gucu ortalamasi, ELO'da zayif rakiplere karsi sisirilmis/guclu rakiplere
    # karsi bastirilmis olabilir; farki (puan-sapmasi olceginde) geri ekler/cikarir.
    elo_adj = (COEF["BK_ELO_W"] * (sos_h - sos_a) / 400.0 * COEF["BK_SD_M"]) if elo_sos else 0.0
    mh = eh * tadj + COEF["BK_HA"] / 2.0 + rest_adj / 2.0 + elo_adj / 2.0
    ma = ea * tadj - COEF["BK_HA"] / 2.0 - rest_adj / 2.0 - elo_adj / 2.0
    tots = [g["pf"] + g["pa"] for g in gh + ga]
    sd_t = COEF["BK_SD_T"]
    if len(tots) >= 10 and std(tots):
        sd_t = math.sqrt(0.5 * std(tots) ** 2 + 0.5 * COEF["BK_SD_T"] ** 2)
    return {"kind": "normal", "mh": mh, "ma": ma, "sd_m": COEF["BK_SD_M"], "sd_t": sd_t,
            "sd_h": math.sqrt((sd_t ** 2 + COEF["BK_SD_M"] ** 2) / 4.0),
            "F_h": ort([g["pf"] - g["pa"] for g in gh]), "F_a": ort([g["pf"] - g["pa"] for g in ga]),
            "n": (len(gh), len(ga)),
            "f": {"dinlenme": [_r(rh), _r(ra)], "b2b": [b2b_mi(rh), b2b_mi(ra)],
                  "tempo": _r(pace_idx, 3), "tempo_kats": round(tadj, 3), "dinlenme_puan": round(rest_adj, 2),
                  "elo_sos": [_r(sos_h, 0), _r(sos_a, 0)]},
            "eksik": [x for x, v in (("dinlenme", rh is None or ra is None),
                                     ("tempo-havuzu", pace_idx is None)) if v]}


def _r(x, d=1):
    return None if x is None else round(x, d)


def olas_normal(mod, tur, taraf, cizgi, uz, anahtarlar):
    """Normal model -> {secenek: olasilik} | None."""
    mu, sd = mod["mh"] - mod["ma"], mod["sd_m"]
    if tur == "win":
        p1 = 1.0 - ncdf((0.5 - mu) / sd) if not uz else 1.0 - ncdf((0.0 - mu) / sd)
        if "0" in anahtarlar:
            p2 = ncdf((-0.5 - mu) / sd)
            return {"1": p1, "0": max(1.0 - p1 - p2, 0.0), "2": p2}
        return {"1": p1, "2": 1.0 - p1}
    if tur == "hcp":
        if not yarim_mi(cizgi) or "0" in anahtarlar:
            return None                                  # tam sayi cizgi = iade (push) riski, fiyatlanmaz
        p1 = 1.0 - ncdf((-cizgi - mu) / sd)
        return {"1": p1, "2": 1.0 - p1}
    if tur == "total" and yarim_mi(cizgi):
        ust = 1.0 - ncdf((cizgi - (mod["mh"] + mod["ma"])) / mod["sd_t"])
        return {"Üst": ust, "Alt": 1.0 - ust}
    if tur == "team" and yarim_mi(cizgi):
        ust = 1.0 - ncdf((cizgi - (mod["mh"] if taraf == "h" else mod["ma"])) / mod["sd_h"])
        return {"Üst": ust, "Alt": 1.0 - ust}
    return None


# ---------------------------------------------------------------- BUZ HOKEYI modeli
def model_hokey(gh, ga, ko_ts, havuz_gol=None, kal_h=None, kal_a=None, elo_sos=None):
    """elo_sos: (sos_h, sos_a) - elo_tracker.sos()'tan ELO SoS farki; None ise duzeltme uygulanmaz."""
    if min(len(gh), len(ga)) < COEF["MIN_N"]:
        return None
    pool = havuz_gol or (sum(g["pf"] + g["pa"] for g in gh + ga) / (2.0 * len(gh + ga)))
    eh = (shrunk([g["pf"] for g in gh], pool) + shrunk([g["pa"] for g in ga], pool)) / 2.0
    ea = (shrunk([g["pf"] for g in ga], pool) + shrunk([g["pa"] for g in gh], pool)) / 2.0
    rh, ra = K.dinlenme_gun(gh, ko_ts), K.dinlenme_gun(ga, ko_ts)
    bh, ba = b2b_mi(rh), b2b_mi(ra)
    lam = eh * (1 + COEF["HK_HA"]) * (COEF["HK_B2B_OWN"] if bh else 1.0) * (COEF["HK_B2B_OPP"] if ba else 1.0)
    mu = ea * (1 - COEF["HK_HA"]) * (COEF["HK_B2B_OWN"] if ba else 1.0) * (COEF["HK_B2B_OPP"] if bh else 1.0)
    sos_h, sos_a = elo_sos if elo_sos else (None, None)
    if elo_sos:
        elo_kat = max(0.85, min(1.15, 1.0 + COEF["HK_ELO_W"] * (sos_h - sos_a) / 400.0))
        lam, mu = lam * elo_kat, mu / elo_kat
    gk_h, gk_a = gk_faktor(kal_h), gk_faktor(kal_a)
    lam, mu = max(lam * gk_a, 0.2), max(mu * gk_h, 0.2)      # rakip kalecisi gol yemesini degistirir
    dur_h = (kal_h or {}).get("durum", "belirsiz")
    dur_a = (kal_a or {}).get("durum", "belirsiz")
    return {"kind": "poisson", "lam": lam, "mu": mu, "n": (len(gh), len(ga)),
            "kaleci": [dur_h, dur_a],
            "f": {"dinlenme": [_r(rh), _r(ra)], "b2b": [bh, ba], "kaleci": [dur_h, dur_a],
                  "kaleci_kats": [round(gk_h, 3), round(gk_a, 3)], "elo_sos": [_r(sos_h, 0), _r(sos_a, 0)]},
            "eksik": [x for x, v in (("kaleci-onayi", "onayli" not in (dur_h, dur_a) and "yedek" not in (dur_h, dur_a)),
                                     ("dinlenme", rh is None or ra is None)) if v]}


def _hokey_dagilim(mod, uz):
    """Resmi sure fark dagilimi {fark: p}. uz=True: beraberlik %50/%50 uzatma kazananina (+-1) dagitilir."""
    n = 14
    g = pynum.outer(pynum.pois(mod["lam"], n), pynum.pois(mod["mu"], n))
    d = {}
    for i, satir in enumerate(g):
        for j, v in enumerate(satir):
            d[i - j] = d.get(i - j, 0.0) + v
    tot = sum(d.values())
    d = {k: v / tot for k, v in d.items()}
    if uz and 0 in d:
        b = d.pop(0)
        d[1] = d.get(1, 0.0) + b / 2.0
        d[-1] = d.get(-1, 0.0) + b / 2.0
    return d


def olas_hokey(mod, tur, taraf, cizgi, uz, anahtarlar):
    if tur == "win":
        d = _hokey_dagilim(mod, uz)
        p1, p2 = sum(v for k, v in d.items() if k > 0), sum(v for k, v in d.items() if k < 0)
        if "0" in anahtarlar:
            return {"1": p1, "0": d.get(0, 0.0), "2": p2}
        return {"1": p1, "2": p2} if uz else None
    if tur == "hcp":
        d = _hokey_dagilim(mod, uz)
        if "0" in anahtarlar:                             # 3'lu (Avrupa) handikap: tam sayi cizgi
            if yarim_mi(cizgi):
                return None
            p1 = sum(v for k, v in d.items() if k + cizgi > 0)
            p0 = sum(v for k, v in d.items() if abs(k + cizgi) < 1e-9)
            return {"1": p1, "0": p0, "2": max(1.0 - p1 - p0, 0.0)}
        if not yarim_mi(cizgi):
            return None
        p1 = sum(v for k, v in d.items() if k + cizgi > 0)
        return {"1": p1, "2": 1.0 - p1}
    if uz:
        return None                                       # uzatma-dahil toplamlar fiyatlanmaz
    g = pynum.outer(pynum.pois(mod["lam"], 14), pynum.pois(mod["mu"], 14))
    if tur == "total" and yarim_mi(cizgi):
        ust = sum(v for i, r in enumerate(g) for j, v in enumerate(r) if i + j > cizgi)
        return {"Üst": ust, "Alt": 1.0 - ust}
    if tur == "team" and yarim_mi(cizgi):
        ust = sum(v for i, r in enumerate(g) for j, v in enumerate(r) if (i if taraf == "h" else j) > cizgi)
        return {"Üst": ust, "Alt": 1.0 - ust}
    return None


# ---------------------------------------------------------------- TENIS modeli
def _logit(p):
    return math.log(p / (1.0 - p))


def _sig(x):
    return 1.0 / (1.0 + math.exp(-x))


def q_coz(p, bo=3):
    """Mac kazanma olasiligi p -> tek set olasiligi q (bisection). bo3: q^2(3-2q); bo5: q^3(10-15q+6q^2)."""
    f = (lambda q: q * q * (3 - 2 * q)) if bo == 3 else (lambda q: q ** 3 * (10 - 15 * q + 6 * q * q))
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        lo, hi = (mid, hi) if f(mid) < p else (lo, mid)
    return (lo + hi) / 2.0


def zemin_wr(oy, zemin):
    """(zemin_wr, n_zemin, genel_wr). zemin yoksa genel."""
    kazan = lambda g: g["pf"] > g["pa"]
    n_all = len(oy)
    genel = (sum(1 for g in oy if kazan(g)) + 1.0) / (n_all + 2.0)
    if not zemin:
        return genel, 0, genel
    zz = [g for g in oy if K.tenis_zemin(g["lig"]) == zemin]
    k = COEF["TN_ZEMIN_K"]
    return (sum(1 for g in zz if kazan(g)) + k * genel) / (len(zz) + k), len(zz), genel


def model_tenis(gh, ga, lig, servis_h=None, servis_a=None, bo=3, surf_elo=None):
    """surf_elo: (elo_h, elo_a) - elo_tracker'dan AYNI ZEMINDE oynanan maclarla kurulmus
    oyuncu ELO'su. Servis/karsilama verisi YOKKEN (servis_ok=False) ve zemin orneklemi
    yeterliyse (COEF['TN_ELO_MIN_N']) servis teriminin YERINE kullanilir (fallback, ikisi
    birlikte uygulanmaz - ikisi de ayni 'rakip gucune gore duzeltme' sinyalini tasir)."""
    if min(len(gh), len(ga)) < COEF["MIN_N"]:
        return None
    zemin = K.tenis_zemin(lig)
    wh, nzh, _ = zemin_wr(gh, zemin)
    wa, nza, _ = zemin_wr(ga, zemin)
    x = COEF["TN_BETA"] * (_logit(wh) - _logit(wa))
    servis_ok = bool(servis_h and servis_a and None not in (servis_h.get("spw"), servis_h.get("rpw"),
                                                             servis_a.get("spw"), servis_a.get("rpw")))
    sv_fark = None
    elo_fallback = False
    if servis_ok:
        sv_fark = (servis_h["spw"] + servis_h["rpw"]) - (servis_a["spw"] + servis_a["rpw"])
        x += max(-1.5, min(1.5, COEF["TN_GAMMA"] * sv_fark))
    elif surf_elo and min(nzh, nza) >= COEF["TN_ELO_MIN_N"]:
        elo_h, elo_a = surf_elo
        x += max(-1.0, min(1.0, COEF["TN_ELO_W"] * (elo_h - elo_a) / 400.0))
        elo_fallback = True
    lo, hi = COEF["TN_P_CLAMP"]
    p = max(lo, min(hi, _sig(x)))
    return {"kind": "tennis", "p": p, "q": q_coz(p, bo), "bo": bo, "n": (len(gh), len(ga)),
            "f": {"zemin": zemin, "zemin_n": [nzh, nza], "zemin_wr": [round(wh, 3), round(wa, 3)],
                  "servis_farki": _r(sv_fark, 3), "elo_fallback": elo_fallback,
                  "zemin_elo": [_r(surf_elo[0], 0), _r(surf_elo[1], 0)] if elo_fallback else [None, None]},
            "eksik": [x for x, v in (("servis/karsilama", not servis_ok),
                                     ("zemin", zemin is None or min(nzh, nza) < 2)) if v]}


def olas_tenis(mod, tur, taraf, cizgi, uz, anahtarlar):
    p, q = mod["p"], mod["q"]
    if tur == "win":
        return {"1": p, "2": 1.0 - p}
    if tur == "sethcp" and mod["bo"] == 3 and cizgi in (-1.5, 1.5):
        p1 = q * q if cizgi < 0 else 1.0 - (1.0 - q) ** 2
        return {"1": p1, "2": 1.0 - p1}
    return None


OLAS = {"normal": olas_normal, "poisson": olas_hokey, "tennis": olas_tenis}


# ---------------------------------------------------------------- tahmini skor
def skor_tahmin(mod):
    if mod["kind"] == "normal":
        return "%d-%d" % (round(mod["mh"]), round(mod["ma"]))
    if mod["kind"] == "poisson":
        g = pynum.outer(pynum.pois(mod["lam"], 10), pynum.pois(mod["mu"], 10))
        i, j = max(((i, j) for i in range(10) for j in range(10)), key=lambda ij: g[ij[0]][ij[1]])
        return "%d-%d" % (i, j)
    q = mod["q"]
    if mod["bo"] == 3:
        adaylar = {"2-0": q * q, "2-1": 2 * q * q * (1 - q), "1-2": 2 * (1 - q) ** 2 * q, "0-2": (1 - q) ** 2}
    else:
        adaylar = {"3-0": q ** 3, "3-1": 3 * q ** 3 * (1 - q), "3-2": 6 * q ** 3 * (1 - q) ** 2,
                   "2-3": 6 * q ** 2 * (1 - q) ** 3, "1-3": 3 * q * (1 - q) ** 3, "0-3": (1 - q) ** 3}
    return max(adaylar, key=adaylar.get) + " set"


# ---------------------------------------------------------------- fiyatlama + secim
def secenek_adi(sec, home, away):
    return {"1": home, "2": away, "0": "Beraberlik", "X": "Beraberlik"}.get(sec, sec)


def fiyatla(spor, mod, pazar_listesi, home, away, hcp_ok=True):
    """Tum fiyatlanabilir pazar/secenekler -> satir listesi (ok=False olanlar da doner)."""
    kind = SPORLAR[spor]["model"]
    satirlar = []
    for ad, sov, outs in pazar_listesi:
        sn = siniflandir(ad, sov)
        if not sn or len(outs) < 2 or any(v <= 1.01 for v in outs.values()):
            continue
        tur, taraf, cizgi, uz = sn
        if tur == "hcp" and not hcp_ok:
            continue                                      # isaret konvansiyonu dogrulanamadi (hcp_isaret_testi)
        if not (1.0 <= sum(1.0 / v for v in outs.values()) <= 1.35):
            continue
        pm = devig.shin_probs(outs)
        pmod = OLAS[kind](mod, tur, taraf, cizgi, uz, set(outs))
        if not pm or not pmod:
            continue
        for sec, oran in outs.items():
            a, b = pm.get(sec), pmod.get("X" if sec == "0" and "X" in pmod else sec)
            if a is None or b is None:
                continue
            rel = abs(b - a) / a if a > 0 else 9.0
            pb = COEF["W_MKT"] * a + (1.0 - COEF["W_MKT"]) * b
            satirlar.append({"pazar": ad, "sov": sov, "tur": tur, "cizgi": cizgi, "sec": sec, "oran": oran,
                             "pick": "%s: %s" % (ad, secenek_adi(sec, home, away)),
                             "p_mkt": a, "p_mod": b, "p": pb, "ev": pb * oran - 1.0, "ev_ham": b * oran - 1.0,
                             "rel": rel, "grup": (tur, taraf, uz, "0" in outs, ad.split(" ")[0] == "Deplasman"),
                             "denge": _denge(pm),
                             "ok": (a >= COEF["MIN_PROB"] and rel <= COEF["MAX_REL_DIV"]
                                    and oran >= COEF["MIN_ORAN"])})
    return ana_hat(satirlar)


def _denge(pm):
    """Piyasanin 'esit' hatta ne kadar yakin oldugu (0 = tam ortada). 3'lu: |p1-p2|, 2'li: |p-0.5|."""
    k1 = "1" if "1" in pm else "Üst"
    if "0" in pm:
        return abs(pm.get("1", 0) - pm.get("2", 0))
    return abs(pm.get(k1, 0.5) - 0.5)


def ana_hat(satirlar):
    """Hat pazarlarinda (handikap/toplam/takim toplami) YALNIZ ana hatti birakir.

    Neden: ~30 alt-hat icinden en yuksek EV'yi secmek (winner's curse) model gurultusunu secmek
    demek; ana hat piyasanin en verimli fiyatladigi, modelle karsilastirmanin anlamli oldugu hattir.
    Ana hat = ayni pazar grubunda piyasa olasiligi 0.5'e en yakin hat."""
    en_iyi = {}
    for s_ in satirlar:
        if s_["tur"] in ("hcp", "total", "team", "sethcp"):
            g = s_["grup"]
            if g not in en_iyi or s_["denge"] < en_iyi[g][0]:
                en_iyi[g] = (s_["denge"], s_["cizgi"])
    return [s_ for s_ in satirlar
            if s_["tur"] not in ("hcp", "total", "team", "sethcp") or s_["cizgi"] == en_iyi[s_["grup"]][1]]


def hcp_isaret_testi(pazar_listesi):
    """Handikap isaret konvansiyonunu OLCER: iddaa'da sov'un EV SAHIBINE eklendigi varsayiliyor
    (sov=-6.5, ev sahibi 6.5 verir). Dogrulama: Mac Sonucu (2'li) favorisi ev sahibiyse, ana
    handikap hatti NEGATIF olmali (ev sahibi puan verir). -> True uyumlu / False ters / None test edilemedi."""
    ml = next((o for ad, sov, o in pazar_listesi
               if (siniflandir(ad, sov) or (None,))[0] == "win" and set(o) == {"1", "2"}), None)
    if not ml:
        return None
    p1 = devig.shin_probs(ml).get("1")
    if p1 is None or abs(p1 - 0.5) < 0.06:
        return None
    en_iyi = None
    for ad, sov, o in pazar_listesi:
        sn = siniflandir(ad, sov)
        if not sn or sn[0] != "hcp" or set(o) != {"1", "2"} or not yarim_mi(sn[2]):
            continue
        d = abs(devig.shin_probs(o).get("1", 0.5) - 0.5)
        if en_iyi is None or d < en_iyi[0]:
            en_iyi = (d, sn[2])
    if not en_iyi or en_iyi[1] == 0:
        return None
    return (en_iyi[1] < 0) == (p1 > 0.5)


def hcp_riski(spor, mod, satir):
    """Basketbol handikap kurali: (|H| - |F|) > 3 -> YUKSEK RISK. H, secilen takimin handikabi;
    F, secilen takimin son-mac ortalama farki. -> (yuksek_mi, aciklama) ya da (False, None)."""
    if spor != "basketbol" or satir["tur"] != "hcp" or satir["sec"] not in ("1", "2"):
        return False, None
    H = satir["cizgi"] if satir["sec"] == "1" else -satir["cizgi"]
    F_ = mod["F_h"] if satir["sec"] == "1" else mod["F_a"]
    if F_ is None:
        return False, None
    fark = abs(H) - abs(F_)
    return fark > COEF["BK_HCP_RISK"], "|H|=%.1f |F|=%.1f fark %.1f" % (abs(H), abs(F_), fark)


def risk_belirle(spor, mod, satir):
    """-> (risk, [gerekce]). Yuksek: handikap kurali / hokey kaleci belirsiz / ornek <8 + eksik veri."""
    neden, puan = [], 0
    yuksek, aciklama = hcp_riski(spor, mod, satir)
    if yuksek:
        neden.append("handikap riski (%s)" % aciklama)
        puan += 2
    if min(mod["n"]) < 8:
        neden.append("ornek %d/%d < 8" % mod["n"])
        puan += 1
    if "kaleci-onayi" in mod["eksik"]:
        neden.append("kaleci onayi yok")
        puan += 1
    if "servis/karsilama" in mod["eksik"]:
        neden.append("servis/karsilama verisi yok")
        puan += 1
    return ("Yüksek" if puan >= 2 else "Orta" if puan == 1 else "Düşük"), neden


def guven(spor, mod, aday_sayisi, ev):
    g = 2 + (min(mod["n"]) >= 5) + (min(mod["n"]) >= 8) + (aday_sayisi >= 4) + (ev >= -0.05)
    tavan = SPORLAR[spor]["cap"]
    if "kaleci-onayi" in mod["eksik"]:
        tavan = min(tavan, 4)
    if spor == "tenis" and mod.get("f", {}).get("elo_fallback"):
        tavan = max(tavan, 4)       # servis verisi yok ama zemin-ELO ikinci bir sinyal veriyor
    return max(1, min(g, tavan))


def degerlendir(spor, mac, mod, pazar_listesi, hcp_ok=True):
    """Tek mac -> sonuc sozlugu (bayrak dahil) ya da None (fiyatlanabilir aday yok)."""
    satirlar = fiyatla(spor, mod, pazar_listesi, mac["home"], mac["away"], hcp_ok)
    adaylar = [s for s in satirlar if s["ok"]]
    if not adaylar:
        return None
    b = max(adaylar, key=lambda s: s["ev"])
    ilk, _ = K.ilk_oran("%s|%s|%s|%s" % (SPORLAR[spor]["st"], mac["id"], b["pazar"], b["sec"]), b["oran"])
    dusus = (b["oran"] / ilk - 1.0) if ilk else 0.0
    risk, neden = risk_belirle(spor, mod, b)
    if b["ev"] >= COEF["EV_SINYAL"]:
        bayrak = "SINYAL"
    elif b["ev_ham"] >= COEF["MIN_EV_HAM"]:
        bayrak = "IZLE"
    elif abs(dusus) >= COEF["KIRILIM"]:
        bayrak = "KIRILIM"
    else:
        bayrak = None
    kasa = kelly_pct(b["p"], b["oran"], risk)
    return {"spor": spor, "mac": "%s - %s" % (mac["home"], mac["away"]), "lig": mac["league"], "ts": mac["ts"],
            "pick": b["pick"], "oran": b["oran"], "ilk": ilk, "dusus": dusus, "p_mod": b["p_mod"],
            "p_mkt": b["p_mkt"], "p": b["p"], "ev": b["ev"], "ev_ham": b["ev_ham"], "skor": skor_tahmin(mod),
            "kasa": kasa, "risk": risk, "risk_neden": neden, "bayrak": bayrak,
            "guven": guven(spor, mod, len(adaylar), b["ev"]), "f": mod["f"], "eksik": mod["eksik"], "n": mod["n"],
            "karar": ("OYNA" if bayrak == "SINYAL" and kasa > 0 else
                      "İZLE" if bayrak else "SKIP")}


def kompakt(r):
    """LLM'e giden tek-mac JSON: kisa anahtarlar, yuvarlanmis sayilar, ham veri YOK."""
    return {"s": r["spor"], "m": r["mac"], "lg": (r["lig"] or "")[:30],
            "ko": datetime.fromtimestamp(r["ts"], TR).strftime("%d.%m %H:%M"),
            "pick": r["pick"], "oran": r["oran"], "ilk": r["ilk"], "dusus%": round(r["dusus"] * 100, 1),
            "pm": round(r["p_mod"], 3), "pp": round(r["p_mkt"], 3), "pb": round(r["p"], 3),
            "ev": round(r["ev"] * 100, 1), "ev_ham": round(r["ev_ham"] * 100, 1), "skor": r["skor"],
            "kasa%": r["kasa"], "risk": r["risk"], "guven": r["guven"], "bayrak": r["bayrak"],
            "f": r["f"], "eksik": r["eksik"], "n": list(r["n"]), "not": r["risk_neden"]}


# ---------------------------------------------------------------- ultra-compact PIPE serializer
_SPOR_KOD = {"basketbol": "BSK", "hokey": "HOK", "tenis": "TEN"}
_FLG_KOD = {"SINYAL": "SGN", "IZLE": "IZL", "KIRILIM": "KRL"}
_MKT_KOD = [(r"handikapli mac sonucu \(set\)", "SHC"), (r"handikapli mac sonucu", "HCD"),
            (r"mac sonucu", "MS"), (r"ev sahibi (alti/ustu|alt/ust)", "TAH"),
            (r"deplasman (alti/ustu|alt/ust)", "TAD"), (r"(alti/ustu|alt/ust|toplam sayi)", "AU")]


def _kisalt_takim(ad):
    """Takim/oyuncu adi -> 2-3 harfli yaklasik kod (GERCEK lig kisaltmasi DEGIL, yalniz PIPE
    formatinda yer kazanmak icin yaklasik/deterministik kisaltma)."""
    kelimeler = (ad or "?").split()
    if len(kelimeler) >= 3:
        return "".join(w[0] for w in kelimeler[:3]).upper()
    if len(kelimeler) == 2:
        return (kelimeler[0][:1] + kelimeler[1][:2]).upper()
    return kelimeler[0][:3].upper() if kelimeler else "???"


def _pick_kod(pick, home, away):
    """'<pazar adi>: <secenek>' -> 'KOD(cizgi):1|2|Üst|Alt|X' kisa gosterim."""
    pazar, _, sec = pick.partition(": ")
    f = K.fold(pazar)
    kod = next((k for pat, k in _MKT_KOD if re.search(pat, f)), K.fold(pazar)[:3].upper())
    m = re.search(r"-?\d+(?:\.\d+)?$", pazar.strip())
    cizgi = "(%s)" % m.group(0) if m else ""
    if sec == home:
        etiket = "1"
    elif sec == away:
        etiket = "2"
    elif sec in ("Üst", "Alt"):
        etiket = sec
    else:
        etiket = "X"
    return "%s%s:%s" % (kod, cizgi, etiket)


def _fct_pipe(spor, f):
    """Spora gore en onemli 1-2 faktoru kisa 'AD(deger)' parcalarina cevirir."""
    f = f or {}
    bits = []
    if spor == "basketbol":
        if f.get("dinlenme_puan"):
            bits.append("B2B(%+.1f)" % f["dinlenme_puan"])
        tk = f.get("tempo_kats")
        if tk and abs(tk - 1.0) > 1e-9:
            bits.append("TMP(%.2f)" % tk)
    elif spor == "hokey":
        b2b = f.get("b2b") or [False, False]
        if any(b2b):
            bits.append("B2B(%d,%d)" % (int(b2b[0]), int(b2b[1])))
        gk = f.get("kaleci_kats") or [1.0, 1.0]
        if any(abs(x - 1.0) > 1e-9 for x in gk):
            bits.append("GK(%.2f,%.2f)" % tuple(gk))
    elif spor == "tenis":
        zw = f.get("zemin_wr")
        if zw:
            bits.append("ZMN(%.2f,%.2f)" % tuple(zw))
        sf = f.get("servis_farki")
        if sf is not None:
            bits.append("SRV(%+.3f)" % sf)
    return ",".join(bits)


def to_pipe_format(r):
    """kompakt() yerine LLM'e giden tek-mac JSON'u maç basina ~180 bayta indiren ultra-compact
    PIPE gosterim. Ornek: '[BSK]|LAL-BOS|FLG:SGN|MKT:HCD(-3.5)@1.85|EV:4.2%|KLY:1.5%|FCT:B2B(-2),TMP(1.02)'
    Ham veri (pf/pa/recent) TASIMAZ - kompakt() ile ayni token-frugality ilkesi."""
    home, _, away = r["mac"].partition(" - ")
    parcalar = [
        "[%s]" % _SPOR_KOD.get(r["spor"], r["spor"][:3].upper()),
        "%s-%s" % (_kisalt_takim(home), _kisalt_takim(away)),
        "FLG:%s" % _FLG_KOD.get(r["bayrak"], r["bayrak"] or "NA"),
        "MKT:%s@%.2f" % (_pick_kod(r["pick"], home.strip(), away.strip()), r["oran"]),
        "EV:%+.1f%%" % (r["ev"] * 100.0),
        "KLY:%.1f%%" % r["kasa"],
    ]
    fct = _fct_pipe(r["spor"], r.get("f"))
    if fct:
        parcalar.append("FCT:%s" % fct)
    return "|".join(parcalar)


# ---------------------------------------------------------------- tarama (ag katmani)
def _veri_cek(session, cfg, yol, mac):
    return {"pz": K.pazarlar(session, cfg, mac["id"]),
            "gh": K.oyunlar(K.son_maclar(session, yol, mac["id"], 0)),
            "ga": K.oyunlar(K.son_maclar(session, yol, mac["id"], 1))}


def _ml_p1(pazar_listesi):
    """Mac Sonucu (2'li) pazarindan Shin fair ev-sahibi kazanma olasiligi. elo_tracker seed'i icin."""
    ml = next((o for ad, sov, o in pazar_listesi
               if (siniflandir(ad, sov) or (None,))[0] == "win" and set(o) == {"1", "2"}), None)
    return devig.shin_probs(ml).get("1") if ml else None


def _tenis_zemin_elo(mac, v):
    """Zemin belirleniyorsa, o zeminde oynanmis gecmis maclardan AYRI isimli (tenis_<zemin>)
    bir ELO replay'i yapar ve (elo_ev, elo_dep) doner; zemin yoksa None (model_tenis servis
    yoksa 'eksik' kalir, ekstra sinyal uygulanmaz)."""
    zemin = K.tenis_zemin(mac["league"])
    if not zemin:
        return None
    sp_key = "tenis_%s" % zemin
    gh_z = [g for g in v["gh"] if K.tenis_zemin(g["lig"]) == zemin]
    ga_z = [g for g in v["ga"] if K.tenis_zemin(g["lig"]) == zemin]
    return (ET.gecmisten_kur(sp_key, mac["home"], gh_z), ET.gecmisten_kur(sp_key, mac["away"], ga_z))


def tara(spor_adi="hepsi", saat=12.0, n=30, tum=False, log=True):
    """-> (sonuclar, ozet). sonuclar: modeli kurulabilen tum maclar; bayrakli olanlar r['bayrak'] ile secilir."""
    ad = TAKMA.get(spor_adi, spor_adi)
    secili = list(SPORLAR) if ad == "hepsi" else [ad]
    s = F._get_session()
    comps = F._fetch_competitions(s)
    sonuclar, ozet = [], {}
    nhl = None
    for sp in secili:
        cfgs = SPORLAR[sp]
        cfg = F._fetch_market_config(s, st=cfgs["st"])
        maclar = K.gunluk_maclar(s, cfgs["st"], comps, saat, n)
        with ThreadPoolExecutor(max_workers=6) as ex:
            veri = list(ex.map(lambda m: _veri_cek(s, cfg, cfgs["yol"], m), maclar))
        # lig havuzlari (tempo / gol baz cizgisi): ayni ligdeki TUM taranan maclarin son oyunlari
        havuz = {}
        for m, v in zip(maclar, veri):
            havuz.setdefault(m["league"], []).extend(v["gh"] + v["ga"])
        ozet[sp] = {"taranan": len(maclar), "model": 0, "bayrakli": 0, "aday_yok": 0, "ornek_yetersiz": 0}
        testler = [t for t in (hcp_isaret_testi(v["pz"]) for v in veri) if t is not None]
        hcp_ok = not (len(testler) >= 4 and sum(1 for t in testler if t) / len(testler) < 0.7)
        ozet[sp]["hcp_isaret"] = "%d/%d uyumlu%s" % (sum(1 for t in testler if t), len(testler),
                                                      "" if hcp_ok else " -> HANDIKAP KAPALI")
        if sp == "hokey" and any("nhl" in K.fold(m["league"]) for m in maclar):
            nhl = K.nhl_kaleciler()
        for m, v in zip(maclar, veri):
            oyun_h = havuz.get(m["league"], [])
            if sp in ("basketbol", "hokey"):
                seed_h, seed_a = ET.seed_from_market(_ml_p1(v["pz"]))
                ET.gecmisten_kur(sp, m["home"], v["gh"], seed_h)
                ET.gecmisten_kur(sp, m["away"], v["ga"], seed_a)
                elo_sos = (ET.sos(sp, v["gh"])[0], ET.sos(sp, v["ga"])[0])
            if sp == "basketbol":
                tot = [g["pf"] + g["pa"] for g in oyun_h]
                mod = model_basket(v["gh"], v["ga"], m["ts"], ort(tot) if len(tot) >= 20 else None, elo_sos)
            elif sp == "hokey":
                gol = [g["pf"] for g in oyun_h] + [g["pa"] for g in oyun_h]
                kh, ka = (_kaleci(m["home"], nhl), _kaleci(m["away"], nhl))
                mod = model_hokey(v["gh"], v["ga"], m["ts"], ort(gol) if len(gol) >= 40 else None, kh, ka, elo_sos)
            else:
                bo = 5 if any("(5 set)" in K.fold(p[0]) for p in v["pz"]) else 3
                mod = model_tenis(v["gh"], v["ga"], m["league"], K.tenis_servis(m["home"]),
                                  K.tenis_servis(m["away"]), bo, _tenis_zemin_elo(m, v))
            if not mod:
                ozet[sp]["ornek_yetersiz"] += 1
                continue
            r = degerlendir(sp, m, mod, v["pz"], hcp_ok)
            if not r:
                ozet[sp]["aday_yok"] += 1
                continue
            ozet[sp]["model"] += 1
            if r["bayrak"]:
                ozet[sp]["bayrakli"] += 1
            sonuclar.append(r)
    if log:
        _logla([r for r in sonuclar if r["bayrak"]])
    return sonuclar, ozet


def _kaleci(takim, nhl):
    """Dosyadaki onayli/yedek kaydi > NHL API onerisi ('belirsiz') > None."""
    k = K.kaleci_oku(takim)
    if k:
        o = nhl and K.nhl_bul(takim, nhl)
        return {"durum": k["durum"], "sv": k.get("sv") or (o or {}).get("sv"), "gp": (o or {}).get("gp") or 20}
    o = nhl and K.nhl_bul(takim, nhl)
    return {"durum": "belirsiz", "sv": o["sv"], "gp": o["gp"]} if o else None


def _logla(bayrakli):
    """Bayrakli sinyalleri history_log'a yazar. AYNI sinyal (spor, mac, pick) tekrar taramada
    YENIDEN YAZILMAZ: tekrarlar CLV orneklemini sisirirdi (n=30 esigi sahte dolardi)."""
    try:
        import history_log as HL
        var = {(k.get("kaynak"), k.get("esd_ms"), k.get("ev"), k.get("pazar"))
               for k in HL.sinyal_oku() if str(k.get("kaynak", "")).startswith("multi:")}
        yeni = []
        for r in bayrakli:
            anah = ("multi:" + r["spor"], int(r["ts"] * 1000), r["mac"], r["pick"])
            if anah in var:
                continue
            var.add(anah)
            yeni.append({"kaynak": anah[0], "esd_ms": anah[1], "ev": r["mac"], "lig": r["lig"],
                         "pazar": r["pick"], "oran": r["oran"], "p_model": round(r["p_mod"], 4),
                         "p_piyasa": round(r["p_mkt"], 4), "ev_yuzde": round(r["ev"] * 100, 2),
                         "tip": r["bayrak"], "karar": r["karar"], "kasa_pct": r["kasa"],
                         "risk": r["risk"], "f": r["f"], "gonderildi": False})
        HL.sinyal_coklu_yaz(yeni)
    except Exception:
        pass


# ---------------------------------------------------------------- CLI
def tablo(sonuclar, ozet, tum=False):
    for sp, o in ozet.items():
        print("%-9s taranan %2d | modelli %2d | bayrakli %2d | aday yok (suzgec) %2d | ornek yetersiz %2d | hcp isaret: %s"
              % (sp, o["taranan"], o["model"], o["bayrakli"], o["aday_yok"], o["ornek_yetersiz"], o.get("hcp_isaret", "-")))
    gor = sonuclar if tum else [r for r in sonuclar if r["bayrak"]]
    if not gor:
        print("\nBayrakli mac yok (esikler gevsetilmedi; iddaa marji nedeniyle bu beklenen sonuctur).")
        return
    print("")
    print("| Spor | Maç (saat) | Pick | Oran | p_bl | EV | Skor | Kasa% | Risk | Bayrak/Karar |")
    print("|---|---|---|---:|---:|---:|---|---:|---|---|")
    for r in sorted(gor, key=lambda x: -x["ev"]):
        print("| %s | %s (%s) | %s | %.2f | %.0f%% | %+.1f%% | %s | %.2f | %s | %s / %s |" % (
            r["spor"], r["mac"][:34], datetime.fromtimestamp(r["ts"], TR).strftime("%H:%M"), r["pick"][:46],
            r["oran"], r["p"] * 100, r["ev"] * 100, r["skor"], r["kasa"], r["risk"], r["bayrak"] or "-", r["karar"]))


def durum():
    print("--- multi_sport_analyzer kaynak sagligi ---")
    s = F._get_session()
    for sp, c in SPORLAR.items():
        try:
            ms = K.gunluk_maclar(s, c["st"], F._fetch_competitions(s), 48.0, 200)
            print("iddaa %-9s: %d mac (48 sa)" % (sp, len(ms)))
        except Exception as ex:
            print("iddaa %-9s: HATA %s" % (sp, str(ex)[:60]))
    nhl = K.nhl_kaleciler()
    print("NHL API kaleci tablosu: %d takim" % len(nhl))
    print("kaleci.json: %s | tenis_servis.json: %s" % (
        "var" if os.path.exists(K.kaleci_dosyasi()) else "yok",
        "var" if os.path.exists(os.path.join(K.VERI, "tenis_servis.json")) else "YOK (servis/karsilama uygulanmaz)"))
    print("COEF kalibre mi: %s" % CALIBRATED)


def main(argv=None):
    p = argparse.ArgumentParser(prog="multi_sport_analyzer.py")
    p.add_argument("komut", choices=["tara", "kaleci", "durum"])
    p.add_argument("args", nargs="*")
    p.add_argument("--spor", default="hepsi")
    p.add_argument("--saat", type=float, default=12.0)
    p.add_argument("--n", type=int, default=30)
    p.add_argument("--json", action="store_true", help="LLM'e gidecek kompakt JSON (yalniz bayrakli)")
    p.add_argument("--pipe", action="store_true", help="LLM'e gidecek ultra-compact PIPE satirlari (yalniz bayrakli)")
    p.add_argument("--tum", action="store_true", help="bayraksiz modelli maclari da goster")
    p.add_argument("--max", type=int, default=8, help="LLM'e en cok kac mac")
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    if a.komut == "kaleci":
        if len(a.args) < 2:
            sys.exit("kullanim: kaleci <takim> <onayli|yedek|belirsiz> [kurtaris_orani]")
        print(K.kaleci_yaz(a.args[0], a.args[1], float(a.args[2]) if len(a.args) > 2 else None))
    elif a.komut == "durum":
        durum()
    else:
        sonuclar, ozet = tara(a.spor, a.saat, a.n)
        fl = sorted([r for r in sonuclar if r["bayrak"]], key=lambda x: -x["ev"])[:a.max]
        if a.pipe:
            for r in fl:
                print(to_pipe_format(r))
        elif a.json:
            print(json.dumps([kompakt(r) for r in fl], ensure_ascii=False, separators=(",", ":")))
        else:
            tablo(sonuclar, ozet, a.tum)


if __name__ == "__main__":
    main()
