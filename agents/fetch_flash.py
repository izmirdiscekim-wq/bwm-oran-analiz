"""
Flashscore veri katmani: iddaa maclarini Flashscore ile eslestirir, takimlarin SON MACLARININ gercek
istatistiklerini (xG, sut, isabetli sut, korner, kart, gol) ve hakem/stadyum bilgisini ceker.

Kaynak: Flashscore'un herkese acik veri akisi (global.flashscore.ninja, 'x-fsign' basligi siteden alinir).
Resmi/belgeli bir API degil; nazik hizda (paralel <=8) ve yerel onbellekle cagrilir. Bot korumasi asilmaz.
Onbellek: data/flash_stat_cache.json (bitmis mac istatistigi degismez -> kalici).

Beklenen alanlar YOK: hakemin kart ortalamasi (RCO/RTY anlami cozulemedi), sakatlik/eksik oyuncu listesi.
"""
import os
import re
import sys
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests

from dixon_coles_model import _similarity

sys.stdout.reconfigure(encoding="utf-8")

BASE = "https://global.flashscore.ninja/2/x/feed/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
           "x-fsign": "SW9D1eZo", "Referer": "https://www.flashscore.com/", "Origin": "https://www.flashscore.com",
           "Accept-Language": "en-US,en;q=0.9"}
LINK_SIM = 0.72
LINK_WINDOW = 1800          # saniye
LAST_N = 6
WORKERS = 8
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_lock = threading.Lock()


def make_session():
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def feed(session, name):
    try:
        r = session.get(BASE + name, timeout=20)
        return r.text if r.status_code == 200 else ""
    except Exception:
        return ""


def parse(text):
    out = []
    for chunk in text.split("~"):
        d = {}
        for part in chunk.split("¬"):
            if "÷" in part:
                k, v = part.split("÷", 1)
                d[k] = v
        if d:
            out.append(d)
    return out


def load_list(session, day=0):
    """Flashscore futbol maclari (day: bugunden kac gun sonra, 0=bugun): [{id, ts, home, away, league, status}]"""
    league, out = None, []
    for d in parse(feed(session, f"f_1_{day}_3_en_1")):
        if "ZA" in d:
            league = d["ZA"]
        if "AA" in d and "AD" in d:
            out.append({"id": d["AA"], "ts": int(d["AD"]), "home": d.get("AE", ""), "away": d.get("AF", ""),
                        "league": league, "status": d.get("AB")})
    return out


def link(iddaa_matches, flash_list):
    """iddaa match_id -> flash maci. Saat +-30 dk ve takim adi benzerligi >= LINK_SIM; her flash maci bir kez."""
    buckets = {}
    for f in flash_list:
        buckets.setdefault(f["ts"] // 900, []).append(f)
    used, out = set(), {}
    scored = []
    for m in iddaa_matches:
        try:
            t = int(datetime.fromisoformat(m["kickoff_time"]).timestamp())
        except (KeyError, ValueError):
            continue
        for b in (t // 900 - 2, t // 900 - 1, t // 900, t // 900 + 1, t // 900 + 2):
            for f in buckets.get(b, []):
                if abs(f["ts"] - t) <= LINK_WINDOW:
                    sc = min(_similarity(m["home"], f["home"]), _similarity(m["away"], f["away"]))
                    if sc >= LINK_SIM:
                        scored.append((sc, m["match_id"], f["id"], f))
    for sc, mid, fid, f in sorted(scored, key=lambda x: -x[0]):
        if mid in out or fid in used:
            continue
        out[mid] = dict(f, sim=round(sc, 2))
        used.add(fid)
    return out


# ------------------------------------------------------------------ istatistik
def _num(v):
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", v or "")
    return float(m.group(1)) if m else None


def match_stats(session, mid, cache):
    """Bitmis bir macin tam-mac istatistigi: {ad: (ev, dep)}; onbellekli."""
    with _lock:
        if mid in cache:
            return cache[mid]
    st = {}
    for r in parse(feed(session, f"df_st_1_{mid}")):
        if "SE" in r and r["SE"] != "Match":
            break
        if "SG" in r and "SH" in r and r["SG"] not in st:
            st[r["SG"]] = (_num(r["SH"]), _num(r["SI"]))
    with _lock:
        cache[mid] = st
    return st


STAT_KEYS = {"xg": "Expected goals (xG)", "sh": "Total shots", "sot": "Shots on target", "co": "Corner kicks",
             "yc": "Yellow cards", "rc": "Red cards"}


def history(session, fid):
    """df_hh: son maclar (ev, deplasman) ve H2H satirlari."""
    secs, cur = {}, None
    for d in parse(feed(session, f"df_hh_1_{fid}")):
        if "KB" in d:
            cur = d["KB"]
            secs[cur] = []
        elif "KP" in d and cur:
            secs[cur].append(d)
    return secs


def _row(d):
    sc = re.match(r"(\d+):(\d+)", d.get("KL", ""))
    if not sc:
        return None
    return {"id": d["KP"], "ts": int(d.get("KC", 0)), "league": d.get("KF", ""), "side": d.get("KS"),
            "hg": int(sc.group(1)), "ag": int(sc.group(2)), "home": d.get("KJ", "").lstrip("*"), "away": d.get("KK", "").lstrip("*")}


def team_agg(session, rows, cache, n=LAST_N):
    """Bir takimin son n bitmis macindan ortalamalar (kendi lehine / aleyhine)."""
    games = [g for g in (_row(r) for r in rows) if g and g["side"] in ("home", "away")][:n]
    tot = {"n": len(games), "n_xg": 0, "gf": 0.0, "ga": 0.0, "xgf": 0.0, "xga": 0.0, "shf": 0.0, "sha": 0.0,
           "sotf": 0.0, "sota": 0.0, "cf": 0.0, "ca": 0.0, "kf": 0.0, "ka": 0.0, "n_c": 0, "n_k": 0}
    if not games:
        return None
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        stats = list(pool.map(lambda g: match_stats(session, g["id"], cache), games))
    for g, st in zip(games, stats):
        mine, opp = (0, 1) if g["side"] == "home" else (1, 0)
        gf, ga = (g["hg"], g["ag"]) if g["side"] == "home" else (g["ag"], g["hg"])
        tot["gf"] += gf; tot["ga"] += ga
        x = st.get(STAT_KEYS["xg"])
        if x and x[0] is not None and x[1] is not None:
            tot["n_xg"] += 1; tot["xgf"] += x[mine]; tot["xga"] += x[opp]
            s_, t_ = st.get(STAT_KEYS["sh"]), st.get(STAT_KEYS["sot"])
            if s_ and s_[0] is not None:
                tot["shf"] += s_[mine]; tot["sha"] += s_[opp]
            if t_ and t_[0] is not None:
                tot["sotf"] += t_[mine]; tot["sota"] += t_[opp]
        c = st.get(STAT_KEYS["co"])
        if c and c[0] is not None and c[1] is not None:
            tot["n_c"] += 1; tot["cf"] += c[mine]; tot["ca"] += c[opp]
        y = st.get(STAT_KEYS["yc"])
        r = st.get(STAT_KEYS["rc"], (0.0, 0.0))
        if y and y[0] is not None and y[1] is not None:
            tot["n_k"] += 1
            tot["kf"] += y[mine] + (r[mine] or 0); tot["ka"] += y[opp] + (r[opp] or 0)
    out = {"n": tot["n"], "n_xg": tot["n_xg"], "n_c": tot["n_c"], "n_k": tot["n_k"],
           "gf": tot["gf"] / tot["n"], "ga": tot["ga"] / tot["n"]}
    if tot["n_xg"]:
        for k in ("xgf", "xga", "shf", "sha", "sotf", "sota"):
            out[k] = tot[k] / tot["n_xg"]
    if tot["n_c"]:
        out["cf"], out["ca"] = tot["cf"] / tot["n_c"], tot["ca"] / tot["n_c"]
    if tot["n_k"]:
        out["kf"], out["ka"] = tot["kf"] / tot["n_k"], tot["ka"] / tot["n_k"]
    return out


def match_info(session, fid):
    """df_sur: hakem, stadyum, kapasite."""
    info, cur = {}, None
    for part in feed(session, f"df_sur_1_{fid}").replace("~", "¬").split("¬"):
        if "÷" not in part:
            continue
        k, v = part.split("÷", 1)
        if k == "MIT":
            cur = v
        elif k == "MIV" and cur in ("REF", "VEN", "CAP", "TWN"):
            info[{"REF": "referee", "VEN": "venue", "CAP": "capacity", "TWN": "city"}[cur]] = v
    return info


def h2h_summary(rows, home_name):
    games = [g for g in (_row(r) for r in rows) if g][:10]
    if not games:
        return None
    hw = aw = d = goals = 0
    for g in games:
        goals += g["hg"] + g["ag"]
        if g["hg"] == g["ag"]:
            d += 1
        elif (g["hg"] > g["ag"]) == (_similarity(g["home"], home_name) >= 0.72):
            hw += 1
        else:
            aw += 1
    return {"played": len(games), "home_wins": hw, "away_wins": aw, "draws": d, "avg_total_goals": round(goals / len(games), 2)}


def for_match(session, fmatch, cache):
    """Bir Flashscore maci icin tum ek veri: {home, away, h2h, referee, venue, capacity}."""
    secs = history(session, fmatch["id"])
    last = [(k, v) for k, v in secs.items() if k.startswith("Last matches")]
    h_rows = a_rows = []
    for k, rows in last:
        name = k.split(":", 1)[1].strip()
        if _similarity(name, fmatch["home"]) >= _similarity(name, fmatch["away"]):
            if not h_rows:
                h_rows = rows
        elif not a_rows:
            a_rows = rows
    x_rows = next((v for k, v in secs.items() if k.startswith("Head-to-head")), [])
    out = {"flash_id": fmatch["id"], "sim": fmatch.get("sim"), "home": team_agg(session, h_rows, cache),
           "away": team_agg(session, a_rows, cache), "h2h": h2h_summary(x_rows, fmatch["home"])}
    out.update(match_info(session, fmatch["id"]))
    return out


def load_cache():
    p = os.path.join(DATA_DIR, "flash_stat_cache.json")
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return {k: {n: tuple(v) for n, v in st.items()} for k, st in json.load(f).items()}
        except Exception:
            return {}
    return {}


def save_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "flash_stat_cache.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
