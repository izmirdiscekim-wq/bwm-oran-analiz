"""
KATMAN 1 - MAC BULMA. Yalnizca "hangi maclar?" sorusunu yanitlar; analiz/istatistik yapmaz.

  python bwm.py bul [--takim "Barcelona,Ajax"] [--lig "Serie A"] [--saat 20:30-00:00] [--tarih 2026-09-20] [--n 40] [--canli]

Cikti: her mac tek satir "id saat Ev - Dep (Lig)". Filtre yoksa yalnizca baslamamis maclar; --takim/--lig ile
baslamis (canli olmayan) maclar da gelir; --canli canli maclari da dahil eder. Diger katmanlar ayni fonksiyonu kullanir.
"""
import sys
from datetime import datetime, timedelta, timezone

import fetch_iddaa as F

sys.stdout.reconfigure(encoding="utf-8")
TR = timezone(timedelta(hours=3))
MIN_MARKETS = 20      # pazari cok az olan (bulten disi/yari hazir) maclari ele


def tr_lower(s):
    return (s or "").replace("İ", "i").replace("I", "ı").lower()


def find(session=None, comps=None, takim=None, lig=None, saat=None, day=None, n=50, include_live=False, only_live=False):
    """[{id, ts, time, home, away, league}] kalkis saatine gore sirali."""
    session = session or F._get_session()
    comps = comps if comps is not None else F._fetch_competitions(session)
    raw = F._get_json(session, F.EVENTS_URL)
    live = set((raw.get("data", {}).get("sc") or {}).keys())
    day = day or datetime.now(TR).date()
    now_ts = datetime.now(TR).timestamp()
    ev = [e for e in raw["data"]["events"]
          if e.get("d") and (include_live or str(e.get("i")) not in live)
          and datetime.fromtimestamp(e["d"], TR).date() == day
          and len(e.get("m", [])) >= (1 if (include_live and str(e.get("i")) in live) else MIN_MARKETS)]   # canlida pazar sayisi az
    if takim or lig:
        tks = [tr_lower(t.strip()) for t in (takim or "").split(",") if t.strip()]
        lg = tr_lower(lig)
        ev = [e for e in ev if (not tks or any(t in tr_lower(e.get("hn")) or t in tr_lower(e.get("an")) for t in tks))
              and (not lg or lg in tr_lower(comps.get(e.get("ci"), "")))]
    else:
        ev = [e for e in ev if e["d"] > now_ts or (include_live and str(e.get("i")) in live)]
    if only_live:
        ev = [e for e in ev if str(e.get("i")) in live]
    if saat:   # 'SS:DD-SS:DD'; bitis <= baslangic ise ertesi gun (00:00 = gece yarisi)
        a, b = saat.split("-")
        day0 = datetime.combine(day, datetime.min.time(), TR)
        t0 = day0 + timedelta(hours=int(a[:2]), minutes=int(a[3:5]))
        t1 = day0 + timedelta(hours=int(b[:2]), minutes=int(b[3:5]))
        if t1 <= t0:
            t1 += timedelta(days=1)
        ev = [e for e in ev if t0.timestamp() <= e["d"] < t1.timestamp()]
    ev.sort(key=lambda e: e["d"])
    return [{"id": str(e["i"]), "ts": e["d"], "time": datetime.fromtimestamp(e["d"], TR).strftime("%H:%M"),
             "home": e.get("hn") or "?", "away": e.get("an") or "?", "league": comps.get(e.get("ci"), ""), "nm": len(e.get("m", [])),
             "live": str(e.get("i")) in live} for e in ev[:n]]


def main(argv):
    opt, i = {"takim": None, "lig": None, "saat": None, "tarih": None, "n": 40, "canli": False, "sadece": False}, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--takim", "--lig", "--saat", "--tarih", "--n") and i + 1 < len(argv):
            opt[a[2:]] = argv[i + 1]; i += 2; continue
        if a == "--canli":
            opt["canli"] = True
        elif a == "--sadece-canli":
            opt["canli"] = opt["sadece"] = True
        elif a.isdigit():
            opt["n"] = a
        i += 1
    day = datetime.strptime(opt["tarih"], "%Y-%m-%d").date() if opt["tarih"] else None
    res = find(None, None, opt["takim"], opt["lig"], opt["saat"], day, int(opt["n"]), opt["canli"], opt["sadece"])
    if not res:
        print("Eşleşen maç yok (gün/filtre/canlı durumunu kontrol et).")
        return
    print(f"{len(res)} maç:")
    for m in res:
        print(f"{m['id']} {m['time']}{' CANLI' if m['live'] else ''} {m['home']} - {m['away']} ({m['league'][:28]})")
