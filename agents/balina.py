"""
BALINA (para akisi) + ORAN KAYMASI katmani - DEPLOY SURUMU (Render/Telegram botu icin).

Tez: balina parasi dogrudan gorunmez; gorunur olan "fiyatin paraya verdigi tepki".
Kazanc kaynagi dunya keskin pazari yeniden fiyatlarken NESINE'nin gec kalmasi = bayat cizgi.
Tam kural seti: skills/bwm-balina-akis/PROMPT.md

Bu dosya BAGIMSIZDIR: yalniz requests + stdlib + nesine.py kullanir (numpy/pandas yok),
cunku Render'da sadece bu depo var. Masaustundeki zengin surum (oddsapi.py'yi yeniden
kullanan) C:/Users/Tuffy/.claude/agents/balina.py'dir; ikisi ayni kurallari uygular.

  python balina.py tanila | kaydet | rapor | sinyal [--telegram]
  --radar  : yalniz T-6sa/T-2sa/T-60dk/T-30dk kademesindeki maclar (kademe yoksa 0 kredi)
  --butce N: bu calismada azami N kredi (lig basina 1)

Kredi: The Odds API ucretsiz plan 500/AY. /sports ve /events UCRETSIZ, /odds h2h x eu = 1 kredi/lig.
Gunluk tavan GUNLUK_TAVAN (12) -> en kotu 360/ay. Anahtar: env ODDS_API_KEY.

SINIRLAR: hacim/balina verisi yok (A ile D hucresi ayirt edilemez), Nesine ve dunya oranlari
farkli anlarda cekilir, Render'da kalici disk yok -> JSONL log gecicidir, kalici kayit Telegram'dir.
"""
import argparse
import difflib
import json
import math
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone, timedelta

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import nesine as N

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(SCRIPT_DIR, ".env"))
except Exception:
    pass
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TR = timezone(timedelta(hours=3))
LOG_DIR = os.path.join(SCRIPT_DIR, "data", "balina")
CACHE_DIR = os.path.join(LOG_DIR, "cache")
GONDERILDI = os.path.join(LOG_DIR, "gonderildi.json")
KOTA = os.path.join(LOG_DIR, "kota.json")

BASE = "https://api.the-odds-api.com/v4"
SHARP_W = {"pinnacle": 3.0, "betfair_ex_eu": 2.0, "matchbook": 1.5}
MIN_BOOKS, MIN_BOOKS_PIN = 4, 3
MAX_AGE = 4 * 3600                 # kitabin son guncellemesi bundan eskiyse sayilmaz
SPORTS_TTL, EVENTS_TTL, ODDS_TTL = 12 * 3600, 3600, 20 * 60
GUNLUK_TAVAN = 12                  # gunluk azami kredi (12 x 30 = 360/ay < 500)
RESERVE = 60                       # API kotasi bunun altina inerse odeme yapilmaz

RADAR = [(360, "T-6sa"), (120, "T-2sa"), (60, "T-60dk"), (30, "T-30dk")]
RADAR_TOL = 8
DP_ESIK, DP_SESSIZ = 2.0, 1.0      # anlamli kayma / "kipirdamadi" bandi (yuzde puan)
EV_ESIK, MIN_ORAN, MIN_P = 0.02, 1.30, 0.10
SEC = ["1", "X", "2"]
SEC_AD = {"1": "Ev sahibi kazanır (MS 1)", "X": "Beraberlik (MS X)", "2": "Deplasman kazanır (MS 2)"}
AY = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül",
      "Ekim", "Kasım", "Aralık"]
EK_TAKIM = {"fc", "sk", "ac", "as", "cf", "sc", "afc", "cd", "ca", "sv", "if", "fk", "bk", "the"}


# ------------------------------------------------------------------ altyapi
def _json_oku(yol, varsayilan):
    try:
        with open(yol, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return varsayilan


def _json_yaz(yol, veri):
    os.makedirs(os.path.dirname(yol), exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False)


def _anahtar():
    return (os.environ.get("ODDS_API_KEY") or "").strip()


def _get(path, **params):
    k = _anahtar()
    if not k:
        raise RuntimeError("ODDS_API_KEY yok")
    r = requests.get(BASE + path, params=dict(params, apiKey=k), timeout=30)
    rem = r.headers.get("x-requests-remaining")
    if rem is not None:
        _json_yaz(os.path.join(LOG_DIR, "api_kota.json"), {"kalan": float(rem), "ts": time.time()})
    if r.status_code != 200:
        raise RuntimeError("Odds API %s: %s" % (r.status_code, r.text[:100].replace(k, "***")))
    return r.json()


def _api_kalan():
    return _json_oku(os.path.join(LOG_DIR, "api_kota.json"), {}).get("kalan")


def _cache(ad, ttl, fn):
    os.makedirs(CACHE_DIR, exist_ok=True)
    p = os.path.join(CACHE_DIR, ad + ".json")
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        v = _json_oku(p, None)
        if v is not None:
            return v, True
    v = fn()
    _json_yaz(p, v)
    return v, False


def _norm(s):
    s = (s or "").replace("ı", "i").replace("İ", "i")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return " ".join(p for p in s.split() if p and p not in EK_TAKIM)


def _benzer(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    ta, tb = set(na.split()), set(nb.split())
    kapsama = len(ta & tb) / min(len(ta), len(tb))
    return max(difflib.SequenceMatcher(None, na, nb).ratio(), kapsama * 0.9)


def _shin(oranlar):
    """Shin (1993) vig arindirma (dixon_coles_model.shin_probs ile ayni; saf matematik)."""
    it = {k: v for k, v in oranlar.items() if v and v > 1.0}
    if len(it) < 2:
        return {}
    pi = {k: 1.0 / v for k, v in it.items()}
    s = sum(pi.values())

    def probs(z):
        return {k: (math.sqrt(z * z + 4 * (1 - z) * (p * p) / s) - z) / (2 * (1 - z))
                for k, p in pi.items()}
    lo, hi = 0.0, 0.4
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(probs(mid).values()) > 1:
            lo = mid
        else:
            hi = mid
    p = probs((lo + hi) / 2)
    t = sum(p.values())
    return {k: v / t for k, v in p.items()}


def _devig(oranlar):
    sp = _shin(oranlar)
    if sp:
        return sp
    s = sum(1 / v for v in oranlar.values() if v > 1)
    return {k: (1 / v) / s for k, v in oranlar.items() if v > 1} if s else {}


# ------------------------------------------------------------------ Nesine
def nesine_1x2(saat=6.0, alt_dk=20):
    """Pencere T-20dk .. T-6sa: erken cekim kredi yakar, gec cekim oynanamaz."""
    veri, taze = N.bulten()
    simdi = time.time()
    out = []
    for e in veri["olaylar"]:
        if not (simdi + alt_dk * 60 < e["esd"] < simdi + saat * 3600):
            continue
        o = N._mk(e, 1)
        if len(o) < 3:
            continue
        ks = sorted(o, key=lambda k: int(k) if str(k).isdigit() else 99)[:3]
        oran = [o[k] for k in ks]
        if any((not x or x <= 1.0) for x in oran):
            continue
        out.append({"ts": e["esd"], "esd_ms": e["esd_ms"], "home": e["hn"], "away": e["an"],
                    "lig": e["lig"], "oran": oran})
    return out, taze


# ------------------------------------------------------------------ dunya
def _sports():
    sp, _ = _cache("sports", SPORTS_TTL, lambda: _get("/sports/"))
    return [s["key"] for s in sp if s.get("group") == "Soccer" and s.get("active")
            and "women" not in s["key"]]


def _events(sport):
    ev, _ = _cache("ev_" + sport, EVENTS_TTL,
                   lambda: _get("/sports/%s/events" % sport, dateFormat="unix"))
    return [{"id": e["id"], "ts": e["commence_time"], "home": e["home_team"],
             "away": e["away_team"]} for e in ev]


def _odds(sport):
    return _cache("od_" + sport, ODDS_TTL, lambda: _get(
        "/sports/%s/odds" % sport, regions="eu", markets="h2h",
        oddsFormat="decimal", dateFormat="unix"))


def _esle(m, events):
    """Kalkis +-20 dk; iki takim >= 0.72 ya da +-5 dk icinde biri >= 0.85 digeri >= 0.35."""
    best = None
    for ev in events:
        dt = abs(ev["ts"] - m["ts"])
        if dt > 20 * 60:
            continue
        sh, sa = _benzer(m["home"], ev["home"]), _benzer(m["away"], ev["away"])
        lo, hi = min(sh, sa), max(sh, sa)
        if (lo >= 0.72 or (dt <= 300 and hi >= 0.85 and lo >= 0.35)) and (best is None or lo + hi > best[0]):
            best = (lo + hi, ev)
    return best[1] if best else None


def _konsensus(ev, now=None):
    """-> {'1'/'X'/'2': {p, disp, n, pin_p}} (yeterli kitap yoksa {})."""
    now = now or time.time()
    coll = {}
    for bk in ev.get("bookmakers", []):
        if now - bk.get("last_update", now) > MAX_AGE:
            continue
        w = SHARP_W.get(bk["key"], 1.0)
        for mk in bk.get("markets", []):
            if mk.get("key") != "h2h":
                continue
            od = {}
            for o in mk.get("outcomes", []):
                k = ("1" if o["name"] == ev.get("home_team") else
                     "2" if o["name"] == ev.get("away_team") else
                     "X" if o["name"] == "Draw" else None)
                if k:
                    od[k] = o["price"]
            if set(od) == {"1", "X", "2"}:
                for k, p in _devig(od).items():
                    coll.setdefault(k, []).append((w, p, bk["key"]))
    out = {}
    for k, arr in coll.items():
        n, pin = len(arr), any(b == "pinnacle" for _, _, b in arr)
        if not (n >= MIN_BOOKS or (pin and n >= MIN_BOOKS_PIN)):
            continue
        tw = sum(w for w, _, _ in arr)
        mu = sum(w * p for w, p, _ in arr) / tw
        sd = math.sqrt(sum(w * (p - mu) ** 2 for w, p, _ in arr) / tw)
        out[k] = {"p": mu, "disp": sd, "n": n,
                  "pin_p": next((p for _, p, b in arr if b == "pinnacle"), None)}
    return out if len(out) == 3 else {}


def dunya(maclar, max_lig=6, butce=3):
    bilgi = {"lig": [], "maliyet": 0, "atlanan": None}
    if not _anahtar():
        bilgi["atlanan"] = "ODDS_API_KEY yok"
        return {}, bilgi
    try:
        sportlar = _sports()
    except Exception as ex:
        bilgi["atlanan"] = "sports: %s" % ex
        return {}, bilgi
    eslesme, sayac = {}, {}
    for sp in sportlar:
        try:
            evs = _events(sp)
        except Exception:
            continue
        for i, m in enumerate(maclar):
            if i in eslesme:
                continue
            ev = _esle(m, evs)
            if ev:
                eslesme[i] = (sp, ev["id"])
                sayac[sp] = sayac.get(sp, 0) + 1
    if not eslesme:
        bilgi["atlanan"] = "dunya bulteniyle eslesen mac yok"
        return {}, bilgi
    sirali = [s for s, _ in sorted(sayac.items(), key=lambda x: -x[1])][:max_lig]
    taze = [s for s in sirali if os.path.exists(os.path.join(CACHE_DIR, "od_%s.json" % s))
            and time.time() - os.path.getmtime(os.path.join(CACHE_DIR, "od_%s.json" % s)) < ODDS_TTL]
    gun = datetime.now(TR).strftime("%Y%m%d")
    kota = _json_oku(KOTA, {})
    kalan_gunluk = max(0, GUNLUK_TAVAN - kota.get(gun, 0))
    api_kalan = _api_kalan()
    if api_kalan is not None and api_kalan < RESERVE:
        kalan_gunluk = 0
        bilgi["atlanan"] = "API kotasi rezervin altinda (%s)" % api_kalan
    ucretli = [s for s in sirali if s not in taze][:max(0, min(butce, kalan_gunluk))]
    ligler = taze + ucretli
    bilgi["lig"] = ["%s(%d)%s" % (s, sayac[s], "" if s in ucretli else "*") for s in ligler]
    bilgi["maliyet"] = len(ucretli)
    if not ligler:
        bilgi["atlanan"] = (bilgi.get("atlanan") or "gunluk kredi tavani doldu")
        return {}, bilgi
    ham = {}
    for sp in ligler:
        try:
            veri, _ = _odds(sp)
        except Exception as ex:
            bilgi["atlanan"] = "%s: %s" % (sp, ex)
            continue
        for e in veri:
            ham[e["id"]] = e
    if ucretli:
        kota[gun] = kota.get(gun, 0) + len(ucretli)
        _json_yaz(KOTA, {k: v for k, v in kota.items() if k >= gun})
    bilgi["bugun"] = kota.get(gun, 0)
    cikti = {}
    for i, (sp, eid) in eslesme.items():
        e = ham.get(eid)
        if e:
            c = _konsensus(e)
            if c:
                cikti[i] = c
    return cikti, bilgi


# ------------------------------------------------------------------ karar
def _kademe(dk_kala):
    for dk, ad in RADAR:
        if abs(dk_kala - dk) <= RADAR_TOL:
            return ad
    return None


def _hucre(dp_w, dp_n, hacim="YOK"):
    """A = bilgili para + bayat Nesine | B = fiyat gitti | C = emilen para | D = hacimsiz hareket."""
    para = hacim in ("YUKSEK", "ASIRI")
    if dp_w >= DP_ESIK and abs(dp_n) < DP_SESSIZ:
        return "A" if (para or hacim == "YOK") else "D"
    if dp_w >= DP_ESIK and dp_n >= DP_SESSIZ:
        return "B"
    if para and abs(dp_w) < DP_SESSIZ:
        return "C"
    return "D" if dp_w >= DP_ESIK else "-"


def _gun_dosyasi():
    os.makedirs(LOG_DIR, exist_ok=True)
    return os.path.join(LOG_DIR, "balina_%s.jsonl" % datetime.now(TR).strftime("%Y%m%d"))


def kaydet(max_lig=6, butce=3, saat=6.0, radar=False, sessiz=False):
    maclar, taze = nesine_1x2(saat)
    simdi = time.time()
    for m in maclar:
        m["kademe"] = _kademe((m["ts"] - simdi) / 60.0)
    if radar:
        maclar = [m for m in maclar if m["kademe"]]
        if not maclar:
            if not sessiz:
                print("radar bos: kademe penceresinde mac yok, kredi harcanmadi")
            return []
    d, bilgi = dunya(maclar, max_lig, butce) if maclar else ({}, {"atlanan": "mac yok"})
    yol = _gun_dosyasi()
    temel = {}
    if os.path.exists(yol):
        with open(yol, encoding="utf-8") as f:
            for s in f:
                try:
                    k = json.loads(s)
                except Exception:
                    continue
                temel.setdefault(k["anahtar"], k)
    satir, sinyal = [], []
    for i, m in enumerate(maclar):
        pn = _devig({"1": m["oran"][0], "X": m["oran"][1], "2": m["oran"][2]})
        if len(pn) != 3:
            continue
        marj = sum(1.0 / o for o in m["oran"]) - 1.0
        c = d.get(i)
        for j, sec in enumerate(SEC):
            ak = "%s|%s|%s|%s" % (m["esd_ms"], _norm(m["home"]), _norm(m["away"]), sec)
            cc = c.get(sec) if c else None
            p_w = cc["p"] if cc else None
            t = temel.get(ak)
            dp_w = (p_w - t["p_w"]) * 100 if (p_w is not None and t and t.get("p_w") is not None) else 0.0
            dp_n = (pn[sec] - t["p_n"]) * 100 if (t and t.get("p_n") is not None) else 0.0
            hucre = _hucre(dp_w, dp_n) if p_w is not None else "-"
            ev = (p_w * m["oran"][j] - 1.0) if p_w is not None else None
            k = {"ts": simdi, "anahtar": ak, "esd_ms": m["esd_ms"], "hn": m["home"], "an": m["away"],
                 "lig": m["lig"], "sec": sec, "oran_n": m["oran"][j], "p_n": round(pn[sec], 5),
                 "p_w": round(p_w, 5) if p_w is not None else None,
                 "n_kitap": cc["n"] if cc else 0, "marj_n": round(marj, 4),
                 "dp_w": round(dp_w, 2), "dp_n": round(dp_n, 2), "hucre": hucre,
                 "ev": round(ev, 4) if ev is not None else None, "hacim": "YOK",
                 "dk_kala": int((m["ts"] - simdi) / 60), "kademe": m.get("kademe")}
            satir.append(k)
            if (hucre == "A" and ev is not None and ev >= EV_ESIK
                    and m["oran"][j] >= MIN_ORAN and p_w >= MIN_P):
                sinyal.append(k)
    try:
        with open(yol, "a", encoding="utf-8") as f:
            for k in satir:
                f.write(json.dumps(k, ensure_ascii=False) + "\n")
    except Exception:
        pass            # Render'da disk kalici degil; log yazilamazsa sinyal yine gider
    if not sessiz:
        print("kayit: %d satir / %d mac | konsensus: %d | A-sinyal: %d" %
              (len(satir), len(maclar), len(d), len(sinyal)))
        print("ligler: %s (* bedava) | bugun harcanan: %s/%s | atlanan: %s" %
              (", ".join(bilgi.get("lig") or []), bilgi.get("bugun", 0), GUNLUK_TAVAN,
               bilgi.get("atlanan")))
        if not taze:
            print("UYARI: Nesine bulteni onbellekten")
    return sinyal


# ------------------------------------------------------------------ mesaj
def _mesaj(k):
    d = datetime.fromtimestamp(k["esd_ms"] / 1000, TR)
    tarih = "%d %s %02d:%02d" % (d.day, AY[d.month - 1], d.hour, d.minute)
    dk = k["dk_kala"]
    sure = ("%d saat %d dakika" % (dk // 60, dk % 60)) if dk >= 60 else ("%d dakika" % dk)
    onceki = k["p_w"] * 100 - k["dp_w"]
    adil = round(1.0 / k["p_w"], 2)
    s = [
        "\U0001F40B BALİNA SİNYALİ",
        "%s - %s" % (k["hn"], k["an"]),
        "%s \u00b7 %s" % (k["lig"] or "?", tarih),
        "",
        "\u2705 OYNA: %s @ %.2f (Nesine)" % (SEC_AD.get(k["sec"], k["sec"]), k["oran_n"]),
        "",
        "NEDEN:",
        "1) Dünya piyasası bu tarafa para bastı: gerçek şans %%%.1f -> %%%.1f (+%.1f puan, %d bahis şirketi)"
        % (onceki, k["p_w"] * 100, k["dp_w"], k["n_kitap"]),
        "2) Nesine fiyatı kıpırdamadı (%+.1f puan) -> geç kaldı, oran hâlâ eski" % k["dp_n"],
        "3) Bu oran dünyanın adil fiyatından (%.2f) %%%.1f yüksek = DEĞER" % (adil, (k["ev"] or 0) * 100),
        "",
        "\u23f1 SÜRE: Maça %s var. BEKLEME, şimdi oyna - bu fark genelde birkaç saatte kapanır." % sure,
        "Son geçerlilik: maça 20 dakika kalana kadar. Nesine oranı %.2f altına düşerse iptal et." % adil,
        "",
        "\u26a0 Test aşaması (hacim onayı yok): en fazla 0,25 birim.",
    ]
    return "\n".join(s)


def telegram_gonder(metinler, token=None, chat_id=None):
    tok = (token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (chat_id or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not (tok and chat):
        return "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID yok -> gonderilmedi"
    ok = 0
    for m in metinler:
        try:
            if requests.post("https://api.telegram.org/bot%s/sendMessage" % tok,
                             json={"chat_id": chat, "text": m}, timeout=20).ok:
                ok += 1
        except Exception:
            pass
    return "telegram: %d/%d gonderildi" % (ok, len(metinler))


def yeni_sinyaller(max_lig=6, butce=3, saat=6.0, radar=True):
    """Bot icin: daha once gonderilmemis A-hucresi mesajlari (liste)."""
    gond = set(_json_oku(GONDERILDI, []))
    out = []
    for k in kaydet(max_lig, butce, saat, radar, sessiz=True):
        if k["anahtar"] in gond:
            continue
        out.append(_mesaj(k))
        gond.add(k["anahtar"])
    if out:
        _json_yaz(GONDERILDI, sorted(gond))
    return out


# ------------------------------------------------------------------ rapor
def _kayitlar(gun=None, hepsi=False):
    if not os.path.isdir(LOG_DIR):
        return []
    ds = sorted(d for d in os.listdir(LOG_DIR) if d.startswith("balina_"))
    ds = [d for d in ds if gun in d] if gun else (ds if hepsi else ds[-1:])
    out = []
    for d in ds:
        with open(os.path.join(LOG_DIR, d), encoding="utf-8") as f:
            for s in f:
                try:
                    out.append(json.loads(s))
                except Exception:
                    pass
    return out


def rapor(gun=None, hepsi=False, yazdir=True):
    kayit = _kayitlar(gun, hepsi)
    if not kayit:
        return "balina kaydi yok (once: balina.py kaydet)"
    son = {}
    for k in kayit:
        son[k["anahtar"]] = k
    sayim = {}
    for k in son.values():
        sayim[k["hucre"]] = sayim.get(k["hucre"], 0) + 1
    dl = [k for k in son.values() if k.get("p_w") is not None]
    sat = ["kayit %d satir | secenek %d | tur %d | dunya verisi %d" %
           (len(kayit), len(son), len(set(k["ts"] for k in kayit)), len(dl)),
           "hucre: " + ", ".join("%s=%d" % x for x in sorted(sayim.items()))]
    if dl:
        sat.append("Nesine EV ort %+.1f%% | pozitif %d/%d | marj ort %.1f%%" %
                   (100 * sum(k["ev"] for k in dl) / len(dl),
                    sum(1 for k in dl if k["ev"] > 0), len(dl),
                    100 * sum(k["marj_n"] for k in dl) / len(dl)))
    ilginc = sorted([k for k in son.values() if k["hucre"] in ("A", "B", "C", "D")],
                    key=lambda k: -(k.get("ev") if k.get("ev") is not None else -9))[:20]
    for k in ilginc:
        d = datetime.fromtimestamp(k["esd_ms"] / 1000, TR).strftime("%d.%m %H:%M")
        sat.append("[%s] %s-%s %s %s @%.2f | dp_w %+.1f dp_n %+.1f | EV %+.1f%%" %
                   (k["hucre"], k["hn"], k["an"], d, k["sec"], k["oran_n"],
                    k["dp_w"], k["dp_n"], (k["ev"] or 0) * 100))
    if not ilginc:
        sat.append("A-D hucresine giren satir yok (dunya fiyati >= %.0f puan kaymadan sinyal olmaz)" % DP_ESIK)
    m = "\n".join(sat)
    if yazdir:
        print(m)
    return m


def tanila(yazdir=True):
    sat = ["--- balina tanilama ---"]
    try:
        maclar, taze = nesine_1x2()
        sat.append("Nesine (T-20dk..T-6sa): %d mac, taze=%s" % (len(maclar), taze))
    except Exception as ex:
        maclar = []
        sat.append("Nesine HATA: %s" % ex)
    sat.append("ODDS_API_KEY: %s | API kalan: %s" % ("var" if _anahtar() else "YOK", _api_kalan()))
    gun = datetime.now(TR).strftime("%Y%m%d")
    sat.append("bugun harcanan kredi: %s/%s" % (_json_oku(KOTA, {}).get(gun, 0), GUNLUK_TAVAN))
    if maclar:
        sat.append("radar kademesindeki mac: %d" %
                   sum(1 for m in maclar if _kademe((m["ts"] - time.time()) / 60.0)))
    m = "\n".join(sat)
    if yazdir:
        print(m)
    return m


def main(argv=None):
    p = argparse.ArgumentParser(prog="balina.py")
    p.add_argument("komut", nargs="?", default="rapor",
                   choices=["kaydet", "rapor", "sinyal", "tanila"])
    p.add_argument("--lig", type=int, default=6)
    p.add_argument("--butce", type=int, default=3)
    p.add_argument("--saat", type=float, default=6.0)
    p.add_argument("--radar", action="store_true")
    p.add_argument("--gun")
    p.add_argument("--hepsi", action="store_true")
    p.add_argument("--telegram", action="store_true")
    a = p.parse_args(argv)
    if a.komut == "kaydet":
        kaydet(a.lig, a.butce, a.saat, a.radar)
    elif a.komut == "rapor":
        rapor(a.gun, a.hepsi)
    elif a.komut == "sinyal":
        m = yeni_sinyaller(a.lig, a.butce, a.saat, a.radar)
        print("\n\n".join(m) if m else "yeni A-hucresi sinyali yok")
        if a.telegram and m:
            print(telegram_gonder(m))
    else:
        tanila()


if __name__ == "__main__":
    main(sys.argv[1:])
