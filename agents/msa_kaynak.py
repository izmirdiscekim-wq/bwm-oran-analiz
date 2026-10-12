# -*- coding: utf-8 -*-
"""
MSA_KAYNAK - multi_sport_analyzer'in VERI katmani (saf Python: requests + stdlib).

Ne getirir (hepsi ucretsiz, kimlik gerektirmez):
  - iddaa: gunun maclari, pazarlar, takimin son 10 maci (skor + TARIH + turnuva)
  - NHL resmi API: kaleci sezon istatistigi (savePctg, gamesPlayed)  -> yalniz NHL
  - yerel dosyalar: kaleci durumu, tenis servis/karsilama (kullanici ya da bir ileri araç doldurur)

Ne GETIRMEZ (olculdu 07.10.2026, uydurma yok):
  - Kaleci ONAYI: NHL API yalniz sezon liderlerini verir, "bu gece baslayacak" bilgisi yok.
    Onay yoksa durum 'belirsiz' kalir ve model kaleci katsayisi UYGULAMAZ (risk yukselir).
  - Tenis servis/karsilama verimliligi ve zemin gecmisi: Sackmann deposu GitHub'dan kalkmis (404),
    tennis-data.co.uk 404. Zemin yuzdesi iddaa'nin son-10-mac verisinden + turnuva->zemin tablosundan
    TURETILIR (zayif, orneklem kucuk); servis/karsilama dosyadan okunur, yoksa uygulanmaz.
"""
import io
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta

import requests

import fetch_iddaa as F

TR = timezone(timedelta(hours=3))
BASE = os.path.dirname(os.path.abspath(__file__))
VERI = os.path.join(BASE, "data", "multi")
EVENTS_T = "https://sportsbookv2.iddaa.com/sportsbook/events?st={}&type=0&version=0&live=true"
DETAIL_URL = "https://sportsbookv2.iddaa.com/sportsbook/event/{}"
STATS_T = "https://statisticsv2.iddaa.com/statistics/{}/recent-matches/{}?matchHistoryType={}&isOnlyCurrentTournament=false"
NHL_SCHEDULE = "https://api-web.nhle.com/v1/schedule/now"
NHL_LANDING = "https://api-web.nhle.com/v1/gamecenter/{}/landing"
SON_MAC = 10                       # iddaa'nin verdigi pencere
ILK_ORAN_OMUR = 3 * 86400          # ilk-gorulen oran kaydi bu sureden sonra budanir

_TR_FOLD = str.maketrans({"ş": "s", "Ş": "s", "ı": "i", "İ": "i", "ç": "c", "Ç": "c",
                          "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "ğ": "g", "Ğ": "g"})


def fold(s):
    """Kucuk harf + Turkce karakterleri ASCII'ye indir (adlari eslestirmek icin)."""
    return re.sub(r"\s+", " ", (s or "").translate(_TR_FOLD).lower()).strip()


# ---------------------------------------------------------------- yerel JSON
def _oku(yol, varsayilan):
    try:
        with io.open(yol, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return varsayilan


def _yaz(yol, veri):
    try:
        os.makedirs(os.path.dirname(yol), exist_ok=True)
        with io.open(yol, "w", encoding="utf-8") as f:
            json.dump(veri, f, ensure_ascii=False, separators=(",", ":"))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- iddaa
def gunluk_maclar(session, st, comps, saat=12.0, n=30, min_pazar=1):
    """Baslamamis maclar (simdi .. simdi+saat). -> [{id, ts, home, away, league, nm}] kalkis sirali.
    min_pazar: iddaa'nin acmadigi (pazarsiz) maclari eler."""
    raw = F._get_json(session, EVENTS_T.format(st))
    live = set((raw.get("data", {}).get("sc") or {}).keys())
    simdi = time.time()
    out = []
    for e in raw.get("data", {}).get("events", []) or []:
        if not e.get("d") or str(e.get("i")) in live or not (simdi < e["d"] <= simdi + saat * 3600):
            continue
        if len(e.get("m") or []) < min_pazar:
            continue
        out.append({"id": str(e["i"]), "ts": e["d"], "home": (e.get("hn") or "?").strip(),
                    "away": (e.get("an") or "?").strip(), "league": comps.get(e.get("ci"), ""),
                    "nm": len(e.get("m") or [])})
    out.sort(key=lambda x: x["ts"])
    return out[:n]


def pazarlar(session, cfg, eid):
    """Mac detayi -> [(pazar_adi, sov, {secenek: oran})]."""
    d = F._get_json(session, DETAIL_URL.format(eid))["data"]
    out = []
    for m in d.get("m", []):
        ad = F._resolve_market_name(m.get("t"), m.get("st"), m.get("sov"), cfg)
        outs = {}
        for o in m.get("o", []):
            try:
                v = float(o.get("odd", 0))
            except (TypeError, ValueError):
                v = 0.0
            if o.get("n") not in (None, "") and v > 0:
                outs[str(o["n"])] = v
        if outs:
            out.append((ad, m.get("sov"), outs))
    return out


def son_maclar(session, yol, eid, tip):
    """iddaa recent-matches (tip 0 = ev sahibi, 1 = deplasman). -> ham mac listesi."""
    try:
        r = session.get(STATS_T.format(yol, eid, tip), timeout=15)
        return ((r.json().get("data") or {}).get("m") or []) if r.status_code == 200 else []
    except Exception:
        return []


def oyunlar(ham):
    """Ham liste -> [{ts, pf, pa, lig, sets_f, sets_a, ad}] (bitmis, ciftler HARIC), yeniden eskiye.

    Takim adi bu listede tutarsiz yazilabilir ('Tomic B.' / 'Tomic, Bernard'); konu takim,
    listede EN COK gecen isimdir (her mac onu icerir)."""
    sayac = {}
    for x in ham:
        for k in ("h", "a"):
            n = (x.get(k) or {}).get("n")
            if n:
                sayac[n] = sayac.get(n, 0) + 1
    if not sayac:
        return []
    konu = max(sayac, key=sayac.get)
    out = []
    for x in ham:
        h, a = x.get("h") or {}, x.get("a") or {}
        if h.get("rs") is None or a.get("rs") is None:
            continue
        lig = ((x.get("l") or {}).get("n")) or ""
        if re.search(r"cift|double", fold(lig)):
            continue
        if h.get("n") == konu:
            own, opp = h, a
        elif a.get("n") == konu:
            own, opp = a, h
        else:
            continue
        out.append({"ts": x.get("t"), "pf": own["rs"], "pa": opp["rs"], "lig": lig, "ad": konu,
                    "rakip": opp.get("n") or "",
                    "sets": [(s.get("s") or 0) for s in (own.get("ss") or [])],
                    "sets_opp": [(s.get("s") or 0) for s in (opp.get("ss") or [])]})
    out.sort(key=lambda g: -(g["ts"] or 0))
    return out


def dinlenme_gun(oy, ko_ts):
    """Son macin bitisinden degil BASLANGICINDAN kickoff'a gecen gun (B2B ~1.0). Veri yoksa None."""
    onceki = [g["ts"] for g in oy if g.get("ts") and g["ts"] < ko_ts]
    return (ko_ts - max(onceki)) / 86400.0 if onceki else None


# ---------------------------------------------------------------- oran kirilimi (ilk-gorulen)
def ilk_oran(anahtar, oran):
    """Ilk gorulen orani saklar/dondurur. -> (ilk_oran, ilk_ts). Ilk cagrida (oran, simdi)."""
    yol = os.path.join(VERI, "ilk_oran.json")
    d = _oku(yol, {})
    simdi = time.time()
    if anahtar not in d:
        d[anahtar] = [oran, simdi]
        d = {k: v for k, v in d.items() if simdi - v[1] < ILK_ORAN_OMUR}
        _yaz(yol, d)
    return d[anahtar][0], d[anahtar][1]


def ekstrem_oran(anahtar, ilk, oran):
    """ilk'e karsi gorulen EN BUYUK (mutlak deger) sapmayi surekli saklar - REBOUND tespiti icin:
    'oran kirildi ama sonra geri sicradi' aninda GUNCEL (ilk vs simdi) fark kucuk olabilir, bu
    yuzden sadece anlik karsilastirma yetmez; en-kotu (ya da en-iyi) nokta hatirlanmali.
    -> (ekstrem_oran, ekstrem_dusus, ekstrem_ts). ilk yoksa (0, 0.0, simdi) - kirilma olcusuz.

    11.10.2026: kayit [ekstrem_oran, ekstrem_dusus, ekstrem_ts, SON_oran, SON_ts] olarak 5 alana
    cikarildi. Neden: FAKE-OUT (sahte kirilim) orani GERIYE DONUK olculemiyordu - yalniz 'ilk' ve
    'ekstrem' saklaniyordu, 'guncel' saklanmiyordu; dolayisiyla "kirilim esigi asti, sonra acilisa
    geri mi dondu?" sorusu ancak maci o anda bultende yakalayarak cevaplanabiliyordu (11.10
    taramasinda bu sarti saglayan tek bir ornek bulundu - olcum yapilamadi).
    SON_* her gorusmede guncellenir; ekstrem mantigi AYNEN korunur. Donus imzasi DEGISMEDI
    (3 deger) - mevcut cagiranlar (_kirilim_tara) etkilenmez. Eski 3 alanli kayitlar okunabilir."""
    simdi = time.time()
    if not ilk:
        return oran, 0.0, simdi
    yol = os.path.join(VERI, "ekstrem_oran.json")
    d = _oku(yol, {})
    dusus = oran / ilk - 1.0
    mevcut = d.get(anahtar)
    if mevcut is None or abs(dusus) > abs(mevcut[1]):
        kayit = [oran, dusus, simdi, oran, simdi]
    else:
        # ekstrem degismedi: yalniz SON_* tazelenir (zaman serisinin ucu)
        kayit = [mevcut[0], mevcut[1], mevcut[2], oran, simdi]
    d[anahtar] = kayit
    # budama: en son GORULDUGU ana gore (eski surumde v[2] kullaniliyordu; aktif izlenen
    # anahtarlar ekstremi eskidigi icin silinmesin)
    d = {k: v for k, v in d.items() if simdi - (v[4] if len(v) > 4 else v[2]) < ILK_ORAN_OMUR}
    _yaz(yol, d)
    return kayit[0], kayit[1], kayit[2]


# ---------------------------------------------------------------- telegram mukerrer-gonderim kilidi
TELEGRAM_GONDERILEN_OMUR = 2 * 86400     # sinyal_log.jsonl append-only (satir-ici guncelleme YAPILMAZ -
                                          # Render efemeral disk + es-zamanli yazim riski); ayri, kucuk bir
                                          # "gonderildi" anahtar kumesi ayni isi gorur, ilk_oran ile ayni desen


def telegram_gonderildi_mi(anahtar):
    d = _oku(os.path.join(VERI, "telegram_gonderilen.json"), {})
    return anahtar in d


def telegram_isaretle(anahtar):
    """Basariyla gonderilen sinyali kaydeder (bir daha gonderilmesin). -> True/False (yazim basarisi)."""
    yol = os.path.join(VERI, "telegram_gonderilen.json")
    d = _oku(yol, {})
    simdi = time.time()
    d[anahtar] = simdi
    d = {k: v for k, v in d.items() if simdi - v < TELEGRAM_GONDERILEN_OMUR}
    return _yaz(yol, d)


# ---------------------------------------------------------------- hokey: kaleci
def kaleci_dosyasi():
    return os.path.join(VERI, "kaleci.json")


def _anahtar(home, away):
    return "%s|%s" % (fold(home), fold(away))


def kaleci_yaz(takim, durum, sv=None):
    """Takim adina gore kaleci durumu kaydeder. durum: onayli | yedek | belirsiz. sv: kaleci kurtaris %."""
    if durum not in ("onayli", "yedek", "belirsiz"):
        raise ValueError("durum: onayli | yedek | belirsiz")
    d = _oku(kaleci_dosyasi(), {})
    d[fold(takim)] = {"durum": durum, "sv": sv, "ts": time.time()}
    _yaz(kaleci_dosyasi(), d)
    return d[fold(takim)]


def kaleci_oku(takim):
    """-> {'durum', 'sv', 'gp'} (gunluk; 18 saatten eski kayit gecersiz)."""
    k = _oku(kaleci_dosyasi(), {}).get(fold(takim))
    if k and time.time() - (k.get("ts") or 0) < 18 * 3600:
        return k
    return None


def nhl_kaleciler():
    """Bugunun NHL maclari icin takim -> en cok oynayan kaleci (sv, gp). Onay DEGIL, oneri.
    -> {fold(takim_adi): {'ad','sv','gp'}}; erisim yoksa {}."""
    out = {}
    try:
        d = requests.get(NHL_SCHEDULE, timeout=20).json()
        maclar = [g for w in d.get("gameWeek", []) for g in w.get("games", [])
                  if g.get("gameState") in ("FUT", "PRE")][:16]
        for g in maclar:
            try:
                gc = requests.get(NHL_LANDING.format(g["id"]), timeout=20).json()["matchup"]["goalieComparison"]
            except Exception:
                continue
            for taraf in ("homeTeam", "awayTeam"):
                tk = g[taraf]
                ad = fold("%s %s" % ((tk.get("placeName") or {}).get("default", ""),
                                     (tk.get("commonName") or {}).get("default", "")))
                lider = sorted((gc.get(taraf) or {}).get("leaders") or [], key=lambda x: -(x.get("gamesPlayed") or 0))
                if lider and lider[0].get("savePctg"):
                    out[ad] = {"ad": (lider[0].get("name") or {}).get("default"),
                               "sv": lider[0]["savePctg"], "gp": lider[0].get("gamesPlayed") or 0}
    except Exception:
        return {}
    return out


def nhl_bul(takim, tablo):
    """iddaa takim adi -> NHL tablosundaki kayit (ad icerme eslesmesi)."""
    f = fold(takim)
    for ad, v in tablo.items():
        if ad == f or ad in f or f in ad:
            return v
    return None


# ---------------------------------------------------------------- tenis
# TURNUVA -> ZEMIN: el ile tutulan, KUCUK tablo (varsayim). Eslesmezse zemin = None ve o mac
# zemin istatistigine SAYILMAZ (uydurma zemin yok). Anahtarlar fold() edilmis alt-dizgelerdir.
ZEMIN = {
    "sert": ["shanghai", "sanghay", "beijing", "pekin", "wuning", "suzhou", "tokyo", "tokyo", "hangzhou",
             "chengdu", "zhuhai", "guangzhou", "seoul", "osaka", "hong kong", "vienna", "viyana", "basel",
             "paris", "metz", "stockholm", "stokholm", "antwerp", "anvers", "almaty", "astana", "belgrade",
             "indian wells", "miami", "cincinnati", "toronto", "montreal", "dubai", "doha", "acapulco",
             "australian", "avustralya", "us open", "abd acik", "brisbane", "adelaide", "auckland",
             "winston", "washington", "los cabos", "san diego", "delray", "dallas", "pune", "chennai",
             "cleveland", "columbus", "tiburon", "hersonissos", "monastir", "tashkent", "sao paulo"],
    "kil": ["roland garros", "fransa acik", "monte carlo", "madrid", "rome", "roma", "barcelona", "hamburg",
            "umag", "gstaad", "kitzbuhel", "bastad", "estoril", "marrakech", "marakes", "houston",
            "buenos aires", "cordoba", "rio de janeiro", "santiago", "antofagasta", "braga", "villena",
            "palermo", "bucharest", "bukres", "geneva", "cenevre", "lyon", "parma", "prague", "praga",
            "rabat", "bogota", "charleston", "lausanne", "iasi", "split", "zagreb", "sardegna", "cagliari",
            "tallahassee", "cali", "lima", "punta del este", "florianopolis", "curitiba", "temuco"],
    "cim": ["wimbledon", "halle", "queen", "london", "londra", "eastbourne", "hertogenbosch", "newport",
            "mallorca", "stuttgart", "nottingham", "birmingham", "bad homburg", "berlin"],
}


def tenis_zemin(turnuva_adi):
    """Turnuva adi (iddaa, TR/EN karisik) -> 'sert' | 'kil' | 'cim' | None."""
    f = fold(turnuva_adi)
    for zemin, anahtarlar in ZEMIN.items():
        if any(a in f for a in anahtarlar):
            return zemin
    return None


def tenis_servis(oyuncu):
    """data/multi/tenis_servis.json -> {'spw','rpw'} (servis/karsilama nokta kazanma %). Yoksa None."""
    d = _oku(os.path.join(VERI, "tenis_servis.json"), {})
    f = fold(oyuncu)
    for ad, v in d.items():
        if fold(ad) == f:
            return v
    return None
