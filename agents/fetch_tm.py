"""
Transfermarkt katmani: takimlarin sakat/cezali oyunculari ve hakemin kart ortalamasi.

Kaynak: transfermarkt.com herkese acik sayfalari (resmi API degil; nazik hizda, en cok 4 es zamanli istek, yerel onbellek).
Kitle kaynaklidir: alt liglerde eksik/gecikmeli olabilir. Isim eslesmesi benzerlik esigiyle yapilir ve raporda eslesen
ad gosterilir (yanlis takim/hakem riski gorunur olsun diye). Bot korumasi asilmaz; 403 gelirse sessizce veri yok sayilir.

Onbellek: data/tm_cache.json  (takim/hakem kimligi kalici, sakatlik 6 saat, hakem istatistigi 3 gun)
"""
import os
import re
import sys
import json
import time
import threading
from datetime import datetime

import requests

from dixon_coles_model import _similarity

sys.stdout.reconfigure(encoding="utf-8")

TM = "https://www.transfermarkt.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
CLUB_SIM = 0.60
INJ_TTL, REF_TTL = 6 * 3600, 3 * 86400
MIN_REF_GAMES = 8
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
CACHE_PATH = os.path.join(DATA_DIR, "tm_cache.json")
_lock = threading.Lock()
_gate = threading.Semaphore(4)
_state = {"session": None, "cache": None, "blocked": False}


def _session():
    if _state["session"] is None:
        s = requests.Session()
        s.headers.update(HEADERS)
        _state["session"] = s
    return _state["session"]


def cache():
    if _state["cache"] is None:
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                _state["cache"] = json.load(f)
        except Exception:
            _state["cache"] = {}
        for k in ("club", "ref", "inj", "refstat"):
            _state["cache"].setdefault(k, {})
    return _state["cache"]


def save():
    if _state["cache"] is not None:
        os.makedirs(DATA_DIR, exist_ok=True)
        with _lock, open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_state["cache"], f, ensure_ascii=False)


def _get(path):
    if _state["blocked"]:
        return ""
    with _gate:
        try:
            r = _session().get(TM + path, timeout=20)
        except Exception:
            return ""
    if r.status_code in (403, 429):
        _state["blocked"] = True   # nazik ol: engellenirse bu calistirmada tekrar deneme
        return ""
    return r.text if r.status_code == 200 else ""


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).replace("&nbsp;", " ").strip()


def _ascii(s):
    import unicodedata
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")


def _norm_key(s):
    return re.sub(r"[^a-z0-9]+", " ", _ascii(s).lower()).strip()


_YOUTH = re.compile(r"\b(u\s?\d{2}|amateurs?|youth|young|jong|reserves?|ii|iii|b|women|frauen|fem|feminin|juniors?|academy|aka|sub\s?\d+|next generation)\b")


def _is_youth(cand, query):
    q = set(_norm_key(query).split())
    return any(m.group(0).replace(" ", "") not in {x.replace(" ", "") for x in q} for m in _YOUTH.finditer(_norm_key(cand)))


def _club_score(query, cand):
    """Benzerlik; ayni ilk sozcuk + gencler/rezerv degil ise en az 0.65 (TM Ingilizce adlar: Wien->Vienna)."""
    q = _ascii(query).replace("St ", "Saint ").replace("St. ", "Saint ")
    sc = min(_similarity(q, _ascii(cand)), 0.70)   # alt dize benzerligi (Angers ~ Rangers) tek basina guvenilmez
    qt, ct = _norm_key(q).split(), _norm_key(cand).split()
    if qt and set(qt) <= set(ct):                   # sorgunun tum sozcukleri adayda tam sozcuk olarak var (Angers ⊂ Angers SCO)
        sc = max(sc, 0.90 - 0.02 * (len(ct) - len(qt)))
    elif qt and ct and qt[0] == ct[0] and len(qt[0]) >= 4:
        sc = max(sc, 0.65)
    return sc


# ------------------------------------------------------------------ takim
def find_club(name):
    """iddaa/Flashscore takim adi -> {id, slug, name} (benzerlik >= CLUB_SIM) ya da None."""
    c = cache()["club"]
    key = _norm_key(name)
    if key in c:
        return c[key]
    best = None
    tokens = sorted(re.findall(r"[^\W\d_]{4,}", _ascii(name)), key=len, reverse=True)
    queries = [name]
    if re.search(r"\bSt\.?\s", name):
        queries.append(re.sub(r"\bSt\.?\s", "Saint ", name))
    queries += tokens[:1]
    for q in queries:
        html = _get("/schnellsuche/ergebnis/schnellsuche?query=" + requests.utils.quote(_ascii(q)))
        seen = set()
        for m in re.finditer(r'<a\s[^>]*?href="/([a-z0-9\-]+)/startseite/verein/(\d+)"[^>]*>', html):
            t = re.search(r'title="([^"]+)"', m.group(0))
            if not t or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            if _is_youth(t.group(1), name):
                continue
            sc = _club_score(name, t.group(1))
            if sc >= CLUB_SIM and (not best or sc > best[0] + 0.05):   # esitlikte arama sirasi (daha erken) kazanir
                best = (sc, {"id": m.group(2), "slug": m.group(1), "name": t.group(1)})
        if best:
            break
    with _lock:
        c[key] = best[1] if best else None
    return c[key]


def _dedupe(rows):
    """Ayni oyuncu birden fazla sakatlik satiriyla gelebilir (Injuries + Suspensions): ilki tutulur."""
    seen, out = set(), []
    for r in rows or []:
        if r["name"] not in seen:
            seen.add(r["name"]); out.append(r)
    return out


def injuries(club):
    """Sakat/cezali listesi: [{name, pos, reason, since, ret, missed, kind}]; onbellekli (6 saat)."""
    if not club:
        return None
    inj = cache()["inj"]
    hit = inj.get(club["id"])
    if hit and time.time() - hit["ts"] < INJ_TTL:
        return _dedupe(hit["rows"])
    html = _get(f"/{club['slug']}/sperrenundverletzungen/verein/{club['id']}")
    if not html:
        return hit["rows"] if hit else None
    tb = re.search(r'<table class="items">(.*)', html, flags=re.S)
    rows, kind = [], "Injuries"
    if tb:
        body = tb.group(1).split("</table>\n", 1)[0] if False else tb.group(1)
        marks = [(m.start(), "H", _text(m.group(1))) for m in re.finditer(r'<td class="extrarow[^"]*" colspan="\d+">(.*?)</td>', body, flags=re.S)]
        marks += [(m.start(), "R", None) for m in re.finditer(r'<tr class="(?:odd|even)">', body)]
        marks.sort()
        for i, (pos, typ, val) in enumerate(marks):
            if typ == "H":
                kind = val or kind
                continue
            chunk = body[pos: marks[i + 1][0] if i + 1 < len(marks) else len(body)]
            nm = re.search(r'<td class="hauptlink">\s*<a title="([^"]+)"', chunk)
            if not nm:
                continue
            pos_ = re.search(r"</tr>\s*<tr>\s*<td>([^<]+)</td>", chunk)
            z = [_text(x) for x in re.findall(r'<td class="zentriert[^"]*"[^>]*>(.*?)</td>', chunk, flags=re.S)]
            rs_ = re.search(r'<td class="links hauptlink[^"]*">(.*?)</td>', chunk, flags=re.S)
            dates = [x for x in z if re.match(r"^\d{2}/\d{2}/\d{4}$", x)]
            rows.append({"name": nm.group(1), "pos": pos_.group(1).strip() if pos_ else "", "reason": _text(rs_.group(1)) if rs_ else "",
                         "since": dates[0] if dates else "", "ret": dates[1] if len(dates) > 1 else "",
                         "missed": z[-1] if z and z[-1].isdigit() else "", "kind": kind})
    rows = _dedupe(rows)
    with _lock:
        inj[club["id"]] = {"ts": time.time(), "rows": rows}
    return rows


# ------------------------------------------------------------------ hakem
def _ref_key_parts(fs_name):
    """'van der Laan J.' -> ('van der laan', 'j')  (Flashscore: soyad + bas harf)"""
    m = re.match(r"^(.*?)[\s,]+([A-Z])\.?\s*$", (fs_name or "").strip())
    if m:
        return _norm_key(m.group(1)), m.group(2).lower()
    return _norm_key(fs_name), ""


def find_referee(fs_name):
    if not fs_name:
        return None
    r = cache()["ref"]
    key = _norm_key(fs_name)
    if key in r:
        return r[key]
    sur, ini = _ref_key_parts(fs_name)
    found = None
    if sur:
        html = _get("/schnellsuche/ergebnis/schnellsuche?query=" + requests.utils.quote(sur))
        html = html or ""
        cands = []
        for m in re.finditer(r'<a\s[^>]*?href="/([a-z0-9\-]+)/profil/schiedsrichter/(\d+)"[^>]*>', html):
            slug = m.group(1)
            parts = slug.replace("-", " ")
            if sur in parts and (not ini or parts.startswith(ini)):
                t = re.search(r'title="([^"]+)"', m.group(0))
                cands.append({"id": m.group(2), "slug": slug, "name": t.group(1) if t else slug})
        uniq = {c["id"]: c for c in cands}
        if len(uniq) == 1:   # birden fazla aday = belirsiz -> kullanma (yanlis hakem riski)
            found = next(iter(uniq.values()))
    with _lock:
        r[key] = found
    return found


def referee_stats(ref):
    """Son 2 sezon: {n, yc, rc, pen} mac basina; yeterli mac (>= MIN_REF_GAMES) yoksa None."""
    if not ref:
        return None
    rs = cache()["refstat"]
    hit = rs.get(ref["id"])
    if hit and time.time() - hit["ts"] < REF_TTL:
        return hit["v"]
    now = datetime.now()
    sy = now.year if now.month >= 7 else now.year - 1
    tot = [0, 0, 0, 0, 0]
    ok = False
    for season in (sy, sy - 1):
        html = _get(f"/{ref['slug']}/profil/schiedsrichter/{ref['id']}/saison_id/{season}")
        tb = re.search(r'<table class="items">(.*?)</table>', html, flags=re.S)
        if not tb:
            continue
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", tb.group(1), flags=re.S)[1:]:
            nums = re.findall(r"\d+", _text(row))
            if len(nums) >= 5 and not re.search(r"[A-Za-z]", re.sub(r"[\d\s]", "", _text(row))):   # toplam satiri (adsiz)
                for i in range(5):
                    tot[i] += int(nums[i])
                ok = True
                break
    v = None
    if ok and tot[0] >= MIN_REF_GAMES:
        n = tot[0]
        v = {"n": n, "yc": tot[1] / n, "rc": (tot[2] + tot[3]) / n, "pen": tot[4] / n}
    with _lock:
        rs[ref["id"]] = {"ts": time.time(), "v": v}
    return v


# ------------------------------------------------------------------ birlesik
def for_match(home, away, referee_name=None):
    """{home:{club,inj}, away:{club,inj}, ref:{name,stats}} - hicbir sey bulunamazsa alanlar None."""
    ch, ca = find_club(home), find_club(away)
    rf = find_referee(referee_name)
    return {"home": {"club": ch, "inj": injuries(ch)}, "away": {"club": ca, "inj": injuries(ca)},
            "ref": {"info": rf, "stats": referee_stats(rf)} if rf else None}


def summary_line(tm, ht, at):
    """Kunyede tek satir: sakat/cezali + hakem."""
    if not tm:
        return None
    parts = []
    for side, nm in (("home", ht), ("away", at)):
        d = tm[side]
        if not d["club"]:
            parts.append(f"{nm}: TM eşleşmedi")
            continue
        rows = d["inj"]
        if rows is None:
            parts.append(f"{nm}: liste alınamadı")
        elif not rows:
            parts.append(f"{nm}: eksik yok")
        else:
            shown = "; ".join(f"{r['name']} ({r['pos'] or '?'}, {r['reason'] or r['kind']}"
                              + (f", dönüş {r['ret']}" if r["ret"] else "") + ")" for r in rows[:4])
            parts.append(f"{nm}: {len(rows)} eksik — {shown}" + (" …" if len(rows) > 4 else ""))
    return " | ".join(parts)


def missing_count(tm, side):
    if not tm or not tm[side]["inj"]:
        return 0
    return len(tm[side]["inj"])
