"""
KATMAN 2 - VERI TOPLAMA (tek konu, kisa cikti). Analiz/oneri uretmez; yalnizca veriyi getirir.

  python bwm.py sakat "Ajax,Excelsior"        Transfermarkt sakat/cezali oyuncular
  python bwm.py hakem "van der Laan J."        Transfermarkt hakem kart/penalti ortalamasi (son 2 sezon)
  python bwm.py hakem --takim Ajax             o macin (Flashscore) hakemi + TM istatistigi
  python bwm.py istat Ajax                     Flashscore son 6 mac: xG, sut, korner, kart, H2H, hakem/stadyum
  python bwm.py oran Ajax                      Iddaa oranlari: 1X2, Cifte Sans, KG, Alt/Ust 2.5, marj, pazar sayisi
"""
import re
import sys
from datetime import datetime

import fetch_iddaa as F
import fetch_flash as FL
import fetch_tm as TM
import find_matches as FM
from dixon_coles_model import _similarity

sys.stdout.reconfigure(encoding="utf-8")
DETAIL_URL = "https://sportsbookv2.iddaa.com/sportsbook/event/{}"


def _flash_match(team, ses=None):
    """Bugunun Flashscore listesinde takim adini iceren/benzeyen mac (baslamamis oncelikli)."""
    ses = ses or FL.make_session()
    lst = FL.load_list(ses)
    t = FM.tr_lower(team)
    youth = re.compile(r"\bU\d{2}\b|\bwomen\b|\(w\)|\breserves?\b|\bjong\b|\bII\b", re.I)   # genclik/kadin/rezerv takimlari
    hits = [m for m in lst if (t in FM.tr_lower(m["home"]) or t in FM.tr_lower(m["away"]))
            and not (youth.search(m["home"]) or youth.search(m["away"]) or youth.search(m["league"] or ""))]
    if not hits:   # tam/alt dize yoksa yalnizca cok yuksek benzerlik (yanlis mac riskine karsi)
        hits = [m for m in lst if max(_similarity(team, m["home"]), _similarity(team, m["away"])) >= 0.9]
    hits.sort(key=lambda m: m["ts"])
    return (hits[0] if hits else None), ses


def sakat(argv):
    names = [t.strip() for a in argv for t in a.split(",") if t.strip()]
    if not names:
        print("Kullanım: bwm.py sakat \"Takım1,Takım2\""); return
    for nm in names:
        club = TM.find_club(nm)
        if not club:
            print(f"{nm}: Transfermarkt'ta güvenli eşleşme yok"); continue
        rows = TM.injuries(club)
        head = f"{nm} (TM: {club['name']})"
        if rows is None:
            print(f"{head}: liste alınamadı")
        elif not rows:
            print(f"{head}: eksik yok")
        else:
            print(f"{head}: {len(rows)} eksik")
            for r in rows[:8]:
                print(f"  - {r['name']} ({r['pos'] or '?'}) {r['reason'] or r['kind']}"
                      + (f" | dönüş {r['ret']}" if r["ret"] else "") + (f" | {r['missed']} maç kaçırdı" if r["missed"] else ""))
    TM.save()


def hakem(argv):
    if "--takim" in argv:
        team = argv[argv.index("--takim") + 1]
        fm, ses = _flash_match(team)
        if not fm:
            print(f"{team}: bugün Flashscore'da maç bulunamadı"); return
        name = FL.match_info(ses, fm["id"]).get("referee")
        print(f"Maç: {fm['home']} - {fm['away']} | Flashscore hakemi: {name or 'yok'}")
    else:
        name = " ".join(a for a in argv if not a.startswith("--")).strip()
    if not name:
        print("Kullanım: bwm.py hakem \"Soyad B.\" | --takim Ajax"); return
    ref = TM.find_referee(name)
    if not ref:
        print(f"{name}: TM'de tek/güvenli eşleşme yok (aynı soyad+baş harfle birden fazla aday olabilir)"); return
    st = TM.referee_stats(ref)
    TM.save()
    if not st:
        print(f"{name} (TM: {ref['name']}): son 2 sezonda <{TM.MIN_REF_GAMES} maç, ortalama güvenilmez"); return
    print(f"{name} (TM: {ref['name']}): son 2 sezon {st['n']} maç | sarı {st['yc']:.2f}/maç | kırmızı(+2.sarı) {st['rc']:.2f} | penaltı {st['pen']:.2f}")


def istat(argv):
    team = " ".join(argv).strip()
    if not team:
        print("Kullanım: bwm.py istat Takım"); return
    fm, ses = _flash_match(team)
    if not fm:
        print(f"{team}: bugün Flashscore'da maç bulunamadı"); return
    cache = FL.load_cache()
    d = FL.for_match(ses, fm, cache)
    FL.save_cache(cache)

    def fs(a):
        if not a:
            return "veri yok"
        t = f"{a['n']} maç, gol {a['gf']:.2f}/{a['ga']:.2f}"
        if a.get("n_xg"):
            t += f", xG {a['xgf']:.2f}/{a['xga']:.2f} ({a['n_xg']} maç), şut {a['shf']:.0f}/{a['sha']:.0f}, isabetli {a['sotf']:.1f}/{a['sota']:.1f}"
        else:
            t += ", xG yok"
        if a.get("n_c"):
            t += f", korner {a['cf']:.1f}/{a['ca']:.1f}"
        if a.get("n_k"):
            t += f", kart {a['kf']:.1f}/{a['ka']:.1f}"
        return t
    print(f"{fm['home']} - {fm['away']} ({fm['league']}) | Flashscore, lehine/aleyhine")
    print(f"  {fm['home']}: {fs(d['home'])}")
    print(f"  {fm['away']}: {fs(d['away'])}")
    h = d.get("h2h")
    print("  H2H: " + (f"{h['played']} maç {h['home_wins']}-{h['draws']}-{h['away_wins']}, ort {h['avg_total_goals']} gol" if h else "yok")
          + f" | hakem {d.get('referee') or 'yok'} | stadyum {d.get('venue') or 'yok'}")


def oran(argv):
    team = " ".join(a for a in argv if not a.startswith("--")).strip()
    res = FM.find(None, None, team, None, None, None, 3, include_live=True) if team else []
    if not res:
        print(f"{team}: bugün iddaa'da maç bulunamadı"); return
    s = F._get_session()
    cfg, comps = F._fetch_market_config(s), F._fetch_competitions(s)
    for m in res[:2]:
        d = F._get_json(s, DETAIL_URL.format(m["id"]))["data"]
        p = F._parse_event(d, cfg, comps, m["live"])
        mk = p["markets"]
        mo = mk["match_outcome"]
        marj = (sum(1 / v for v in mo.values()) - 1) * 100 if len(mo) == 3 else None
        n_mk = len(d.get("m", []))
        print(f"{m['home']} - {m['away']} ({m['time']}{', CANLI: oranlar maç içi' if m['live'] else ''}) | {n_mk} pazar"
              + (f" | 1X2 marj %{marj:.1f}" if marj is not None else ""))
        print(f"  1X2: " + " / ".join(f"{k} {mo.get(k, '-')}" for k in ("1", "X", "2"))
              + " | ÇŞ: " + " / ".join(f"{k} {v}" for k, v in mk["double_chance"].items())
              + " | KG: " + " / ".join(f"{k} {v}" for k, v in mk["both_teams_score"].items()))
        ou = sorted((e for e in mk["totals_goals"] if e["market_name"].startswith("Alt/Üst") and str(e["line"]) in ("1.5", "2.5", "3.5")),
                    key=lambda e: float(e["line"]))
        print("  Alt/Üst: " + " | ".join(f"{e['line']}: " + " / ".join(f"{k} {v}" for k, v in e["outcomes"].items()) for e in ou))
