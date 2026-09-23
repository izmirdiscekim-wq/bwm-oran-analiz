"""
Piyasa tutarliligi (cross-market coherence) analizi - dis veri gerektirmez.

Fikir: Ayni macin 1X2, Alt/Ust (tum cizgiler), Karsilikli Gol ve Toplam Gol araligi fiyatlari
TEK bir (lambda_ev, lambda_dep) Poisson cifti ile uyumlu olmali. Ortak modeli tum piyasalara
oturtur, sonra her sonuc icin 'konsensus adil fiyat' ile sunulan oranin farkini (EV) olcer.
Ayrica her piyasanin komisyonunu (overround) hesaplar: EV, marjin uzerinde bir kenar
gerektirir; Iddaa marjlari yuksek oldugu icin cogu mac dogal olarak elenir.

Durust not: bu yontem yalnizca piyasanin KENDI ic tutarsizligini gorur, gercek dunyadan bagimsiz
bilgi getirmez. Bu yuzden 'izleme listesi' uretir; onay icin bagimsiz veri (H2H/Dixon-Coles) gerekir.
"""
import os
import re
import glob
import json
import math
import numpy as np
from datetime import datetime
from scipy.optimize import minimize
from scipy.special import gammaln

from dixon_coles_model import shin_probs

GRID = 13
MIN_EDGE_PROB = 0.10      # cok dusuk olasilikli sonuclarda model hatasi EV'yi sisirir
FIT_RMSE_LIMIT = 0.035    # bunun ustunde piyasalar tek modele uymuyor (tutarsiz)
WATCH_EV = 0.04


def _pois(lam):
    g = np.arange(GRID)
    return np.exp(g * math.log(lam) - lam - gammaln(g + 1))


def _grid(lam, mu):
    return np.outer(_pois(lam), _pois(mu))


def _parse_band(label):
    nums = re.findall(r"\d+", label)
    if len(nums) >= 2 and "-" in label:
        return int(nums[0]), int(nums[1])
    if len(nums) >= 1 and "+" in label:
        return int(nums[0]), 99
    return None


def collect_markets(match):
    """Doner: list of dict(kind, name, odds{label: odd}, fn(grid)->{label: prob})"""
    m = match.get("markets", {})
    tot = np.add.outer(np.arange(GRID), np.arange(GRID))
    out = []

    ms = m.get("match_outcome", {}) or {}
    if all(k in ms for k in ("1", "X", "2")):
        out.append({"name": "1X2", "odds": {"1": ms["1"], "X": ms["X"], "2": ms["2"]},
                    "fn": lambda g: {"1": float(np.tril(g, -1).sum()), "X": float(np.trace(g)), "2": float(np.triu(g, 1).sum())}})

    for e in m.get("totals_goals", []) or []:
        name = e.get("market_name", "")
        oc = e.get("outcomes", {})
        if name.startswith("Alt/Üst") and " ve " not in name.lower() and "Alt" in oc and "Üst" in oc:
            try:
                line = float(e.get("line"))
            except (TypeError, ValueError):
                continue
            if line not in (1.5, 2.5, 3.5):
                continue
            out.append({"name": f"A/Ü {line}", "odds": {"Alt": oc["Alt"], "Üst": oc["Üst"]},
                        "fn": (lambda ln: lambda g: {"Üst": float(g[tot > ln].sum()), "Alt": float(g[tot < ln].sum())})(line)})
        elif name == "Toplam Gol":
            bands = {}
            for label, odd in oc.items():
                rng = _parse_band(label)
                if rng:
                    bands[label] = (rng, odd)
            if len(bands) >= 3:
                out.append({"name": "Toplam Gol", "odds": {k: v[1] for k, v in bands.items()},
                            "fn": (lambda bd: lambda g: {k: float(g[(tot >= r[0]) & (tot <= r[1])].sum()) for k, (r, _) in bd.items()})(bands)})

    btts = m.get("both_teams_score", {}) or {}
    if "Var" in btts and "Yok" in btts:
        out.append({"name": "KG", "odds": {"Var": btts["Var"], "Yok": btts["Yok"]},
                    "fn": lambda g: {"Var": float(g[1:, 1:].sum()), "Yok": float(1 - g[1:, 1:].sum())}})
    return out


def analyze_match(match):
    if match.get("is_live"):
        return None
    markets = collect_markets(match)
    if len(markets) < 3:
        return None

    targets = []
    for mk in markets:
        if any(o <= 1.01 for o in mk["odds"].values()):
            continue  # askiya alinmis/bozuk piyasa
        p = shin_probs(mk["odds"])
        if not p or set(p) != set(mk["odds"]):
            continue
        mk["market_p"] = p
        mk["margin"] = round(sum(1.0 / o for o in mk["odds"].values()) - 1.0, 4)
        targets.append(mk)
    if len(targets) < 3:
        return None

    def loss(x):
        lam, mu = math.exp(x[0]), math.exp(x[1])
        g = _grid(lam, mu)
        g = g / g.sum()
        se, n = 0.0, 0
        for mk in targets:
            mp = mk["fn"](g)
            for k, p in mk["market_p"].items():
                se += (mp.get(k, 0.0) - p) ** 2
                n += 1
        return se / max(n, 1)

    st = match.get("stats", {})
    x0 = [math.log(max(st.get("home_avg_goals_scored", 1.3), 0.2)), math.log(max(st.get("away_avg_goals_scored", 1.0), 0.2))]
    res = minimize(loss, x0, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-8, "maxiter": 200})
    lam, mu = math.exp(res.x[0]), math.exp(res.x[1])
    rmse = math.sqrt(res.fun)
    g = _grid(lam, mu)
    g = g / g.sum()

    edges = []
    for mk in targets:
        fair = mk["fn"](g)
        for k, odd in mk["odds"].items():
            p = fair.get(k, 0.0)
            if p < MIN_EDGE_PROB:
                continue
            edges.append({"market": f"{mk['name']} {k}", "odds": odd, "fair_prob": round(p, 3),
                          "market_prob": round(mk["market_p"][k], 3), "ev": round(p * odd - 1, 4),
                          "kelly": round(max((p * odd - 1) / (odd - 1), 0.0), 4)})
    edges.sort(key=lambda e: e["ev"], reverse=True)
    return {
        "lam_home": round(lam, 2), "lam_away": round(mu, 2), "rmse": round(rmse, 4),
        "coherent": rmse <= FIT_RMSE_LIMIT, "n_markets": len(targets),
        "margins": {mk["name"]: mk["margin"] for mk in targets},
        "avg_margin": round(sum(mk["margin"] for mk in targets) / len(targets), 4),
        "edges": edges[:3],
    }


def get_latest_enriched_file(data_dir):
    files = glob.glob(os.path.join(data_dir, "football_matches_*_enriched.json"))
    return max(files, key=os.path.getmtime) if files else None


def run():
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    src = get_latest_enriched_file(data_dir)
    if not src:
        print("Hata: '*_enriched.json' yok.")
        return
    with open(src, "r", encoding="utf-8") as f:
        matches = json.load(f)
    out = {}
    snap_lines = []
    now = datetime.now().isoformat(timespec="minutes")
    for m in matches:
        r = analyze_match(m)
        if r:
            out[m["match_id"]] = r
        ms = (m.get("markets", {}).get("match_outcome") or {})
        if not m.get("is_live") and all(k in ms for k in ("1", "X", "2")):
            snap_lines.append(json.dumps({"id": m["match_id"], "t": now, "o": [ms["1"], ms["X"], ms["2"]]}))
    # Oran hareketi icin anlik goruntu: her calistirmada eklenir, deep_dive.py ilk/son farkini gosterir
    with open(os.path.join(data_dir, "odds_snapshots.jsonl"), "a", encoding="utf-8") as f:
        f.write("\n".join(snap_lines) + "\n")
    dst = os.path.join(data_dir, os.path.basename(src).replace("_enriched.json", "_coherence.json"))
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"{len(out)} maç için piyasa tutarlılık analizi yazıldı: {dst}")
    return out


if __name__ == "__main__":
    run()
