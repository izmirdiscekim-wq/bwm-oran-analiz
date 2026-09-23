"""
Mac basina TAM pazar listesi (detay uc noktasi) - ornek/kisa liste cekimi.

Liste uc noktasi mac basina en fazla 29 pazar verir; /sportsbook/event/{id} 84-105 pazar verir
(Mac Skoru, 1. Yari Skoru, Ilk Golu Atan, tum Alt/Ust ve handikap cizgileri, korner/kart...).

Kullanim:
    python fetch_details.py [N]            # bugunun ilk N (varsayilan 50) baslamamis macini, en cok pazarli once
Cikti: data/football_detail_YYYYMMDD.json (football_matches_*.json desenine UYMAZ; enrich_stats bunu almaz)
Ekrana yalnizca kisa ozet basilir (token tasarrufu).
"""
import os
import sys
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import fetch_iddaa as F

sys.stdout.reconfigure(encoding="utf-8")
DETAIL_URL = "https://sportsbookv2.iddaa.com/sportsbook/event/{}"


def count_markets(markets):
    n = sum(1 for k in ("match_outcome", "double_chance", "both_teams_score") if markets.get(k))
    return n + sum(len(markets.get(b, [])) for b in F.LIST_BUCKETS)


def count_odds(markets):
    n = sum(len(markets.get(k, {})) for k in ("match_outcome", "double_chance", "both_teams_score"))
    return n + sum(len(x["outcomes"]) for b in F.LIST_BUCKETS for x in markets.get(b, []))


def fetch_details(target_date, n=50):
    session = F._get_session()
    cfg = F._fetch_market_config(session)
    comps = F._fetch_competitions(session)
    raw = F._get_json(session, F.EVENTS_URL)
    live_ids = set((raw.get("data", {}).get("sc") or {}).keys())
    now_ts = datetime.now(F.TR_TZ).timestamp()

    todays = [e for e in raw["data"]["events"]
              if e.get("d") and e["d"] > now_ts and str(e.get("i")) not in live_ids
              and datetime.fromtimestamp(e["d"], F.TR_TZ).date() == target_date]
    todays.sort(key=lambda e: -len(e.get("m", [])))
    picked = todays[:n]

    def one(ev):
        try:
            d = F._get_json(session, DETAIL_URL.format(ev["i"]))["data"]
            return F._parse_event(d, cfg, comps, False)
        except Exception as e:
            return {"error": f"{type(e).__name__}", "match_id": str(ev["i"]), "home": ev.get("hn"), "away": ev.get("an")}

    with ThreadPoolExecutor(max_workers=6) as pool:
        parsed = list(pool.map(one, picked))
    ok = [p for p in parsed if p and "error" not in p]

    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(data_dir, exist_ok=True)
    out = os.path.join(data_dir, f"football_detail_{target_date.strftime('%Y%m%d')}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(ok, f, ensure_ascii=False, indent=1)
    return ok, len(parsed) - len(ok), len(todays), out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    target = datetime.now(F.TR_TZ).date()
    ok, failed, pool_size, out = fetch_details(target, n)
    if not ok:
        print("Detay çekilemedi.")
        return
    mk = [count_markets(m["markets"]) for m in ok]
    od = [count_odds(m["markets"]) for m in ok]
    print(f"{len(ok)} maç çekildi (hata: {failed}); bugün başlamamış {pool_size} maç arasından en çok pazarlı {n} seçildi.")
    print(f"Maç başına pazar: min {min(mk)} / ort. {sum(mk) / len(mk):.0f} / max {max(mk)} | toplam {sum(mk)} pazar, {sum(od)} oran")
    print(f"Kaydedildi: {out}\n")

    print("| Saat | Lig | Maç | Pazar | Oran | En olası 3 skor (Maç Skoru) |\n| :--- | :--- | :--- | ---: | ---: | :--- |")
    for m, k, o in list(zip(ok, mk, od))[:6]:
        score = next((x for x in m["markets"]["other_markets"] if x["market_name"].startswith("Maç Skoru")), None)
        top = ", ".join(f"{s} @{v}" for s, v in sorted(score["outcomes"].items(), key=lambda t: t[1])[:3]) if score else "-"
        print(f"| {m['kickoff_time'][11:16]} | {m['league'][:22]} | {m['home']} - {m['away']} | {k} | {o} | {top} |")


if __name__ == "__main__":
    main()
