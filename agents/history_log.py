"""
Tahmin gunlugu + sonuc degerlendirme (kendi verini biriktirme).

Neden: 'su saatte/gunde baslayan maclar Ust/Alt oluyor' iddiasi dis kaynaklarda
kanitsiz (TV slotlari buyuk takimlara verildiginden takim kalitesiyle karisik).
Bu iddiayi ancak KENDI biriktirdigin veriyle, takim/lig etkisinden arindirarak
test edebilirsin. Bu modul her tahmini mac oncesi kaydeder; sonuclar gelince
model dogrulugunu (RPS) ve saat/gun kovalarini olcer. Kova basina n < MIN_BUCKET_N
ise rapor 'yetersiz orneklem' der - gurultuyu sinyal diye sunmaz.

Kullanim:
    python history_log.py settle     # data/history/*.csv'deki sonuclarla kayitlari kapat
    python history_log.py evaluate   # RPS + saat/gun kovalari
"""
import os
import sys
import json
import glob
import math
from datetime import datetime

import pandas as pd

MIN_BUCKET_N = 300


def _log_path(data_dir):
    return os.path.join(data_dir, "history_log.jsonl")


def _read_log(data_dir):
    path = _log_path(data_dir)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _write_log(data_dir, rows):
    with open(_log_path(data_dir), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def log_predictions(data_dir, matches, results, real_stats_by_id, model_by_id):
    """Sadece bagimsiz veriyle (model veya form/H2H) desteklenen maclari, mac basina bir kez kaydeder."""
    rows = _read_log(data_dir)
    seen = {r["match_id"] for r in rows}
    res_by_id = {r.get("match_id"): r for r in results}
    added = 0
    for m in matches:
        mid = m.get("match_id")
        if mid in seen or m.get("is_live"):
            continue
        model, real = model_by_id.get(mid), real_stats_by_id.get(mid)
        if not model and not real:
            continue
        try:
            ko = datetime.fromisoformat(m["kickoff_time"])
        except (KeyError, ValueError):
            continue
        r = res_by_id.get(mid, {})
        rows.append({
            "match_id": mid, "logged_at": datetime.now().isoformat(),
            "kickoff": m["kickoff_time"], "kickoff_hour_tr": ko.hour, "weekday": ko.weekday(),
            "date": ko.strftime("%Y-%m-%d"),
            "league": m.get("league"), "home": m.get("home"), "away": m.get("away"),
            "league_code": (model or {}).get("league_code"),
            "home_model_name": (model or {}).get("home_model_name"),
            "away_model_name": (model or {}).get("away_model_name"),
            "market_shin": (model or {}).get("market_shin"), "model": (model or {}).get("model"),
            "blend": (model or {}).get("blend"),
            "weather": (real or {}).get("weather"),
            "decision": r.get("decision"), "confidence": r.get("confidence"),
            "result": None,
        })
        added += 1
    _write_log(data_dir, rows)
    return added


def settle(data_dir):
    rows = _read_log(data_dir)
    frames = []
    for path in glob.glob(os.path.join(data_dir, "history", "*.csv")):
        code = os.path.basename(path).split("_")[0]
        try:
            df = pd.read_csv(path, encoding="latin-1")[["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
            df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df["code"] = code
            frames.append(df.dropna(subset=["Date"]))
        except Exception:
            continue
    if not frames:
        print("Sonuç kaynağı yok: data/history/*.csv bulunamadı.")
        return 0
    res = pd.concat(frames, ignore_index=True)
    res["day"] = res["Date"].dt.strftime("%Y-%m-%d")
    idx = {(r.code, r.day, r.HomeTeam, r.AwayTeam): (int(r.FTHG), int(r.FTAG)) for r in res.itertuples()}

    settled = 0
    for r in rows:
        if r.get("result") or not r.get("league_code"):
            continue
        hit = idx.get((r["league_code"], r["date"], r["home_model_name"], r["away_model_name"]))
        if hit:
            hg, ag = hit
            r["result"] = {"hg": hg, "ag": ag, "total": hg + ag, "over25": hg + ag >= 3,
                           "outcome": "1" if hg > ag else ("X" if hg == ag else "2")}
            settled += 1
    _write_log(data_dir, rows)
    print(f"{settled} kayıt sonuçlandırıldı.")
    return settled


def _rps(p1, px, p2, outcome):
    o = {"1": (1, 0, 0), "X": (0, 1, 0), "2": (0, 0, 1)}[outcome]
    return 0.5 * ((p1 - o[0]) ** 2 + ((p1 + px) - (o[0] + o[1])) ** 2)


def evaluate(data_dir):
    rows = [r for r in _read_log(data_dir) if r.get("result") and r.get("model") and r.get("market_shin")]
    print(f"Değerlendirilebilir kayıt: {len(rows)}")
    if not rows:
        print("Henüz sonuçlanmış kayıt yok. Günlük çalıştır, maçlar bitince 'settle' çalıştır.")
        return

    def mean_rps(key):
        vals = []
        for r in rows:
            p = r[key]
            if all(k in p for k in ("p1", "pX", "p2")):
                vals.append(_rps(p["p1"], p["pX"], p["p2"], r["result"]["outcome"]))
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    print("\nRPS (düşük = iyi):")
    for label, key in (("Piyasa (Shin)", "market_shin"), ("Dixon-Coles", "model"), ("Harman", "blend")):
        v, n = mean_rps(key)
        print(f"  {label:15s} {v:.4f}  (n={n})" if v is not None else f"  {label:15s} -")
    print("  Yorum: Harman < Piyasa değilse model bilgi eklemiyor demektir; MARKET_WEIGHT artırılmalı.")

    print(f"\nSaat/Gün kovaları (n < {MIN_BUCKET_N} ise 'yetersiz örneklem'; takım/lig etkisinden arındırılmış DEĞİL):")
    for name, keyf in (("TR saat", lambda r: r["kickoff_hour_tr"]), ("Haftanın günü", lambda r: r["weekday"])):
        buckets = {}
        for r in rows:
            buckets.setdefault(keyf(r), []).append(r["result"]["over25"])
        for k in sorted(buckets):
            n = len(buckets[k])
            if n < MIN_BUCKET_N:
                print(f"  {name} {k}: n={n} -> yetersiz örneklem")
            else:
                rate = sum(buckets[k]) / n
                se = math.sqrt(rate * (1 - rate) / n)
                print(f"  {name} {k}: Üst2.5 oranı %{rate * 100:.1f} ± {1.96 * se * 100:.1f} (n={n})")


if __name__ == "__main__":
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "evaluate"
    if cmd == "settle":
        settle(d)
    else:
        evaluate(d)
