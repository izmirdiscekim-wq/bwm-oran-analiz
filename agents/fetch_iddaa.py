import os
import json
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone, timedelta

# Sadece Futbol maçlarını çeken uç nokta (st=1)
EVENTS_URL = "https://sportsbookv2.iddaa.com/sportsbook/events?st=1&type=0&version=0&live=true"
MARKET_CONFIG_URL = "https://sportsbookv2.iddaa.com/sportsbook/get_market_config"
COMPETITIONS_URL = "https://sportsbookv2.iddaa.com/sportsbook/competitions"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.iddaa.com",
    "Referer": "https://www.iddaa.com/",
}

TR_TZ = timezone(timedelta(hours=3))
LIST_BUCKETS = {"totals_goals", "corners", "cards", "handicaps", "half_time", "other_markets"}

DOUBLE_CHANCE_MAP = {
    "1 ve 0": "1X", "0 ve 1": "1X",
    "1 ve 2": "12", "2 ve 1": "12",
    "0 ve 2": "X2", "2 ve 0": "X2",
}

def _get_session():
    """Bağlantı kopmalarına karşı Retry mekanizmalı session oluşturur."""
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update(HEADERS)
    return session

def _get_json(session, url, timeout=20):
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def _fetch_market_config(session, st=1):
    """st=1 (futbol, varsayılan) dışında bir spor için önce events?st=<st> ile oturumu o spora kapsar (sunucu tarafı
    oturum durumu buna göre değişiyor), sonra market config'i çeker. Böylece aynı fonksiyon basketbol (st=2) için de kullanılabilir."""
    try:
        if st != 1:
            session.get(EVENTS_URL.replace("st=1", f"st={st}"), timeout=20)
        data = _get_json(session, MARKET_CONFIG_URL)
        return data.get("data", {}).get("m", {}) or {}
    except Exception as e:
        print(f"Uyarı: Pazar konfigürasyonu alınamadı ({e}). Kodlar kullanılacak.")
        return {}

def _fetch_competitions(session):
    try:
        data = _get_json(session, COMPETITIONS_URL)
        rows = data.get("data", []) or []
        return {row.get("i"): row.get("n") for row in rows if isinstance(row, dict)}
    except Exception as e:
        print(f"Uyarı: Lig listesi alınamadı ({e}).")
        return {}

def _resolve_market_name(t, st, sov, market_config):
    entry = market_config.get(f"{t}_{st}")
    name = entry["n"] if entry and entry.get("n") else f"Pazar {t}-{st}"
    if sov:
        name = name.replace("{0}", str(sov)).replace("{h}", str(sov))
    return name

def _classify_market(markets, name, sov, outcomes):
    lname = name.lower()

    if name == "Maç Sonucu":
        for o_name, odd in outcomes.items():
            key = "X" if o_name == "0" else o_name
            markets["match_outcome"][key] = odd
        return

    if name == "Çifte Şans":
        for o_name, odd in outcomes.items():
            key = DOUBLE_CHANCE_MAP.get(o_name, o_name)
            markets["double_chance"][key] = odd
        return

    if name == "Karşılıklı Gol":
        markets["both_teams_score"].update(outcomes)
        return

    entry = {"market_name": name, "line": sov, "outcomes": outcomes}

    if "korner" in lname:
        markets["corners"].append(entry)
    elif "kart" in lname:
        markets["cards"].append(entry)
    elif "handikap" in lname:
        markets["handicaps"].append(entry)
    elif "alt/üst" in lname or "altı/üstü" in lname or "toplam gol" in lname:
        markets["totals_goals"].append(entry)
    elif "yarı" in lname:
        markets["half_time"].append(entry)
    else:
        markets["other_markets"].append(entry)

def _empty_markets():
    m = {"match_outcome": {}, "double_chance": {}, "both_teams_score": {}}
    for b in LIST_BUCKETS:
        m[b] = []
    return m

def _parse_event(ev, market_config, competitions, is_live):
    match_id = str(ev.get("i", ""))
    if not match_id:
        return None

    markets = _empty_markets()
    for mkt in ev.get("m", []):
        if not isinstance(mkt, dict):
            continue
            
        t, st, sov = mkt.get("t"), mkt.get("st"), mkt.get("sov")
        name = _resolve_market_name(t, st, sov, market_config)
        
        outcomes = {}
        for out in mkt.get("o", []):
            if not isinstance(out, dict):
                continue
            o_name = str(out.get("n", ""))
            try:
                o_odd = float(out.get("odd", 0.0))
            except (ValueError, TypeError):
                o_odd = 0.0
                
            if o_name and o_odd > 0:
                outcomes[o_name] = o_odd
                
        if outcomes:
            _classify_market(markets, name, sov, outcomes)

    kickoff_dt = datetime.fromtimestamp(ev.get("d", 0), TR_TZ) if ev.get("d") else None

    return {
        "match_id": match_id,
        "sport": "Futbol",
        "is_live": is_live,
        "home": ev.get("hn") or "Ev Sahibi",
        "away": ev.get("an") or "Deplasman",
        "league": competitions.get(ev.get("ci"), "Bilinmeyen Lig"),
        "kickoff_time": kickoff_dt.isoformat() if kickoff_dt else "",
        "markets": markets,
    }

def fetch_all_matches(target_date):
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(SCRIPT_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    
    date_str = target_date.strftime("%Y%m%d")
    output_file = os.path.join(data_dir, f"football_matches_{date_str}.json")

    print(f"\n{target_date.strftime('%Y-%m-%d')} tarihi için İddaa futbol bülten verisi çekiliyor...")
    session = _get_session()
    
    market_config = _fetch_market_config(session)
    competitions = _fetch_competitions(session)

    parsed_matches = []
    seen_ids = set()

    try:
        raw = _get_json(session, EVENTS_URL)
    except Exception as e:
        print(f"HATA: Futbol API'sine ulaşılamadı ({e}).")
        return 0

    events = raw.get("data", {}).get("events", []) or []
    live_ids = set((raw.get("data", {}).get("sc") or {}).keys())
    
    for ev in events:
        if not isinstance(ev, dict):
            continue
        
        match_id = str(ev.get("i", ""))
        
        # ID kontrolü
        if not match_id or match_id in seen_ids:
            continue
            
        # TARIH FILTRESI: Maçın timestamp'ini alıp istenen tarihle karşılaştırıyoruz
        d = ev.get("d")
        if not d:
            continue
        match_date = datetime.fromtimestamp(d, TR_TZ).date()
        
        if match_date != target_date:
            continue
            
        is_live = match_id in live_ids
        
        parsed = _parse_event(ev, market_config, competitions, is_live)
        if parsed is None:
            continue
            
        seen_ids.add(match_id)
        parsed_matches.append(parsed)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(parsed_matches, f, ensure_ascii=False, indent=2)

    print(f"İşlem tamamlandı! Seçilen tarihe ait toplam {len(parsed_matches)} maç '{output_file}' konumuna yazıldı.")
    return len(parsed_matches)

if __name__ == "__main__":
    # Kullanıcıdan tarih alma işlemi
    user_input = input("Hangi günün maçlarını çekmek istiyorsunuz? (Örn: 2026-09-19) [Bugün için boş bırakıp Enter'a basın]: ").strip()
    
    if not user_input:
        target_date = datetime.now(TR_TZ).date()
    else:
        try:
            target_date = datetime.strptime(user_input, "%Y-%m-%d").date()
        except ValueError:
            print("HATA: Yanlış tarih formatı girdiniz. Lütfen YYYY-AA-GG formatında giriniz.")
            exit(1)
            
    fetch_all_matches(target_date)
    