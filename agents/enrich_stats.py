import os
import glob
import json
import math

# BWM ilkesi: gercek veri yoksa sahte/varsayilan deger uretilmez.
# Bu modul TUM istatistikleri, o mac icin Iddaa'nin GERCEKTEN sundugu oranlardan turetir
# (vig arindirma + Poisson modeli). 

MAX_GOALS = 12          # Poisson toplamlarinda kullanilan pratik ust sinir
HANDICAP_DIVERGENCE_THRESHOLD = 0.15  # model-piyasa uyusmazligi esigi (olasilik puani)


def poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_cdf(k, lam):
    return sum(poisson_pmf(i, lam) for i in range(0, k + 1))


def devig(outcomes):
    """Oranlari (1/odd) gercek olasiliga cevirir; bahis firmasi payini (vig) arindirir."""
    raw = {name: (1.0 / odd) for name, odd in outcomes.items() if odd and odd > 0}
    overround = sum(raw.values())
    if overround <= 0:
        return {}
    return {name: p / overround for name, p in raw.items()}


def solve_lambda_from_under_prob(p_under, line, lo=0.05, hi=10.0, iters=60):
    """Poisson(lambda) icin P(X <= floor(line)) = p_under olacak sekilde lambda bulur."""
    k = math.floor(line)
    for _ in range(iters):
        mid = (lo + hi) / 2
        if poisson_cdf(k, mid) > p_under:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def _find_totals_entry(totals_list, scope="full"):
    candidates = []
    for entry in totals_list:
        name = entry.get("market_name", "")
        if "Maç Sonucu" in name or "Karşılıklı Gol" in name or "ve " in name.lower():
            continue  
        if scope == "full":
            if any(tag in name for tag in ["1. Yarı", "2. Yarı", "Ev Sahibi", "Deplasman"]):
                continue
            if not name.startswith("Alt/Üst"):
                continue
        candidates.append(entry)
    if not candidates:
        return None
        
    def _dist(e):
        try:
            return abs(float(e.get("line") or 2.5) - 2.5)
        except (TypeError, ValueError):
            return 99
    return sorted(candidates, key=_dist)[0]


def _find_side_totals_entry(totals_list, side_tag):
    for entry in totals_list:
        name = entry.get("market_name", "")
        if name.startswith(side_tag) and "Yarı" not in name:
            return entry
    return None


def _find_goal_band_entry(totals_list):
    for entry in totals_list:
        if entry.get("market_name") == "Toplam Gol":
            return entry
    return None


def _find_full_corner_total(corner_list):
    for entry in corner_list:
        name = entry.get("market_name", "")
        if "Yarı" not in name and "Ev Sahibi" not in name and "Deplasman" not in name:
            return entry
    return None


def _find_full_card_total(card_list):
    for entry in card_list:
        name = entry.get("market_name", "")
        if "Yarı" not in name and "Ev Sahibi" not in name and "Deplasman" not in name and "Alt" in name.replace("ı", "i"):
            return entry
    return None


def _lambda_from_total_market(entry):
    if not entry:
        return None
    dv = devig(entry.get("outcomes", {}))
    p_under = dv.get("Alt")
    line = entry.get("line")
    if p_under is None or line is None:
        return None
    try:
        line = float(line)
    except (TypeError, ValueError):
        return None
    return solve_lambda_from_under_prob(p_under, line)


def _handicap_check(handicap_list, home_xg, away_xg):
    if not handicap_list or home_xg is None or away_xg is None:
        return None

    def _dist(e):
        try:
            return abs(float(e.get("line") or 0))
        except (TypeError, ValueError):
            return 99

    entry = sorted(handicap_list, key=_dist)[0] if handicap_list else None
    if not entry:
        return None

    dv = devig(entry.get("outcomes", {}))
    market_p_home_covers = dv.get("1")
    line = entry.get("line")
    
    if market_p_home_covers is None or line is None:
        return None
        
    try:
        h_line = float(line)
    except (TypeError, ValueError):
        return None

    threshold = -h_line  
    model_p = 0.0
    for h in range(0, MAX_GOALS + 1):
        ph = poisson_pmf(h, home_xg)
        for a in range(0, MAX_GOALS + 1):
            if h - a > threshold:
                model_p += ph * poisson_pmf(a, away_xg)

    divergence = round(abs(market_p_home_covers - model_p), 3)
    return {
        "market_line": h_line,
        "market_implied_home_cover_prob": round(market_p_home_covers, 3),
        "model_implied_home_cover_prob": round(model_p, 3),
        "divergence": divergence,
        "high_risk": divergence > HANDICAP_DIVERGENCE_THRESHOLD,
    }


def enrich_match(match):
    markets = match.get("markets", {})
    ms = markets.get("match_outcome", {}) or {}
    availability = {
        "ms_market": False, "totals_market": False, "corners_market": False,
        "cards_market": False, "btts_market": False, "goal_band_market": False,
        "handicap_market": False,
    }
    stats = {}

    # --- 1X2 vig-arindirma & BWM Valueless Bet Kontrolü ---
    p1 = pX = p2 = None
    if all(k in ms for k in ("1", "X", "2")):
        dv = devig(ms)
        p1, pX, p2 = dv.get("1"), dv.get("X"), dv.get("2")
        availability["ms_market"] = True
        
        # BWM Kuralı: Eğer üç ihtimal de birbirine çok yakınsa (Favorinin olasılığı %42'den azsa)
        # Bu maç "Valueless Bet" (Değersiz Bahis) olarak işaretlenir.
        max_prob = max(p1, pX, p2)
        stats["valueless_bet_balanced_odds"] = bool(max_prob < 0.42)

    # --- Toplam gol (tam mac) -> lambda_total ---
    totals_list = markets.get("totals_goals", []) or []
    full_totals_entry = _find_totals_entry(totals_list, scope="full")
    lambda_total = _lambda_from_total_market(full_totals_entry)
    
    if lambda_total is not None:
        availability["totals_market"] = True

    home_xg = away_xg = None
    if lambda_total is not None and p1 is not None:
        home_xg = round(lambda_total * (p1 + 0.5 * pX), 2)
        away_xg = round(lambda_total * (p2 + 0.5 * pX), 2)
        stats["home_avg_goals_scored"] = home_xg
        stats["away_avg_goals_scored"] = away_xg
        stats["home_avg_goals_conceded"] = away_xg
        stats["away_avg_goals_conceded"] = home_xg
        stats["expected_total_goals"] = round(home_xg + away_xg, 2)

    # --- 2-3 gol araligi ---
    goal_band_entry = _find_goal_band_entry(totals_list)
    if goal_band_entry:
        dv_band = devig(goal_band_entry.get("outcomes", {}))
        band_key = next((k for k in dv_band if "2-3" in k.replace(" ", "")), None)
        if band_key:
            stats["goal_range_2_3_prob"] = round(dv_band[band_key], 3)
            availability["goal_band_market"] = True
            
    if "goal_range_2_3_prob" not in stats and lambda_total is not None:
        stats["goal_range_2_3_prob"] = round(poisson_pmf(2, lambda_total) + poisson_pmf(3, lambda_total), 3)

    # --- Korner ---
    corner_list = markets.get("corners", []) or []
    full_corner_entry = _find_full_corner_total(corner_list)
    lambda_corners = _lambda_from_total_market(full_corner_entry)
    if lambda_corners is not None:
        availability["corners_market"] = True
        stats["expected_corners"] = lambda_corners
        
        home_corner_entry = _find_side_totals_entry(corner_list, "Ev Sahibi")
        away_corner_entry = _find_side_totals_entry(corner_list, "Deplasman")
        lam_home_c = _lambda_from_total_market(home_corner_entry)
        lam_away_c = _lambda_from_total_market(away_corner_entry)
        
        if lam_home_c is not None and lam_away_c is not None:
            stats["expected_home_corners"] = lam_home_c
            stats["expected_away_corners"] = lam_away_c
        elif p1 is not None and p2 is not None and (p1 + p2) > 0:
            home_share = round(p1 / (p1 + p2), 3)
            stats["expected_home_corners"] = round(lambda_corners * home_share, 1)
            stats["expected_away_corners"] = round(lambda_corners - stats["expected_home_corners"], 1)
            stats["corner_split_estimated"] = True

    # --- Kart ---
    card_list = markets.get("cards", []) or []
    full_card_entry = _find_full_card_total(card_list)
    lambda_cards = _lambda_from_total_market(full_card_entry)
    if lambda_cards is not None:
        availability["cards_market"] = True
        stats["expected_cards"] = lambda_cards
        
        home_card_entry = _find_side_totals_entry(card_list, "Ev Sahibi")
        away_card_entry = _find_side_totals_entry(card_list, "Deplasman")
        lam_home_k = _lambda_from_total_market(home_card_entry)
        lam_away_k = _lambda_from_total_market(away_card_entry)
        
        if lam_home_k is not None and lam_away_k is not None:
            stats["expected_home_cards"] = lam_home_k
            stats["expected_away_cards"] = lam_away_k

    # --- Karsilikli gol (BTTS) ---
    btts = markets.get("both_teams_score", {}) or {}
    if "Var" in btts and "Yok" in btts:
        dv_btts = devig(btts)
        if "Var" in dv_btts:
            stats["btts_probability"] = round(dv_btts["Var"], 3)
            availability["btts_market"] = True

    # --- Handikap ---
    handicap_list = markets.get("handicaps", []) or []
    handicap_check = _handicap_check(handicap_list, home_xg, away_xg)
    if handicap_check is not None:
        stats["handicap_check"] = handicap_check
        availability["handicap_market"] = True

    stats["data_availability"] = availability
    return stats


def get_latest_match_file(data_dir):
    """En son oluşturulan football_matches_*.json dosyasını bulur."""
    search_pattern = os.path.join(data_dir, "football_matches_*.json")
    list_of_files = glob.glob(search_pattern)
    if not list_of_files:
        return None
    # En son değiştirilme tarihine göre sırala ve en yenisini al
    return max(list_of_files, key=os.path.getmtime)


def enrich_stats():
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(SCRIPT_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    
    # 1. Kodun (fetch_iddaa.py) ürettiği dinamik dosyayı otomatik buluyoruz
    input_file = get_latest_match_file(data_dir)
    
    if not input_file:
        print("Hata: data klasöründe işlenecek 'football_matches_*.json' dosyası bulunamadı!")
        print("Lütfen önce verileri çekmek için veri toplama scriptini çalıştırın.")
        return

    # Okunan dosyanın ismine göre enriched dosya ismi oluştur (örn: football_matches_20260919_enriched.json)
    base_name = os.path.basename(input_file).replace('.json', '')
    output_file = os.path.join(data_dir, f"{base_name}_enriched.json")

    with open(input_file, "r", encoding="utf-8") as f:
        matches = json.load(f)

    if not matches:
        print(f"Uyarı: Okunan {os.path.basename(input_file)} dosyası boş.")
        return

    print(f"[{os.path.basename(input_file)}] okunuyor...")
    print(f"Toplam {len(matches)} maç BWM modeli (Vig-Arındırma + Poisson) ile zenginleştiriliyor...")

    enriched_matches = []
    skipped = 0
    
    for match in matches:
        stats = enrich_match(match)
        # BWM'nin temeli maç sonucu (1X2) ihtimallerine dayandığı için bu veri yoksa maç es geçilir
        if not stats.get("data_availability", {}).get("ms_market"):
            skipped += 1
            continue  
            
        match["stats"] = stats
        enriched_matches.append(match)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(enriched_matches, f, ensure_ascii=False, indent=2)

    print(f"\nİşlem tamamlandı! {len(enriched_matches)} maç zenginleştirildi, {skipped} maç yetersiz oran verisi nedeniyle atlandı.")
    print(f"BWM analiz sonuçları kaydedildi: {output_file}")


if __name__ == "__main__":
    enrich_stats()
    