import unittest
from unittest.mock import Mock, patch

import telegram_bot


class TelegramBotTests(unittest.TestCase):
    def test_parse_allowed_user_ids(self):
        self.assertEqual(telegram_bot.parse_allowed_user_ids("123, 456"), {123, 456})

    def test_parse_allowed_user_ids_rejects_empty_value(self):
        with self.assertRaises(ValueError):
            telegram_bot.parse_allowed_user_ids(" , ")

    def test_parse_command_removes_bot_suffix(self):
        self.assertEqual(telegram_bot.parse_command("/taktik@bwm_bot 4,5 Üst A"), ("/taktik", ["4,5", "Üst", "A"]))

    def test_groups_are_ignored_and_wrong_private_id_is_reported(self):
        base = {"from": {"id": 123}, "text": "/yardim"}
        group_update = {"message": {**base, "chat": {"id": -1, "type": "group"}}}
        private_update = {"message": {**base, "chat": {"id": 123, "type": "private"}}}

        self.assertIsNone(telegram_bot.response_for_update(group_update, {123}))
        self.assertIn("ID'si 123", telegram_bot.response_for_update(private_update, {456}))

    def test_allowed_private_help_command(self):
        update = {
            "message": {
                "from": {"id": 123},
                "chat": {"id": 123, "type": "private"},
                "text": "/yardim",
            }
        }

        self.assertIn("/taktik", telegram_bot.response_for_update(update, {123}))

    def test_split_message_respects_limit(self):
        chunks = telegram_bot.split_message("a" * 25, limit=10)

        self.assertEqual([len(chunk) for chunk in chunks], [10, 10, 5])

    def test_telegram_api_error_reports_status_without_token(self):
        response = Mock()
        response.status_code = 401
        response.json.return_value = {"ok": False, "description": "Unauthorized"}

        with patch.object(telegram_bot.requests, "post", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401: Unauthorized") as error:
                telegram_bot.telegram_call("test-secret", "getMe", {})

        self.assertNotIn("test-secret", str(error.exception))

    def test_telegram_timeout_has_safe_diagnostic(self):
        with patch.object(telegram_bot.requests, "post", side_effect=telegram_bot.requests.Timeout):
            with self.assertRaisesRegex(RuntimeError, "zaman aşımı") as error:
                telegram_bot.telegram_call("test-secret", "getUpdates", {})

        self.assertNotIn("test-secret", str(error.exception))

    def test_token_preflight_reports_unauthorized(self):
        with patch.object(telegram_bot, "telegram_call", side_effect=RuntimeError("Telegram API HTTP 401: Unauthorized")):
            with self.assertRaisesRegex(SystemExit, "HTTP 401: Unauthorized"):
                telegram_bot.validate_bot_token("test-secret")


    def test_radar_stages_are_60_30_and_15_minutes_before_kickoff(self):
        now = 1_000_000.0
        self.assertIsNone(telegram_bot.radar_asamasi({"esd": now + 61 * 60}, now))
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 60 * 60}, now), "60")
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 31 * 60}, now), "60")
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 30 * 60}, now), "30")
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 16 * 60}, now), "30")
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 15 * 60}, now), "15")
        self.assertEqual(telegram_bot.radar_asamasi({"esd": now + 1}, now), "15")
        self.assertIsNone(telegram_bot.radar_asamasi({"esd": now - 1}, now))


class RadarSenaryoTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.kickoff = 1_000_000.0 + 3600
        self.dosya = Path(tempfile.mkdtemp()) / "analyzed.json"
        self.sent = []

    def calistir(self, scan_offsets_and_match):
        import taktik
        event = {"esd": self.kickoff, "esd_ms": 1, "hn": "A", "an": "B", "lig": "L"}
        telegram_bot.ANALYZED_FILE = self.dosya
        for kalan_dk, uyuyor in scan_offsets_and_match:
            now = self.kickoff - kalan_dk * 60
            maclar = {"k": (event, [({"ad": "T"}, [(1.0, True)], ["x"])])} if uyuyor else {}
            with patch.object(telegram_bot.time, "time", return_value=now),                  patch.object(taktik.N, "bulten", return_value=({"olaylar": [event], "cekim": now}, True)),                  patch.object(taktik, "dosya_oku", return_value=(None, [{"aktif": True, "spor": "futbol", "kurallar": [1], "ad": "T"}])),                  patch.object(taktik, "telegram_adaylari", return_value=maclar),                  patch.object(taktik, "telegram_metni", side_effect=lambda m, c, b: b),                  patch.object(telegram_bot, "send_message", side_effect=lambda tok, uid, txt: self.sent.append(txt)):
                telegram_bot.otomatik_tara("x", {1})
        return self.sent

    def test_no_at_60_yes_at_30_no_at_15_sends_only_30(self):
        mesajlar = self.calistir([(58, False), (45, False), (30, True), (25, True), (20, False), (15, False)])
        self.assertEqual(mesajlar, ["Maçın başlamasına yarım saat kaldı. Oran analize uyuyor."])

    def test_no_at_60_no_at_30_yes_at_15_sends_only_15(self):
        mesajlar = self.calistir([(58, False), (30, False), (25, False), (15, True), (10, True)])
        self.assertEqual(mesajlar, ["Maçın başlamasına 15 dakika kaldı. Oran analize uyuyor."])

    def test_yes_at_60_yes_at_30_yes_at_15_sends_three_messages_once_each(self):
        mesajlar = self.calistir([(58, True), (55, True), (30, True), (27, True), (15, True), (10, True), (5, True)])
        self.assertEqual(len(mesajlar), 3)
        self.assertIn("1 saat", mesajlar[0])
        self.assertIn("yarım saat", mesajlar[1])
        self.assertIn("15 dakika", mesajlar[2])


class AcilisRaporuTests(unittest.TestCase):
    def test_acilis_raporu_sends_once_per_match(self):
        import tempfile
        import taktik
        from pathlib import Path
        from datetime import datetime
        now = datetime.now(taktik.N.TR).replace(hour=12, minute=0, second=0, microsecond=0).timestamp()
        event = {"esd": now + 3600, "esd_ms": 9, "hn": "A", "an": "B", "lig": "L"}
        telegram_bot.OPENING_TRACKER = Path(tempfile.mkdtemp()) / "tracker.json"
        sent = []
        with patch.object(telegram_bot.time, "time", return_value=now),              patch.object(taktik.N, "bulten", return_value=({"olaylar": [event], "cekim": now, "surum": 1}, True)),              patch.object(taktik, "dosya_oku", return_value=(None, [{"aktif": True, "spor": "futbol", "kurallar": [1], "ad": "T"}])),              patch.object(taktik, "telegram_adaylari", return_value={"k": (event, [({"ad": "T"}, [(1.0, True)], ["x"])])}),              patch.object(taktik, "telegram_metni", side_effect=lambda m, c, b, **kw: b),              patch.object(telegram_bot, "send_message", side_effect=lambda tok, uid, txt: sent.append(txt)):
            telegram_bot.acilis_raporu("x", [1], otomatik=True)
            telegram_bot.acilis_raporu("x", [1], otomatik=True)
        self.assertEqual(sent, [telegram_bot.OPENING_TITLE])


if __name__ == "__main__":
    unittest.main()