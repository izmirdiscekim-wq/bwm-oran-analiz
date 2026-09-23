"""
Tek komutluk gunluk tarama (hizli + token-tasarruflu): bulteni cek -> oran istatistigi -> Iddaa istatistigi (+model)
-> piyasa tutarliligi -> BWM ozet. Ara adimlar sessiz; ekrana yalnizca kisa durum + nihai tablo basilir.

Kullanim:
    python run_daily.py [YYYY-AA-GG] [--n 150] [--api] [--dc]
      --n N   Iddaa istatistigi cekilecek en fazla mac (varsayilan 150)
      --api   API-Football H2H+hava adimini da calistir (YAVAS: dakikada 10 istek; API_FOOTBALL_KEY gerekir)
      --dc    Dixon-Coles adimini zorla (varsayilan: yalnizca data/history/*.csv varsa)
"""
import os
import re
import io
import sys
import json
import glob
import time
import contextlib
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
TR_TZ = timezone(timedelta(hours=3))


def quiet(fn, *args, **kw):
    buf, t0 = io.StringIO(), time.time()
    try:
        with contextlib.redirect_stdout(buf):
            result = fn(*args, **kw)
        return result, buf.getvalue(), None, time.time() - t0
    except Exception as e:
        return None, buf.getvalue(), e, time.time() - t0


def count_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return len(json.load(f))
    except Exception:
        return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    use_api, force_dc, n_stat, date_arg = "--api" in argv, "--dc" in argv, 150, None
    for i, a in enumerate(argv):
        if a == "--n" and i + 1 < len(argv):
            n_stat = int(argv[i + 1])
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", a):
            date_arg = a
    target = datetime.strptime(date_arg, "%Y-%m-%d").date() if date_arg else datetime.now(TR_TZ).date()
    stamp = target.strftime("%Y%m%d")
    base = os.path.join(DATA, f"football_matches_{stamp}")
    T0 = time.time()
    print(f"BWM günlük tarama — {target}")

    import fetch_iddaa
    n, _, err, dt = quiet(fetch_iddaa.fetch_all_matches, target)
    if err or not n:
        print(f"1) Maç çekme BAŞARISIZ: {err or 'maç yok'}")
        return
    print(f"1) Bülten: {n} maç ({dt:.0f}s)")

    import enrich_stats
    _, _, err, dt = quiet(enrich_stats.enrich_stats)
    print(f"2) Oran istatistiği: {count_json(base + '_enriched.json')} maç ({dt:.0f}s)" + (f" HATA {err}" if err else ""))

    import fetch_iddaa_stats
    _, _, err, dt = quiet(fetch_iddaa_stats.run, n_stat, True)
    print(f"3) İddaa istatistiği (form, puan durumu, H2H, model): {count_json(base + '_istat.json')} maç ({dt:.0f}s)" + (f" HATA {err}" if err else ""))

    has_history = bool(glob.glob(os.path.join(DATA, "history", "*.csv")))
    if force_dc or has_history:
        import dixon_coles_model
        _, _, err, dt = quiet(dixon_coles_model.run)
        print(f"3b) Dixon-Coles: {count_json(base + '_model.json')} maç ({dt:.0f}s)" + (f" HATA {err}" if err else ""))

    if use_api and os.environ.get("API_FOOTBALL_KEY", "").strip():
        import fetch_realstats
        _, out, err, dt = quiet(fetch_realstats.enrich_with_real_stats)
        print(f"3c) API-Football H2H+hava: {count_json(base + '_realstats.json')} maç ({dt:.0f}s)" + (f" HATA {err}" if err else ""))

    import market_coherence
    _, _, err, dt = quiet(market_coherence.run)
    print(f"4) Piyasa tutarlılığı: {count_json(base + '_coherence.json')} maç ({dt:.0f}s)" + (f" HATA {err}" if err else ""))

    import analyze_bwm
    _, _, err, dt = quiet(analyze_bwm.analyze_bwm)
    if err:
        print(f"5) BWM analizi HATA: {err}")
        return
    report_path = os.path.join(DATA, f"bwm_report_{stamp}.md")
    with open(report_path, "r", encoding="utf-8") as f:
        report = f.read()
    m = re.search(r"Toplam \*\*(\d+)\*\*", report)
    print(f"5) BWM: {m.group(1) if m else 0} maç onaylandı ({dt:.0f}s) | toplam süre {time.time() - T0:.0f}s\n")
    idx = report.find("## Nihai Tahmin ve Puanlama")
    print(report[idx:] if idx >= 0 else "Onaylı maç yok -> bugün SKIP (BWM disiplini).")


if __name__ == "__main__":
    main()
