"""Neden bu kadar cok mac SKIP? Her macin ilk engelini siniflandirip sayar (kompakt cikti)."""
import os
import sys
import json
import glob
import collections

sys.stdout.reconfigure(encoding="utf-8")
import analyze_bwm as A
from dixon_coles_model import LEAGUE_CODES

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    src = max(glob.glob(os.path.join(DATA, "football_matches_*_enriched.json")), key=os.path.getmtime)
    base = src.replace("_enriched.json", "")
    matches = load(src)
    rs, md, coh, _ = A.load_inputs(DATA, os.path.basename(base))

    reasons = collections.Counter()
    examples = collections.defaultdict(list)
    for m in matches:
        r = A.analyze_match(m, rs.get(m["match_id"]), md.get(m["match_id"]), coh.get(m["match_id"]))
        if r["confidence"] >= A.CONFIDENCE_THRESHOLD:
            key = "ONAYLI"
        elif m.get("is_live"):
            key = "Canlı maç (oranlar bozuk)"
        elif m["stats"].get("valueless_bet_balanced_odds"):
            key = "Dengeli oran (Valueless Bet kuralı)"
        elif m["match_id"] not in rs and m["match_id"] not in md:
            key = ("Bağımsız veri yok: desteklenen lig ama kısa liste/kota dışı" if m.get("league") in LEAGUE_CODES
                   else "Bağımsız veri yok: alt/egzotik lig (istatistik kaynağı yok)")
        elif r["decision"].startswith("SKIP (Model-Piyasa"):
            key = "Bağımsız model piyasadan çok ayrışıyor (>=10 puan): veri/örneklem güvenilmez"
        elif r["decision"].startswith("SKIP (model teyitli"):
            key = "Bağımsız model var ama pozitif EV yok (komisyon ~%16)"
        elif m["match_id"] in rs and ("sapıyor" in r["step_4"] or "Yeterli gerçek veri yok" in r["step_4"]):
            key = "H2H var ama modelle uyuşmuyor / yetersiz (<3 maç)"
        else:
            key = "Temel sinyal zayıf (güven < 7)"
        reasons[key] += 1
        if len(examples[key]) < 3:
            examples[key].append(f"{m['home']}-{m['away']}")

    total = len(matches)
    print(f"Toplam analiz edilen: {total}\n\n| Engel | Maç | % | Örnek |\n| :--- | ---: | ---: | :--- |")
    for k, v in reasons.most_common():
        print(f"| {k} | {v} | %{round(100 * v / total, 1)} | {'; '.join(examples[k][:2])} |")


if __name__ == "__main__":
    main()
