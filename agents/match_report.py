"""
BWM MAC ANALIZ MOTORU - tek mac icin 'Mukemmel Futbol Bahis Analiz' sablonunun tam uygulamasi.

Asamalar: (1) Dixon-Coles skor matrisi -> adil oranlar, (2) yan piyasalar (korner/kart/IY-MS/ozel),
(3) BWM kural motoru, (4) EV ve deger tespiti. Cikti: sablondaki 4 bolum.

Veri: iddaa detay uc noktasi (tum pazarlar) + iddaa istatistik servisi (puan durumu, son 10 mac,
H2H, korner/kart, kadro). YOK olanlar (raporda acikca belirtilir): xG/sut verisi, hakem, sakatlik listesi,
oyuncu/sut/faul/ofsayt fiyatlamasi.

Kullanim:
    python match_report.py            # bugunun baslamamis en iyi 5 maci
    python match_report.py 8          # 8 mac
    python match_report.py 3140074    # belirli mac kimligi(leri)
Ozet ekrana, tam raporlar data/match_reports_YYYYMMDD.md dosyasina yazilir.
"""
import os
import re
import sys
import json
import math
import collections
from datetime import datetime, timezone, timedelta

import numpy as np
from scipy.special import gammaln

import fetch_iddaa as F
import fetch_iddaa_stats as S
import fetch_flash as FL
import fetch_tm as TM
import find_matches as FM
from dixon_coles_model import shin_probs, LEAGUE_CODES
import market_coherence as MC
import decision as DEC

sys.stdout.reconfigure(encoding="utf-8")

RHO = -0.10              # Dixon-Coles dusuk skor duzeltmesi (literatur araligi -0.05..-0.15; VARSAYIM)
G = 8                    # 8x8 skor matrisi (0-7), sablon geregi
HG = 8                   # yari izgarasi
FH_SHARE = 0.45          # ilk yari gol payi (sablon: lambda_IY = lambda x 0.45)
FATIGUE = 0.90           # dar fikstur / Avrupa donusu: sablon %10-20 ister, alt sinir
HOME_ADV = S.HOME_ADV
CORNER_R, CARD_R = 40.0, 25.0         # negatif binom asiri-dagilim parametreleri (VARSAYIM)
CORNER_FH, CARD_FH = 0.46, 0.40       # ilk yari payi (VARSAYIM)
MIN_EV = 0.05
MAX_REL_DIV = 0.30       # model olasiligi piyasa adil olasiligindan en fazla %30 (goreli) ayrisabilir
HALF_REL_DIV = 0.15      # yari bazli pazarlarda (ilk yari gol payi varsayimi) daha siki sapma siniri
XG_CONFLICT = 0.25      # model xG'si piyasa-turevli xG'den bu orandan fazla ayrisirsa deger secenekleri spekulatif
MIN_PROB = 0.10
REL_AGREE, REL_MIN_P, REL_MIN_ODDS = 0.10, 0.75, 1.10   # "guvenilir" secenek: model-piyasa sapmasi <=%10, harman olasilik >=%75, oran >=1.10
DETAIL_URL = "https://sportsbookv2.iddaa.com/sportsbook/event/{}"
TR = timezone(timedelta(hours=3))


# ------------------------------------------------------------------ olasilik yardimcilari
def pois(l, n=G):
    g = np.arange(n)
    return np.exp(g * math.log(max(l, 1e-6)) - l - gammaln(g + 1))


def dc_grid(lam, mu):
    g = np.outer(pois(lam), pois(mu))
    g[0, 0] *= max(1 - lam * mu * RHO, 1e-6)
    g[0, 1] *= max(1 + lam * RHO, 1e-6)
    g[1, 0] *= max(1 + mu * RHO, 1e-6)
    g[1, 1] *= max(1 - RHO, 1e-6)
    return g / g.sum()


def nb_pmf(mean, r, n=45):
    k = np.arange(n)
    p = r / (r + max(mean, 1e-6))
    logp = gammaln(k + r) - gammaln(r) - gammaln(k + 1) + r * math.log(p) + k * math.log(1 - p)
    v = np.exp(logp)
    return v / v.sum()


def nrm(s):
    s = s.replace("İ", "i").replace("I", "ı").lower()
    return re.sub(r"\s+", " ", s).strip()


def num(s):
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group(0)) if m else None


# ------------------------------------------------------------------ veri toplama
def games_full(rm, team, base_name, lg_name):
    out = []
    for m in (rm or {}).get("m", []):
        if not S.same_league((m.get("l") or {}).get("n"), base_name, lg_name):
            continue
        h, a = m.get("h", {}), m.get("a", {})
        if h.get("rs") is None or a.get("rs") is None:
            continue
        if h.get("n") == team:
            own, opp = h, a
        elif a.get("n") == team:
            own, opp = a, h
        else:
            continue
        out.append({"gf": own["rs"], "ga": opp["rs"], "fgf": own.get("fhs"), "fga": opp.get("fhs"), "t": m.get("t")})
    return out


CONTINENTAL = ("uefa", "şampiyonlar", "avrupa", "konferans", "libertadores", "sudamericana", "afc", "caf ")


def rest_info(rm, ko_ts):
    ms = sorted((m for m in (rm or {}).get("m", []) if m.get("t") and m["t"] < ko_ts), key=lambda m: m["t"])
    if not ms:
        return None
    last_comp = ((ms[-1].get("l") or {}).get("n") or "")
    return {"rest_days": round((ko_ts - ms[-1]["t"]) / 86400, 1), "last14": sum(1 for m in ms if ko_ts - m["t"] <= 14 * 86400),
            "euro_return": any(k in _lower_tr(last_comp) for k in CONTINENTAL) and (ko_ts - ms[-1]["t"]) <= 5 * 86400}


def _lower_tr(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def cc_raw(cc):
    if not cc:
        return None
    out = {}
    for side in ("h", "a"):
        team = cc[side]["n"]
        cf = ca = kf = ka = rf = n = 0
        for m in cc[side].get("m", []):
            own, opp = (m["h"], m["a"]) if m["h"].get("n") == team else (m["a"], m["h"])
            if any(own.get(k) is None or opp.get(k) is None for k in ("c", "yc", "rc")):
                continue
            n += 1
            cf += own["c"]; ca += opp["c"]
            kf += own["yc"] + own["rc"]; ka += opp["yc"] + opp["rc"]; rf += own["rc"]
        if n < 3:
            return None
        out[side] = {"n": n, "cf": cf / n, "ca": ca / n, "kf": kf / n, "ka": ka / n, "rc": rf / n}
    return out


XG_WEIGHT = 0.70          # takim oranlarinda Flashscore xG agirligi (kalan gol)
FLASH = {"session": None, "list": None, "cache": None}
TARGET = [None]   # --tarih ile verilen gun (None = bugun)
TM_ON = [True]    # gun taramasinda (gun) kapatilir: 150 macta yuzlerce Transfermarkt istegi


def target_date():
    return TARGET[0] or datetime.now(TR).date()


def flash_init():
    """Flashscore bugunun listesi ve istatistik onbellegi: calistirma basina bir kez."""
    try:
        FLASH["session"] = FL.make_session()
        FLASH["list"] = FL.load_list(FLASH["session"], max(0, (target_date() - datetime.now(TR).date()).days)) or None
        FLASH["cache"] = FL.load_cache()
    except Exception:
        FLASH["list"] = None


def flash_for(match):
    if not FLASH["list"]:
        return None
    fm = FL.link([match], FLASH["list"]).get(match["match_id"])
    return FL.for_match(FLASH["session"], fm, FLASH["cache"]) if fm else None


def gather(session, cfg, comps, eid):
    d = F._get_json(session, DETAIL_URL.format(eid))["data"]
    match = F._parse_event(d, cfg, comps, False)
    mc = S.api(session, f"/match-card/{eid}?isLive=false")
    if not mc:
        return match, None
    lg = mc.get("lg") or {}
    st = S.api(session, f"/standings/{eid}?standingType=0&isLive=false")
    ctx = {
        "mc": mc, "st": st, "lg_name": lg.get("n"), "base": S.league_base_name(st, lg.get("n")),
        "rm_h": S.api(session, f"/recent-matches/{eid}?matchHistoryType=0&isOnlyCurrentTournament=false"),
        "rm_a": S.api(session, f"/recent-matches/{eid}?matchHistoryType=1&isOnlyCurrentTournament=false"),
        "rm_x": S.api(session, f"/recent-matches/{eid}?matchHistoryType=2&isOnlyCurrentTournament=false"),
        "cc": cc_raw(S.api(session, f"/card-corners/{eid}")),
        "lineup": S.api(session, f"/lineup/{eid}"),
        "flash": flash_for(match),
    }
    ctx["tm"] = None
    if TM_ON[0]:   # Transfermarkt: sakat/cezali + hakem kart ortalamasi (toplu taramada kapali; ulasilamazsa sessizce veri yok)
        try:
            ctx["tm"] = TM.for_match(match["home"], match["away"], (ctx["flash"] or {}).get("referee"))
        except Exception:
            ctx["tm"] = None
    return match, ctx


# ------------------------------------------------------------------ 1. asama: lambda/mu ve modeller
def build_model(match, ctx):
    mc = ctx["mc"]
    ht, at = mc["h"]["n"], mc["a"]["n"]
    rows = [r for g_ in (ctx["st"] or {}).get("g", []) for r in g_.get("t", [])]
    tp = sum(r.get("p", 0) for r in rows)
    mu_lg = (sum(r.get("gf", 0) for r in rows) / tp) if tp >= 20 else S.MU_DEFAULT
    gh = games_full(ctx["rm_h"], ht, ctx["base"], ctx["lg_name"])
    ga = games_full(ctx["rm_a"], at, ctx["base"], ctx["lg_name"])
    info = {"ht": ht, "at": at, "rows": {r["n"]: r for r in rows}, "mu_lg": mu_lg, "gh": gh, "ga": ga, "flags": []}

    mkt = MC.analyze_match(match)
    info["market_lam"] = (mkt["lam_home"], mkt["lam_away"]) if mkt else None
    info["market_margin"] = mkt["avg_margin"] if mkt else None

    fl = ctx.get("flash")
    info["flash"] = fl
    fh_, fa_ = (fl or {}).get("home"), (fl or {}).get("away")
    use_xg = bool(fh_ and fa_ and fh_["n_xg"] >= 4 and fa_["n_xg"] >= 4)
    reliable = use_xg or (len(gh) >= S.MIN_SAME_LEAGUE and len(ga) >= S.MIN_SAME_LEAGUE)
    info["reliable"] = reliable
    info["xg_source"] = "flash" if use_xg else None
    info["n_eff"] = min(fh_["n_xg"], fa_["n_xg"]) if use_xg else min(len(gh), len(ga))
    info["n_desc"] = (f"Flashscore son {fh_['n_xg']}/{fa_['n_xg']} maç (xG, tüm turnuvalar)" if use_xg else f"{len(gh)}/{len(ga)} lig maçı")
    if use_xg:
        K = S.K_SHRINK

        def sr(x, g, n):   # xG ve golun harmani, lig ortalamasina cekilmis oran
            return (((XG_WEIGHT * x + (1 - XG_WEIGHT) * g) * n + K * mu_lg) / (n + K)) / mu_lg
        ah, dh = sr(fh_["xgf"], fh_["gf"], fh_["n_xg"]), sr(fh_["xga"], fh_["ga"], fh_["n_xg"])
        aa, da = sr(fa_["xgf"], fa_["gf"], fa_["n_xg"]), sr(fa_["xga"], fa_["ga"], fa_["n_xg"])
        lam, mu = mu_lg * ah * da * HOME_ADV, mu_lg * aa * dh / HOME_ADV
        info["source"] = f"Flashscore xG (%{int(XG_WEIGHT * 100)}) + gol (%{int((1 - XG_WEIGHT) * 100)})"
    elif reliable:
        ah, dh, _ = S.strength([(g["gf"], g["ga"]) for g in gh], mu_lg)
        aa, da, _ = S.strength([(g["gf"], g["ga"]) for g in ga], mu_lg)
        lam, mu = mu_lg * ah * da * HOME_ADV, mu_lg * aa * dh / HOME_ADV
        info["source"] = "istatistik (aynı ligdeki son maçlar, ligin gol ortalamasına çekilmiş)"
    elif info["market_lam"]:
        lam, mu = info["market_lam"]
        info["source"] = "piyasa-türevli xG (istatistik yetersiz; BAĞIMSIZ DEĞİL)"
        info["flags"].append("İstatistik örneklemi yetersiz (<3 lig maçı): λ/μ piyasadan türetildi, EV bağımsız değil.")
    else:
        return None
    info["lam_raw"], info["mu_raw"] = lam, mu

    ko = mc.get("ts") or 0
    for side, rm, key in (("h", ctx["rm_h"], "rest_h"), ("a", ctx["rm_a"], "rest_a")):
        info[key] = rest_info(rm, ko)
    f_h = f_a = 1.0
    for key, name in (("rest_h", "Ev sahibi"), ("rest_a", "Deplasman")):
        r = info[key]
        if r and (r["rest_days"] <= 3 or r["last14"] >= 4 or r["euro_return"]):
            if key == "rest_h":
                f_h = FATIGUE
            else:
                f_a = FATIGUE
            why = "Avrupa maçı dönüşü" if r["euro_return"] else f"{r['rest_days']} gün dinlenme, {r['last14']} maç/14 gün"
            info["flags"].append(f"Yorgunluk düzeltmesi (şablon kuralı %10): {name} — {why} → gol beklentisi x{FATIGUE}. Sakatlık listesi ayrı satırda (oyuncu önemi/λ etkisi hesaba katılmadı).")
    lam *= f_h
    mu *= f_a
    info["lam"], info["mu"] = lam, mu

    info["f_share"] = FH_SHARE
    info["G"] = dc_grid(lam, mu)
    f = info["f_share"]
    info["G1"] = np.outer(pois(lam * f, HG), pois(mu * f, HG)); info["G1"] /= info["G1"].sum()
    info["G2"] = np.outer(pois(lam * (1 - f), HG), pois(mu * (1 - f), HG)); info["G2"] /= info["G2"].sum()

    ccx = ctx["cc"]
    if ccx:
        h_c, a_c = (ccx["h"]["cf"] + ccx["a"]["ca"]) / 2, (ccx["a"]["cf"] + ccx["h"]["ca"]) / 2
        h_k, a_k = (ccx["h"]["kf"] + ccx["a"]["ka"]) / 2, (ccx["a"]["kf"] + ccx["h"]["ka"]) / 2
        red = ((ccx["h"]["rc"] + 0.05 * 6 / max(ccx["h"]["n"], 1)) + (ccx["a"]["rc"] + 0.05 * 6 / max(ccx["a"]["n"], 1))) / 2
        info["cc"] = {"h_c": h_c, "a_c": a_c, "h_k": h_k, "a_k": a_k, "red": min(red, 0.5), "n": (ccx["h"]["n"], ccx["a"]["n"]),
                      "corners_ok": (h_c + a_c) >= 4.0, "cards_ok": (h_k + a_k) > 0.3}   # servis bazi liglerde 0 doner = veri yok
    else:
        info["cc"] = None

    if fh_ and fa_:   # Flashscore: gercek korner/kart ortalamalari (son <=6 mac) iddaa'nin bos donen alanlarindan ustundur
        cc_ = dict(info["cc"]) if info["cc"] else {"h_c": 0.0, "a_c": 0.0, "h_k": 0.0, "a_k": 0.0, "red": 0.05, "n": (0, 0),
                                                   "corners_ok": False, "cards_ok": False}
        if fh_["n_c"] >= 4 and fa_["n_c"] >= 4:
            cc_["h_c"], cc_["a_c"] = (fh_["cf"] + fa_["ca"]) / 2, (fa_["cf"] + fh_["ca"]) / 2
            cc_["corners_ok"], cc_["n"] = True, (fh_["n_c"], fa_["n_c"])
        if fh_["n_k"] >= 4 and fa_["n_k"] >= 4:
            cc_["h_k"], cc_["a_k"] = (fh_["kf"] + fa_["ka"]) / 2, (fa_["kf"] + fh_["ka"]) / 2
            cc_["cards_ok"] = True
        info["cc"] = cc_ if (cc_["corners_ok"] or cc_["cards_ok"]) else info["cc"]

    tmr = (((ctx.get("tm") or {}).get("ref")) or {}).get("stats")   # hakemin son 2 sezon kart ortalamasi (Transfermarkt)
    if tmr and info["cc"] and info["cc"]["cards_ok"] and (info["cc"]["h_k"] + info["cc"]["a_k"]) > 0:
        team_k = info["cc"]["h_k"] + info["cc"]["a_k"]
        k = (0.5 * team_k + 0.5 * (tmr["yc"] + tmr["rc"])) / team_k
        info["cc"]["h_k"] *= k; info["cc"]["a_k"] *= k
        info["cc"]["ref_k"] = tmr["yc"] + tmr["rc"]

    def margin(gs):
        return sum(g["gf"] - g["ga"] for g in gs) / len(gs) if gs else 0.0
    info["F_h"], info["F_a"] = margin(gh), margin(ga)

    x = ctx["rm_x"]
    info["h2h"] = S.h2h_summary(x, ht)
    info["h2h_last"] = None
    if x and x.get("m"):
        m0 = max(x["m"], key=lambda m: m.get("t", 0))
        if m0.get("h", {}).get("rs") is not None:
            hs, as_ = m0["h"]["rs"], m0["a"]["rs"]
            home_is_h = m0["h"].get("n") == ht
            info["h2h_last"] = {"margin_for_home": (hs - as_) if home_is_h else (as_ - hs), "t": m0.get("t")}
    return info


# ------------------------------------------------------------------ fiyatlama
def _agg(grid):
    n = grid.shape[0]
    g = np.arange(n)
    return np.add.outer(g, g), np.subtract.outer(g, g)


def price_goal_markets(info):
    return _agg(info["G"])


def price(name, line, outs, info):
    """Pazar adina gore {secenek: model olasiligi}; fiyatlanamazsa None."""
    n = nrm(name)
    Gm = info["G"]
    tot, diff = _agg(Gm)
    ph, pa = Gm.sum(1), Gm.sum(0)
    G1, G2 = info["G1"], info["G2"]
    t1, d1 = _agg(G1)
    t2, d2 = _agg(G2)
    half = 1 if n.startswith("1. yarı") else (2 if n.startswith("2. yarı") else None)
    cc = info["cc"]

    def P(mask, grid=Gm):
        return float(grid[mask].sum())

    # ---- korner / kart
    if "korner" in n or "kart" in n:
        if not cc or ("korner" in n and not cc["corners_ok"]) or ("kart" in n and "korner" not in n and not cc["cards_ok"]):
            return None
        if "korner" in n:
            mh, ma, r, fh_ = cc["h_c"], cc["a_c"], CORNER_R, CORNER_FH
        else:
            mh, ma, r, fh_ = cc["h_k"], cc["a_k"], CARD_R, CARD_FH
        s = fh_ if half == 1 else 1.0
        ph_c, pa_c = nb_pmf(mh * s, r), nb_pmf(ma * s, r)
        cg = np.outer(ph_c, pa_c)
        ct, cd = _agg(cg)
        L = line if line is not None else num(n)
        if "kırmızı kart" in n:
            p_none = math.exp(-2 * cc["red"])   # cc["red"]: takim basina ortalama kirmizi kart
            return {"Evet": 1 - p_none, "Hayır": p_none}
        if "tek/çift" in n:
            po = float(cg[(ct % 2) == 1].sum()); return {"Tek": po, "Çift": 1 - po}
        if "aralığı" in n:
            res = {}
            for k in outs:
                m_ = re.match(r"(\d+)-(\d+)", k)
                if m_:
                    res[k] = float(cg[(ct >= int(m_.group(1))) & (ct <= int(m_.group(2)))].sum())
                elif k.endswith("+"):
                    res[k] = float(cg[ct >= int(k[:-1])].sum())
            return res or None
        if "kim daha çok" in n:
            return {"1": float(cg[cd > 0].sum()), "0": float(cg[cd == 0].sum()), "2": float(cg[cd < 0].sum())}
        if "ilk korneri" in n:
            p1 = mh / (mh + ma) * 0.99
            return {"1": p1, "2": 0.99 - p1, "Olmaz": 0.01}
        if "handikap" in n and L is not None:
            p1 = float(cg[(cd + L) > 0].sum()); p2 = float(cg[(cd + L) < 0].sum())
            z = p1 + p2
            return {"1": p1 / z, "2": p2 / z}
        if L is None:
            return None
        k_ = np.arange(len(ph_c))
        if n.startswith("ev sahibi"):
            return {"Üst": float(ph_c[k_ > L].sum()), "Alt": float(ph_c[k_ < L].sum())}
        if n.startswith("deplasman"):
            return {"Üst": float(pa_c[k_ > L].sum()), "Alt": float(pa_c[k_ < L].sum())}
        if "alt" in n and "üst" in n:
            return {"Üst": float(cg[ct > L].sum()), "Alt": float(cg[ct < L].sum())}
        return None

    if any(w in n for w in ("oyuncu", "şut", "faul", "ofsayt", "taç", "kale vuruşu", "kurtarış", "top çalar",
                            "kombo", "özel bahis", "penaltı", "kendi kalesine")):
        return None

    L = line if line is not None else num(n)
    res = {}

    def m_res(d):
        return {"1": float(Gm[d > 0].sum()), "0": float(Gm[d == 0].sum()), "2": float(Gm[d < 0].sum())}

    # ---- temel
    if n == "maç sonucu":
        r = m_res(diff)
        return {"1": r["1"], "X": r["0"], "2": r["2"]}
    if n == "çifte şans":
        r = m_res(diff)
        return {"1X": r["1"] + r["0"], "12": r["1"] + r["2"], "X2": r["0"] + r["2"]}
    if n == "karşılıklı gol":
        p = float(Gm[1:, 1:].sum()); return {"Var": p, "Yok": 1 - p}
    if n == "tek / çift":
        p = float(Gm[(tot % 2) == 1].sum()); return {"Tek": p, "Çift": 1 - p}
    if n == "1. yarı tek/çift":
        p = float(G1[(t1 % 2) == 1].sum()); return {"Tek": p, "Çift": 1 - p}
    if n == "toplam gol":
        for k in outs:
            b = re.findall(r"\d+", k)
            if len(b) == 2:
                res[k] = float(Gm[(tot >= int(b[0])) & (tot <= int(b[1]))].sum())
            elif len(b) == 1 and "+" in k:
                res[k] = float(Gm[tot >= int(b[0])].sum())
        return res or None
    if n == "maç skoru":
        for k in outs:
            b = re.findall(r"\d+", k)
            if len(b) == 2 and int(b[0]) < G and int(b[1]) < G:
                res[k] = float(Gm[int(b[0]), int(b[1])])
        return res or None
    if n == "1. yarı skoru":
        for k in outs:
            b = re.findall(r"\d+", k)
            if len(b) == 2 and int(b[0]) < HG and int(b[1]) < HG:
                res[k] = float(G1[int(b[0]), int(b[1])])
        return res or None
    if n.startswith("handikaplı maç sonucu") and L is not None:
        return m_res(diff + L)
    if n == "hangi takım kaç farkla kazanır?":
        return {"Ev 3+": float(Gm[diff >= 3].sum()), "Ev 2": float(Gm[diff == 2].sum()), "Ev 1": float(Gm[diff == 1].sum()),
                "0": float(Gm[diff == 0].sum()), "Dep 1": float(Gm[diff == -1].sum()), "Dep 2": float(Gm[diff == -2].sum()),
                "Dep 3+": float(Gm[diff <= -3].sum())}
    if n == "i̇lk golü hangi takım atar" or n == "ilk golü hangi takım atar":
        p00 = float(Gm[0, 0]); lam, mu = info["lam"], info["mu"]
        return {"1": (1 - p00) * lam / (lam + mu), "2": (1 - p00) * mu / (lam + mu), "Gol Olmaz": p00}
    if n == "ev sahibi gol yemeden kazanır":
        return {"Evet": float(Gm[1:, 0].sum()), "Hayır": 1 - float(Gm[1:, 0].sum())}
    if n == "deplasman gol yemeden kazanır":
        return {"Evet": float(Gm[0, 1:].sum()), "Hayır": 1 - float(Gm[0, 1:].sum())}

    # ---- alt/ust ailesi
    if L is not None and ("alt/üst" in n or "altı/üstü" in n) and " ve " not in n:
        if n.startswith("alt/üst"):
            return {"Üst": float(Gm[tot > L].sum()), "Alt": float(Gm[tot < L].sum())}
        if n.startswith("1. yarı alt/üst"):
            return {"Üst": float(G1[t1 > L].sum()), "Alt": float(G1[t1 < L].sum())}
        if n.startswith("2. yarı alt/üst"):
            return {"Üst": float(G2[t2 > L].sum()), "Alt": float(G2[t2 < L].sum())}
        mg, side_home = None, n.startswith("ev sahibi")
        if n.startswith("ev sahibi") or n.startswith("deplasman"):
            if "1. yarı" in n:
                marg = G1.sum(1) if side_home else G1.sum(0)
            elif "2. yarı" in n:
                marg = G2.sum(1) if side_home else G2.sum(0)
            else:
                marg = ph if side_home else pa
            k = np.arange(len(marg))
            return {"Üst": float(marg[k > L].sum()), "Alt": float(marg[k < L].sum())}

    # ---- kombinasyonlar
    if n.startswith("maç sonucu ve alt/üst") and L is not None:
        for tag, mk in (("1", diff > 0), ("0", diff == 0), ("2", diff < 0)):
            res[f"{tag} ve Alt"] = float(Gm[mk & (tot < L)].sum()); res[f"{tag} ve Üst"] = float(Gm[mk & (tot > L)].sum())
        return res
    if n.startswith("altı/üstü") and "karşılıklı gol" in n and L is not None:
        bt = np.zeros_like(Gm, dtype=bool); bt[1:, 1:] = True
        for ou_, mo in (("Alt", tot < L), ("Üst", tot > L)):
            res[f"{ou_} ve Var"] = float(Gm[mo & bt].sum()); res[f"{ou_} ve Yok"] = float(Gm[mo & ~bt].sum())
        return res
    if n == "maç sonucu ve karşılıklı gol":
        bt = np.zeros_like(Gm, dtype=bool); bt[1:, 1:] = True
        for tag, mk in (("1", diff > 0), ("0", diff == 0), ("2", diff < 0)):
            res[f"{tag} ve Var"] = float(Gm[mk & bt].sum()); res[f"{tag} ve Yok"] = float(Gm[mk & ~bt].sum())
        return res
    if n.startswith("1. yarı sonucu ve altı/üstü") and L is not None:
        for tag, mk in (("1", d1 > 0), ("0", d1 == 0), ("2", d1 < 0)):
            res[f"{tag} ve Alt"] = float(G1[mk & (t1 < L)].sum()); res[f"{tag} ve Üst"] = float(G1[mk & (t1 > L)].sum())
        return res
    if n.startswith("1. yarı sonucu ve ilk yarı karşılıklı gol"):
        bt = np.zeros_like(G1, dtype=bool); bt[1:, 1:] = True
        for tag, mk in (("1", d1 > 0), ("0", d1 == 0), ("2", d1 < 0)):
            res[f"{tag} ve Var"] = float(G1[mk & bt].sum()); res[f"{tag} ve Yok"] = float(G1[mk & ~bt].sum())
        return res
    if n == "1. yarı sonucu":
        return {"1": float(G1[d1 > 0].sum()), "0": float(G1[d1 == 0].sum()), "2": float(G1[d1 < 0].sum())}
    if n == "2. yarı sonucu":
        return {"1": float(G2[d2 > 0].sum()), "0": float(G2[d2 == 0].sum()), "2": float(G2[d2 < 0].sum())}
    if n == "1. yarı çifte şans":
        a1, x1, b1 = float(G1[d1 > 0].sum()), float(G1[d1 == 0].sum()), float(G1[d1 < 0].sum())
        return {"1 ve 0": a1 + x1, "1 ve 2": a1 + b1, "0 ve 2": x1 + b1}
    if n == "1. yarı karşılıklı gol":
        p = float(G1[1:, 1:].sum()); return {"Var": p, "Yok": 1 - p}
    if n == "2. yarı karşılıklı gol":
        p = float(G2[1:, 1:].sum()); return {"Var": p, "Yok": 1 - p}

    # ---- IY/MS ortak dagilim
    if n in ("1. yarı / maç sonucu", "1. yarı/maç sonucu skorları"):
        joint, score_joint = {}, {}
        for i in range(HG):
            for j in range(HG):
                p1 = G1[i, j]
                if p1 < 1e-9:
                    continue
                hr = "1" if i > j else ("0" if i == j else "2")
                for k in range(HG):
                    for l in range(HG):
                        p = p1 * G2[k, l]
                        fr = "1" if i + k > j + l else ("0" if i + k == j + l else "2")
                        joint[(hr, fr)] = joint.get((hr, fr), 0.0) + p
                        score_joint[f"{i}-{j} {i + k}-{j + l}"] = score_joint.get(f"{i}-{j} {i + k}-{j + l}", 0.0) + p
        if n == "1. yarı / maç sonucu":
            return {f"{a}/{b}": v for (a, b), v in joint.items()}
        return {k: score_joint.get(k, 0.0) for k in outs} or None
    if n in ("hangi yarıda daha fazla gol olur", "ev sahibi hangi yarıda daha fazla gol atar", "deplasman hangi yarıda daha fazla gol atar"):
        if n.startswith("ev"):
            a1, a2 = G1.sum(1), G2.sum(1)
        elif n.startswith("dep"):
            a1, a2 = G1.sum(0), G2.sum(0)
        else:
            a1 = np.bincount(t1.ravel(), weights=G1.ravel()); a2 = np.bincount(t2.ravel(), weights=G2.ravel())
        m = max(len(a1), len(a2)); a1 = np.pad(a1, (0, m - len(a1))); a2 = np.pad(a2, (0, m - len(a2)))
        o = np.outer(a1, a2); idx = np.arange(m)
        return {"1.": float(o[np.subtract.outer(idx, idx) > 0].sum()), "Eşit": float(o[np.subtract.outer(idx, idx) == 0].sum()),
                "2.": float(o[np.subtract.outer(idx, idx) < 0].sum())}
    if n in ("ev sahibi her iki yarıyı kazanır", "deplasman her iki yarıyı kazanır"):
        s = 1 if n.startswith("ev") else -1
        p = float(G1[(d1 * s) > 0].sum()) * float(G2[(d2 * s) > 0].sum())
        return {"Evet": p, "Hayır": 1 - p}
    if n in ("ev sahibi yarı kazanır", "deplasman yarı kazanır"):
        s = 1 if n.startswith("ev") else -1
        p = 1 - (1 - float(G1[(d1 * s) > 0].sum())) * (1 - float(G2[(d2 * s) > 0].sum()))
        return {"Evet": p, "Hayır": 1 - p}
    if n in ("ev sahibi her iki yarıda da gol atar", "deplasman her iki yarıda da gol atar"):
        a1, a2 = (G1.sum(1), G2.sum(1)) if n.startswith("ev") else (G1.sum(0), G2.sum(0))
        p = (1 - a1[0]) * (1 - a2[0])
        return {"Evet": float(p), "Hayır": float(1 - p)}
    if n.startswith("her iki yarıda da") and L is not None:
        pa1 = float(G1[t1 <= 1].sum()) if L == 1.5 else None
        if pa1 is not None:
            p_alt = float(G1[t1 <= 1].sum()) * float(G2[t2 <= 1].sum())
            p_ust = float(G1[t1 >= 2].sum()) * float(G2[t2 >= 2].sum())
            p = p_alt if "alt" in n else p_ust
            return {"Evet": p, "Hayır": 1 - p}
    return None


# ------------------------------------------------------------------ pazarlari tara
CATS = [("Taraf ve Handikap", ("maç sonucu", "çifte şans", "handikaplı", "kaç farkla", "yarı kazanır", "yarıyı kazanır", "gol yemeden", "ilk golü")),
        ("Korner ve Kart", ("korner", "kart")),
        ("İY/MS ve Özel", ("yarı", "skor", "tek / çift", "toplam gol", "hangi yarı"))]


def category(name):
    n = nrm(name)
    if "korner" in n or "kart" in n:
        return "Korner ve Kart"
    if any(w in n for w in ("1. yarı", "2. yarı", "yarı /", "skor", "hangi yarı", "her iki yarı")):
        return "İY/MS ve Özel"
    if any(w in n for w in ("maç sonucu", "çifte şans", "handikaplı", "kaç farkla", "gol yemeden", "ilk golü", "yarı kazanır")) and " ve " not in n:
        return "Taraf ve Handikap"
    return "Gol ve Baretler"


def iter_markets(match):
    mk = match["markets"]
    for k, nm in (("match_outcome", "Maç Sonucu"), ("double_chance", "Çifte Şans"), ("both_teams_score", "Karşılıklı Gol")):
        if mk.get(k):
            yield nm, None, mk[k]
    for b in ("totals_goals", "corners", "cards", "handicaps", "half_time", "other_markets"):
        for x in mk.get(b, []):
            line = x.get("line")
            try:
                line = float(line) if line not in (None, "") else None
            except (TypeError, ValueError):
                line = None
            yield x["market_name"], line, x["outcomes"]


def evaluate(match, info):
    picks, priced, unpriced, total = [], 0, collections.Counter(), 0
    for name, line, outs in iter_markets(match):
        total += 1
        probs = price(name, line, outs, info)
        if not probs:
            unpriced[category_unpriced(name)] += 1
            continue
        if any(v <= 1.01 for k, v in outs.items() if k in probs):
            unpriced["askıdaki pazar"] += 1   # bir seçeneği 1.0: pazar askıda/bozuk
            continue
        priced += 1
        offered = {k: v for k, v in outs.items() if k in probs and v > 1.01}
        complete = len(offered) == len(outs) and abs(sum(probs.get(k, 0) for k in outs) - 1.0) < 0.02
        shin = shin_probs(offered) if complete and len(offered) >= 2 else {}
        for k, odd in offered.items():
            p = probs[k]
            pm = shin.get(k)
            blend = (0.7 * pm + 0.3 * p) if pm is not None else None
            picks.append({"market": name, "line": line, "outcome": k, "odds": odd, "p": p, "p_mkt": pm, "blend": blend,
                          "ev": p * odd - 1, "ev_blend": (blend * odd - 1) if blend is not None else None,
                          "div": abs(p - pm) if pm is not None else None,
                          "rel_div": abs(p - pm) / pm if pm else None, "cat": category(name)})
    return picks, priced, unpriced, total


def category_unpriced(name):
    n = nrm(name)
    for key, lab in (("oyuncu", "oyuncu"), ("şut", "şut"), ("faul", "faul"), ("ofsayt", "ofsayt"), ("taç", "taç atışı"),
                     ("kale vuruşu", "kale vuruşu"), ("kurtarış", "kaleci"), ("kombo", "kombo"), ("özel", "özel"),
                     ("penaltı", "penaltı"), ("top çalar", "oyuncu")):
        if key in n:
            return lab
    return "diğer"


# ------------------------------------------------------------------ 3. asama: BWM kurallari
def apply_rules(match, info, picks):
    notes, excl = [], {}
    ms = match["markets"]["match_outcome"]
    sh = shin_probs({k: ms[k] for k in ("1", "X", "2")}) if all(k in ms for k in ("1", "X", "2")) else {}
    max_p = max(sh.values()) if sh else 0
    balanced = bool(sh) and max_p < 0.42
    if balanced:
        notes.append(f"VALUELESS BET / Yüksek Riskli Dengeli Maç: 1X2 dengeli (en yüksek olasılık %{round(max_p * 100)} < %42; {ms['1']}-{ms['X']}-{ms['2']}). Tekli 1/X/2 yok; yalnızca Çifte Şans, Alt/Üst, Korner/Kart.")

    fav_home = info["lam"] >= info["mu"]
    lam_f, lam_o = (info["lam"], info["mu"]) if fav_home else (info["mu"], info["lam"])
    tier = lambda l: "Tier 1" if l >= 1.8 else ("Tier 2" if l >= 1.0 else "Zayıf")
    notes.append(f"Takım seviyesi (gol beklentisine göre): Ev {tier(info['lam'])} ({info['lam']:.2f}) | Deplasman {tier(info['mu'])} ({info['mu']:.2f}).")

    rv = info["h2h_last"]
    revenge = False
    if rv and abs(rv["margin_for_home"]) >= 3 and rv["t"] and (info["mc_ts"] - rv["t"]) < 400 * 86400:
        revenge = True
        who = "Ev sahibi" if rv["margin_for_home"] > 0 else "Deplasman"
        exempt = max(info["lam"], info["mu"]) >= 2.2
        notes.append(f"RÖVANŞ KURALI: Son karşılaşmayı {who} {abs(rv['margin_for_home'])} farkla kazandı → yüksek handikap ve Üst 3.5/4.5'ten kaçın"
                     + (" (Tier 1 istisnası: çok yüksek gol beklentisi, yine de yüksek risk)." if exempt else "."))
    info["xg_conflict"] = False
    ml = info["market_lam"]
    if ml and info["reliable"]:
        rl, rmu = abs(info["lam_raw"] - ml[0]) / ml[0], abs(info["mu_raw"] - ml[1]) / ml[1]
        if max(rl, rmu) > XG_CONFLICT:
            info["xg_conflict"] = True
            notes.append(f"MODEL-PİYASA xG ÇELİŞKİSİ: model λ/μ = {info['lam_raw']:.2f}/{info['mu_raw']:.2f}, piyasa-türevli xG = {ml[0]:.2f}/{ml[1]:.2f} "
                         f"(sapma %{round(max(rl, rmu) * 100)} > %{int(XG_CONFLICT * 100)}). Model örneklemi küçük olabilir; tüm 'değerli' seçenekler SPEKÜLATİF, güven tavanı 4.")
    weak_vs_t2 = min(info["lam"], info["mu"]) < 1.0 and max(info["lam"], info["mu"]) < 2.0
    if weak_vs_t2:
        notes.append("ZAYIF TAKIM KURALI: zayıf takım (savunmaya çekilen) Tier 2 ile oynuyor → Alt 3.5 / 2-3 Gol / Çifte Şans öncelikli; Üst 3.5+ elenir.")

    for p in picks:
        n = nrm(p["market"]); why = None
        if balanced and p["market"] in ("Maç Sonucu",) and p["outcome"] in ("1", "X", "2"):
            why = "Valueless Bet (dengeli 1X2)"
        elif n.startswith("handikaplı maç sonucu") and p["line"] is not None:
            H = p["line"]; Fv = info["F_h"] if H <= 0 else info["F_a"]
            if abs(H) - abs(Fv) > 3:
                why = f"Handikap YÜKSEK RİSK (|H|={abs(H)} - |F|={abs(Fv):.2f} > 3)"
            elif revenge and abs(H) >= 2 and max(info["lam"], info["mu"]) < 2.2:
                why = "Rövanş kuralı: yüksek handikap"
        elif "korner handikap" in n and p["line"] is not None and info["cc"]:
            Fc = info["cc"]["h_c"] - info["cc"]["a_c"]
            if abs(p["line"]) - abs(Fc) > 3:
                why = f"Korner handikap YÜKSEK RİSK (|H|={abs(p['line'])} - |F|={abs(Fc):.2f} > 3)"
        elif p["outcome"] == "Üst" and p["line"] is not None and p["line"] >= 3.5 and n.startswith("alt/üst"):
            if revenge and max(info["lam"], info["mu"]) < 2.2:
                why = "Rövanş kuralı: Üst 3.5+"
            elif weak_vs_t2:
                why = "Zayıf takım kuralı: Üst 3.5+"
        if why is None and p["p_mkt"] is None:
            why = "Piyasa adil olasılığı doğrulanamadı (pazar tam değil, ör. kesin skor)"
        if why is None and "yarı" in n and p["cat"] != "Korner ve Kart" and p["rel_div"] is not None and p["rel_div"] > HALF_REL_DIV:
            why = f"Yarı bazlı pazar: ilk yarı gol payı varsayımına duyarlı (göreli sapma %{round(p['rel_div'] * 100)} > %{int(HALF_REL_DIV * 100)})"
        if why is None and p["rel_div"] is not None and p["rel_div"] > MAX_REL_DIV:
            why = f"Model-Piyasa çelişkisi (göreli sapma %{round(p['rel_div'] * 100)} > %{int(MAX_REL_DIV * 100)})"
        if why is None and p["cat"] == "Korner ve Kart" and info["cc"] and min(info["cc"]["n"]) < 5:
            why = "Korner/kart örneklemi < 5 maç"
        excl[id(p)] = why
    return notes, excl, balanced


def quarter_kelly_units(p, odds):
    k = (p * odds - 1) / (odds - 1)
    return max(0.0, min(0.25 * k * 100, 2.0))   # 1 Unit = kasanin %1'i, en fazla 2 Unit


# ------------------------------------------------------------------ rapor
def fmt_pct(x):
    return f"%{x * 100:.0f}"


ROWS = [3]          # kategori basina tablo satiri (--tam: 6)
SHORT = (("valueless", "Valueless"), ("handikap yüksek", "Handikap riski"), ("korner handikap yüksek", "Handikap riski"),
         ("rövanş", "Rövanş"), ("zayıf takım", "Zayıf takım"), ("çelişkisi", "Piyasa çelişkisi"),
         ("yarı bazlı", "Yarı payı"), ("doğrulanamadı", "Piyasa p yok"), ("örneklemi", "Örneklem<5"))


def short_reason(why):
    w = why.lower()
    return next((lab for key, lab in SHORT if key in w), why[:14])


def report(match, info, ctx):
    rows = ROWS[0]
    picks, priced, unpriced, total = evaluate(match, info)
    info["mc_ts"] = ctx["mc"].get("ts") or 0
    notes, excl, balanced = apply_rules(match, info, picks)
    mc = ctx["mc"]
    ht, at = info["ht"], info["at"]
    lam, mu, G0 = info["lam"], info["mu"], info["G"]
    tot, diff = _agg(G0)
    ko = datetime.fromtimestamp(mc.get("ts") or 0, TR).strftime("%d.%m %H:%M")
    ss_m = re.search(r"\d{2,4}/\d{2,4}|\d{4}", ((ctx.get("st") or {}).get("ss") or {}).get("n") or "")
    ss = ss_m.group(0) if ss_m else ""
    mm = info["market_margin"]
    L = [f"## {ht} vs {at}  ({match['league']}, {ko} TR)", "### 1. MAÇ KÜNYESİ VE METRİKLER"]
    L.append(f"- **Karşılaşma:** {ht} vs {at} | {match['league']} {ss}".rstrip())
    L.append(f"- **xG:** Ev λ **{lam:.2f}** | Dep μ **{mu:.2f}** (toplam {lam + mu:.2f}) | kaynak: "
             f"{('Flashscore xG+gol' if info.get('xg_source') else 'istatistik') if info['reliable'] else 'PİYASA-TÜREVLİ, bağımsız değil'}; örneklem {info['n_desc']}; "
             f"piyasa xG {('%.2f-%.2f' % info['market_lam']) if info['market_lam'] else '-'}; komisyon {('%%%.0f' % (mm * 100)) if mm else '-'}")
    cc = info["cc"]
    fl = info.get("flash")
    ref = (fl or {}).get("referee")
    if fl and fl.get("home") and fl.get("away"):
        def fs(a):
            t = f"xG {a['xgf']:.2f}/{a['xga']:.2f}, şut {a['shf']:.0f}/{a['sha']:.0f}, isabetli {a['sotf']:.1f}/{a['sota']:.1f}" if a.get("n_xg") else "xG yok"
            if a.get("n_c"): t += f", korner {a['cf']:.1f}/{a['ca']:.1f}"
            if a.get("n_k"): t += f", kart {a['kf']:.1f}/{a['ka']:.1f}"
            return t
        L.append(f"- **Flashscore son maçlar (lehine/aleyhine):** {ht}: {fs(fl['home'])} | {at}: {fs(fl['away'])}"
                 + (f" | stadyum {fl['venue']}" + (f" ({fl['capacity']})" if fl.get("capacity") else "") if fl.get("venue") else ""))
    tm = ctx.get("tm")
    rinfo = (tm or {}).get("ref") or {}
    rst = rinfo.get("stats")
    if rst:
        ref_txt = (f"hakem {ref} (TM: {rinfo['info']['name']}, son 2 sezon {rst['n']} maç: sarı {rst['yc']:.2f}, kırmızı {rst['rc']:.2f}, "
                   f"penaltı {rst['pen']:.2f} /maç; kart beklentisine %50 ağırlıkla katıldı)")
    else:
        ref_txt = f"hakem {ref if ref else 'verisi yok'} (kart ortalaması bulunamadı: TM'de tek/güvenli eşleşme yok ya da <{TM.MIN_REF_GAMES} maç)"
    inj_txt = TM.summary_line(tm, ht, at)
    if inj_txt:
        L.append(f"- **Sakat/cezalı (Transfermarkt; oyuncu önemi ve λ'ya etkisi hesaba katılmadı):** {inj_txt}")
    L.append(f"- **Hakem/disiplin:** {ref_txt}"
             + ("; xG modele katılmadı (iki takımda da ≥4 maçlık xG yok, lig maçları kullanıldı)" if not info.get("xg_source") else "")
             + ("; son maçlardan beklenen " + ", ".join(x for x in (
                 f"korner {cc['h_c'] + cc['a_c']:.1f}" if cc["corners_ok"] else "",
                 f"kart {cc['h_k'] + cc['a_k']:.1f}" if cc["cards_ok"] else "") if x) if cc and (cc["corners_ok"] or cc["cards_ok"]) else "; korner/kart verisi yok"))
    rw = info["rows"]
    tr = lambda t: (f"{rw[t]['r']}. ({rw[t]['pt']}p)" if t in rw else "-")
    h2 = info["h2h"]
    lu = ctx.get("lineup")
    L.append(f"- Sıra {tr(ht)} / {tr(at)} | Form {''.join(mc['h'].get('lfive') or []) or '-'} / {''.join(mc['a'].get('lfive') or []) or '-'}"
             + (f" | H2H {h2['home_wins']}-{h2['draws']}-{h2['away_wins']} ort {h2['avg_total_goals']} gol" if h2 else "")
             + (f" | Dizilim {lu['h'].get('f')} / {lu['a'].get('f')}" if lu and lu["h"].get("f") and lu["a"].get("f") else ""))

    # 2
    L.append("### 2. TÜM SEÇENEKLER VE PAZAR ANALİZLERİ")
    r1 = (float(G0[diff > 0].sum()), float(G0[diff == 0].sum()), float(G0[diff < 0].sum()))
    flat = sorted(((float(G0[i, j]), f"{i}-{j}") for i in range(G) for j in range(G)), reverse=True)[:4]
    L.append(f"- **Model 1X2:** %{r1[0] * 100:.0f} / %{r1[1] * 100:.0f} / %{r1[2] * 100:.0f} (adil oran {1 / r1[0]:.2f}/{1 / r1[1]:.2f}/{1 / r1[2]:.2f}) | "
             f"skorlar: " + ", ".join(f"{s} %{p * 100:.0f}" for p, s in flat) + f" | {priced}/{total} pazar fiyatlandı")
    main = [p for p in picks if p["p"] >= MIN_PROB]

    def tbl(title, sel):
        sel = sorted(sel, key=lambda p: (excl[id(p)] is not None, -p["ev"]))[:rows]
        if not sel:
            return
        L.append(f"- **{title}**\n")
        L.append("| Pazar | Seçim | Oran | Adil | Model/Piyasa | EV | Durum |\n| :--- | :--- | ---: | ---: | :--- | ---: | :--- |")
        for p in sel:
            state = short_reason(excl[id(p)]) if excl[id(p)] else ("DEĞER" if p["ev"] >= MIN_EV else "-")
            pm = f"{p['p_mkt'] * 100:.0f}" if p["p_mkt"] is not None else "-"
            L.append(f"| {p['market'][:30]} | {p['outcome']} | {p['odds']} | {1 / max(p['p'], 1e-6):.2f} | %{p['p'] * 100:.0f}/{pm} | {p['ev'] * 100:+.0f}% | {state} |")
        L.append("")

    tbl("Taraf ve Handikap", [p for p in main if p["cat"] == "Taraf ve Handikap"])
    tbl("Gol (Alt/Üst, KG, aralık)", [p for p in main if p["cat"] == "Gol ve Baretler"])
    tbl("Korner ve Kart", [p for p in main if p["cat"] == "Korner ve Kart"])
    tbl("İY/MS ve Özel", [p for p in main if p["cat"] == "İY/MS ve Özel"])

    # 3
    L.append("### 3. KURAL MOTORU VE RİSK FİLTRESİ")
    for n_ in notes + info["flags"]:
        L.append(f"- {n_}")
    raw_n = sum(1 for p in main if p["ev"] >= MIN_EV)
    ex = [short_reason(excl[id(p)]) for p in main if excl[id(p)] and p["ev"] >= MIN_EV]
    L.append(f"- Ham kural EV ≥ %5: {raw_n} seçenek → süzgeç sonrası {raw_n - len(ex)}"
             + (" (elenen: " + ", ".join(f"{k} ×{v}" for k, v in collections.Counter(ex).most_common(4)) + ")" if ex else "")
             + f". Ek süzgeçler: model olasılığı piyasa adil olasılığından göreli ≤%{int(MAX_REL_DIV * 100)} (yarı bazlı ≤%{int(HALF_REL_DIV * 100)}) ayrışmalı; küçük örneklem/kuyruk hatası önlemi.")

    # 4
    values = sorted([p for p in main if not excl[id(p)] and p["ev"] >= MIN_EV], key=lambda p: -p["ev"])
    L.append("### 4. DEĞERLİ SEÇENEKLER (+EV)")
    if values:
        for p in values[:5]:
            L.append(f"- {p['market'][:34]} → **{p['outcome']}** @{p['odds']} | model %{p['p'] * 100:.0f} vs piyasa %{p['p_mkt'] * 100:.0f} | EV {p['ev'] * 100:+.1f}%"
                     + (f" | harman EV {p['ev_blend'] * 100:+.1f}%" if p["ev_blend"] is not None else ""))
    else:
        L.append("- Yok (filtrelerden geçen, EV ≥ %5 seçenek bulunamadı).")

    # 5
    n_min = info["n_eff"]
    conf = 3 if n_min >= 8 else (2 if n_min >= 5 else (1 if n_min >= 3 else 0))
    ms_div = max((p["div"] for p in picks if p["market"] == "Maç Sonucu" and p["div"] is not None), default=None)
    conf += 0 if ms_div is None else (2 if ms_div <= 0.05 else (1 if ms_div <= 0.10 else -2))
    if h2 and h2["played"] >= 3 and abs(h2["avg_total_goals"] - (lam + mu)) <= 0.6:
        conf += 1
    if not info["flags"] and not balanced:
        conf += 1
    if values:
        conf += 2 if values[0]["ev"] >= 0.10 else 1
    if not info["reliable"] or info.get("xg_conflict"):
        conf = min(conf, 4)
    if values:   # spekulatif (harman Kelly<=0) ya da hakemsiz korner/kart tabanli oneride guven en fazla 6
        b0 = values[0]
        if quarter_kelly_units(b0["blend"] if b0["blend"] is not None else b0["p"], b0["odds"]) <= 0 or b0["cat"] == "Korner ve Kart":
            conf = min(conf, 6)
    conf = max(1, min(10, conf))

    safe = sorted([p for p in main if not excl[id(p)] and p["p"] >= 0.6 and p["odds"] >= 1.25 and p["ev"] >= -0.08 and p not in values[:1]],
                  key=lambda p: -p["p"])[:1]
    # en GUVENILIR (tutma olasiligi yuksek) secenek: deger degil, isabet olasiligi olculur; model-piyasa uyumu sart
    rel_c = sorted([p for p in main if not excl[id(p)] and p["p_mkt"] is not None and p["rel_div"] is not None and p["rel_div"] <= REL_AGREE
                    and (p["blend"] if p["blend"] is not None else p["p"]) >= REL_MIN_P and p["odds"] >= REL_MIN_ODDS
                    and p["cat"] != "Korner ve Kart" and "yarı" not in nrm(p["market"])],
                   key=lambda p: -(p["blend"] if p["blend"] is not None else p["p"])) if info["reliable"] and not info.get("xg_conflict") else []
    L.append("### 5. NİHAİ KARAR, PUANLAMA VE KASA YÖNETİMİ")
    if values:
        b = values[0]
        pb = b["blend"] if b["blend"] is not None else b["p"]
        rk = DEC.risk(pb, b["odds"], "tek", None, conf)   # tek modelli (Model A) + piyasa: teyit yok -> en az Orta
        spec = pb * b["odds"] - 1 <= 0 or bool(info.get("xg_conflict"))
        units = DEC.stake(pb, b["odds"], rk) if pb * b["odds"] - 1 > 0 else 0.0   # yalniz harman EV > 0 ise stake; aksi izleme
        if info.get("xg_conflict"):
            units = min(units, 0.25)
        L.append(f"- **Nihai tercih:** {b['market']} → **{b['outcome']}** @{b['odds']}" + (f" | alternatif güvenli: {safe[0]['market'][:30]} {safe[0]['outcome']} @{safe[0]['odds']} (EV {safe[0]['ev'] * 100:+.0f}%)" if safe else ""))
        L.append(f"- **Güven:** {conf}/10 | **Risk:** {rk} | **Kasa ({'1/8' if rk == 'Yüksek' else '1/4'} Kelly):** "
                 + (f"{units:.2f} Unit (1 Unit = kasa %1)" if units else "0 Unit (harman EV ≤ 0: öneri değil, yalnız izle)") + (" — SPEKÜLATİF (harman EV negatif/xG çelişkisi)" if spec else ""))
        L.append(f"- **Özet:** {len(values)} filtreli +EV seçenek; örneklem {n_min} maç olduğundan " + ("spekülatif." if spec else "orta güvenli."))
    else:
        L.append("- **Nihai tercih:** SKIP" + (f" | (değer garantisiz) en yakın güvenli seçenek: {safe[0]['market'][:30]} {safe[0]['outcome']} @{safe[0]['odds']} (EV {safe[0]['ev'] * 100:+.0f}%)" if safe else ""))
        L.append(f"- **Güven:** {conf}/10 (analiz güvenilirliği) | **Kasa:** 0 Unit")
        L.append(f"- **Özet:** komisyon {('%%%.0f' % (mm * 100)) if mm else '~%16'} iken filtreli modelde ≥%5 EV yok.")
    L.append("")
    lb_c = [p for p in main if not excl[id(p)]]   # en az kotu (harman EV en yuksek) secenek: siralama icin
    lb = max(lb_c, key=lambda p: (p["blend"] if p["blend"] is not None else p["p"]) * p["odds"] - 1) if lb_c else None
    def _pk(p):
        b = p["blend"] if p["blend"] is not None else p["p"]
        return {"market": p["market"], "outcome": p["outcome"], "odds": p["odds"], "p": b, "p_mkt": p["p_mkt"], "ev": b * p["odds"] - 1, "cat": p["cat"],
                "pa": p["p"], "rd": p["rel_div"]}   # pa = Model A olasiligi (derin.py: A+B teyidi, decision.blend)
    tops = [_pk(p) for p in sorted(lb_c, key=lambda p: -((p["blend"] if p["blend"] is not None else p["p"]) * p["odds"] - 1))[:6]]
    sig = {}
    for p in picks:   # model/piyasa sinyalleri (derin mod: 1X2, Ust 2.5, KG Var)
        nn = nrm(p["market"])
        if nn == "maç sonucu" and p["outcome"] in ("1", "X", "2"):
            sig.setdefault("1X2", {})[p["outcome"]] = (p["p"], p["p_mkt"])
        elif nn == "alt/üst 2.5" and p["outcome"] == "Üst":
            sig["O25"] = (p["p"], p["p_mkt"])
        elif nn == "karşılıklı gol" and p["outcome"] == "Var":
            sig["KG"] = (p["p"], p["p_mkt"])
    rp = rel_c[0] if rel_c else None
    summary = {"match": f"{ht} - {at}", "league": match["league"], "lam": lam, "mu": mu, "value": len(values), "ko": mc.get("ts") or 0,
               "tops": tops, "ok": [_pk(p) for p in lb_c if p["p_mkt"] is not None], "rels": [_pk(p) for p in rel_c[:3]], "sig": sig, "n_ok": len(lb_c), "n_min": n_min,
               "flags": {"valueless": bool(balanced), "xg_conf": bool(info.get("xg_conflict"))},
               "lb": ({"pick": f"{lb['market'][:30]} {lb['outcome']}", "odds": lb["odds"], "p": (lb["blend"] if lb["blend"] is not None else lb["p"]),
                       "ev": (lb["blend"] if lb["blend"] is not None else lb["p"]) * lb["odds"] - 1} if lb else None),
               "rel": ({"pick": f"{rp['market'][:30]} {rp['outcome']}", "odds": rp["odds"], "p": (rp["blend"] if rp["blend"] is not None else rp["p"]),
                        "p_mkt": rp["p_mkt"], "ev": rp["ev"]} if rp else None),
               "conf": conf, "best": (f"{values[0]['market'][:24]} {values[0]['outcome']} @{values[0]['odds']} ({values[0]['ev'] * 100:+.0f}%)" if values else "SKIP"),
               "priced": priced, "total": total,
               "quality": ("YETERSİZ" if not info["reliable"] else ("xG çelişkisi" if info.get("xg_conflict") else ("flash xG" if info.get("xg_source") else "yeterli")))}
    return "\n".join(L), summary


# ------------------------------------------------------------------ giris
def _tr_lower(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def pick_matches(session, n, comps=None, takim=None, lig=None, saat=None):
    """Katman 1'e (find_matches.py) devreder: analiz edilecek mac kimlikleri."""
    return [m["id"] for m in FM.find(session, comps, takim, lig, saat, target_date(), n)]


REPORT_SECTIONS = {"1": "### 1.", "2": "### 2.", "3": "### 3.", "4": "### 4.", "5": "### 5."}


def pick_sections(text, wanted):
    """Rapordan yalnizca istenen bolumleri (1-4) dondurur (baslik satiri her zaman dahil)."""
    if not wanted:
        return text
    lines, out, keep = text.split("\n"), [], True
    for ln in lines:
        if ln.startswith("## "):
            out.append(ln); keep = False; continue
        if ln.startswith("### "):
            keep = any(ln.startswith(REPORT_SECTIONS[w]) for w in wanted)
        if keep:
            out.append(ln)
    return "\n".join(out)


def report_path():
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"match_reports_{target_date().strftime('%Y%m%d')}.md")


def analyze_one(session, cfg, comps, eid):
    match, ctx = gather(session, cfg, comps, eid)
    if not ctx:
        return eid, None, "İddaa istatistiği yok"
    info = build_model(match, ctx)
    if not info:
        return eid, None, "model kurulamadı"
    info["mc_ts"] = ctx["mc"].get("ts") or 0
    text, s = report(match, info, ctx)
    return eid, (text, s), None


def show_saved(name, wanted):
    p = report_path()
    if not os.path.exists(p):
        print("Kayıtlı rapor yok; önce analizi çalıştır.")
        return
    with open(p, "r", encoding="utf-8") as f:
        blocks = f.read().split("\n---\n\n")
    hit = [b for b in blocks if _tr_lower(name) in _tr_lower(b.split("\n", 1)[0])]
    if not hit:
        print("Eşleşen maç yok. Kayıtlı maçlar: " + "; ".join(b.split("\n", 1)[0][3:40] for b in blocks))
        return
    for b in hit[:3]:
        print(pick_sections(b, wanted) + "\n")


def parse_args(argv):
    opt, pos, i = {"bolum": "", "takim": None, "lig": None, "goster": None, "tarih": None, "saat": None, "tam": False}, [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--bolum", "--takim", "--lig", "--goster", "--tarih", "--saat") and i + 1 < len(argv):
            opt[a[2:]] = argv[i + 1]; i += 2; continue
        if a == "--tam":
            opt["tam"] = True
        elif a == "--guvenilir":
            opt["guvenilir"] = True
        else:
            pos.append(a)
        i += 1
    opt["bolum"] = [c for c in opt["bolum"].replace(",", "") if c in "12345"]
    return opt, pos


def main(argv=None):
    from concurrent.futures import ThreadPoolExecutor
    t0 = datetime.now()
    opt, pos = parse_args(sys.argv[1:] if argv is None else argv)
    ROWS[0] = 6 if opt["tam"] else 3
    if opt["tarih"]:
        TARGET[0] = datetime.strptime(opt["tarih"], "%Y-%m-%d").date()
    if opt["goster"]:
        show_saved(opt["goster"], opt["bolum"])
        return
    session = F._get_session()
    cfg, comps = F._fetch_market_config(session), F._fetch_competitions(session)
    flash_init()
    if pos and all(a.isdigit() and len(a) > 3 for a in pos):
        ids = pos
    else:
        n = int(pos[0]) if pos else (10 if (opt["takim"] or opt["lig"] or opt["saat"]) else 5)
        ids = pick_matches(session, n, comps, opt["takim"], opt["lig"], opt["saat"])
    if not ids:
        print("Eşleşen maç bulunamadı (bugün / başlamamış / canlı değil).")
        return
    workers = int(os.environ.get("BWM_WORKERS", "8"))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda e: _safe(analyze_one, session, cfg, comps, e), ids))
    if FLASH["cache"] is not None:
        FL.save_cache(FLASH["cache"])
    TM.save()
    parts, sums = [], []
    live_ids = set(((F._get_json(session, F.EVENTS_URL).get("data") or {}).get("sc") or {}).keys())
    pids = []
    for eid, res, err in results:
        if err:
            print(f"- {eid}: {err}")
        else:
            parts.append(res[0]); sums.append(res[1]); pids.append(str(eid))
            if str(eid) in live_ids:
                print(f"! {eid}: CANLI maç — oranlar maç içi (ön maç EV karşılaştırması geçersiz); rapor kaydedilmedi.")
    path = report_path()
    store = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for b in f.read().split("\n---\n\n"):
                if b.strip():
                    store[b.split("\n", 1)[0].split("  (")[0]] = b
    for p, pid in zip(parts, pids):
        if pid in live_ids:
            continue
        store[p.split("\n", 1)[0].split("  (")[0]] = p  # ayni mac yeniden analiz edilirse guncellenir, digerleri korunur
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n---\n\n".join(store.values()))
    dt = (datetime.now() - t0).total_seconds()
    print(f"{len(sums)} maç analiz edildi ({dt:.0f}s) -> {path}\n")
    if opt.get("guvenilir"):
        R = sorted([s for s in sums if s.get("rel")], key=lambda s: -s["rel"]["p"])[:10]
        print(f"Güvenilir adaylar ({len(R)}/{len(sums)} maçta model-piyasa uyumlu ≥%{int(REL_MIN_P * 100)} seçenek var; tutma olasılığı, DEĞER değil):")
        print("| Maç | Saat | Seçenek | Oran | Harman p | Piyasa p | EV |\n| :--- | :--- | :--- | ---: | ---: | ---: | ---: |")
        for s in R:
            r = s["rel"]
            print(f"| {s['match']} | {datetime.fromtimestamp(s['ko'], TR).strftime('%H:%M') if s.get('ko') else '-'} | {r['pick']} | {r['odds']} | %{r['p'] * 100:.0f} | %{r['p_mkt'] * 100:.0f} | {r['ev'] * 100:+.0f}% |")
        return
    shown = sums
    if len(sums) > 12:   # cok maçta yalnizca en cok deger/guven tasiyan ilk 12 (token tasarrufu); tumu raporda
        shown = sorted(sums, key=lambda s: (s["value"] > 0 and s["quality"] != "YETERSİZ", s["conf"], s["value"]), reverse=True)[:12]
        n_skip = sum(1 for s in sums if s["value"] == 0)
        print(f"Toplam {len(sums)} maç: {n_skip} SKIP (filtreli +EV yok), {len(sums) - n_skip} maçta spekülatif/değerli seçenek. En güçlü 12:")
    print("| Maç | Lig | λ / μ | Fiyatlanan | Değerli | En iyi | Güven | İstatistik |")
    print("| :--- | :--- | :--- | ---: | ---: | :--- | ---: | :--- |")
    for s in shown:
        print(f"| {s['match']} | {s['league'][:20]} | {s['lam']:.2f} / {s['mu']:.2f} | {s['priced']}/{s['total']} | {s['value']} | {s['best']} | {s['conf']}/10 | {s['quality']} |")
    if parts and (len(parts) == 1 or opt["tam"]):
        sec = opt["bolum"] or ([] if opt["tam"] else list("1345"))   # varsayilan kisa cikti: buyuk pazar tablolari (bolum 2) yalnizca --tam / --bolum 2 ile
        for p in parts[: (1 if len(parts) == 1 else 3)]:
            print("\n" + pick_sections(p, sec))


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return a[-1], None, f"HATA {type(e).__name__}: {e}"


if __name__ == "__main__":
    main()
