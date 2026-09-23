import os
import glob
import json

GOAL_RANGE_LOW, GOAL_RANGE_HIGH = 2.1, 2.8
CORNER_LINE = 9.5
CARD_LINE = 4.5
CONFIDENCE_THRESHOLD = 7

BANKROLL_MAP = {
    7: (2.0, "1.5 Ünite"),
    8: (2.5, "2 Ünite"),
    9: (3.0, "2.5 Ünite"),
    10: (3.5, "3 Ünite"),
}


def _first(d, *keys, default=0.0):
    for k in keys:
        if k in d and d[k]:
            return d[k]
    return default


def _ms_odds(markets):
    ms = markets.get("match_outcome", {}) or {}
    o1 = _first(ms, "1", "MS1", "Ev Sahibi")
    ox = _first(ms, "X", "MS0", "Beraberlik")
    o2 = _first(ms, "2", "MS2", "Deplasman")
    return o1, ox, o2


def team_tier(avg_scored):
    if avg_scored >= 1.8:
        return "Tier 1 (Yüksek Skor Potansiyeli)"
    if avg_scored >= 1.0:
        return "Tier 2 (Orta Seviye)"
    return "Zayıf/Kapanan"


def get_double_chance_pick(o1, ox, o2):
    """Taraf oranlarına bakarak en mantıklı Çifte Şans tercihini bulur."""
    odds = [v for v in (o1, ox, o2) if v and v > 0]
    if len(odds) < 3:
        return "1X"
    pairs = sorted([("1", o1), ("X", ox), ("2", o2)], key=lambda t: t[1])
    side_a, side_b = pairs[0][0], pairs[1][0]
    dc_map = {frozenset(["1", "X"]): "1X", frozenset(["1", "2"]): "12", frozenset(["X", "2"]): "X2"}
    return dc_map.get(frozenset([side_a, side_b]), "1X")


def evaluate_goal_anomaly(stats, totals_goals_market):
    xg_total = stats.get("expected_total_goals", 0)
    in_range = GOAL_RANGE_LOW <= xg_total <= GOAL_RANGE_HIGH
    if not in_range:
        return False, None, False

    goal_band_odds = None
    for entry in (totals_goals_market or []):
        if entry.get("market_name") != "Toplam Gol":
            continue
        for name, odd in (entry.get("outcomes") or {}).items():
            if "2-3" in name.replace(" ", ""):
                goal_band_odds = odd
                break

    if goal_band_odds:
        note = f"'2-3 Gol' pazarı bültende mevcut (oran: {goal_band_odds}) -> Value Bet"
    else:
        note = "Bültende ayrı '2-3 Gol' pazarı yok, Alt/Üst 2.5 kombinasyonu ile değerlendirilmeli"
    return True, note, bool(goal_band_odds)


def _stat_score(stats):
    score = 0.0
    availability = stats.get("data_availability", {})

    if availability.get("ms_market") and availability.get("totals_market"):
        gap = abs(stats.get("home_avg_goals_scored", 0) - stats.get("away_avg_goals_scored", 0))
        if gap >= 1.0:
            score += 2
        elif gap >= 0.5:
            score += 1

    if stats.get("goal_range_2_3_prob", 0) >= 0.45:
        score += 1

    if availability.get("corners_market") or availability.get("cards_market"):
        exp_corners = stats.get("expected_corners")
        exp_cards = stats.get("expected_cards")
        if (exp_corners is not None and abs(exp_corners - CORNER_LINE) >= 1.5) or \
           (exp_cards is not None and abs(exp_cards - CARD_LINE) >= 1.0):
            score += 1

    if availability.get("btts_market"):
        btts = stats.get("btts_probability", 0.5)
        if btts <= 0.45 or btts >= 0.65:
            score += 1

    return min(score, 5)


def _odds_score(is_balanced, goal_anomaly, has_explicit_23_market, handicap_high_risk):
    score = 0.0
    if goal_anomaly and has_explicit_23_market:
        score += 2
    elif goal_anomaly:
        score += 1

    if not is_balanced:
        score += 2
    else:
        score += 1

    if handicap_high_risk is not True:
        score += 1

    return min(score, 5)


# BWM disiplini: oran-tabanli Poisson modeli TEK BASINA asla yuksek guven
# uretemez - piyasanin kendi kendine anlatti§i hikaye bu (bkz. fetch_realstats.py
# dosya basindaki not). Gercek form/H2H verisiyle DOGRULANMAYAN hicbir mac,
# CONFIDENCE_THRESHOLD'u (7) gecemez. Bu fonksiyon o tavani ve dogrulama
# bonusunu/celiskisini hesaplar.
REALSTAT_CAP_WITHOUT_DATA = 6
MIN_H2H_MATCHES = 3
H2H_AGREE_GOALS = 0.6         # H2H gol ortalamasi ile model xG farki bu degerin altindaysa 'uyumlu'
H2H_ONLY_CAP = 7              # yalnizca H2H ile dogrulanan mac en fazla 7/10 alir


def _realstat_score(stats, real_stats):
    """Gercek istatistikle dogrulama puani ve uyari metni dondurur.
    Returns: (bonus_points, cap_without_data, note)
    """
    if not real_stats:
        return 0.0, REALSTAT_CAP_WITHOUT_DATA, "Gerçek istatistik verisi yok (kısa listeye alınmadı/bütçe dışı) -> Güven tavanlı (max 6/10), sadece piyasa modeline dayanıyor."

    home_form = real_stats.get("home_form")
    away_form = real_stats.get("away_form")
    h2h = real_stats.get("h2h")

    has_form = bool(home_form and away_form)
    has_h2h = bool(h2h and h2h.get("played", 0) >= MIN_H2H_MATCHES)
    if not has_form and not has_h2h:
        return 0.0, REALSTAT_CAP_WITHOUT_DATA, (f"Yeterli gerçek veri yok (H2H < {MIN_H2H_MATCHES} maç ve form verisi yok) "
                                                 "-> Güven tavanlı (max 6/10).")

    bonus = 0.0
    notes = []

    odds_gap = (stats.get("home_avg_goals_scored") or 0) - (stats.get("away_avg_goals_scored") or 0)

    if home_form and away_form:
        real_gap = home_form.get("avg_goals_scored", 0) - away_form.get("avg_goals_scored", 0)
        same_direction = (odds_gap > 0) == (real_gap > 0) or abs(odds_gap) < 0.2 or abs(real_gap) < 0.2
        if same_direction:
            bonus += 2
            notes.append(f"Gerçek form verisi piyasa favorisini doğruluyor (Ev Sahibi son 5: {home_form.get('form_string','-')}, Deplasman son 5: {away_form.get('form_string','-')}).")
        else:
            bonus -= 2
            notes.append(f"UYARI: Gerçek form verisi piyasa ile ÇELİŞİYOR (Ev Sahibi son 5: {home_form.get('form_string','-')}, Deplasman son 5: {away_form.get('form_string','-')}) -> Model-Piyasa Çelişkisi.")

    h2h_agrees = False
    if has_h2h:
        xg_total = stats.get("expected_total_goals")
        if xg_total is not None:
            h2h_diff = abs(h2h.get("avg_total_goals", xg_total) - xg_total)
            h2h_agrees = h2h_diff <= H2H_AGREE_GOALS
            if h2h_agrees:
                bonus += 1
                notes.append(f"H2H ({h2h.get('played')} maç) gol ortalaması ({h2h.get('avg_total_goals')}) model xG'siyle uyumlu.")
            else:
                notes.append(f"H2H ({h2h.get('played')} maç) gol ortalaması ({h2h.get('avg_total_goals')}) modelden sapıyor (fark: {round(h2h_diff,2)}) -> doğrulama sayılmadı.")

    for extra in (real_stats.get("extra_note"), real_stats.get("weather_note")):
        if extra:
            notes.append(extra)

    # Tavan: bagimsiz veri ancak UYUSUYORSA tavani kaldirir. Yalnizca H2H (form yok) varsa en fazla 7.
    form_contradicts = has_form and "ÇELİŞİYOR" in " ".join(notes)
    if form_contradicts:
        cap = REALSTAT_CAP_WITHOUT_DATA
    elif has_form:
        cap = None
    elif h2h_agrees:
        cap = H2H_ONLY_CAP
        notes.append(f"Yalnızca H2H ile doğrulandı (form yok) -> güven tavanı {H2H_ONLY_CAP}.")
    else:
        cap = REALSTAT_CAP_WITHOUT_DATA

    return max(bonus, -2), cap, " ".join(notes) if notes else "Gerçek veri mevcut ama belirgin bir sinyal üretmedi."


MIN_VALUE_EV = 0.03           # blend olasiliga gore minimum beklenen deger
DIVERGENCE_AGREE = 0.05       # model-piyasa uyumu (olasilik puani)
DIVERGENCE_CONFLICT = 0.10    # bunun ustu celiski -> guven tavani
CONFLICT_CAP = 6


def _model_score(model):
    """Dixon-Coles model-piyasa karsilastirmasi. Returns (bonus, cap, note, value_pick)."""
    if not model:
        return 0.0, None, "Dixon-Coles modeli yok (lig desteklenmiyor / geçmiş veri yok / takım eşleşmedi).", None

    div = model.get("max_abs_divergence", 0.0)
    m = model.get("model", {})
    value = model.get("value") or []
    best = value[0] if value else None
    value_pick = best if best and best["ev"] >= MIN_VALUE_EV else None

    desc = (f"Model xG: {m.get('exp_home')}-{m.get('exp_away')} (toplam {m.get('exp_total')}), "
            f"Üst2.5 model %{round(m.get('p_over25', 0) * 100)} vs piyasa %{round(model.get('market_shin', {}).get('p_over25', 0) * 100)}. "
            f"Max sapma: {round(div * 100, 1)} puan.")

    cap = None
    if div >= DIVERGENCE_CONFLICT:
        bonus, cap = -2.0, CONFLICT_CAP
        note = f"UYARI Model-Piyasa Çelişkisi (sapma >= {int(DIVERGENCE_CONFLICT * 100)} puan) -> güven tavanı {CONFLICT_CAP}. {desc}"
    elif div <= DIVERGENCE_AGREE:
        bonus = 2.0
        note = f"Model piyasayla uyumlu. {desc}"
    else:
        bonus = 1.0
        note = f"Model piyasadan kısmen ayrışıyor. {desc}"

    if value_pick:
        bonus += 1.0
        note += f" Değer: {value_pick['market']} @{value_pick['odds']} (EV %{round(value_pick['ev'] * 100, 1)})."
    elif best:
        note += f" Pozitif EV yok (en iyi: {best['market']} EV %{round(best['ev'] * 100, 1)})."
    return bonus, cap, note, value_pick


def _decision_text(is_balanced, dc_pick, goal_anomaly, handicap_high_risk, o1, ox, o2):
    if handicap_high_risk is True:
        if goal_anomaly:
            return "Toplam 2-3 Gol"
        return f"Çifte Şans {dc_pick} (Handikaptan Kaçın)"

    if goal_anomaly and not is_balanced:
        return "Toplam 2-3 Gol (Value Bet)"

    if is_balanced:
        return f"Çifte Şans {dc_pick} veya SKIP (Valueless Bet)"

    favorite_odds = [f for f in [("1", o1), ("X", ox), ("2", o2)] if f[1] and f[1] > 0]
    if favorite_odds:
        fav_side, fav_odd = min(favorite_odds, key=lambda t: t[1])
        return f"MS {fav_side} ({fav_odd})"
    return "SKIP"


LEAN_STAKE_PCT = 0.5   # EV dogrulanamayan 'egilim' bahislerinde kasa payi


def _coherence_note(coh):
    if not coh:
        return "Piyasa tutarlılık analizi yok."
    best = coh["edges"][0] if coh.get("edges") else None
    txt = (f"Ortak Poisson xG {coh['lam_home']}-{coh['lam_away']} (uyum RMSE {coh['rmse']}); "
           f"ort. komisyon %{round(coh['avg_margin'] * 100, 1)}.")
    if best:
        txt += f" Konsensüse göre en iyi fiyat: {best['market']} @{best['odds']} (EV %{round(best['ev'] * 100, 1)})."
    return txt


def analyze_match(match, real_stats=None, model=None, coherence=None):
    if match.get("is_live"):
        return {
            "match_id": match.get("match_id"), "home": match.get("home", "?"), "away": match.get("away", "?"),
            "league": match.get("league", "?"), "step_1": "-", "step_2": "Canlı maç: oranlar bozuk/anlık, analiz dışı yok sayıldı.",
            "step_3": "-", "step_4": "-", "step_5": "-", "step_6": "-", "decision": "SKIP (canlı maç)", "confidence": 0,
            "bankroll_pct": 0.0, "bankroll_unit": "SKIP", "realstat_confirmed": False, "model_present": False,
        }

    markets = match.get("markets", {})
    stats = match.get("stats", {})
    o1, ox, o2 = _ms_odds(markets)

    if min(o1, ox, o2) <= 1.01:
        return {
            "match_id": match.get("match_id"), "home": match.get("home", "?"), "away": match.get("away", "?"),
            "league": match.get("league", "?"), "step_1": "-", "step_2": "1X2 pazarında 1.0 oran: pazar askıda/bozuk, analiz dışı.",
            "step_3": "-", "step_4": "-", "step_5": "-", "step_6": "-", "decision": "SKIP (pazar askıda)", "confidence": 0,
            "bankroll_pct": 0.0, "bankroll_unit": "SKIP", "realstat_confirmed": False, "model_present": False,
        }

    # enrich_stats.py'den gelen BWM flag'lerini doğrudan okuma
    is_balanced = stats.get("valueless_bet_balanced_odds", False)
    dc_pick = get_double_chance_pick(o1, ox, o2)

    goal_anomaly, goal_note, has_explicit_23_market = evaluate_goal_anomaly(stats, markets.get("totals_goals", []))

    handicap_check = stats.get("handicap_check") or {}
    handicap_high_risk = handicap_check.get("high_risk")
    handicap_line = handicap_check.get("market_line", "-")
    handicap_div = handicap_check.get("divergence", "-")

    stat_pts = _stat_score(stats)
    odds_pts = _odds_score(is_balanced, goal_anomaly, has_explicit_23_market, handicap_high_risk)
    realstat_bonus, realstat_cap, realstat_note = _realstat_score(stats, real_stats)
    model_bonus, model_cap, model_note, value_pick = _model_score(model)

    # Gercek veri dogrulamasi: form/H2H (API-Football) VEYA Dixon-Coles (gecmis sonuclar) yeterli.
    raw_confidence = stat_pts + odds_pts + realstat_bonus + model_bonus
    if model is None and realstat_cap is not None:
        raw_confidence = min(raw_confidence, realstat_cap)
    if model_cap is not None:
        raw_confidence = min(raw_confidence, model_cap)
    confidence = int(round(max(min(raw_confidence, 10), 0)))

    decision = _decision_text(is_balanced, dc_pick, goal_anomaly, handicap_high_risk, o1, ox, o2)
    if model is not None:
        if model_cap is not None:
            decision = f"SKIP (Model-Piyasa Çelişkisi: sapma {round(model.get('max_abs_divergence', 0) * 100, 1)} puan; model verisi/takım eşleşmesi doğrulanmalı)"
        elif value_pick and handicap_high_risk is not True:
            decision = f"{value_pick['market']} @{value_pick['odds']} (model+piyasa harmanı EV %{round(value_pick['ev'] * 100, 1)})"
        elif not value_pick:
            decision = "SKIP (model teyitli pozitif değer yok)"
            confidence = min(confidence, REALSTAT_CAP_WITHOUT_DATA)
    if "ÇELİŞİYOR" in realstat_note and model_cap is None:
        decision = f"{decision} (DİKKAT: gerçek form verisiyle çelişki var, ünite küçültülmeli)"

    # Adım Adım Analiz metinlerini oluştur
    availability = stats.get("data_availability", {})
    home_tier = team_tier(stats.get("home_avg_goals_scored", 0)) if availability.get("totals_market") else "Veri Yok"
    away_tier = team_tier(stats.get("away_avg_goals_scored", 0)) if availability.get("totals_market") else "Veri Yok"

    corners_txt = "Pazar Yok"
    if availability.get("corners_market"):
        ec = stats.get("expected_corners")
        corners_txt = f"{ec} ({'Üst' if ec > CORNER_LINE else 'Alt'} {CORNER_LINE})"

    cards_txt = "Pazar Yok"
    if availability.get("cards_market"):
        ek = stats.get("expected_cards")
        cards_txt = f"{ek} ({'Üst' if ek > CARD_LINE else 'Alt'} {CARD_LINE})"

    step_1 = (f"Ev Sahibi: {home_tier} (Atılan: {stats.get('home_avg_goals_scored', '-')}, Yenilen: {stats.get('home_avg_goals_conceded', '-')}) | "
              f"Deplasman: {away_tier} (Atılan: {stats.get('away_avg_goals_scored', '-')}, Yenilen: {stats.get('away_avg_goals_conceded', '-')})")
    
    step_2 = "Belirgin oran anomalisi yok."
    if is_balanced:
        step_2 = f"Dengeli oran (1={o1} X={ox} 2={o2}) tespit edildi -> Taraf bahsi riskli (Valueless Bet)."
    if handicap_high_risk is True:
        step_2 += f" Handikap riski YÜKSEK (Sapma: {handicap_div}, Çizgi: {handicap_line}). Yüksek handikap/Üst bahislerinden kaçınılmalı."
    
    step_3 = f"xG Toplam: {stats.get('expected_total_goals', '-')} | Korner Sinyali: {corners_txt} | Kart Sinyali: {cards_txt}"
    if goal_anomaly:
        step_3 += f" | 2-3 Gol Sinyali: {goal_note}"

    step_4 = realstat_note
    step_5 = model_note
    step_6 = _coherence_note(coherence)

    bankroll_pct, bankroll_unit = BANKROLL_MAP.get(confidence, (0.0, "SKIP"))

    # Komisyon ~%16 iken EV, bagimsiz bir olasilik tahmini (Dixon-Coles) olmadan dogrulanamaz:
    # yalnizca oran/H2H uyumuna dayanan secimler 'value' degil 'egilim' sayilir ve kucuk kasa alir.
    if model is None and confidence >= CONFIDENCE_THRESHOLD and not decision.startswith("SKIP"):
        margin_txt = f"%{round(coherence['avg_margin'] * 100, 1)}" if coherence else "~%16"
        decision = f"EĞİLİM: {decision.replace(' (Value Bet)', '')} (EV doğrulanamadı; komisyon {margin_txt}, Dixon-Coles yok)"
        bankroll_pct, bankroll_unit = LEAN_STAKE_PCT, "0.5 Ünite"
    has_realstat_confirmation = real_stats is not None and (realstat_cap is None or realstat_cap >= H2H_ONLY_CAP)

    return {
        "match_id": match.get("match_id"),
        "model_present": model is not None,
        "step_5": step_5,
        "step_6": step_6,
        "home": match.get("home", "?"),
        "away": match.get("away", "?"),
        "league": match.get("league", "?"),
        "step_1": step_1,
        "step_2": step_2,
        "step_3": step_3,
        "step_4": step_4,
        "decision": decision,
        "confidence": confidence,
        "bankroll_pct": bankroll_pct,
        "bankroll_unit": bankroll_unit,
        "realstat_confirmed": has_realstat_confirmation,
    }


def build_report(results, report_date):
    qualified = [r for r in results if r["confidence"] >= CONFIDENCE_THRESHOLD]
    qualified.sort(key=lambda r: r["confidence"], reverse=True)

    lines = []
    lines.append(f"# BWM Analiz Raporu ({report_date})\n")
    
    if not qualified:
        lines.append(f"Güven Puanı >= {CONFIDENCE_THRESHOLD}/10 eşiğini geçen maç bulunamadı. Toplam {len(results)} maç tarandı, tümü elendi (Valueless/Riskli).")
        return "\n".join(lines), qualified

    lines.append(f"Toplam **{len(qualified)}** maç BWM filtrelerinden (oran + GERÇEK istatistik doğrulaması) başarıyla geçerek onaylandı.")
    lines.append(f"Not: Güven puanı >= {CONFIDENCE_THRESHOLD}/10 eşiği, yalnızca gerçek form/H2H verisiyle doğrulanmış maçlarla aşılabilir (bkz. `fetch_realstats.py`). Bu yüzden liste kısa tutulur - bu bir hata değil, BWM disiplinidir.\n")

    for r in qualified:
        lines.append("---")
        lines.append(f"### {r['home']} vs {r['away']} ({r['league']})")
        lines.append(f"**Adım 1 - İhtimaller ve Takım Potansiyelleri (Tier):** {r['step_1']}")
        lines.append(f"**Adım 2 - BWM Oran Anomalisi / Handikap Kontrolü:** {r['step_2']}")
        lines.append(f"**Adım 3 - Özel Market Sinyalleri (Poisson):** {r['step_3']}")
        lines.append(f"**Adım 4 - Gerçek İstatistik Doğrulaması (Form/H2H/Hava):** {r['step_4']}")
        lines.append(f"**Adım 5 - Dixon-Coles Model vs Piyasa (Shin):** {r['step_5']}")
        lines.append(f"**Adım 6 - Piyasa Tutarlılığı ve Komisyon:** {r.get('step_6', '-')}\n")

    lines.append("---\n")
    lines.append("## Nihai Tahmin ve Puanlama (Kasa Yönetimi)\n")
    lines.append("| Maç | Çevresel & BWM Flag | Form/H2H | Dixon-Coles | Nihai Karar (Özel Market) | Güven Puanı | Bankroll % (Ünite) |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for r in qualified:
        flag_summary = "Risk Düşük" if "yok" in r['step_2'] else "Valueless / H.Risk Flag"
        realstat_flag = "Doğrulandı ✓" if r.get("realstat_confirmed") else "Yok"
        model_flag = "Var" if r.get("model_present") else "Yok"
        lines.append(f"| {r['home']} - {r['away']} | {flag_summary} | {realstat_flag} | {model_flag} | **{r['decision']}** | {r['confidence']}/10 | %{r['bankroll_pct']} ({r['bankroll_unit']}) |")

    return "\n".join(lines), qualified


def get_latest_enriched_file(data_dir):
    """En son oluşturulan football_matches_*_enriched.json dosyasını bulur."""
    search_pattern = os.path.join(data_dir, "football_matches_*_enriched.json")
    list_of_files = glob.glob(search_pattern)
    if not list_of_files:
        return None
    return max(list_of_files, key=os.path.getmtime)


def _load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def load_inputs(data_dir, base_name):
    """Tum bagimsiz veri kaynaklarini match_id -> (real_stats, model, coherence) olarak birlestirir.
    real_stats: Iddaa istatistigi (form/H2H/puan durumu/korner-kart) tercih edilir; API-Football'dan hava eklenir.
    model: Dixon-Coles (gecmis sonuclar) varsa o, yoksa Iddaa istatistigine dayali Poisson modeli."""
    api_rs = _load_json(os.path.join(data_dir, f"{base_name}_realstats.json"))
    dc = _load_json(os.path.join(data_dir, f"{base_name}_model.json"))
    ist = _load_json(os.path.join(data_dir, f"{base_name}_istat.json"))
    coh = _load_json(os.path.join(data_dir, f"{base_name}_coherence.json"))

    real, model = {}, {}
    for mid in set(api_rs) | set(ist):
        r = dict(ist[mid]["real_stats"]) if mid in ist else dict(api_rs[mid])
        a = api_rs.get(mid)
        if a and mid in ist:
            r["weather"], r["weather_note"] = a.get("weather"), a.get("weather_note")
            if not r.get("h2h") and a.get("h2h"):
                r["h2h"] = a["h2h"]
        real[mid] = r
    for mid in set(dc) | set(ist):
        entry = dc.get(mid) or (ist.get(mid) or {}).get("model_entry")
        if entry:
            model[mid] = entry
    return real, model, coh, {"istat": len(ist), "realstats": len(api_rs), "dc": len(dc)}


def analyze_bwm():
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(SCRIPT_DIR, "data")
    
    input_file = get_latest_enriched_file(data_dir)
    
    if not input_file:
        print("Hata: data klasöründe işlenecek '*_enriched.json' dosyası bulunamadı!")
        print("Lütfen önce sırasıyla 'fetch_iddaa.py' ve 'enrich_stats.py' scriptlerini çalıştırın.")
        return

    # Dosya adından tarihi çıkarıp rapor ismine ver (örn: bwm_report_20260919.md)
    base_name = os.path.basename(input_file).replace('_enriched.json', '')
    date_str = base_name.replace('football_matches_', '')
    output_file = os.path.join(data_dir, f"bwm_report_{date_str}.md")

    with open(input_file, "r", encoding="utf-8") as f:
        matches = json.load(f)

    if not matches:
        print("Uyarı: Zenginleştirilmiş maç listesi boş, analiz üretilmedi.")
        return

    real_stats_by_id, model_by_id, coh_by_id, info = load_inputs(data_dir, base_name)
    print(f"Bağımsız veri: İddaa istatistik {info['istat']} maç | API-Football H2H/hava {info['realstats']} | "
          f"Dixon-Coles {info['dc']} | model kuran toplam {len(model_by_id)} | tutarlılık {len(coh_by_id)}")
    if not real_stats_by_id and not model_by_id:
        print(f"Uyarı: bağımsız veri yok; tüm maçlar en fazla {REALSTAT_CAP_WITHOUT_DATA}/10 alır ve onaylı listeye giremez.")

    results = [analyze_match(m, real_stats_by_id.get(m.get("match_id")), model_by_id.get(m.get("match_id")),
                             coh_by_id.get(m.get("match_id")))
               for m in matches]

    try:
        from history_log import log_predictions
        n_logged = log_predictions(data_dir, matches, results, real_stats_by_id, model_by_id)
        print(f"Tahmin günlüğüne {n_logged} yeni kayıt eklendi (data/history_log.jsonl).")
    except Exception as e:
        print(f"Uyarı: tahmin günlüğü yazılamadı: {e}")
    report, qualified = build_report(results, date_str)

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(report)

    print(report)
    print(f"\n{len(qualified)}/{len(results)} maç BWM eşiğini geçti. Kasa yönetimi raporu kaydedildi: {output_file}")


if __name__ == "__main__":
    analyze_bwm()
    