"""Bülten arşivi (Katman A).

Geçmiş bülteni sonradan halka açık hiçbir siteden TAM pazarla (İY/MS dahil) almak mümkün değil
(iddaa/Nesine biten maçı siler; Mackolik yalnız ~5 gün ve az pazar; Sahadan tarih vermiyor).
Bu yüzden iki katman:
  kaydet   : iddaa + Nesine bülteninden BAŞLAMAMIŞ tüm maçların TÜM pazarlarını arşive yazar.
             Her çalıştırmada başlamamış maçın 'son' kaydı güncellenir; başladıktan sonra donar
             (= kapanışa en yakın oran). Günde birkaç kez / maçlardan önce çalıştır.
  mackolik : Mackolik iddaa programından (yaklaşık 16.09 -> +9 gün) skor (MS+İY) ve sınırlı oranlar.
             Mackolik oranları iddaa API oranının ~%96.2'sidir (Nesine gibi kanal oranı; 123 maçta doğrulandı).
             Sütunlar (doğrulandı): 16-18 MS 1/X/2, 19-21 ÇŞ 1X/12/X2, 22-23 Alt/Üst 2.5, 36-38 handikaplı MS,
             39-40 KG Var/Yok, 42-43 İY Alt/Üst 1.5, 8-9 MS skoru, 11-12 İY skoru, 5 durum (>=4 bitti, 9 ert., 11 yarıda kaldı).
  goster   : bir günün arşivini (iddaa tüm pazarlar + Mackolik skoru) kısa satırlarla gösterir.
  ice-aktar: eski football_matches_*.json anlık dosyalarını arşive alır.
  durum    : arşivde hangi günler var.
Yalnızca robots.txt'in izin verdiği yollar kullanılır (Mackolik /AjaxHandlers/, Nesine bülten JSON,
iddaa spor API'si); kendini dürüstçe tanıtan kimlik, istekler arası bekleme, sahte tarayıcı kimliği YOK.
"""
import os, sys, json, re, time, argparse, unicodedata, difflib
from datetime import datetime, timezone, timedelta

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
ARSIV = os.path.join(SCRIPT_DIR, "data", "arsiv")
LEGACY = os.path.join(os.path.expanduser("~"), ".claude", "agents", "data")
TR_TZ = timezone(timedelta(hours=3))
UA = "Mozilla/5.0 (compatible; BWM-Bulten/1.0; kisisel-kullanim)"
NESINE_URLS = ["https://bulten.nesine.com/api/bulten/getprebultenfull",
               "https://cdnbulten.nesine.com/api/bulten/getprebultenfull"]
MACKOLIK_URL = "https://arsiv.mackolik.com/AjaxHandlers/ProgramDataHandler.ashx"
IYMS_ORDER = ["1/1", "1/0", "1/2", "0/1", "0/0", "0/2", "2/1", "2/0", "2/2"]
DELAY = 2.5

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------- yardımcılar
def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _load(path, default=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def _day_file(kind, ymd):
    return os.path.join(ARSIV, f"{kind}_{ymd}.json")


def _iso(ts):
    return datetime.fromtimestamp(ts, TR_TZ).isoformat(timespec="seconds")


def _similar(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _iyms(markets):
    for e in (markets or {}).get("half_time", []):
        if "Yarı / Maç" in e.get("market_name", "") and len(e.get("outcomes", {})) == 9:
            return e["outcomes"]
    return None


# ---------------------------------------------------------------- arşive birleştirme
def _merge(store, key, meta, ts_kickoff, now_ts, markets):
    """Başlamamış maçın 'son' kaydını günceller; başlamışsa dondurur. -> 'yeni'|'guncel'|'donuk'"""
    rec = store.get(key)
    if now_ts >= ts_kickoff:
        return "donuk"
    if rec is None:
        store[key] = dict(meta, kickoff=_iso(ts_kickoff), ilk_cekim=_iso(now_ts), ilk=markets,
                          son_cekim=_iso(now_ts), son=markets, cekim=1, kapanis_oncesi=True)
        return "yeni"
    rec["son"], rec["son_cekim"], rec["cekim"] = markets, _iso(now_ts), rec.get("cekim", 1) + 1
    return "guncel"


def _write_groups(kind, groups):
    """groups: {ymd: {key: rec}} -> mevcut gün dosyalarıyla birleştirip yazar."""
    for ymd, recs in groups.items():
        path = _day_file(kind, ymd)
        cur = _load(path, {"tarih": ymd, "maclar": {}})
        cur["maclar"].update(recs)
        _save(path, cur)


# ---------------------------------------------------------------- kaydet (iddaa + Nesine)
def _iddaa_events():
    import fetch_iddaa as fi
    s = fi._get_session()
    mc, comp = fi._fetch_market_config(s), fi._fetch_competitions(s)
    d = fi._get_json(s, fi.EVENTS_URL).get("data", {})
    live = set((d.get("sc") or {}).keys())
    for ev in d.get("events", []) or []:
        if isinstance(ev, dict) and ev.get("d"):
            p = fi._parse_event(ev, mc, comp, str(ev.get("i", "")) in live)
            if p:
                yield ev["d"], p


def _nesine_events():
    """Korumalı çekim (nesine.bulten): eski CDN kopyasını reddeder; taze bülten yoksa arşive hiçbir şey yazılmaz."""
    import nesine
    veri, taze = nesine.bulten(True)
    if not taze:
        print("Nesine: taze bülten alınamadı, arşive yazılmadı")
        return
    for e in veri["olaylar"]:
        yield e["esd"], {"home": e["hn"], "away": e["an"], "league": e["lig"], "id": f"{e['esd_ms']}_{e['hn']}_{e['an']}"},             [{"t": m["t"], "sov": m["sov"], "o": m["o"]} for m in e["mk"]]


def cmd_kaydet(_a):
    now = time.time()
    groups, stat = {}, {"yeni": 0, "guncel": 0, "donuk": 0}
    for ts, p in _iddaa_events():
        if p.get("is_live"):
            stat["donuk"] += 1
            continue
        ymd = datetime.fromtimestamp(ts, TR_TZ).strftime("%Y%m%d")
        store = groups.setdefault(ymd, _load(_day_file("iddaa", ymd), {"maclar": {}})["maclar"])
        meta = {"id": p["match_id"], "home": p["home"], "away": p["away"], "league": p["league"]}
        stat[_merge(store, p["match_id"], meta, ts, now, p["markets"])] += 1
    _write_groups("iddaa", groups)
    print(f"iddaa: yeni {stat['yeni']} | güncellenen {stat['guncel']} | başlamış/canlı atlanan {stat['donuk']} | gün dosyası {len(groups)}")
    groups, stat = {}, {"yeni": 0, "guncel": 0, "donuk": 0}
    for ts, meta, mk in _nesine_events():
        ymd = datetime.fromtimestamp(ts, TR_TZ).strftime("%Y%m%d")
        store = groups.setdefault(ymd, _load(_day_file("nesine", ymd), {"maclar": {}})["maclar"])
        stat[_merge(store, meta["id"], meta, ts, now, mk)] += 1
    _write_groups("nesine", groups)
    print(f"nesine: yeni {stat['yeni']} | güncellenen {stat['guncel']} | başlamış atlanan {stat['donuk']} | gün dosyası {len(groups)}")


# ---------------------------------------------------------------- ice-aktar
def cmd_ice_aktar(a):
    files = a.dosya or [os.path.join(LEGACY, f) for f in sorted(os.listdir(LEGACY))
                        if re.fullmatch(r"football_matches_\d{8}\.json|matches_today\.json", f)]
    for path in files:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"okunamadı {path}: {e}")
            continue
        mt = os.path.getmtime(path)
        groups, n = {}, 0
        for m in data:
            if not m.get("kickoff_time") or not m.get("markets"):
                continue
            ko = datetime.fromisoformat(m["kickoff_time"]).timestamp()
            ymd = datetime.fromtimestamp(ko, TR_TZ).strftime("%Y%m%d")
            store = groups.setdefault(ymd, _load(_day_file("iddaa", ymd), {"maclar": {}})["maclar"])
            if str(m["match_id"]) in store:
                continue
            store[str(m["match_id"])] = {"id": str(m["match_id"]), "home": m["home"], "away": m["away"],
                                         "league": m["league"], "kickoff": _iso(ko), "ilk_cekim": _iso(mt),
                                         "ilk": m["markets"], "son_cekim": _iso(mt), "son": m["markets"], "cekim": 1,
                                         "kapanis_oncesi": mt < ko, "kaynak": "eski-dosya"}
            n += 1
        _write_groups("iddaa", groups)
        print(f"{os.path.basename(path)}: {n} maç arşive alındı (anlık {datetime.fromtimestamp(mt, TR_TZ):%d.%m %H:%M}; kapanış garantisi YOK)")


# ---------------------------------------------------------------- Mackolik
def _js_to_json(s):
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c == "'":
            j, buf = i + 1, []
            while j < n and s[j] != "'":
                if s[j] == chr(92) and j + 1 < n:
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            out.append(json.dumps("".join(buf), ensure_ascii=False))
            i = j + 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (s[j].isalnum() or s[j] == "_"):
                j += 1
            w, k = s[i:j], j
            while k < n and s[k] == " ":
                k += 1
            out.append(json.dumps(w) if k < n and s[k] == ":" else {"undefined": "null"}.get(w, w))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _odd(x):
    try:
        v = float(str(x).replace(",", "."))
        return v if v > 1.0 else None
    except ValueError:
        return None


def _int(x):
    return int(x) if str(x).lstrip("-").isdigit() else None


def _mackolik_row(r):
    g = lambda i: r[i] if i < len(r) else None
    durum = _int(g(5))
    bitti = durum is not None and durum >= 4 and durum not in (9, 11)
    return {"id": g(0), "home": g(1), "away": g(3), "saat": g(6), "durum": durum,
            "bitti": bitti, "ertelendi": durum == 9, "yarida_kaldi": durum == 11,
            "ms": [_int(g(8)), _int(g(9))] if bitti else None,
            "iy": [_int(g(11)), _int(g(12))] if bitti else None,
            "iddaa_kodu": g(10) or None, "mbs": g(13) or None, "lig": g(26),
            "o": {"1": _odd(g(16)), "X": _odd(g(17)), "2": _odd(g(18)),
                  "1X": _odd(g(19)), "12": _odd(g(20)), "X2": _odd(g(21)),
                  "a25": _odd(g(22)), "u25": _odd(g(23)),
                  "hcp": [g(14) or None, g(15) or None], "h1": _odd(g(36)), "hX": _odd(g(37)), "h2": _odd(g(38)),
                  "kg_var": _odd(g(39)), "kg_yok": _odd(g(40)),
                  "iy15_alt": _odd(g(42)), "iy15_ust": _odd(g(43))}}


def mackolik_gun(ymd_dash, yenile=False):
    path = _day_file("mackolik", ymd_dash.replace("-", ""))
    cur = _load(path)
    today = datetime.now(TR_TZ).date()
    d = datetime.strptime(ymd_dash, "%Y-%m-%d").date()
    if cur and not yenile and d < today and all(m["bitti"] or m["ertelendi"] or m["yarida_kaldi"] for m in cur["maclar"]):
        return cur, "önbellek"
    day = d.strftime("%d.%m.%Y")
    time.sleep(DELAY)
    r = requests.get(MACKOLIK_URL, timeout=90,
                     params={"type": 6, "sortValue": "DATE", "day": day, "sort": -1, "sortDir": -1,
                             "groupId": -1, "np": 0, "sport": 1},
                     headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest",
                              "Referer": "https://arsiv.mackolik.com/Genis-Iddaa-Programi"})
    r.raise_for_status()
    text = r.content.decode("utf-8", "ignore").strip()
    if not text:
        return None, "pencere dışı (Mackolik bu gün için boş döndü)"
    groups = json.loads(_js_to_json(text)).get("m", [])
    rows = [_mackolik_row(x) for g in groups if g.get("d") == day for x in g.get("m", [])]
    cur = {"tarih": ymd_dash, "cekim": _iso(time.time()), "maclar": rows}
    _save(path, cur)
    return cur, "ağdan"


def cmd_mackolik(a):
    cur, kaynak = mackolik_gun(a.tarih, a.yenile)
    if not cur:
        print(kaynak)
        return
    ms = cur["maclar"]
    if a.takim:
        ks = [_norm(t) for t in a.takim.split(",")]
        ms = [m for m in ms if any(k in _norm(m["home"]) or k in _norm(m["away"]) for k in ks)]
    if a.lig:
        ms = [m for m in ms if _norm(a.lig) == _norm(m["lig"])]
    bitti = sum(1 for m in cur["maclar"] if m["bitti"])
    print(f"Mackolik {a.tarih} ({kaynak}): {len(cur['maclar'])} maç, {bitti} bitti | gösterilen {min(len(ms), a.n)}/{len(ms)}")
    for m in ms[:a.n]:
        o = m["o"]
        sk = f"{m['ms'][0]}-{m['ms'][1]} (İY {m['iy'][0]}-{m['iy'][1]})" if m["ms"] else ("ERT" if m["ertelendi"] else "-")
        print(f"{m['saat']} {m['home'][:20]} - {m['away'][:20]} | {sk} | MS {o['1']}/{o['X']}/{o['2']} | A/Ü2.5 {o['a25']}/{o['u25']} | KG {o['kg_var']}/{o['kg_yok']}")


# ---------------------------------------------------------------- goster
def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def _mk_summary(mk):
    n = (len(mk.get("match_outcome", {})) > 0) + sum(len(mk.get(b, [])) for b in
                                                     ("totals_goals", "corners", "cards", "handicaps", "half_time", "other_markets"))
    return n + (1 if mk.get("double_chance") else 0) + (1 if mk.get("both_teams_score") else 0)


def cmd_goster(a):
    ymd = a.tarih.replace("-", "")
    ar = _load(_day_file("iddaa", ymd))
    if not ar and _load(_day_file("pdf", ymd)):
        return _goster_pdf(_load(_day_file("pdf", ymd)), a)
    mack = _load(_day_file("mackolik", ymd))
    if not mack and not a.sadece_arsiv:
        try:
            mack, _ = mackolik_gun(a.tarih)
        except Exception as e:
            print(f"(Mackolik alınamadı: {e})")
    if not ar:
        print(f"iddaa arşivi YOK ({a.tarih}): tam pazar/İY-MS kaydı alınmamış."
              + (" Yalnız Mackolik (skor + sınırlı oran) gösteriliyor." if mack else ""))
        if mack:
            cmd_mackolik(argparse.Namespace(tarih=a.tarih, takim=a.takim, lig=None, n=a.n, yenile=False))
    recs =sorted((ar or {"maclar": {}})["maclar"].values(), key=lambda r: r["kickoff"])
    ks = [_norm(t) for t in a.takim.split(",")] if a.takim else []
    shown = 0
    for r in recs:
        if ks and not any(k in _norm(r["home"]) or k in _norm(r["away"]) for k in ks):
            continue
        mk = r["son"]
        mo = mk.get("match_outcome", {})
        iy = _iyms(mk)
        sk = ""
        if mack:
            best = max(mack["maclar"], key=lambda m: min(_similar(r["home"], m["home"]), _similar(r["away"], m["away"])), default=None)
            if best and min(_similar(r["home"], best["home"]), _similar(r["away"], best["away"])) >= 0.6 and best["ms"]:
                sk = f" | SONUÇ {best['ms'][0]}-{best['ms'][1]} (İY {best['iy'][0]}-{best['iy'][1]})"
        flag = "" if r.get("kapanis_oncesi", True) else " [KAPANIŞ SONRASI ANLIK]"
        print(f"{r['kickoff'][11:16]} {r['home'][:22]} - {r['away'][:22]} | MS {mo.get('1')}/{mo.get('X')}/{mo.get('2')} | "
              f"{_mk_summary(mk)} pazar | İY/MS {'VAR' if iy else 'yok'} | son çekim {r['son_cekim'][11:16]} ({r.get('cekim', 1)}x){flag}{sk}")
        if iy and a.tam:
            print("   İY/MS: " + "  ".join(f"{k} {iy[k]}" for k in IYMS_ORDER if k in iy))
        if a.tam:
            for b in ("totals_goals", "handicaps", "half_time", "corners", "cards", "other_markets"):
                for e in mk.get(b, []):
                    print(f"   [{b}] {e['market_name']}: " + ", ".join(f"{k}={_fmt(v)}" for k, v in e["outcomes"].items()))
            print(f"   [DC] {mk.get('double_chance')} [KG] {mk.get('both_teams_score')}")
        shown += 1
        if shown >= a.n:
            break
    if ar:
        print(f"-- arşivde {len(recs)} maç | gösterilen {shown} | İY/MS açık: {sum(1 for r in recs if _iyms(r['son']))}")


def cmd_durum(_a):
    if not os.path.isdir(ARSIV):
        print("arşiv boş")
        return
    print("Tür     Gün        Maç  İY/MS   Not")
    for f in sorted(os.listdir(ARSIV), key=lambda x: (x.split("_")[1], x)):
        kind, ymd = f[:-5].split("_")
        d = _load(os.path.join(ARSIV, f))
        ms = d["maclar"]
        if kind == "mackolik":
            print(f"{kind:8}{ymd} {len(ms):4}  -       {sum(1 for m in ms if m['bitti'])} maç skorlu")
        elif kind == "iddaa":
            print(f"{kind:8}{ymd} {len(ms):4}  {sum(1 for r in ms.values() if _iyms(r['son'])):4}    "
                  f"{sum(1 for r in ms.values() if r.get('kapanis_oncesi', True))} kapanış öncesi kayıt")
        else:
            print(f"{kind:8}{ymd} {len(ms):4}  -       ham MTID kodlu")


# ---------------------------------------------------------------- pdf-aktar (eski basılı bülten)
PDF_KOLON = ["MS", "Handikaplı MS", "İlk gol", "MS ve A/Ü 2.5", "A/Ü 2.5", "A/Ü 3.5", "KG", "Toplam gol",
             "İY sonucu", "İY 1.5 A/Ü", "İY/MS"]


def _pdf_rows(pdf, base):
    """Basılı iddaa bülteni PDF'i (pdftotext -table: satırlar takım+kod+oranla hizalı) -> satır listesi.
    Gün sınırı: saat geri sarınca yeni bölüm (gece maçları bölüm sonunda). 11 bloklu satır = tüm pazarlar."""
    import subprocess
    txt = subprocess.run(["pdftotext", "-table", "-enc", "UTF-8", pdf, "-"], capture_output=True, check=True).stdout.decode("utf-8", "ignore")
    row_re = re.compile(r"^\s*([FBHV])\s+(\S+)\s+(\d\d:\d\d)\s+(.*)$")
    code_re = re.compile(r"(?<!\S)(\d{5})(?!\S)")
    rows, sec, prev = [], 0, None
    for l in txt.split("\n"):
        if re.search(r"Bankolu|SPOR TOTO", l):
            break
        r = row_re.match(l)
        if not r:
            continue
        spor, lig, saat, rest = r.groups()
        h, m = map(int, saat.split(":"))
        t = h * 60 + m + (1440 if h < 8 else 0)
        if prev is not None and t < prev - 30:
            sec += 1
        prev = t
        parts = code_re.split(rest, maxsplit=1)
        if len(parts) < 3:
            continue
        pieces = [x for x in re.split(r"\s{2,}", parts[0].strip()) if x]
        while pieces and re.fullmatch(r"\d+(\.\d)?", pieces[-1]):
            pieces.pop()
        if len(pieces) < 2:
            continue
        blocks, cur = [], None
        for tk in (parts[1] + " " + parts[2]).split():
            if re.fullmatch(r"\d{5}", tk):
                cur = []
                blocks.append(cur)
            elif re.fullmatch(r"\d+,\d\d|-", tk) and cur is not None:
                cur.append(None if tk == "-" else float(tk.replace(",", ".")))
        rows.append({"gun": (base + timedelta(days=sec)).strftime("%Y%m%d"), "spor": spor, "lig": lig,
                     "saat": saat, "home": pieces[-2], "away": pieces[-1], "bloklar": blocks})
    return rows


def _pdf_aktar_tek(a):
    m = re.match(r"(\d\d)-(\d\d)-(\d{4})", os.path.basename(a.pdf))
    base = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else datetime.strptime(a.baslangic, "%Y-%m-%d")
    rows = _pdf_rows(a.pdf, base)
    days = {}
    for r in rows:
        days.setdefault(r["gun"], {})[f"{r['saat']}_{r['home']}_{r['away']}"] = r
    for ymd, recs in days.items():
        _save(_day_file("pdf", ymd), {"tarih": ymd, "kaynak": os.path.basename(a.pdf), "maclar": recs})
    fut = [r for r in rows if r["spor"] == "F"]
    print(f"{os.path.basename(a.pdf)}: {len(rows)} satır ({len(fut)} futbol, "
          f"{sum(1 for r in fut if any(len(b) == 9 for b in r['bloklar']))} İY/MS'li, "
          f"{sum(1 for r in fut if len(r['bloklar']) == 11)} tam-pazar) | günler {', '.join(f'{k}:{len(v)}' for k, v in sorted(days.items()))}")


def _goster_pdf(pdf, a):
    ks = [_norm(t) for t in a.takim.split(",")] if a.takim else []
    print(f"[basılı bülten {pdf['kaynak']}] pazar etiketi yalnız 11 bloklu satırlarda; diğerlerinde blok sayısı gösterilir")
    n = 0
    for r in sorted(pdf["maclar"].values(), key=lambda r: r["saat"] if r["saat"] >= "08:00" else "9" + r["saat"]):
        if r["spor"] != "F" or (ks and not any(k in _norm(r["home"]) or k in _norm(r["away"]) for k in ks)):
            continue
        b = r["bloklar"]
        iy = next((x for x in b if len(x) == 9), None)
        print(f"{r['saat']} {r['lig']} {r['home'][:22]} - {r['away'][:22]} | MS {b[0] if b else None} | {len(b)} blok | İY/MS {'VAR' if iy else 'yok'}")
        if a.tam and iy:
            print("   İY/MS: " + "  ".join(f"{k} {v}" for k, v in zip(IYMS_ORDER, iy)))
        if a.tam and len(b) == 11:
            print("   " + " | ".join(f"{k}: {v}" for k, v in zip(PDF_KOLON[:-1], b[:-1])))
        n += 1
        if n >= a.n:
            break


# ---------------------------------------------------------------- sablon (oran tekrarı)
def _hane(v):
    return "1" if v[0] > v[1] else ("0" if v[0] == v[1] else "2")


def _ims(sonuc):
    """{'ms':[h,a],'iy':[h,a]} -> 'İY/MS' hanesi ('1/0' = İY ev önde, MS beraber)."""
    if not sonuc or not sonuc.get("ms") or not sonuc.get("iy") or None in sonuc["iy"] or None in sonuc["ms"]:
        return None
    return f"{_hane(sonuc['iy'])}/{_hane(sonuc['ms'])}"


def _sablon_kaynak(ymd):
    """(kaynak, ev, dep, MS[1,X,2], İY/MS[9]|None, kapanış öncesi mi, sonuç) — Nesine + iddaa arşivi + PDF."""
    out = []
    for r in (_load(_day_file("iddaa", ymd)) or {"maclar": {}})["maclar"].values():
        mo, iy = r["son"].get("match_outcome", {}), _iyms(r["son"])
        out.append((f"iddaa {r['kickoff'][11:16]}", r["home"], r["away"], [mo.get("1"), mo.get("X"), mo.get("2")],
                    [iy.get(k) for k in IYMS_ORDER] if iy else None, r.get("kapanis_oncesi", True), r.get("sonuc")))
    for r in (_load(_day_file("nesine", ymd)) or {"maclar": {}})["maclar"].values():
        ms = next((m["o"] for m in r["son"] if m["t"] == 1 and len(m["o"]) == 3), None)
        iy = next((m["o"] for m in r["son"] if m["t"] == 5 and len(m["o"]) == 9), None)
        out.append((f"nesine {r['kickoff'][11:16]}", r["home"], r["away"], [ms.get("1"), ms.get("2"), ms.get("3")] if ms else [None] * 3,
                    [iy.get(str(i)) for i in range(1, 10)] if iy else None, r.get("kapanis_oncesi", True), r.get("sonuc")))
    for r in (_load(_day_file("pdf", ymd)) or {"maclar": {}})["maclar"].values():
        if r["spor"] == "F" and r["bloklar"]:
            ms = r["bloklar"][0]
            out.append((f"pdf {r['saat']}", r["home"], r["away"], ms if len(ms) == 3 else [None] * 3,
                        next((b for b in r["bloklar"] if len(b) == 9), None), True, r.get("sonuc")))
    return out


def cmd_sablon(a):
    """YouTube 'oran tekrarı' şablonu: MS1=MS2 ve İY/MS 1/X=2/X, 1/2=2/1 -> 1/X; fark varsa 2/X.
    Kaynak önceliği Nesine > iddaa > PDF (aynı maç bir kez sayılır). Canlı/1.0 oranlıları ve oran tavanı artefaktını ayıklar."""
    import collections
    src = _sablon_kaynak(a.tarih.replace("-", ""))
    if not src:
        print("arşivde bu gün yok (iddaa/nesine/pdf)")
        return
    pri = {"nesine": 0, "iddaa": 1, "pdf": 2}
    src.sort(key=lambda s: pri[s[0].split()[0]])
    uniq = []
    for s in src:
        if not any(_similar(s[1], u[1]) >= 0.8 and _similar(s[2], u[2]) >= 0.8 for u in uniq):
            uniq.append(s)
    hi = collections.Counter(v for s in uniq if s[4] for v in s[4] if v and v >= 25)
    cap = max(hi, key=lambda v: (hi[v], v)) if hi else 999
    st, rows = collections.Counter(), []
    for kay, ev, dep, ms, iy, kok, son in uniq:
        if None in ms or 1.0 in ms or not kok:
            st["atlanan(canlı/bozuk/kapanış sonrası)"] += 1
            continue
        d = round(abs(ms[0] - ms[2]), 2)
        grp = "yakın(<=0.08)" if d <= 0.08 else ("orta" if d <= 0.30 else "uzak(>0.30)")
        st["MS'li maç"] += 1
        st["MS1=MS2 birebir"] += d == 0
        b = c = None
        if iy and None not in iy:
            b, c = iy[1] == iy[7], iy[2] == iy[6]
            st["İY/MS'li"] += 1
            st[f"{grp}: n"] += 1
            st[f"{grp}: 1/X=2/X"] += b
            st[f"{grp}: 1/2=2/1"] += c
        if d == 0 or (b and c):
            tavan = bool(c and iy[2] >= cap - 0.5)
            yon = "2/X" if (iy and not (b and c)) else "1/X"
            gercek = _ims(son)
            rows.append((kay, f"{ev[:20]} - {dep[:20]}", ms, d, iy and (iy[1], iy[7], iy[2], iy[6]), yon,
                         gercek or "-", "TUTTU" if gercek == yon else ("tutmadı" if gercek else "sonuç yok"),
                         "1/2=2/1 TAVAN" if tavan else ""))
    print(f"{a.tarih}: {len(uniq)} maç (tekil) | oran tavanı ≈ {cap} | " + " | ".join(f"{k} {v}" for k, v in st.items()))
    for r in rows:
        print(r)


def cmd_sablon_hafta(a):
    """Son N gün (Mackolik penceresi ≤6 gün): Mackolik programı (= Nesine oranı) + gerçek İY/MS sonuçları.
    MS1=MS2 gruplarında 1/X ve 2/X'in gerçekleşme sıklığını tüm maçlarla (taban) karşılaştırır.
    İY/MS ORANI Mackolik'te yok: ayna koşulu için `sablon GÜN` (Nesine/iddaa arşivi) kullan."""
    import collections
    today = datetime.now(TR_TZ).date()
    veri, gunler = [], []
    for i in range(min(a.gun, 6), -1, -1):
        d = today - timedelta(days=i)
        cur, _ = mackolik_gun(d.isoformat())
        if not cur:
            continue
        gunler.append(d.strftime("%d.%m"))
        for m in cur["maclar"]:
            o = m["o"]
            if o["1"] and o["X"] and o["2"]:
                veri.append((d, m, round(abs(o["1"] - o["2"]), 2), _ims({"ms": m["ms"], "iy": m["iy"]}) if m["bitti"] else None))
    gruplar = [("MS1=MS2 birebir", lambda x: x == 0), ("|MS1-MS2|<=0.05", lambda x: x <= 0.05),
               ("|MS1-MS2|<=0.15", lambda x: x <= 0.15), ("TABAN (tüm maçlar)", lambda x: True)]
    print(f"Mackolik (=Nesine oranı) günleri {', '.join(gunler)}: MS oranlı {len(veri)} maç, sonuçlu {sum(1 for v in veri if v[3])}")
    print(f"{'Grup':22} {'maç':>5} {'sonuçlu':>8} {'1/X':>5} {'2/X':>5} {'0/0':>5} {'1/1':>5} {'2/2':>5} {'1/X %':>7} {'2/X %':>7}")
    for ad, f in gruplar:
        sec = [v for v in veri if f(v[2])]
        c = collections.Counter(v[3] for v in sec if v[3])
        n = sum(c.values())
        pct = lambda k: f"{100 * c[k] / n:.1f}" if n else "-"
        print(f"{ad:22} {len(sec):5} {n:8} {c['1/0']:5} {c['2/0']:5} {c['0/0']:5} {c['1/1']:5} {c['2/2']:5} {pct('1/0'):>7} {pct('2/0'):>7}")
    print("\nMS1=MS2 birebir olan maçlar:")
    for d, m, x, ims in veri:
        if x == 0:
            o = m["o"]
            print(f"  {d.strftime('%d.%m')} {m['saat']} {m['home'][:20]} - {m['away'][:20]} | MS {o['1']}/{o['X']}/{o['2']} | "
                  f"{('sonuç ' + str(m['ms'][0]) + '-' + str(m['ms'][1]) + ' (İY ' + str(m['iy'][0]) + '-' + str(m['iy'][1]) + ') → ' + ims) if ims else 'sonuç yok'}")


# ---------------------------------------------------------------- dogruskor (2.5 Üst / KG Var taktiği)
# Nesine MTID 777 = Maç Skoru (29 çıktı; N sırası iddaa ile aynı, 2:2 = N15; hafıza: bwm-nesine-odds)
NESINE_SKOR = ["1:0", "2:0", "2:1", "3:0", "3:1", "3:2", "4:0", "4:1", "4:2", "5:0", "5:1", "6:0", "0:0", "1:1", "2:2", "3:3",
               "0:1", "0:2", "1:2", "0:3", "1:3", "2:3", "0:4", "1:4", "2:4", "0:5", "1:5", "0:6", "diğer"]


def _skor_oranlari(r):
    sk = next((m["o"] for m in r["son"] if m["t"] == 777), None)
    ms = next((m["o"] for m in r["son"] if m["t"] == 1 and len(m["o"]) == 3), None)
    if not sk or not ms:
        return None
    return {NESINE_SKOR[int(k) - 1]: v for k, v in sk.items() if 1 <= int(k) <= len(NESINE_SKOR)}, [ms.get("1"), ms.get("2"), ms.get("3")]


def _skor_olasilik(sk):
    """Doğru skor oranlarından (marjdan arındırılmış) piyasa-örtük olasılık: KG Var, Üst 2.5, ikisi birden ('diğer' Üst sayılır, KG'ye katılmaz)."""
    tot = sum(1 / v for v in sk.values() if v)
    pk = pu = pb = 0.0
    for k, v in sk.items():
        if not v:
            continue
        p = (1 / v) / tot
        if k == "diğer":
            pu += p
            continue
        h, a = map(int, k.split(":"))
        kg, ust = h > 0 and a > 0, h + a >= 3
        pk += p * kg
        pu += p * ust
        pb += p * (kg and ust)
    return pk, pu, pb


def cmd_dogruskor(a):
    """YouTube 'doğru skor oranı' taktiği (2.5 Üst / KG Var): (1) favorinin 2-1 skor oranı 7.00, (2) 1-1 ve 2-1 oranları birbirine
    yakın (uçurum yok), (3) 0-0 oranı > 10.00. Yan faktörler (form, sakat, ceza) oranla ölçülemez; ayrıca bakılır.
    Kaynak: yalnız Nesine arşivi (MTID 777). iddaa API, Mackolik ve basılı PDF'te doğru skor pazarı YOK; Nesine bültende de ~%10 maçta açık.
    Nesine arşivi 21.09.2026'da başladı: öncesi için bu oranlar kaydedilmemiştir."""
    today = datetime.now(TR_TZ).date()
    satirlar = []
    for f in sorted(os.listdir(ARSIV)):
        kind, _, ymd = f[:-5].partition("_")
        if kind != "nesine" or (datetime.strptime(ymd, "%Y%m%d").date() - today).days < -a.gun:
            continue
        for r in _load(os.path.join(ARSIV, f))["maclar"].values():
            x = _skor_oranlari(r)
            if not x:
                continue
            sk, (o1, ox, o2) = x
            if None in (o1, o2, sk.get("2:1"), sk.get("1:2"), sk.get("1:1"), sk.get("0:0")):
                continue
            fav = "ev" if o1 <= o2 else "dep"
            f21 = sk["2:1"] if fav == "ev" else sk["1:2"]
            r1k, r1y = abs(f21 - 7.0) < 0.005, abs(f21 - 7.0) <= a.tol
            r2 = abs(sk["1:1"] - f21) <= a.yakin
            r3 = sk["0:0"] > 10.0
            s = r.get("sonuc")
            sonuc = None
            if s and s.get("ms") and None not in s["ms"]:
                g = s["ms"]
                sonuc = {"skor": f"{g[0]}-{g[1]}", "kg": g[0] > 0 and g[1] > 0, "ust25": sum(g) >= 3}
            kgo = next((m["o"].get("1") for m in r["son"] if m["t"] == 38), None)
            usto = next((m["o"].get("2") for m in r["son"] if m["t"] == 12 and m.get("sov") == 2.5), None)
            satirlar.append((ymd, r["kickoff"][11:16], r["home"], r["away"], (o1, ox, o2), fav, f21, sk["1:1"], sk["0:0"], r1k, r1y, r2, r3, sonuc, sk, kgo, usto))
    if not satirlar:
        print("Nesine arşivinde açık doğru skor pazarı olan maç yok")
        return
    print(f"Nesine doğru skor pazarı açık maç: {len(satirlar)} (gün: {', '.join(sorted({s[0][6:8] + '.' + s[0][4:6] for s in satirlar}))}) | kurallar: 2-1 = 7.00 (yakın: ±{a.tol}), |1-1 − 2-1| ≤ {a.yakin}, 0-0 > 10.00")
    print("gün   saat  maç                                    MS(1/X/2)          fav  fav2-1  1-1   0-0   K1kesin K1yakın K2   K3  | sonuç")
    for ymd, saat, ev, dep, ms, fav, f21, o11, o00, r1k, r1y, r2, r3, sn, *_ek in satirlar:
        print(f"{ymd[6:8]}.{ymd[4:6]} {saat} {(ev[:18] + ' - ' + dep[:18]):38} {str(ms[0]) + '/' + str(ms[1]) + '/' + str(ms[2]):18} {fav:4} {f21:6} {o11:5} {o00:5}   {'E' if r1k else '-':6}  {'E' if r1y else '-':6} {'E' if r2 else '-':3} {'E' if r3 else '-':3} | "
              + (f"{sn['skor']} KG {'VAR' if sn['kg'] else 'yok'} 2.5 {'ÜST' if sn['ust25'] else 'alt'}" if sn else "sonuç yok"))
    tk = [s for s in satirlar if s[9] and s[11] and s[12]]
    ty = [s for s in satirlar if s[10] and s[11] and s[12]]
    print(f"özet: K1 kesin(7.00) {sum(1 for s in satirlar if s[9])} | K1 yakın {sum(1 for s in satirlar if s[10])} | K2 {sum(1 for s in satirlar if s[11])} | K3 {sum(1 for s in satirlar if s[12])} | "
          f"TÜM KURALLAR kesin {len(tk)} / yakın {len(ty)}")
    for s in ty:
        print(f"  TAM (yakın): {s[0]} {s[1]} {s[2]} - {s[3]} | fav 2-1 {s[6]} | 1-1 {s[7]} | 0-0 {s[8]} | " + (f"sonuç {s[13]['skor']}" if s[13] else "sonuç yok"))
        pk, pu, pb = _skor_olasilik(s[14])
        ev = lambda p_, o_: f"EV {100 * (p_ * o_ - 1):+.0f}%" if o_ else "oran yok"
        print(f"      piyasa-örtük (aynı skor tablosundan, marjsız): KG Var %{100 * pk:.0f} | Üst 2.5 %{100 * pu:.0f} | ikisi birden %{100 * pb:.0f}"
              f" | Nesine KG Var {s[15]} ({ev(pk, s[15])}) | Üst 2.5 {s[16]} ({ev(pu, s[16])})")


# ---------------------------------------------------------------- ust45 (4,5 Gol Üstü taktiği)
# Nesine: 155/SOV 4.5 = Alt/Üst 4.5 (N2 = Üst) | 459/SOV 1.5 = İY sonucu ve İY 1.5 A/Ü (sıra: 1A, XA, 2A, 1Ü, XÜ, 2Ü; İY marjsız
# olasılıklarıyla doğrulandı: 1A+1Ü = İY 1) | 43 = Toplam Gol (N1 0-1, N2 2-3, N3 4-5, N4 6+) | 14/SOV 1.5 = İY 1.5 A/Ü (N2 = Üst)
def _nes(r, t, sov=None):
    return next((m["o"] for m in r["son"] if m["t"] == t and (sov is None or m.get("sov") == sov)), None) or {}


def cmd_ust45(a):
    """YouTube '4,5 Gol Üstü' taktiği: (1) 4,5 Üst 4.20-5.10, (2) 'İlk yarı 1 ve 1,5 gol üstü' 5.40-6.20 (İY sonucu 1 + İY 1.5 Üst kombine),
    (3) Toplam Gol 4-5 gol 2.80-3.10. Tam test yalnız Nesine arşivinde (21.09.2026'dan): iddaa API/Mackolik/PDF'te 4,5 Üst yok,
    kombine İY pazarı yok. iddaa arşivinde yalnız kural 3 (×0.962 ≈ Nesine) ve düz İY 1.5 Üst sınanabilir."""
    today = datetime.now(TR_TZ).date()
    rng = {"k1": (4.20, 5.10), "k2": (5.40, 6.20), "k3": (2.80, 3.10)}
    ok = lambda v, k: v is not None and rng[k][0] <= v <= rng[k][1]
    satir, atlanan, gun_say = [], 0, {}
    for f in sorted(os.listdir(ARSIV)):
        kind, _, ymd = f[:-5].partition("_")
        if kind != "nesine" or (datetime.strptime(ymd, "%Y%m%d").date() - today).days < -a.gun:
            continue
        for r in _load(os.path.join(ARSIV, f))["maclar"].values():
            u45, kombo, dz, t45 = _nes(r, 155, 4.5).get("2"), _nes(r, 459, 1.5).get("4"), _nes(r, 14, 1.5).get("2"), _nes(r, 43).get("3")
            gun_say.setdefault(ymd, [0, 0, 0, 0, 0])
            g = gun_say[ymd]
            g[0] += 1
            g[1] += u45 is not None
            g[2] += kombo is not None
            g[3] += t45 is not None
            if not r.get("kapanis_oncesi", True):
                atlanan += 1
                continue
            k1, k2, k2d, k3 = ok(u45, "k1"), ok(kombo, "k2"), ok(dz, "k2"), ok(t45, "k3")
            g[4] += k1 and k3
            s, sn = r.get("sonuc"), None
            if s and s.get("ms") and None not in s["ms"]:
                ms, iy = s["ms"], (s.get("iy") if s.get("iy") and None not in s["iy"] else None)
                sn = {"top": sum(ms), "skor": f"{ms[0]}-{ms[1]}", "iy": f"{iy[0]}-{iy[1]}" if iy else "?",
                      "kombo": (iy[0] > iy[1] and sum(iy) >= 2) if iy else None}
            ms1 = _nes(r, 1)
            satir.append((ymd, r["kickoff"][11:16], r["home"], r["away"], (ms1.get("1"), ms1.get("2"), ms1.get("3")), u45, kombo, dz, t45, k1, k2, k2d, k3, sn))
    print("Nesine arşivi (Alt/Üst 4.5 · İY&1.5 kombine · Toplam Gol açık maç): " + " | ".join(
        f"{d[6:8]}.{d[4:6]}: {v[0]} maç, 4.5Ü {v[1]}, kombine {v[2]}, 4-5g {v[3]}" for d, v in sorted(gun_say.items())))
    if atlanan:
        print(f"canlı/kapanış sonrası kayıtlı {atlanan} maç atlandı")
    print(f"kurallar: 4.5 Üst {rng['k1']} | İY1&İY1.5Üst {rng['k2']} | 4-5 gol {rng['k3']}")
    n = lambda i: sum(1 for x in satir if x[i])
    tam = [x for x in satir if x[9] and x[10] and x[12]]
    tam_d = [x for x in satir if x[9] and x[11] and x[12]]
    k13 = [x for x in satir if x[9] and x[12]]
    print(f"K1 {n(9)} | K2 (kombine) {n(10)} | K2 (düz İY 1.5 Üst, yedek yorum) {n(11)} | K3 {n(12)} | K1+K3 {len(k13)} | "
          f"TÜM KURALLAR {len(tam)} (yedek yorumla {len(tam_d)})")
    print("saat maç MS(1/X/2) | 4.5Ü kombine İY1.5Ü 4-5g | K1 K2 K3 | sonuç   (yalnız ≥2 kural sağlayanlar)")
    for x in satir:
        if sum(bool(x[i]) for i in (9, 10, 12)) >= 2 or (x[9] and x[11] and x[12]):
            sn = x[13]
            print(f"{x[0][6:8]}.{x[0][4:6]} {x[1]} {(x[2][:16] + ' - ' + x[3][:16]):34} {x[4][0]}/{x[4][1]}/{x[4][2]} | {x[5]} {x[6]} {x[7]} {x[8]} | "
                  f"{'E' if x[9] else '-'} {'E' if x[10] else '-'} {'E' if x[12] else '-'} | "
                  + (f"{sn['skor']} (İY {sn['iy']}) top {sn['top']}: 4.5Ü {'TUTTU' if sn['top'] >= 5 else 'yok'}" if sn else "sonuç yok"))
    print("K1 (4.5 Üst 4.20-5.10) sağlayan maçlar — diğer kurallara uzaklık:")
    for x in satir:
        if x[9]:
            print(f"  {x[0][6:8]}.{x[0][4:6]} {x[1]} {(x[2][:16] + ' - ' + x[3][:16]):34} MS {x[4][0]}/{x[4][1]}/{x[4][2]} | 4.5Ü {x[5]} | kombine {x[6]} | 4-5g {x[8]} | "
                  + (f"sonuç {x[13]['skor']} top {x[13]['top']}" if x[13] else "sonuç yok"))
    bit = [x for x in satir if x[13] and x[9] and x[12]]
    if bit:
        print(f"K1+K3 sonuçlu {len(bit)}: 4.5 Üst tuttu {sum(1 for x in bit if x[13]['top'] >= 5)}")
    # kısmi test: iddaa arşivi (18.09'dan) — yalnız 'Toplam Gol 4-5 gol' (×0.962) ve düz İY 1.5 Üst; 4,5 Üst ve kombine pazar kayıtlı değil
    oyn, bit_i, taban = [], [], []
    for f in sorted(os.listdir(ARSIV)):
        kind, _, ymd = f[:-5].partition("_")
        if kind != "iddaa" or (datetime.strptime(ymd, "%Y%m%d").date() - today).days < -a.gun:
            continue
        for r in _load(os.path.join(ARSIV, f))["maclar"].values():
            if not r.get("kapanis_oncesi", True):
                continue
            tg = next((m["outcomes"] for m in r["son"].get("totals_goals", []) if m["market_name"] == "Toplam Gol"), None)
            v45 = next((o for k, o in (tg or {}).items() if k.startswith("4-5")), None)
            if v45 is None:
                continue
            oyn.append(ymd)
            sk = (r.get("sonuc") or {}).get("ms")
            if sk and None not in sk:
                taban.append(sum(sk))
                if ok(round(v45 * 0.962, 2), "k3"):
                    bit_i.append((ymd, r["home"], r["away"], v45, sum(sk)))
    if oyn:
        print(f"iddaa arşivi (kısmi): 'Toplam Gol' açık {len(oyn)} maç, sonuçlu {len(taban)} (taban: ≥5 gol {sum(1 for t in taban if t >= 5)}); "
              f"4-5 gol×0.962 ∈ 2.80-3.10 ve sonuçlu: {len(bit_i)} maç"
              + (f" | ≥5 gol {sum(1 for b in bit_i if b[4] >= 5)}, 4-5 gol {sum(1 for b in bit_i if 4 <= b[4] <= 5)}" if bit_i else "")
              + " (4,5 Üst ve kombine İY pazarı iddaa kaydında yok → tam kural sınanamaz)")


# ---------------------------------------------------------------- kalıcı bellek: PDF kutusu, kayıt defteri
PDF_KUTU =os.path.join(SCRIPT_DIR, "data", "pdf_gelen")
KAYIT = os.path.join(ARSIV, "kaynaklar.json")
PDF_ADRES = "https://www.iddaa.com/dosyalar/bayimalzemeleri/{}"   # yalnız elle indirme için; robots.txt otomatik erişimi yasaklar


def _sha(path):
    import hashlib
    return hashlib.sha1(open(path, "rb").read()).hexdigest()[:12]


def cmd_pdf_aktar(a):
    import shutil
    os.makedirs(PDF_KUTU, exist_ok=True)
    kayit = _load(KAYIT, {})
    if a.pdf:
        hedef = os.path.join(PDF_KUTU, os.path.basename(a.pdf))
        if os.path.abspath(a.pdf) != os.path.abspath(hedef):
            shutil.copy2(a.pdf, hedef)          # orijinal kalıcı kopya: ayrıştırıcı düzelirse yeniden işlenir
        dosyalar = [hedef]
    else:
        dosyalar = [os.path.join(PDF_KUTU, f) for f in sorted(os.listdir(PDF_KUTU)) if f.lower().endswith(".pdf")]
    yeni = 0
    for p in dosyalar:
        ad, sha = os.path.basename(p), _sha(p)
        if kayit.get(ad, {}).get("sha") == sha and not a.pdf:
            continue
        _pdf_aktar_tek(argparse.Namespace(pdf=p, baslangic=getattr(a, "baslangic", None)))
        kayit[ad] = {"sha": sha, "aktarim": _iso(time.time())}
        yeni += 1
    _save(KAYIT, kayit)
    print(f"PDF kutusu: {PDF_KUTU} | işlenen {yeni} | kayıtlı toplam {len(kayit)}")


def cmd_pdf_liste(a):
    """iddaa haftada iki program yayımlar: Salı (Sal-Per) ve Cuma (Cum-Pzt); dosya adı GG-AA-YYYY-Bulten.pdf
    (26-12-2023 Salı, 20-02-2024 Salı, 01-03-2024 Cuma örnekleriyle doğrulandı)."""
    today = datetime.now(TR_TZ).date()
    kayit = _load(KAYIT, {})
    print(f"Elle indir -> {PDF_KUTU} içine koy -> `python bulten_arsiv.py pdf-aktar`")
    print("Dosya                     Durum        Adres")
    for i in range(a.gun, -4, -1):
        d = today - timedelta(days=i)
        if d.weekday() not in (1, 4):
            continue
        ad = d.strftime("%d-%m-%Y") + "-Bulten.pdf"
        durum = "içe aktarıldı" if ad in kayit else ("klasörde" if os.path.exists(os.path.join(PDF_KUTU, ad)) else "BEKLİYOR")
        print(f"{ad:25} {durum:12} {PDF_ADRES.format(ad)}")


# ---------------------------------------------------------------- sonuç ekleme (Mackolik + Flashscore)
def _flash_gun(d, ofs):
    """Flashscore günlük liste (yalnız -7..0 gün): FT = AG/AH; BC/BD ikinci yarı golleri -> İY = FT - BC/BD (Mackolik'le doğrulandı)."""
    path = _day_file("flash", d.strftime("%Y%m%d"))
    cur = _load(path)
    if cur and ofs < 0:
        return cur["maclar"]
    import fetch_flash as FL
    out = []
    for r in FL.parse(FL.feed(FL.make_session(), f"f_1_{ofs}_3_en_1")):
        if r.get("AB") == "3" and "AD" in r and r.get("AG") not in (None, "") and r.get("AH") not in (None, ""):
            ft = [int(r["AG"]), int(r["AH"])]
            ht = [ft[0] - int(r["BC"]), ft[1] - int(r["BD"])] if r.get("BC") not in (None, "") and r.get("BD") not in (None, "") else None
            out.append({"ts": int(r["AD"]), "home": r.get("AE", ""), "away": r.get("AF", ""), "ms": ft, "iy": ht})
    if ofs < 0:
        _save(path, {"tarih": d.isoformat(), "maclar": out})
    return out


def _sonuc_havuz(d, today):
    havuz, fark = [], (today - d).days
    if fark <= 6:
        try:
            cur, _ = mackolik_gun(d.isoformat())
            for m in (cur or {}).get("maclar", []):
                if m["bitti"] and m["ms"] and None not in m["ms"]:
                    ts = datetime.combine(d, datetime.strptime(m["saat"], "%H:%M").time(), tzinfo=TR_TZ).timestamp()
                    havuz.append((ts, m["home"], m["away"], m["ms"], m["iy"], "mackolik"))
        except Exception as e:
            print(f"  (Mackolik {d}: {e})")
    if 0 <= fark <= 7:
        try:
            havuz += [(x["ts"], x["home"], x["away"], x["ms"], x["iy"], "flashscore") for x in _flash_gun(d, -fark)]
        except Exception as e:
            print(f"  (Flashscore {d}: {e})")
    return havuz


def _kickoff_ts(r):
    if r.get("kickoff"):
        return datetime.fromisoformat(r["kickoff"]).timestamp()
    d = datetime.strptime(r["gun"], "%Y%m%d").date()
    h, m = map(int, r["saat"].split(":"))
    return datetime.combine(d + timedelta(days=1 if h < 8 else 0), datetime.min.time(), tzinfo=TR_TZ).timestamp() + h * 3600 + m * 60


def cmd_sonuc(a):
    import collections
    today = datetime.now(TR_TZ).date()
    stat = collections.Counter()
    for f in sorted(os.listdir(ARSIV)):
        kind, _, ymd = f[:-5].partition("_")
        if kind not in ("iddaa", "pdf", "nesine"):
            continue
        d = datetime.strptime(ymd, "%Y%m%d").date()
        if not 0 <= (today - d).days <= a.gun:
            continue
        doc = _load(os.path.join(ARSIV, f))
        bekleyen = [r for r in doc["maclar"].values() if "sonuc" not in r and r.get("spor", "F") == "F"]
        if not bekleyen:
            continue
        havuz = _sonuc_havuz(d, today)
        for r in bekleyen:
            ko, best = _kickoff_ts(r), None
            for ts, h, aw, ms, iy, kay in havuz:
                if abs(ts - ko) <= 7200:
                    sc = min(_similar(r["home"], h), _similar(r["away"], aw))
                    if sc >= 0.7 and (best is None or sc > best[0]):
                        best = (sc, ms, iy, kay)
            if best:
                r["sonuc"] = {"ms": best[1], "iy": best[2], "kaynak": best[3]}
                stat[f"{kind} sonuçlandı ({best[3]})"] += 1
            else:
                stat[f"{kind} sonuçsuz(henüz/eşleşmedi)"] += 1
        _save(os.path.join(ARSIV, f), doc)
    print("sonuc: " + (" | ".join(f"{k} {v}" for k, v in stat.items()) or "işlenecek gün yok"))


# ---------------------------------------------------------------- kapsam, dışa aktarma, temizlik, günlük tek komut
def cmd_kapsam(a):
    today = datetime.now(TR_TZ).date()
    print("Gün         iddaa(İY/MS)  sonuçlu  PDF(İY/MS)  Nesine  Mackolik | not")
    for i in range(a.gun, -8, -1):
        d = today - timedelta(days=i)
        ymd = d.strftime("%Y%m%d")
        idd, pdf, nes, mk = (_load(_day_file(k, ymd)) for k in ("iddaa", "pdf", "nesine", "mackolik"))
        iv = idd["maclar"].values() if idd else []
        pv = [r for r in pdf["maclar"].values() if r["spor"] == "F"] if pdf else []
        sonuclu = sum(1 for r in list(iv) + pv if "sonuc" in r)
        nota = ""
        mko = sum(1 for m in (mk["maclar"] if mk else []) if m["o"]["1"])
        if i > 0 and not idd and not pdf and not nes:
            nota = f"yalnız Mackolik: {mko} maç Nesine oranı (az pazar, İY/MS yok)" if mko else "ODDS YOK -> pdf-liste"
        elif i > 0 and sonuclu == 0 and (idd or pdf):
            nota = "sonuç bekliyor" if i <= 7 else "sonuç kaynağı yok (>7 gün)"
        print(f"{d.isoformat()}  {len(iv):5}({sum(1 for r in iv if _iyms(r['son'])):3})  {sonuclu:7}  {len(pv):5}({sum(1 for r in pv if any(len(b) == 9 for b in r['bloklar'])):3})  {len(nes['maclar']) if nes else 0:6}  {len(mk['maclar']) if mk else 0:8} | {nota}")


def _flat(mk):
    out = {}
    for ad, k in (("Maç Sonucu", "match_outcome"), ("Çifte Şans", "double_chance"), ("Karşılıklı Gol", "both_teams_score")):
        if mk.get(k):
            out[ad] = mk[k]
    for b in ("totals_goals", "corners", "cards", "handicaps", "half_time", "other_markets"):
        for e in mk.get(b, []):
            out[e["market_name"]] = e["outcomes"]
    return out


def _flat_pdf(r):
    b = r["bloklar"]
    out = {}
    if b and len(b[0]) == 3:
        out["Maç Sonucu"] = dict(zip(("1", "X", "2"), b[0]))
    iy = next((x for x in b if len(x) == 9), None)
    if iy:
        out["1. Yarı / Maç Sonucu"] = dict(zip(IYMS_ORDER, iy))
    if len(b) == 11:
        for ad, v in zip(PDF_KOLON[1:-1], b[1:-1]):
            out[ad] = v
    return out


def _disari_eski(a):
    today = datetime.now(TR_TZ).date()
    yol =a.cikti or os.path.join(SCRIPT_DIR, "data", "disari", "tam_veri.jsonl")
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    n = ns = 0
    with open(yol, "w", encoding="utf-8") as fh:
        for f in sorted(os.listdir(ARSIV)):
            kind, _, ymd = f[:-5].partition("_")
            if kind not in ("iddaa", "pdf") or not 0 <= (today - datetime.strptime(ymd, "%Y%m%d").date()).days <= a.gun:
                continue
            for r in _load(os.path.join(ARSIV, f))["maclar"].values():
                if kind == "pdf" and r["spor"] != "F":
                    continue
                if kind == "iddaa":
                    sat = {"tarih": ymd, "saat": r["kickoff"][11:16], "lig": r["league"], "ev": r["home"], "dep": r["away"],
                           "kaynak": "iddaa-api", "oran_turu": "kapanışa en yakın" if r.get("kapanis_oncesi", True) else "KAPANIŞ SONRASI/CANLI (kullanma)",
                           "acilis": _flat(r["ilk"]), "kapanis": _flat(r["son"])}
                else:
                    sat = {"tarih": ymd, "saat": r["saat"], "lig": r["lig"], "ev": r["home"], "dep": r["away"],
                           "kaynak": "basılı-pdf", "oran_turu": "program (basıldığı andaki, kapanış DEĞİL)",
                           "acilis": _flat_pdf(r), "kapanis": None}
                sat["sonuc"] = r.get("sonuc")
                fh.write(json.dumps(sat, ensure_ascii=False) + "\n")
                n += 1
                ns += "sonuc" in r
    print(f"{yol}: {n} maç, {ns} sonuçlu")


def cmd_temizle(a):
    today = datetime.now(TR_TZ).date()
    silinecek = []
    for f in sorted(os.listdir(ARSIV)):
        _, _, ymd = f[:-5].partition("_")
        if ymd.isdigit() and len(ymd) == 8 and (today - datetime.strptime(ymd, "%Y%m%d").date()).days > a.gun:
            silinecek.append(f)
    for f in silinecek:
        if a.sil:
            os.remove(os.path.join(ARSIV, f))
    print(f"{a.gun} günden eski {len(silinecek)} arşiv dosyası " + ("SİLİNDİ" if a.sil else "(silinmedi; --sil ile siler)") + (": " + ", ".join(silinecek[:6]) if silinecek else ""))


def cmd_topla(_a):
    cmd_kaydet(_a)
    cmd_sonuc(argparse.Namespace(gun=7))
    cmd_temizle(argparse.Namespace(gun=35, sil=False))


# Nesine pazar kodları (Mackolik oranlarıyla eşleştirilerek doğrulandı, 21.09.2026): 1, 3, 5, 12(2.5), 38, 14(1.5)
NESINE_MTID = {1: "Maç Sonucu", 3: "Çifte Şans", 5: "1. Yarı / Maç Sonucu", 12: "Alt/Üst", 14: "1. Yarı Alt/Üst",
               38: "Karşılıklı Gol", 777: "Maç Skoru", 779: "İlk Yarı Skoru"}
NESINE_ETIKET = {1: ("1", "X", "2"), 3: ("1X", "12", "X2"), 5: tuple(IYMS_ORDER), 12: ("Alt", "Üst"),
                 14: ("Alt", "Üst"), 38: ("Var", "Yok")}


def _flat_nesine(mk):
    out = {}
    for m in mk or []:
        t, sov = m.get("t"), m.get("sov")
        ad = NESINE_MTID.get(t, f"MTID {t}") + (f" {sov:g}" if sov else "")
        keys, et = sorted(m["o"], key=lambda k: int(k)), NESINE_ETIKET.get(t)
        out[ad] = {(et[i] if et and i < len(et) else k): m["o"][k] for i, k in enumerate(keys)}
    return out


def _flat_mackolik(m):
    o, d = m["o"], {}
    for ad, kv in (("Maç Sonucu", {"1": o["1"], "X": o["X"], "2": o["2"]}),
                   ("Çifte Şans", {"1X": o["1X"], "12": o["12"], "X2": o["X2"]}),
                   ("Alt/Üst 2.5", {"Alt": o["a25"], "Üst": o["u25"]}),
                   ("Karşılıklı Gol", {"Var": o["kg_var"], "Yok": o["kg_yok"]}),
                   ("Handikaplı MS", {"1": o["h1"], "X": o["hX"], "2": o["h2"], "cizgi": o["hcp"]}),
                   ("1. Yarı Alt/Üst 1.5", {"Alt": o["iy15_alt"], "Üst": o["iy15_ust"]})):
        if any(v is not None for k, v in kv.items() if k != "cizgi"):
            d[ad] = kv
    return d


def cmd_disari(a):
    """Geriye dönük test verisi (JSONL). Kaynak öncelik notu: nesine-bulten (tüm pazar, Nesine oranı) >
    mackolik (Nesine oranıyla aynı, ~5 gün, az pazar) > iddaa-api (Nesine'den ~%4 yüksek) > basılı-pdf (program oranı)."""
    today = datetime.now(TR_TZ).date()
    yol = a.cikti or os.path.join(SCRIPT_DIR, "data", "disari", "tam_veri.jsonl")
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    n = ns = 0
    with open(yol, "w", encoding="utf-8") as fh:
        for f in sorted(os.listdir(ARSIV)):
            kind, _, ymd = f[:-5].partition("_")
            if kind not in ("iddaa", "pdf", "nesine", "mackolik") or not 0 <= (today - datetime.strptime(ymd, "%Y%m%d").date()).days <= a.gun:
                continue
            doc = _load(os.path.join(ARSIV, f))
            for r in (doc["maclar"] if kind == "mackolik" else list(doc["maclar"].values())):
                if kind == "iddaa":
                    sat = {"saat": r["kickoff"][11:16], "lig": r["league"], "ev": r["home"], "dep": r["away"], "kaynak": "iddaa-api",
                           "oran_turu": "kapanışa en yakın (iddaa API; Nesine'den ~%4 yüksek)" if r.get("kapanis_oncesi", True) else "KAPANIŞ SONRASI/CANLI (kullanma)",
                           "acilis": _flat(r["ilk"]), "kapanis": _flat(r["son"]), "sonuc": r.get("sonuc")}
                elif kind == "nesine":
                    sat = {"saat": r["kickoff"][11:16], "lig": r.get("league"), "ev": r["home"], "dep": r["away"], "kaynak": "nesine-bulten",
                           "oran_turu": "kapanışa en yakın (Nesine)", "acilis": _flat_nesine(r["ilk"]), "kapanis": _flat_nesine(r["son"]),
                           "sonuc": r.get("sonuc")}
                elif kind == "mackolik":
                    if not r["o"]["1"]:
                        continue
                    sat = {"saat": r["saat"], "lig": r["lig"], "ev": r["home"], "dep": r["away"], "kaynak": "mackolik",
                           "oran_turu": "Mackolik programı = Nesine oranı (son güncel; kapanış olduğu doğrulanmadı)", "acilis": None,
                           "kapanis": _flat_mackolik(r), "sonuc": {"ms": r["ms"], "iy": r["iy"], "kaynak": "mackolik"} if r["bitti"] and r["ms"] else None}
                else:
                    if r["spor"] != "F":
                        continue
                    sat = {"saat": r["saat"], "lig": r["lig"], "ev": r["home"], "dep": r["away"], "kaynak": "basılı-pdf",
                           "oran_turu": "program (basıldığı andaki, kapanış DEĞİL; iddaa oranı)", "acilis": _flat_pdf(r), "kapanis": None,
                           "sonuc": r.get("sonuc")}
                sat["tarih"] = ymd
                fh.write(json.dumps(sat, ensure_ascii=False) + "\n")
                n += 1
                ns += bool(sat["sonuc"])
    print(f"{yol}: {n} kayıt, {ns} sonuçlu")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = p.add_subparsers(dest="cmd", required=True)
    q = sp.add_parser("pdf-aktar", help="PDF bülten(ler)i arşive al: dosya verilmezse data/pdf_gelen/ klasöründekilerin yeni olanları")
    q.add_argument("pdf", nargs="?")
    q.add_argument("--baslangic", help="dosya adı GG-AA-YYYY ile başlamıyorsa ilk gün YYYY-MM-DD")
    q.set_defaults(f=cmd_pdf_aktar)
    q = sp.add_parser("pdf-liste", help="son N gün için gerekli PDF adları + adresleri (elle indirilecek) ve durumları")
    q.add_argument("--gun", type=int, default=30)
    q.set_defaults(f=cmd_pdf_liste)
    q = sp.add_parser("sonuc", help="arşivdeki maçlara Mackolik/Flashscore sonuçlarını (MS+İY) işle")
    q.add_argument("--gun", type=int, default=7)
    q.set_defaults(f=cmd_sonuc)
    q = sp.add_parser("kapsam", help="gün gün: hangi kaynakta kaç maç, kaçında sonuç var")
    q.add_argument("--gun", type=int, default=30)
    q.set_defaults(f=cmd_kapsam)
    q = sp.add_parser("disari", help="son N günün maç+oran+sonuç verisini JSONL'e aktar (geriye dönük test için)")
    q.add_argument("--gun", type=int, default=30)
    q.add_argument("--cikti")
    q.set_defaults(f=cmd_disari)
    q = sp.add_parser("temizle", help="N günden eski arşiv dosyalarını listele (--sil ile siler)")
    q.add_argument("--gun", type=int, default=35)
    q.add_argument("--sil", action="store_true")
    q.set_defaults(f=cmd_temizle)
    q = sp.add_parser("sablon-hafta", help="son N gün (Mackolik penceresi): oran tekrarı grubunda gerçek İY/MS sıklığı")
    q.add_argument("--gun", type=int, default=7)
    q.set_defaults(f=cmd_sablon_hafta)
    q = sp.add_parser("dogruskor", help="doğru skor oranı taktiği (2.5 Üst/KG Var): favori 2-1=7.00, 1-1~2-1, 0-0>10 (Nesine arşivi)")
    q.add_argument("--gun", type=int, default=7, help="kaç gün geriye (ileri günler her zaman dahil)")
    q.add_argument("--tol", type=float, default=0.5, help="'7.00' için yakınlık toleransı")
    q.add_argument("--yakin", type=float, default=1.0, help="1-1 ile 2-1 arasında izin verilen en büyük fark")
    q.set_defaults(f=cmd_dogruskor)
    q = sp.add_parser("ust45", help="4,5 Gol Üstü taktiği: 4.5 Üst 4.20-5.10, İY1&İY1.5Üst 5.40-6.20, 4-5 gol 2.80-3.10 (Nesine arşivi)")
    q.add_argument("--gun", type=int, default=7, help="kaç gün geriye (ileri günler her zaman dahil)")
    q.set_defaults(f=cmd_ust45)
    q = sp.add_parser("topla", help="günlük tek komut: kaydet + sonuc(7 gün) + temizle raporu")
    q.set_defaults(f=cmd_topla)
    q = sp.add_parser("sablon", help="oran tekrarı şablonunu arşivdeki bir günde tara")
    q.add_argument("tarih")
    q.set_defaults(f=cmd_sablon)
    sp.add_parser("kaydet").set_defaults(f=cmd_kaydet)
    q = sp.add_parser("ice-aktar")
    q.add_argument("dosya", nargs="*")
    q.set_defaults(f=cmd_ice_aktar)
    q = sp.add_parser("mackolik")
    q.add_argument("tarih")
    q.add_argument("--takim")
    q.add_argument("--lig")
    q.add_argument("--n", type=int, default=30)
    q.add_argument("--yenile", action="store_true")
    q.set_defaults(f=cmd_mackolik)
    q = sp.add_parser("goster")
    q.add_argument("tarih")
    q.add_argument("--takim")
    q.add_argument("--n", type=int, default=30)
    q.add_argument("--tam", action="store_true", help="tüm pazarları listele (İY/MS dahil)")
    q.add_argument("--sadece-arsiv", action="store_true", help="Mackolik'e gitme")
    q.set_defaults(f=cmd_goster)
    sp.add_parser("durum").set_defaults(f=cmd_durum)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
