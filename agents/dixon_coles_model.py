"""
Dixon-Coles (1997) modeli: zaman-agirlikli, dusuk skor duzeltmeli (rho) Poisson.

Neden: enrich_stats.py'deki model sadece piyasanin fikrini geri yansitir. Bu modul,
liglerin GERCEK gecmis sonuclarindan (football-data.co.uk CSV) her takimin
hucum/savunma gucunu ogrenir ve piyasadan BAGIMSIZ bir olasilik uretir.
Model-piyasa ayrisimi (divergence) BWM'nin "deger" tanimidir.

Durust beklenti: acik kaynak denetimlerde model tek basina bahisci fiyatini
yenmiyor. Bu yuzden nihai olasilik piyasa agirlikli harmanlanir
(MARKET_WEIGHT) ve model 'filtre' olarak kullanilir, kor bahis sinyali olarak degil.

Veri: data/history/{KOD}_{SEZON}.csv (orn. E0_2627.csv). Script indirmeyi dener;
site erisilemezse dosyalari elle bu klasore koyabilirsin.
    https://www.football-data.co.uk/mmz4281/2627/E0.csv
"""
import os
import re
import glob
import json
import math
import difflib
import unicodedata
import numpy as np
import pandas as pd
import requests
from datetime import datetime
from scipy.optimize import minimize
from scipy.special import gammaln

from enrich_stats import _find_totals_entry, _find_goal_band_entry, devig

XI_PER_DAY = 0.0019        # Dixon-Coles orijinal ~0.0065/yarim hafta => ~1 yil yari omur
MAX_GOALS = 10
MARKET_WEIGHT = 0.70       # nihai olasilikta piyasa agirligi
MIN_LEAGUE_MATCHES = 60
MIN_TEAM_MATCHES = 3
NAME_MATCH_THRESHOLD = 0.72
SEASONS = ["2627", "2526"]
FD_URL = "https://www.football-data.co.uk/mmz4281/{season}/{code}.csv"

LEAGUE_CODES = {
    "İngiltere Premier Lig": "E0", "İngiltere Championship": "E1",
    "İngiltere 1. Lig": "E2", "İngiltere 2. Lig": "E3", "İngiltere Ulusal Lig": "EC",
    "İskoçya Premiership": "SC0", "İskoçya Championship": "SC1",
    "İskoçya 1. Lig": "SC2", "İskoçya 2. Lig": "SC3",
    "Almanya Bundesliga": "D1", "Almanya 2. Bundesliga": "D2",
    "İspanya La Liga": "SP1", "İspanya La Liga 2": "SP2",
    "İtalya Serie A": "I1", "İtalya Serie B": "I2",
    "Fransa Ligue 1": "F1", "Fransa Ligue 2": "F2",
    "Hollanda Eredivisie": "N1", "Belçika Pro Lig": "B1",
    "Portekiz Premier Lig": "P1", "Türkiye Süper Lig": "T1",
    "Yunanistan Süper Lig": "G1",
}


# ---------- Shin de-vig ----------
def shin_probs(odds_by_outcome):
    """Shin (1993) yontemi: favori-sansli yanliligini duzelten vig arindirma."""
    items = {k: v for k, v in odds_by_outcome.items() if v and v > 1.0}
    if len(items) < 2:
        return {}
    pi = {k: 1.0 / v for k, v in items.items()}
    s = sum(pi.values())

    def probs(z):
        return {k: (math.sqrt(z * z + 4 * (1 - z) * (p * p) / s) - z) / (2 * (1 - z)) for k, p in pi.items()}

    lo, hi = 0.0, 0.4
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(probs(mid).values()) > 1:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    tot = sum(p.values())
    return {k: v / tot for k, v in p.items()}


# ---------- Dixon-Coles ----------
def _tau(x, y, lam, mu, rho):
    t = np.ones_like(lam)
    t = np.where((x == 0) & (y == 0), 1 - lam * mu * rho, t)
    t = np.where((x == 0) & (y == 1), 1 + lam * rho, t)
    t = np.where((x == 1) & (y == 0), 1 + mu * rho, t)
    t = np.where((x == 1) & (y == 1), 1 - rho, t)
    return np.clip(t, 1e-6, None)


def fit_dixon_coles(df, ref_date):
    """df: Date, HomeTeam, AwayTeam, FTHG, FTAG. Doner: model dict veya None."""
    teams = sorted(set(df["HomeTeam"]) | set(df["AwayTeam"]))
    counts = pd.concat([df["HomeTeam"], df["AwayTeam"]]).value_counts()
    if len(df) < MIN_LEAGUE_MATCHES:
        return None
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    hi = df["HomeTeam"].map(idx).to_numpy()
    ai = df["AwayTeam"].map(idx).to_numpy()
    hg = df["FTHG"].to_numpy(dtype=float)
    ag = df["FTAG"].to_numpy(dtype=float)
    days = (ref_date - df["Date"]).dt.days.clip(lower=0).to_numpy(dtype=float)
    w = np.exp(-XI_PER_DAY * days)

    def negll(p):
        att, dfn, home, rho = p[:n], p[n:2 * n], p[2 * n], p[2 * n + 1]
        lam = np.exp(att[hi] + dfn[ai] + home)
        mu = np.exp(att[ai] + dfn[hi])
        ll = (hg * np.log(lam) - lam) + (ag * np.log(mu) - mu) + np.log(_tau(hg, ag, lam, mu, rho))
        return -(w * ll).sum() + 100.0 * att.mean() ** 2

    x0 = np.concatenate([np.zeros(2 * n), [0.25, -0.05]])
    bounds = [(-3, 3)] * (2 * n) + [(-1, 1), (-0.3, 0.3)]
    res = minimize(negll, x0, method="L-BFGS-B", bounds=bounds)
    if not res.success and not np.isfinite(res.fun):
        return None
    p = res.x
    return {
        "teams": teams, "idx": idx, "counts": counts.to_dict(),
        "att": p[:n], "def": p[n:2 * n], "home": p[2 * n], "rho": p[2 * n + 1],
        "n_matches": len(df),
    }


def predict(model, home, away):
    i, j = model["idx"][home], model["idx"][away]
    lam = math.exp(model["att"][i] + model["def"][j] + model["home"])
    mu = math.exp(model["att"][j] + model["def"][i])
    g = np.arange(0, MAX_GOALS + 1)
    ph = np.exp(g * math.log(lam) - lam - gammaln(g + 1))
    pa = np.exp(g * math.log(mu) - mu - gammaln(g + 1))
    grid = np.outer(ph, pa)
    rho = model["rho"]
    grid[0, 0] *= max(1 - lam * mu * rho, 1e-6)
    grid[0, 1] *= max(1 + lam * rho, 1e-6)
    grid[1, 0] *= max(1 + mu * rho, 1e-6)
    grid[1, 1] *= max(1 - rho, 1e-6)
    grid /= grid.sum()
    tot = np.add.outer(g, g)
    return {
        "p1": float(np.tril(grid, -1).sum()), "pX": float(np.trace(grid)), "p2": float(np.triu(grid, 1).sum()),
        "p_over25": float(grid[tot >= 3].sum()),
        "p_goals_2_3": float(grid[(tot == 2) | (tot == 3)].sum()),
        "exp_home": round(lam, 2), "exp_away": round(mu, 2), "exp_total": round(lam + mu, 2),
    }


# ---------- Takim adi eslestirme ----------
_STOP = {"fc", "cf", "afc", "sc", "ac", "as", "sd", "ud", "cd", "rc", "fk", "sk", "de", "the", "1", "u23"}


_ALIASES = {
    "wolverhampton": "wolves", "wolverhampton wanderers": "wolves", "manchester city": "man city",
    "manchester united": "man united", "nottingham forest": "nott m forest", "paris saint germain": "paris sg",
    "atletico madrid": "ath madrid", "athletic bilbao": "ath bilbao", "real sociedad": "sociedad",
    "real betis": "betis", "celta vigo": "celta", "espanyol": "espanol", "hellas verona": "verona",
    "inter milan": "inter", "ac milan": "milan", "borussia monchengladbach": "m gladbach",
    "eintracht frankfurt": "ein frankfurt", "bayer leverkusen": "leverkusen", "borussia dortmund": "dortmund",
    "newcastle united": "newcastle", "tottenham hotspur": "tottenham", "leicester city": "leicester",
    "brighton hove albion": "brighton", "sheffield united": "sheffield united", "sheffield wednesday": "sheffield weds",
    "queens park rangers": "qpr", "west bromwich albion": "west brom", "sporting lisbon": "sp lisbon",
}


def _norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\butd\b", "united", s)   # Flashscore kisaltmasi ("Manchester Utd"): iddaa "Manchester United" ile eslessin
    s = _ALIASES.get(" ".join(s.split()), s)
    return [t for t in s.split() if t not in _STOP]


def _similarity(a, b):
    ta, tb = _norm(a), _norm(b)
    if not ta or not tb:
        return 0.0
    ratio = difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hits = sum(1 for t in short if any(l.startswith(t) or t.startswith(l) for l in long_))
    prefix = 0.95 * hits / len(short) if hits == len(short) else 0.0
    return max(ratio, prefix)


def resolve_name(name, model):
    best, best_s = None, 0.0
    for t in model["teams"]:
        s = _similarity(name, t)
        if s > best_s:
            best, best_s = t, s
    if best and best_s >= NAME_MATCH_THRESHOLD and model["counts"].get(best, 0) >= MIN_TEAM_MATCHES:
        return best
    return None


# ---------- Veri yukleme ----------
_DOWNLOAD_STATE = {"disabled": False}


def load_league_history(data_dir, code):
    hist_dir = os.path.join(data_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)
    frames = []
    for season in SEASONS:
        path = os.path.join(hist_dir, f"{code}_{season}.csv")
        if not os.path.exists(path):
            if _DOWNLOAD_STATE["disabled"]:
                continue
            try:
                r = requests.get(FD_URL.format(season=season, code=code), timeout=15,
                                 headers={"User-Agent": "Mozilla/5.0"})
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)
            except requests.HTTPError as e:
                print(f"  Uyarı: {code}_{season}.csv yok ({e.response.status_code}); bu sezon atlanır.")
                continue
            except Exception as e:
                print(f"  Uyarı: football-data.co.uk erişilemiyor ({type(e).__name__}); bu çalıştırmada indirme kapatıldı. "
                      f"CSV'leri elle data/history/ içine koyabilirsin.")
                _DOWNLOAD_STATE["disabled"] = True
                continue
        try:
            df = pd.read_csv(path, encoding="latin-1")
            df = df[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            frames.append(df.dropna(subset=["Date"]))
        except Exception as e:
            print(f"  Uyarı: {os.path.basename(path)} okunamadı ({e}).")
    return pd.concat(frames, ignore_index=True) if frames else None


# ---------- Piyasa karsilastirma ----------
def _market_view(match):
    m = match.get("markets", {})
    ms = m.get("match_outcome", {}) or {}
    out = {"odds": {}, "shin": {}}
    if all(k in ms for k in ("1", "X", "2")):
        sh = shin_probs({"1": ms["1"], "X": ms["X"], "2": ms["2"]})
        out["shin"].update({"p1": sh.get("1"), "pX": sh.get("X"), "p2": sh.get("2")})
        out["odds"].update({"1": ms["1"], "X": ms["X"], "2": ms["2"]})
    tot = _find_totals_entry(m.get("totals_goals", []) or [], scope="full")
    if tot and str(tot.get("line")) in ("2.5", "2.50"):
        oc = tot.get("outcomes", {})
        if "Alt" in oc and "Üst" in oc:
            sh = shin_probs({"Alt": oc["Alt"], "Üst": oc["Üst"]})
            out["shin"]["p_over25"] = sh.get("Üst")
            out["odds"].update({"Üst 2.5": oc["Üst"], "Alt 2.5": oc["Alt"]})
    band = _find_goal_band_entry(m.get("totals_goals", []) or [])
    if band:
        key = next((k for k in band["outcomes"] if "2-3" in k.replace(" ", "")), None)
        if key:
            dv = devig(band["outcomes"])
            out["shin"]["p_goals_2_3"] = dv.get(key)
            out["odds"]["2-3 Gol"] = band["outcomes"][key]
    return out


def compare_with_market(pred, market):
    shin, odds = market["shin"], market["odds"]
    keymap = [("p1", "1"), ("pX", "X"), ("p2", "2"), ("p_over25", "Üst 2.5"), ("p_goals_2_3", "2-3 Gol")]
    blend, diverg, value = {}, {}, []
    for pk, ok in keymap:
        mp = shin.get(pk)
        if mp is None:
            continue
        b = MARKET_WEIGHT * mp + (1 - MARKET_WEIGHT) * pred[pk]
        blend[pk] = round(b, 4)
        diverg[pk] = round(pred[pk] - mp, 4)
        if ok in odds:
            value.append({"market": ok, "odds": odds[ok], "blend_prob": round(b, 4), "ev": round(b * odds[ok] - 1, 4)})
    if "p_over25" in shin and "Alt 2.5" in odds:
        b_under = 1 - blend["p_over25"]
        value.append({"market": "Alt 2.5", "odds": odds["Alt 2.5"], "blend_prob": round(b_under, 4),
                      "ev": round(b_under * odds["Alt 2.5"] - 1, 4)})
    value.sort(key=lambda v: v["ev"], reverse=True)
    max_abs = max((abs(v) for v in diverg.values()), default=0.0)
    return {"market_shin": {k: round(v, 4) for k, v in shin.items() if v is not None},
            "blend": blend, "divergence": diverg, "max_abs_divergence": round(max_abs, 4), "value": value}


def get_latest_enriched_file(data_dir):
    files = glob.glob(os.path.join(data_dir, "football_matches_*_enriched.json"))
    return max(files, key=os.path.getmtime) if files else None


def run():
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    src = get_latest_enriched_file(data_dir)
    if not src:
        print("Hata: '*_enriched.json' yok. Önce fetch_iddaa.py + enrich_stats.py çalıştırın.")
        return
    with open(src, "r", encoding="utf-8") as f:
        matches = json.load(f)

    ref_date = pd.Timestamp(datetime.now().date())
    by_code = {}
    for m in matches:
        code = LEAGUE_CODES.get(m.get("league"))
        if code and not m.get("is_live"):
            by_code.setdefault(code, []).append(m)

    print(f"{sum(len(v) for v in by_code.values())} maç, {len(by_code)} desteklenen ligde (canlı maçlar hariç).")
    out, unresolved = {}, []
    for code, ms in by_code.items():
        df = load_league_history(data_dir, code)
        if df is None:
            print(f"  {code}: geçmiş veri yok, atlandı.")
            continue
        model = fit_dixon_coles(df, ref_date)
        if not model:
            print(f"  {code}: yetersiz veri ({len(df)} maç), atlandı.")
            continue
        print(f"  {code}: model {model['n_matches']} maçtan eğitildi (rho={model['rho']:.3f}, ev avantajı={model['home']:.3f}).")
        for m in ms:
            h, a = resolve_name(m["home"], model), resolve_name(m["away"], model)
            if not h or not a:
                unresolved.append(f"{m['home']} - {m['away']} ({code})")
                continue
            pred = predict(model, h, a)
            cmp_ = compare_with_market(pred, _market_view(m))
            out[m["match_id"]] = {
                "league_code": code, "home_model_name": h, "away_model_name": a,
                "league_matches": model["n_matches"], "model": {k: round(v, 4) if isinstance(v, float) else v for k, v in pred.items()},
                **cmp_,
            }

    base = os.path.basename(src).replace("_enriched.json", "")
    dst = os.path.join(data_dir, f"{base}_model.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n{len(out)} maç için Dixon-Coles tahmini yazıldı: {dst}")
    if unresolved:
        print(f"Takım adı eşleşmeyen {len(unresolved)} maç: " + "; ".join(unresolved[:10]))


if __name__ == "__main__":
    run()
