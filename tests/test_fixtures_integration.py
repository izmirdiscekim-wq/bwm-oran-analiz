"""Internet bagimliligi olmadan, sample_fixtures.json'daki donmus (frozen) beklenen
degerlerle BWM matematigini (devig), ELO guncellemesini ve PIPE format donusturucusunu
dogrular. conftest.py agents/ dizinini sys.path'e ekler."""
import json
import os
import shutil
import tempfile
import unittest

import devig
import elo_tracker as ET
import msa_kaynak as K
import multi_sport_analyzer as M

FIXTURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_fixtures.json")

with open(FIXTURES_PATH, encoding="utf-8") as f:
    FIXTURES = json.load(f)


class DevigFixture(unittest.TestCase):
    def test_shin_fair_probs_matches_frozen_fixture(self):
        case = FIXTURES["devig_case"]
        got = devig.devig(case["odds"], yontem=case["yontem"])
        tol = case["tolerance"]
        for outcome, expected in case["expected_probs"].items():
            self.assertAlmostEqual(got[outcome], expected, delta=tol)
        self.assertAlmostEqual(sum(got.values()), 1.0, delta=1e-6)


class EloFixture(unittest.TestCase):
    def setUp(self):
        self.eski_veri, K.VERI = K.VERI, tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(K.VERI, ignore_errors=True)
        K.VERI = self.eski_veri

    def test_beklenen_ve_guncelle_matches_frozen_fixture(self):
        case = FIXTURES["elo_case"]
        tol = case["tolerance"]

        beklenen_h = ET.beklenen(case["elo_a"], case["elo_b"])
        self.assertAlmostEqual(beklenen_h, case["expected_beklenen_h"], delta=tol)

        yeni_a, yeni_b = ET.guncelle(
            "fixture_spor", "A", "B", case["skor_h"], case["skor_a"], k=case["k"]
        )
        self.assertAlmostEqual(yeni_a, case["expected_elo_a"], delta=tol)
        self.assertAlmostEqual(yeni_b, case["expected_elo_b"], delta=tol)


class PipeFormatFixture(unittest.TestCase):
    def test_to_pipe_format_matches_frozen_fixture(self):
        case = FIXTURES["pipe_case"]
        got = M.to_pipe_format(case["record"])
        self.assertEqual(got, case["expected_pipe"])


if __name__ == "__main__":
    unittest.main()
