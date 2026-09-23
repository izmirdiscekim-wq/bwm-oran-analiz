"""
KATMAN 6 - GUN TARAMASI (tum sporlar): istenen gunun tum iddaa maclarini ayni mantikla analiz eder, en mantikliyi basa alip ilk N'i siralar.

  python bwm.py gun [YYYY-MM-DD] [--n 50] [--futbol-n 150] [--spor futbol,basketbol,...] [--goster]

Ayni mantik (her sporda): iddaa'nin istatistik servisinden son <=10 mac skorlari -> sporun skor modeli -> pazarlarin model olasiligi;
iddaa oranlari Shin ile arindirilip piyasa olasiligi; harman p = %70 piyasa + %30 model; EV = harman p x oran - 1.
Ayni durustluk suzgecleri: piyasa olasiligi >=%10 ve tam kume, model-piyasa gorece sapma <=%30, ornek >=4 mac; model toplami piyasadan cok
ayrisirsa (basketbol/hentbol %5, hokey %15) guven <=4. Guven tavani: futbol 6 (xG, cok kaynak); basketbol/hokey/hentbol 5 (yalniz skor modeli); tenis 3 (yalniz form).
Modeller: futbol = Dixon-Coles (match_report.py, ayrintili); basketbol/hentbol = Normal (fark ve toplam); buz hokeyi = Poisson (uzatma 50/50 varsayimi);
tenis = son mac galibiyet oranindan lojistik + toplam oyun Normal (zayif). SD/ev sahibi avantaji onselleri VARSAYIMDIR.
SIRALAMA (en mantiklidan asagi): once guven (analiz guvenilirligi), sonra harman EV. Kayit: data/gun_YYYYMMDD.json (--goster ile agsiz okunur).
Cikti tavani: futbol icin once pazar sayisi en yuksek (en iyi analiz edilebilen) --futbol-n mac derin analiz edilir; kalanlar ozet dipnotta sayilir.
"""
import io
import os
import re
import sys
import json
import math
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from scipy.stats import norm, poisson
from scipy.optimize import brentq

import fetch_iddaa as F
import find_matches as FM
from dixon_coles_model import shin_probs

sys.stdout.reconfigure(encoding="utf-8")

TR = timezone(timedelta(hours=3))
EVENTS_T = "https://sportsbookv2.iddaa.com/sportsbook/events?st={}&type=0&version=0&live=true"
DETAIL_URL = "https://sportsbookv2.iddaa.com/sportsbook/event/{}"
STATS_T = "https://statisticsv2.iddaa.com/statistics/{}/{}"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
W_MKT, MIN_PROB, MAX_REL_DIV, MIN_N, K_SHRINK, MIN_EV = 0.70, 0.10, 0.30, 4, 4.0, 0.05

# st kodu -> ozellikler (iddaa spor kodlari; yeni spor eklemek icin buraya satir)
SPORTS = {
    1: {"ad": "Futbol", "model": "football"},
    2: {"ad": "Basketbol", "path": "basketball", "model": "normal", "ha": 2.5, "sd_m": 13.0, "sd_t": 18.0, "cap": 5, "tot": 0.05, "draw": False},
    4: {"ad": "Buz Hokeyi", "path": "icehockey", "model": "poisson", "ha": 0.04, "cap": 5, "tot": 0.15},
    5: {"ad": "Tenis", "path": "tennis", "model": "tennis", "cap": 3, "tot": 0.15},
    6: {"ad": "Hentbol", "path": "handball", "model": "normal", "ha": 2.0, "sd_m": 7.0, "sd_t": 6.5, "cap": 5, "tot": 0.05, "draw": True},
}
NAMES = {v["ad"].lower(): k for k, v in SPORTS.items()}
NAMES.update({"buz": 4, "hokey": 4, "futbol": 1, "basket": 2, "hent": 6})


def _n(s):
    return re.sub(r"\s+", " ", (s or "").replace("İ", "i").replace("I", "ı").lower()).strip()


# ------------------------------------------------------------------ 1. veri: maclar, pazarlar, istatistik
def day_events(session, st, day, comps):
    raw = F._get_json(session, EVENTS_T.format(st))
    live = set((raw.get("data", {}).get("sc") or {}).keys())
    now_ts = datetime.now(TR).timestamp()
    out = []
    for e in raw.get("data", {}).get("events", []) or []:
        if not e.get("d") or str(e.get("i")) in live or e["d"] <= now_ts:
            continue
        if datetime.fromtimestamp(e["d"], TR).date() != day:
            continue
        out.append({"id": str(e["i"]), "ts": e["d"], "home": e.get("hn") or "?", "away": e.get("an") or "?", "league": comps.get(e.get("ci"), ""),
                    "nm": len(e.get("m", []))})
    out.sort(key=lambda x: x["ts"])
    return out


def read_markets(d, cfg):
    out = []
    for m in d.get("m", []):
        name = F._resolve_market_name(m.get("t"), m.get("st"), m.get("sov"), cfg)
        outs = {}
        for o in m.get("o", []):
            try:
                v = float(o.get("odd", 0))
            except (TypeError, ValueError):
                v = 0.0
            if o.get("n") not in (None, "") and v > 0:
                outs[str(o["n"])] = v
        if outs:
            out.append((name, m.get("sov"), outs))
    return out


def classify(name, sov):
    """(tur, taraf, cizgi, uzatma_dahil) ya da None. tur: win | total | team."""
    n = _n(name)
    base = re.sub(r"[\s]*-?[\d.]+$", "", n)
    ot = bool(re.search(r"\(uz", base))
    if re.fullmatch(r"maç sonucu( \(uz[^)]*\))?", base):
        return ("win", None, None, ot)
    try:
        line = float(str(sov).replace(",", ".")) if sov not in (None, "") else float(re.search(r"-?[\d.]+$", n).group(0))
    except (AttributeError, ValueError):
        return None
    if re.fullmatch(r"(toplam sayı |toplam oyun )?(altı/üstü|alt/üst)( \(uz[^)]*\))?", base):
        return ("total", None, line, ot)
    m = re.fullmatch(r"(ev sahibi|deplasman) (altı/üstü|alt/üst)( \(uz[^)]*\))?", base)
    if m:
        return ("team", "h" if m.group(1) == "ev sahibi" else "a", line, ot)
    return None


def stat(session, path, eid, typ):
    try:
        r = session.get(STATS_T.format(path, f"recent-matches/{eid}?matchHistoryType={typ}&isOnlyCurrentTournament=false"), timeout=15)
        return (r.json().get("data") or {}) if r.status_code == 200 else {}
    except Exception:
        return {}


def games(rm, team):
    out = []
    for x in (rm or {}).get("m", []):
        h, a = x.get("h", {}), x.get("a", {})
        if h.get("rs") is None or a.get("rs") is None:
            continue
        if h.get("n") == team:
            out.append((h["rs"], a["rs"], h, a))
        elif a.get("n") == team:
            out.append((a["rs"], h["rs"], a, h))
    return out[:10]


# ------------------------------------------------------------------ 2. skor modelleri
def _shrunk(vals, pool):
    n = len(vals)
    return (sum(vals) + K_SHRINK * pool) / (n + K_SHRINK) if n else pool


def build_model(sp, gh, ga):
    """Model sozlugu ya da None (ornek < MIN_N)."""
    if min(len(gh), len(ga)) < MIN_N:
        return None
    n_min = min(len(gh), len(ga))
    if sp["model"] == "tennis":
        def wr(g):
            return (sum(1 for x in g if x[0] > x[1]) + 1.0) / (len(g) + 2.0)
        lg = lambda p: math.log(p / (1 - p))
        ph = 1 / (1 + math.exp(-(lg(wr(gh)) - lg(wr(ga)))))
        tot = [sum(s["s"] for s in (x[2].get("ss") or []) + (x[3].get("ss") or [])) for x in gh + ga]
        tot = [t for t in tot if t > 0]
        return {"kind": "tennis", "ph": ph, "mean_t": (sum(tot) / len(tot)) if len(tot) >= 4 else None, "sd_t": 4.5, "n": n_min}
    pf_h, pa_h = [x[0] for x in gh], [x[1] for x in gh]
    pf_a, pa_a = [x[0] for x in ga], [x[1] for x in ga]
    pool = (sum(pf_h + pa_h + pf_a + pa_a)) / (len(pf_h) * 2 + len(pf_a) * 2)
    eh = (_shrunk(pf_h, pool) + _shrunk(pa_a, pool)) / 2
    ea = (_shrunk(pf_a, pool) + _shrunk(pa_h, pool)) / 2
    if sp["model"] == "poisson":
        return {"kind": "poisson", "lam": max(eh * (1 + sp["ha"]), 0.2), "mu": max(ea * (1 - sp["ha"]), 0.2), "n": n_min}
    tots = [x[0] + x[1] for x in gh + ga]
    sd_t = sp["sd_t"]
    if len(tots) >= 10:
        s = float(np.std(tots, ddof=1))
        sd_t = math.sqrt(0.5 * s * s + 0.5 * sd_t * sd_t)
    return {"kind": "normal", "mh": eh + sp["ha"] / 2, "ma": ea - sp["ha"] / 2, "sd_m": sp["sd_m"], "sd_t": sd_t,
            "sd_h": math.sqrt((sd_t ** 2 + sp["sd_m"] ** 2) / 4), "draw": sp["draw"], "n": n_min}


def _pgrid(lam, mu, n=14):
    return np.outer(poisson.pmf(np.arange(n), lam), poisson.pmf(np.arange(n), mu))


def model_prob(mod, kind, side, line, ot, outcome):
    """Modelin bir sonuca verdigi olasilik; hesaplanamiyorsa None."""
    k = mod["kind"]
    if kind == "win":
        o = "X" if outcome in ("0", "X") else outcome
        if k == "normal":
            mu, sd = mod["mh"] - mod["ma"], mod["sd_m"]
            if mod["draw"] and not ot:
                p1, p2 = 1 - norm.cdf((0.5 - mu) / sd), norm.cdf((-0.5 - mu) / sd)
                return {"1": p1, "X": 1 - p1 - p2, "2": p2}.get(o)
            p1 = 1 - norm.cdf((0 - mu) / sd)
            return {"1": p1, "2": 1 - p1}.get(o)
        if k == "poisson":
            g = _pgrid(mod["lam"], mod["mu"]); I, J = np.indices(g.shape)
            p1, px, p2 = g[I > J].sum(), g[I == J].sum(), g[I < J].sum()
            return ({"1": p1 + 0.5 * px, "2": p2 + 0.5 * px} if ot else {"1": p1, "X": px, "2": p2}).get(o)
        if k == "tennis":
            return {"1": mod["ph"], "2": 1 - mod["ph"]}.get(o)
        return None
    if outcome not in ("Alt", "Üst"):
        return None
    over = None
    if kind == "total":
        if k == "normal":
            over = 1 - norm.cdf((line - (mod["mh"] + mod["ma"])) / mod["sd_t"])
        elif k == "poisson":
            g = _pgrid(mod["lam"], mod["mu"]); I, J = np.indices(g.shape); over = g[I + J > line].sum()
        elif k == "tennis" and mod.get("mean_t"):
            over = 1 - norm.cdf((line - mod["mean_t"]) / mod["sd_t"])
    elif kind == "team":
        if k == "normal":
            over = 1 - norm.cdf((line - (mod["mh"] if side == "h" else mod["ma"])) / mod["sd_h"])
        elif k == "poisson":
            over = 1 - poisson.cdf(math.floor(line), mod["lam"] if side == "h" else mod["mu"])
    if over is None:
        return None
    return over if outcome == "Üst" else 1 - over


def model_total(mod):
    return {"normal": lambda m: m["mh"] + m["ma"], "poisson": lambda m: m["lam"] + m["mu"], "tennis": lambda m: m.get("mean_t")}[mod["kind"]](mod)


def implied_total(mod, line, p_over):
    """Piyasanin ima ettigi toplam ortalama (Normal: cizgi + sd x z; Poisson: p_over'a uyan ortalama)."""
    p_over = min(max(p_over, 0.02), 0.98)
    if mod["kind"] in ("normal", "tennis"):
        return line + mod["sd_t"] * norm.ppf(p_over)
    try:
        return brentq(lambda m: (1 - poisson.cdf(math.floor(line), m)) - p_over, 0.1, 30)
    except ValueError:
        return None


# ------------------------------------------------------------------ 3. fiyatlama + guven
def analyze_other(session, cfg, sp, m):
    """Football disi tek mac -> satir sozlugu."""
    d = F._get_json(session, DETAIL_URL.format(m["id"]))["data"]
    mk = read_markets(d, cfg)
    home, away = m["home"], m["away"]
    gh = games(stat(session, sp["path"], m["id"], 0), home)
    ga = games(stat(session, sp["path"], m["id"], 1), away)
    mod = build_model(sp, gh, ga)
    row = {"spor": sp["ad"], "mac": f"{home} - {away}", "ts": m["ts"], "lig": m["league"], "pick": None, "odds": None, "p": None, "ev": None,
           "conf": 1, "not": "", "n": (len(gh), len(ga))}
    if not mod:
        row["not"] = f"model yok (son maç {len(gh)}/{len(ga)} < {MIN_N})"
        return row
    rows, tot_obs = [], []
    for name, sov, outs in mk:
        c = classify(name, sov)
        if not c or len(outs) < 2 or any(v <= 1.01 for v in outs.values()):
            continue
        kind, side, line, ot = c
        if not (1.0 <= sum(1 / v for v in outs.values()) <= 1.35):
            continue
        sp_mkt = shin_probs(outs)
        for o, odds in outs.items():
            pm, pp = sp_mkt.get(o), model_prob(mod, kind, side, line, ot, o)
            if pm is None or pp is None:
                continue
            rel = abs(pp - pm) / pm if pm > 0 else 9
            ok = pm >= MIN_PROB and rel <= MAX_REL_DIV
            pb = W_MKT * pm + (1 - W_MKT) * pp
            label = {"win": "Maç Sonucu" + (" (UD)" if ot else ""), "total": "Toplam" + (" (UD)" if ot else ""),
                     "team": ("Ev" if side == "h" else "Dep") + " Alt/Üst" + (" (UD)" if ot else "")}[kind]
            rows.append({"pazar": label + (f" {line:g}" if line is not None else ""), "sec": o, "oran": odds, "p_mkt": pm, "p_mod": pp, "p": pb,
                         "ev": pb * odds - 1, "ev_raw": pp * odds - 1, "ok": ok})
            if kind == "total" and o == "Üst":
                tot_obs.append((abs(pm - 0.5), line, pm))
    cands = [r for r in rows if r["ok"]]
    if not cands:
        row["not"] = "süzgeçlerden geçen pazar yok"
        row["conf"] = 1
        return row
    b = max(cands, key=lambda r: r["ev"])
    conf = 2 + (row["n"][0] >= 5 and row["n"][1] >= 5) + (min(row["n"]) >= 8) + (len(cands) >= 4) + (b["ev"] >= -0.05)
    notes = [f"son maç {row['n'][0]}/{row['n'][1]}"]
    if tot_obs:
        _, line, pov = min(tot_obs)
        imp, mt = implied_total(mod, line, pov), model_total(mod)
        if imp and mt:
            if abs(mt - imp) / imp > sp["tot"]:
                conf, notes = min(conf, 4), notes + [f"toplam çelişkisi (model {mt:.1f} / piyasa {imp:.1f})"]
            else:
                conf += 1
    conf = max(1, min(conf, sp["cap"]))
    if mod["kind"] == "tennis":
        notes.append("tenis: yalnızca form, zayıf")
    row.update({"pick": f"{b['pazar']} {b['sec']}", "odds": float(b["oran"]), "p": float(b["p"]), "ev": float(b["ev"]), "conf": int(conf),
                "not": "; ".join(notes), "deger": bool(b["ev_raw"] >= MIN_EV)})
    return row


# ------------------------------------------------------------------ 4. futbol koprusu
def football_rows(session, cfg, comps, day, limit):
    import match_report as MR
    MR.TARGET[0] = day
    MR.TM_ON[0] = False
    MR.flash_init()
    evs = FM.find(session, comps, None, None, None, day, 5000)
    evs.sort(key=lambda e: (-e["nm"], e["ts"]))          # pazar sayisi en yuksek = en iyi analiz edilebilen
    pick, rest = evs[:limit], evs[limit:]
    with ThreadPoolExecutor(max_workers=int(os.environ.get("BWM_WORKERS", "8"))) as pool:
        res = list(pool.map(lambda e: MR._safe(MR.analyze_one, session, cfg, comps, e["id"]), pick))
    if MR.FLASH["cache"] is not None:
        MR.FL.save_cache(MR.FLASH["cache"])
    parts, rows = [], []
    for e, (eid, r, err) in zip(pick, res):
        if err or not r:
            continue
        parts.append(r[0]); s = r[1]; lb = s.get("lb")
        rows.append({"spor": "Futbol", "mac": s["match"], "ts": s.get("ko") or e["ts"], "lig": s["league"], "pick": lb["pick"] if lb else None,
                     "odds": lb["odds"] if lb else None, "p": lb["p"] if lb else None, "ev": lb["ev"] if lb else None, "conf": s["conf"],
                     "not": {"xG çelişkisi": "xG çelişkisi", "YETERSİZ": "örneklem yetersiz", "flash xG": "Flashscore xG", "yeterli": ""}.get(s["quality"], ""),
                     "deger": s["value"] > 0})
    path, store = MR.report_path(), {}
    if os.path.exists(path):
        for b in io.open(path, encoding="utf-8").read().split("\n---\n\n"):
            if b.strip():
                store[b.split("\n", 1)[0].split("  (")[0]] = b
    for p in parts:
        store[p.split("\n", 1)[0].split("  (")[0]] = p
    io.open(path, "w", encoding="utf-8", newline="").write("\n---\n\n".join(store.values()))
    return rows, len(rest)


# ------------------------------------------------------------------ 5. siralama + cikti
def rank(rows):
    have = [r for r in rows if r["pick"]]
    return sorted(have, key=lambda r: (r["conf"], r["ev"]), reverse=True)


def show(payload, top):
    rows = payload["rows"][:top]
    print(f"{payload['date']} | {payload['n_total']} maç bulundu, {payload['n_priced']} tanesi fiyatlanabildi | sıralama: önce güven, sonra harman EV")
    print("| # | Spor | Maç (saat) | Seçenek | Oran | Harman p | EV | Güven | Not |\n| ---: | :--- | :--- | :--- | ---: | ---: | ---: | ---: | :--- |")
    for i, r in enumerate(rows, 1):
        t = datetime.fromtimestamp(r["ts"], TR).strftime("%H:%M")
        print(f"| {i} | {r['spor']} | {r['mac'][:34]} ({t}) | {r['pick'][:38]} | {r['odds']} | %{r['p'] * 100:.0f} | {r['ev'] * 100:+.0f}%{'*' if r.get('deger') else ''} | {r['conf']}/10 | {(r['not'] or '-')[:44]} |")
    print("\n".join(payload["footer"]))


def main(argv):
    opt, i, day = {"n": 50, "futbol-n": 150, "spor": None, "goster": False}, 0, datetime.now(TR).date()
    while i < len(argv):
        a = argv[i]
        if a in ("--n", "--futbol-n", "--spor") and i + 1 < len(argv):
            opt[a[2:]] = argv[i + 1]; i += 2; continue
        if a == "--goster":
            opt["goster"] = True
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", a):
            day = datetime.strptime(a, "%Y-%m-%d").date()
        i += 1
    path = os.path.join(DATA_DIR, f"gun_{day.strftime('%Y%m%d')}.json")
    if opt["goster"]:
        if not os.path.exists(path):
            print("Kayıtlı tarama yok; önce: bwm.py gun " + day.isoformat()); return
        show(json.load(io.open(path, encoding="utf-8")), int(opt["n"])); return
    t0 = datetime.now()
    s = F._get_session()
    cfg, comps = F._fetch_market_config(s), F._fetch_competitions(s)
    want = {NAMES[x.strip().lower()] for x in str(opt["spor"]).split(",") if x.strip().lower() in NAMES} if opt["spor"] else set(SPORTS)
    rows, counts, footer, n_total = [], {}, [], 0
    for st in sorted(SPORTS):
        if st not in want:
            continue
        sp = SPORTS[st]
        if st == 1:
            fb, rest = football_rows(s, cfg, comps, day, int(opt["futbol-n"]))
            rows += fb; counts["Futbol"] = (len(fb) + rest, len(fb))
            n_total += len(fb) + rest
            if rest:
                footer.append(f"Futbol: {rest} maç daha var (pazar sayısı düşük ligler) — derin analiz için: bwm.py gun {day} --futbol-n {int(opt['futbol-n']) + rest}")
            continue
        evs = day_events(s, st, day, comps)
        with ThreadPoolExecutor(max_workers=8) as pool:
            rr = list(pool.map(lambda m: analyze_other(s, cfg, sp, m), evs))
        rows += rr; counts[sp["ad"]] = (len(evs), sum(1 for r in rr if r["pick"])); n_total += len(evs)
    # modeli tanimsiz sporlar (yeni/bilinmeyen kodlar): yalnizca sayilir
    unk = []
    if not opt["spor"]:
        for st in (3, 7, 8, 9, 10):
            try:
                n = len(day_events(s, st, day, comps))
            except Exception:
                n = 0
            if n:
                unk.append(f"st={st}: {n}")
    if n_total == 0:
        print(f"{day}: başlamamış maç yok (geçmiş gün ya da bülten henüz açılmadı; iddaa genelde birkaç gün ilerisini listeler)."); return
    ranked = rank(rows)
    footer.append("Sporlara göre (bulunan/fiyatlanan): " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in counts.items())
                  + (f" | modeli tanımsız spor kodları: {', '.join(unk)}" if unk else ""))
    footer.append("* = ham EV ≥%5 (harman EV yine de çoğunlukla negatif: komisyon). Tavsiye değil; SKIP geçerli sonuçtur. Canlı/başlamış maçlar dışarıda.")
    payload = {"date": day.isoformat(), "n_total": n_total, "n_priced": len(ranked), "rows": ranked[:200], "footer": footer,
               "sure_sn": round((datetime.now() - t0).total_seconds())}
    os.makedirs(DATA_DIR, exist_ok=True)
    io.open(path, "w", encoding="utf-8").write(json.dumps(payload, ensure_ascii=False))
    print(f"({payload['sure_sn']} sn) ", end="")
    show(payload, int(opt["n"]))
