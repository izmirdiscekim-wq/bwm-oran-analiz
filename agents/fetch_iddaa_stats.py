"""
Iddaa.com'un kendi istatistik servisi (statisticsv2.iddaa.com, kaynak: BetRadar) - guncel sezon verisi.

Uc noktalar (iddaa.com JS kodundan): match-card (son 5 form), standings (puan durumu),
recent-matches (0: ev sahibi son 10, 1: deplasman son 10, 2: karsilikli gecmis), card-corners
(son 6 mac korner/kart). Resmi/belgeli bir API degil; herkese acik site servisi, nazik hizda cagrilir.

Model: ayni ligdeki son maclardan (max 10) gol atma/yeme oranlari, lig ortalamasina
K_SHRINK sahte-mac ile cekilerek (kucuk orneklem) -> bagimsiz Poisson olasiliklari.
Cikti: data/football_matches_YYYYMMDD_istat.json (real_stats + model_entry, analyze_bwm okur).

Kullanim: python fetch_iddaa_stats.py [N]   (N: en fazla kac mac, varsayilan 150)
"""
import os
import re
import sys
import json
import glob
import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from scipy.special import gammaln

from dixon_coles_model import LEAGUE_CODES, compare_with_market, _market_view

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://statisticsv2.iddaa.com/statistics/soccer"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0 Safari/537.36",
           "Origin": "https://www.iddaa.com", "Referer": "https://www.iddaa.com/",
           "Accept": "application/json, text/plain, */*"}
K_SHRINK = 6           # lig ortalamasina cekme gucu (sahte mac sayisi)
HOME_ADV = 1.12        # ev sahibi gol carpani (deplasman 1/HOME_ADV)
MIN_SAME_LEAGUE = 3    # modelin calismasi icin takim basina en az lig maci
MU_DEFAULT = 1.35
WORKERS = 10
GRID = 11


def make_session():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])))
    s.headers.update(HEADERS)
    return s


def api(session, path):
    try:
        r = session.get(BASE + path, timeout=15)
        if r.status_code != 200:
            return None
        j = r.json()
        return j.get("data") if j.get("isSuccess") else None
    except Exception:
        return None


def league_base_name(standings, lg_name):
    ss = (standings or {}).get("ss") or {}
    n = ss.get("n") or ""
    return re.sub(r"\s*(?:\d{2,4}/\d{2,4}|\d{4})\s*$", "", n) or lg_name


def same_league(m_ln, base_name, lg_name):
    return bool(m_ln) and (m_ln == base_name or m_ln in (lg_name or "") or (base_name or "") in m_ln)


def team_games(rm, team, base_name, lg_name):
    games = []
    for m in (rm or {}).get("m", []):
        if not same_league((m.get("l") or {}).get("n"), base_name, lg_name):
            continue
        h, a = m.get("h", {}), m.get("a", {})
        if h.get("rs") is None or a.get("rs") is None:
            continue
        if h.get("n") == team:
            games.append((h["rs"], a["rs"]))
        elif a.get("n") == team:
            games.append((a["rs"], h["rs"]))
    return games


def strength(games, mu):
    n = len(games)
    gf, ga = sum(g[0] for g in games), sum(g[1] for g in games)
    att = ((gf + K_SHRINK * mu) / (n + K_SHRINK)) / mu
    dfn = ((ga + K_SHRINK * mu) / (n + K_SHRINK)) / mu
    return att, dfn, n


def poisson_pred(lam, mu_):
    g = np.arange(GRID)
    grid = np.outer(np.exp(g * math.log(lam) - lam - gammaln(g + 1)), np.exp(g * math.log(mu_) - mu_ - gammaln(g + 1)))
    grid /= grid.sum()
    tot = np.add.outer(g, g)
    return {"p1": float(np.tril(grid, -1).sum()), "pX": float(np.trace(grid)), "p2": float(np.triu(grid, 1).sum()),
            "p_over25": float(grid[tot >= 3].sum()), "p_goals_2_3": float(grid[(tot == 2) | (tot == 3)].sum()),
            "exp_home": round(lam, 2), "exp_away": round(mu_, 2), "exp_total": round(lam + mu_, 2)}


def cc_summary(cc):
    """Son ~6 macta korner ve kart (sari+kirmizi) ortalamalari; beklenen toplamlar."""
    if not cc:
        return None
    out = {}
    for side in ("h", "a"):
        team = cc[side]["n"]
        cf = ca = kf = ka = n = 0
        for m in cc[side].get("m", []):
            own, opp = (m["h"], m["a"]) if m["h"].get("n") == team else (m["a"], m["h"])
            if any(own.get(k) is None or opp.get(k) is None for k in ("c", "yc", "rc")):
                continue
            n += 1
            cf += own["c"]; ca += opp["c"]
            kf += own["yc"] + own["rc"]; ka += opp["yc"] + opp["rc"]
        if n < 3:
            return None
        out[side] = {"n": n, "corners_for": cf / n, "corners_against": ca / n, "cards_for": kf / n, "cards_against": ka / n}
    h, a = out["h"], out["a"]
    return {"games": [h["n"], a["n"]],
            "exp_corners": round((h["corners_for"] + a["corners_against"]) / 2 + (a["corners_for"] + h["corners_against"]) / 2, 1),
            "exp_cards": round((h["cards_for"] + a["cards_against"]) / 2 + (a["cards_for"] + h["cards_against"]) / 2, 1)}


def h2h_summary(rm, home_team):
    ms = [m for m in (rm or {}).get("m", []) if m.get("h", {}).get("rs") is not None and m.get("a", {}).get("rs") is not None]
    if not ms:
        return None
    hw = aw = d = goals = 0
    for m in ms:
        hs, as_ = m["h"]["rs"], m["a"]["rs"]
        goals += hs + as_
        if hs == as_:
            d += 1
        elif (hs > as_) == (m["h"].get("n") == home_team):
            hw += 1
        else:
            aw += 1
    return {"played": len(ms), "home_wins": hw, "away_wins": aw, "draws": d, "avg_total_goals": round(goals / len(ms), 2)}


def process_match(session, match, standings_cache, light=False):
    eid = match["match_id"]
    mc = api(session, f"/match-card/{eid}?isLive=false")
    if not mc:
        return None
    lg = mc.get("lg") or {}
    key = (lg.get("i"), mc.get("ssi"))
    if key not in standings_cache:
        standings_cache[key] = api(session, f"/standings/{eid}?standingType=0&isLive=false")
    st = standings_cache[key]
    base_name = league_base_name(st, lg.get("n"))

    rows = []
    for grp in (st or {}).get("g", []):
        rows += grp.get("t", [])
    total_p = sum(r.get("p", 0) for r in rows)
    mu = (sum(r.get("gf", 0) for r in rows) / total_p) if total_p >= 20 else MU_DEFAULT
    by_name = {r["n"]: r for r in rows}

    ht, at = mc["h"]["n"], mc["a"]["n"]
    rm_h = api(session, f"/recent-matches/{eid}?matchHistoryType=0&isOnlyCurrentTournament=false")
    rm_a = api(session, f"/recent-matches/{eid}?matchHistoryType=1&isOnlyCurrentTournament=false")
    rm_x = api(session, f"/recent-matches/{eid}?matchHistoryType=2&isOnlyCurrentTournament=false")
    cc = None if light else cc_summary(api(session, f"/card-corners/{eid}"))   # light: 1 istek az/mac (hizli tarama)

    gh, ga_ = team_games(rm_h, ht, base_name, lg.get("n")), team_games(rm_a, at, base_name, lg.get("n"))
    form_h, form_a = "".join(mc["h"].get("lfive") or []), "".join(mc["a"].get("lfive") or [])

    def form_block(games, form):
        if len(games) < MIN_SAME_LEAGUE:
            return None
        return {"played": len(games), "form_string": form or "-",
                "avg_goals_scored": round(sum(g[0] for g in games) / len(games), 2),
                "avg_goals_conceded": round(sum(g[1] for g in games) / len(games), 2)}

    notes = []
    for label, t in (("Ev", ht), ("Dep", at)):
        r = by_name.get(t)
        if r:
            notes.append(f"{label}: {r['r']}. sıra, {r['pt']} puan ({r['w']}G-{r['d']}B-{r['l']}M, av {r['gf']}-{r['ga']})")
    if cc:
        notes.append(f"Korner beklentisi {cc['exp_corners']}, kart {cc['exp_cards']} (son ~6 maç)")

    entry = {"real_stats": {"source": "iddaa-istatistik", "home_form": form_block(gh, form_h), "away_form": form_block(ga_, form_a),
                            "h2h": h2h_summary(rm_x, ht), "extra_note": " | ".join(notes) if notes else None},
             "cc": cc, "league_mu": round(mu, 2), "same_league_games": [len(gh), len(ga_)], "model_entry": None}

    if len(gh) >= MIN_SAME_LEAGUE and len(ga_) >= MIN_SAME_LEAGUE:
        ah, dh, _ = strength(gh, mu)
        aa, da, _ = strength(ga_, mu)
        pred = poisson_pred(mu * ah * da * HOME_ADV, mu * aa * dh / HOME_ADV)
        cmp_ = compare_with_market(pred, _market_view(match))
        entry["model_entry"] = {"source": "iddaa-istatistik", "league_matches": len(gh) + len(ga_),
                                "model": {k: round(v, 4) if isinstance(v, float) else v for k, v in pred.items()}, **cmp_}
    return entry


def get_latest_enriched_file(data_dir):
    files = glob.glob(os.path.join(data_dir, "football_matches_*_enriched.json"))
    return max(files, key=os.path.getmtime) if files else None


def run(n=150, light=False):
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    src = get_latest_enriched_file(data_dir)
    if not src:
        print("Hata: '*_enriched.json' yok.")
        return {}
    with open(src, "r", encoding="utf-8") as f:
        matches = [m for m in json.load(f) if not m.get("is_live")]
    matches.sort(key=lambda m: (0 if m.get("league") in LEAGUE_CODES else 1, -sum(len(v) for v in m["markets"].values() if isinstance(v, (list, dict)))))
    picked = matches[:n]

    session, cache, out = make_session(), {}, {}

    def worker(m):
        time.sleep(0.05)
        try:
            return m["match_id"], process_match(session, m, cache, light)
        except Exception:
            return m["match_id"], None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for mid, entry in pool.map(worker, picked):
            if entry:
                out[mid] = entry

    dst = src.replace("_enriched.json", "_istat.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    n_model = sum(1 for e in out.values() if e["model_entry"])
    print(f"{len(out)}/{len(picked)} maç için İddaa istatistiği çekildi; {n_model} maçta model kuruldu. -> {dst}")
    return out


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
