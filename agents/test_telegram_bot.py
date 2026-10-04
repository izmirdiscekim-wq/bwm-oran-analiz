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


    def test_asama_ilk_within_30_minutes_and_son15_within_15_minutes(self):
        now = 1_000_000.0
        self.assertIsNone(telegram_bot.asama({"esd": now + 31 * 60}, None, now))
        self.assertEqual(telegram_bot.asama({"esd": now + 29 * 60}, None, now), "ilk")
        self.assertIsNone(telegram_bot.asama({"esd": now - 60}, None, now))
        rec = {"ilk_ts": now - 600, "son15_ts": None, "bildirilen": {}}
        self.assertIsNone(telegram_bot.asama({"esd": now + 20 * 60}, rec, now))
        self.assertEqual(telegram_bot.asama({"esd": now + 10 * 60}, rec, now), "son15")
        rec["son15_ts"] = now - 60
        self.assertIsNone(telegram_bot.asama({"esd": now + 10 * 60}, rec, now))

    def test_kritik_degisim_threshold(self):
        self.assertFalse(telegram_bot.kritik_degisim([2.00, 1.50], [2.04, 1.52]))
        self.assertTrue(telegram_bot.kritik_degisim([2.00, 1.50], [2.12, 1.50]))


if __name__ == "__main__":
    unittest.main()