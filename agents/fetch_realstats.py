"""
Gercek istatistik veri hatti (Real-Stats Pipeline) - API-Football entegrasyonu.

BWM disiplini: Iddaa oran-tabanli enrich_stats.py TUM maclari Poisson modeliyle
"tahmin" eder, ama bu sadece piyasanin ne dusundugunu gosterir - gercegi degil.
Bu script, oran motorunun zaten "ilginc" (dengesiz/net favorili) bulundugu
maclardan KISA BIR LISTE secip, o maclar icin GERCEK takim formunu ve
karsilikli gecmisi (H2H) API-Football'dan ceker.

Neden kisa liste? API-Football ucretsiz katman: 100 istek/gun, 10 istek/dk.
Gunde 600+ mac var - hepsini gercek veriyle dogrulamak imkansiz. Bu yuzden
butce, sadece en degerli adaylara ayrilir (bkz. SHORTLIST_SIZE). Bu ayni
zamanda BWM'nin secicilik ilkesini kod seviyesinde zorunlu kilar:
analyze_bwm.py, real-stats dogrulamasi OLMAYAN maclari asla yuksek guvene
tasiyamaz (bkz. analyze_bwm.py CONFIDENCE_THRESHOLD mantigi).

Kullanim:
    $env:API_FOOTBALL_KEY = "xxxxxxxx"   (https://dashboard.api-football.com - ucretsiz, kredi karti gerekmez)
    python fetch_realstats.py
"""
import os
import glob
import json
import time
import re
import difflib
import unicodedata
import requests
from datetime import datetime, timezone, timedelta

from weather_signals import weather_at_kickoff, weather_adjustment
from dixon_coles_model import LEAGUE_CODES

API_KEY = os.environ.get("API_FOOTBALL_KEY", "").strip()
BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_KEY}

SHORTLIST_SIZE = 25          # gunluk butce icinde kalacak sekilde zenginlestirilecek mac sayisi
# Ucretsiz plan sadece 2022-2024 sezonlarina ve 'last' parametresiz sorgulara izin verir; bu yuzden
# guncel sezon formu (/fixtures?team=..&last=5) cekilemez. Ucretli plana gecersen True yap.
FORM_ENABLED = False
MIN_REQUEST_INTERVAL = 6.5   # 10 istek/dk limiti icin guvenli bosluk (saniye)
DAILY_REQUEST_BUDGET = 95    # 100 sinirina karsi tampon

# Sadece bu ligler "major" sayilir; kisa liste once bunlardan doldurulur
# (API-Football veri kalitesi ve isabet orani buyuk liglerde cok daha yuksek).
MAJOR_LEAGUE_HINTS = [
    "premier lig", "championship", "la liga", "laliga", "serie a", "serie b",
    "bundesliga", "ligue 1", "ligue 2", "eredivisie", "primeira liga",
    "süper lig", "super lig", "1. lig",
]

TR_TZ = timezone(timedelta(hours=3))


class RequestBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0
        self.last_call = 0.0
        self.blocked = False

    def can_spend(self, n=1):
        return self.used + n <= self.limit

    def spend(self, n=1):
        self.used += n

    def throttle(self):
        elapsed = time.time() - self.last_call
        if elapsed < MIN_REQUEST_INTERVAL:
            time.sleep(MIN_REQUEST_INTERVAL - elapsed)
        self.last_call = time.time()


def api_get(session, budget, path, params=None):
    if not budget.can_spend(1):
        return None
    if budget.blocked:
        return None
    budget.throttle()
    try:
        resp = session.get(f"{BASE_URL}{path}", params=params, timeout=20)
        budget.spend(1)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        print(f"  Uyarı: API isteği başarısız ({path}): {e}")
        return None

    errors = body.get("errors")
    if errors:
        # API-Football hatalari HTTP 200 icinde 'errors' alaninda doner (plan/sezon/limit kisitlari dahil)
        print(f"  API HATASI ({path}): {errors}")
        # Yalnizca plan/limit/yetki hatalari tum istekleri durdurur; dogrulama hatasi sadece o istegi atlar
        if isinstance(errors, dict) and any(k in errors for k in ("plan", "access", "token", "requests", "rateLimit", "subscription")):
            budget.blocked = True
        return None
    return body


def load_cache(data_dir):
    path = os.path.join(data_dir, "team_id_cache.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    # Eski format (ulkesiz anahtar) -> 'isim|Ulke'. Yalnizca ulkesi bilinen kayitlar tasinir.
    migrated = {}
    for k, v in raw.items():
        if "|" in k:
            migrated[k] = v
        elif v and v.get("country"):
            migrated[f"{k}|{v['country']}"] = v
    return migrated


def server_used_today(session):
    """Gunluk kullanilan istek sayisini sunucudan okur (/status)."""
    try:
        r = session.get(f"{BASE_URL}/status", timeout=15).json()
        return int(r["response"]["requests"]["current"])
    except Exception:
        return None


def save_cache(data_dir, cache):
    path = os.path.join(data_dir, "team_id_cache.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _norm(name):
    return (name or "").lower().strip()


COUNTRY_BY_PREFIX = {
    "İngiltere": "England", "İskoçya": "Scotland", "İspanya": "Spain", "İtalya": "Italy", "Almanya": "Germany",
    "Fransa": "France", "Hollanda": "Netherlands", "Belçika": "Belgium", "Portekiz": "Portugal",
    "Türkiye": "Turkey", "Yunanistan": "Greece",
}


def expected_country(league_name):
    for prefix, country in COUNTRY_BY_PREFIX.items():
        if (league_name or "").startswith(prefix):
            return country
    return None


def resolve_team_id(session, budget, cache, team_name, country=None):
    """Doner: {'id','city','country'} veya None. Ulke verilirse yalnizca o ulkenin takimlari aday olur
    (Yunan 'Aris' ile Kibris 'Aris Limassol' karismasin). Cache anahtari ulkeyi icerir."""
    key = f"{_norm(team_name)}|{country or ''}"
    if key in cache:
        return cache[key]

    # cache icinde ayni ulkeden yakin isim var mi? (istek harcamadan)
    best_match, best_ratio = None, 0.0
    for cached_name, cached_val in cache.items():
        if not cached_val or not cached_name.endswith(f"|{country or ''}"):
            continue
        ratio = difflib.SequenceMatcher(None, key, cached_name).ratio()
        if ratio > best_ratio:
            best_match, best_ratio = cached_val, ratio
    if best_ratio >= 0.92:
        cache[key] = best_match
        return best_match

    term = _search_term(team_name)
    if len(term) < 3:
        cache[key] = None
        return None
    data = api_get(session, budget, "/teams", params={"search": term})
    if not data:
        return None  # hata/blok durumunda negatif cache yazma
    if not data.get("response"):
        cache[key] = None
        return None

    scored = []
    for c in data["response"]:
        team, venue = c.get("team", {}), c.get("venue", {}) or {}
        if country and team.get("country") != country:
            continue
        ratio = difflib.SequenceMatcher(None, _norm(team_name), _norm(team.get("name", ""))).ratio()
        scored.append((ratio, {"id": team.get("id"), "city": venue.get("city"), "country": team.get("country")}))
    scored.sort(key=lambda t: t[0], reverse=True)

    if scored and scored[0][0] >= 0.55:
        cache[key] = scored[0][1]
        return scored[0][1]

    cache[key] = None
    return None


def get_recent_form(session, budget, team_id, n=5):
    if not team_id:
        return None
    data = api_get(session, budget, "/fixtures", params={"team": team_id, "last": n})
    if not data or not data.get("response"):
        return None

    results, scored, conceded, wins, draws, losses = [], 0, 0, 0, 0, 0
    for fx in data["response"]:
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        is_home = teams.get("home", {}).get("id") == team_id
        gf = goals.get("home") if is_home else goals.get("away")
        ga = goals.get("away") if is_home else goals.get("home")
        if gf is None or ga is None:
            continue
        scored += gf
        conceded += ga
        if gf > ga:
            wins += 1
            results.append("G")
        elif gf == ga:
            draws += 1
            results.append("B")
        else:
            losses += 1
            results.append("M")

    played = wins + draws + losses
    if played == 0:
        return None

    return {
        "played": played,
        "form_string": "".join(results),
        "wins": wins, "draws": draws, "losses": losses,
        "avg_goals_scored": round(scored / played, 2),
        "avg_goals_conceded": round(conceded / played, 2),
    }


def get_h2h(session, budget, home_id, away_id, n=5):
    if not home_id or not away_id:
        return None
    # Ucretsiz planda 'last' parametresi yasak; tum gecmis gelir, son n TAMAMLANMIS maci burada seciyoruz.
    data = api_get(session, budget, "/fixtures/headtohead", params={"h2h": f"{home_id}-{away_id}"})
    if not data or not data.get("response"):
        return None

    finished = [fx for fx in data["response"]
                if (fx.get("goals") or {}).get("home") is not None and (fx.get("goals") or {}).get("away") is not None]
    finished.sort(key=lambda fx: fx.get("fixture", {}).get("date", ""), reverse=True)

    home_wins = away_wins = draws = total_goals = played = 0
    for fx in finished[:n]:
        goals = fx.get("goals", {})
        gh, ga = goals.get("home"), goals.get("away")
        if gh is None or ga is None:
            continue
        played += 1
        total_goals += gh + ga
        fx_home_id = fx.get("teams", {}).get("home", {}).get("id")
        if gh == ga:
            draws += 1
        elif (gh > ga) == (fx_home_id == home_id):
            home_wins += 1
        else:
            away_wins += 1

    if played == 0:
        return None

    return {
        "played": played,
        "home_wins": home_wins, "away_wins": away_wins, "draws": draws,
        "avg_total_goals": round(total_goals / played, 2),
    }


EXCLUDED_LEAGUE_TOKENS = ("women", "kadın", "kadin", "amatör", "amator", "u17", "u18", "u19", "u20", "u21", "u23",
                          "juvenil", "rezerv", "kupası", "kupasi")


def _league_priority(league_name):
    return 0 if league_name in LEAGUE_CODES else 1


def _is_excluded_league(league_name):
    lname = (league_name or "").lower()
    return any(t in lname for t in EXCLUDED_LEAGUE_TOKENS)


def _search_term(name):
    """API-Football /teams?search yalnizca harf, rakam ve bosluk kabul eder."""
    s = re.sub(r"\(.*?\)", " ", name or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Za-z0-9 ]", " ", s).split())


def build_shortlist(enriched_matches, size):
    """
    Oran motorunun zaten 'net favori + dengeli olmayan' bulduğu maçlar arasından
    en yuksek sinyal genligine sahip olanlari sec. Once buyuk ligler, sonra genlik.
    """
    candidates = []
    for m in enriched_matches:
        stats = m.get("stats", {})
        if m.get("is_live") or _is_excluded_league(m.get("league")):
            continue  # canli maclarda oranlar bozuk (orn. 18.15 / 1.0); kadin/amator/genc ligler veri kalitesi dusuk
        if stats.get("valueless_bet_balanced_odds"):
            continue
        home_g = stats.get("home_avg_goals_scored")
        away_g = stats.get("away_avg_goals_scored")
        if home_g is None or away_g is None:
            continue
        gap = abs(home_g - away_g)
        if gap < 0.8:
            continue
        candidates.append((m, gap))

    candidates.sort(key=lambda t: (_league_priority(t[0].get("league", "")), -t[1]))
    return [m for m, _ in candidates[:size]]


def get_latest_enriched_file(data_dir):
    files = glob.glob(os.path.join(data_dir, "football_matches_*_enriched.json"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def enrich_with_real_stats():
    if not API_KEY:
        print("HATA: API_FOOTBALL_KEY ortam degiskeni tanimli degil.")
        print("Ücretsiz key için: https://dashboard.api-football.com/register (kredi kartı gerekmez)")
        print('PowerShell: $env:API_FOOTBALL_KEY = "senin-key-in"')
        return

    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(SCRIPT_DIR, "data")

    input_file = get_latest_enriched_file(data_dir)
    if not input_file:
        print("Hata: '*_enriched.json' bulunamadı. Önce fetch_iddaa.py + enrich_stats.py çalıştırın.")
        return

    with open(input_file, "r", encoding="utf-8") as f:
        matches = json.load(f)

    shortlist = build_shortlist(matches, SHORTLIST_SIZE)
    print(f"{len(matches)} zenginleştirilmiş maç arasından {len(shortlist)} maç gerçek istatistik için kısa listeye alındı.")

    cache = load_cache(data_dir)
    session = requests.Session()
    session.headers.update(HEADERS)
    budget = RequestBudget(DAILY_REQUEST_BUDGET)
    used = server_used_today(session)
    if used is not None:
        budget.used = used

    base_name = os.path.basename(input_file).replace("_enriched.json", "")
    output_file = os.path.join(data_dir, f"{base_name}_realstats.json")
    real_stats_by_match_id = {}
    if os.path.exists(output_file):
        with open(output_file, "r", encoding="utf-8") as f:
            real_stats_by_match_id = json.load(f)

    for m in shortlist:
        if m["match_id"] in real_stats_by_match_id:
            continue  # bugun zaten cekildi, istek harcama
        if not budget.can_spend(3):
            print(f"  Günlük istek bütçesi doldu ({budget.used}/{DAILY_REQUEST_BUDGET}), kalan maçlar atlandı.")
            break

        home_name, away_name = m.get("home", ""), m.get("away", "")
        print(f"  -> {home_name} vs {away_name} ({m.get('league')}) gerçek veri çekiliyor...")

        country = expected_country(m.get("league"))
        home_t = resolve_team_id(session, budget, cache, home_name, country)
        away_t = resolve_team_id(session, budget, cache, away_name, country)

        if budget.blocked:
            print("  API erişimi engellendi (yukarıdaki hataya bakın); kalan maçlar atlandı.")
            break
        if not home_t or not away_t:
            print(f"     Takım eşleşmedi (home={home_t}, away={away_t}), atlanıyor.")
            continue

        home_id, away_id = home_t["id"], away_t["id"]
        home_form = get_recent_form(session, budget, home_id) if FORM_ENABLED else None
        away_form = get_recent_form(session, budget, away_id) if FORM_ENABLED else None
        h2h = get_h2h(session, budget, home_id, away_id)

        weather = weather_at_kickoff(session, data_dir, home_t.get("city"), home_t.get("country"), m.get("kickoff_time"))
        _, weather_note = weather_adjustment(weather)

        real_stats_by_match_id[m["match_id"]] = {
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_form": home_form,
            "away_form": away_form,
            "h2h": h2h,
            "weather": weather,
            "weather_note": weather_note,
            "fetched_at": datetime.now(TR_TZ).isoformat(),
        }

    save_cache(data_dir, cache)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(real_stats_by_match_id, f, ensure_ascii=False, indent=2)

    print(f"\nTamamlandı: {len(real_stats_by_match_id)}/{len(shortlist)} maç gerçek veriyle zenginleştirildi.")
    print(f"API bütçesi kullanımı: {budget.used}/{DAILY_REQUEST_BUDGET}")
    print(f"Kaydedildi: {output_file}")


if __name__ == "__main__":
    enrich_with_real_stats()
