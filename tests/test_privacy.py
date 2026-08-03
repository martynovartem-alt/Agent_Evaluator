"""DLP masking for the Sandbox (privacy.py): what gets masked, what must survive."""
import unittest

import privacy


class TestMaskStandard(unittest.TestCase):
    def test_emails_urls_cards_expiry(self):
        s = privacy.mask("пишите на ivan.petrov@mail.ru или на https://alfabank.ru/help, "
                         "карта 4276380012345678, срок 09/27")
        self.assertNotIn("ivan.petrov@mail.ru", s)
        self.assertNotIn("alfabank.ru/help", s)
        self.assertNotIn("4276380012345678", s)      # 16 digits → x-masked
        self.assertNotIn("09/27", s)                 # expiry → xx/xx
        self.assertIn("email", s)
        self.assertIn("url", s)

    def test_sensitive_words_neutralized(self):
        s = privacy.mask("назовите CVV и код из смс, номер карты и PIN")
        for trigger in ("CVV", "код ", "номер карты", "PIN"):
            self.assertNotIn(trigger, s)

    def test_cardholder_caps_name_lowercased(self):
        self.assertNotIn("IVAN PETROV", privacy.mask("на карте IVAN PETROV"))
        self.assertNotIn("ИВАН ПЕТРОВ", privacy.mask("клиент ИВАН ПЕТРОВ"))

    def test_amounts_dates_and_masked_accounts_survive(self):
        # the judge needs amounts/dates; short digit runs and pre-masked accounts stay
        s = privacy.mask("списание 299 ₽ 12 июля со счёта 40817810***7020")
        self.assertIn("299", s)
        self.assertIn("12 июля", s)
        self.assertIn("40817810***7020", s)

    def test_newlines_kept(self):
        s = privacy.mask("CLIENT (06:52:59): Да\nOPERATOR:   Подписку   отключила")
        self.assertEqual(s.count("\n"), 1)           # transcript structure survives
        self.assertIn("Подписку отключила", s)       # runs of spaces collapsed


class TestMaskStrict(unittest.TestCase):
    def test_all_digits_and_topics_masked(self):
        s = privacy.mask("долг по счету 12345, задолженность 500 ₽", strict=True)
        self.assertFalse(any(ch.isdigit() for ch in s))
        self.assertNotIn("счету", s)
        self.assertNotIn("задолженность", s)


class TestMaskMessages(unittest.TestCase):
    def test_system_verbatim_others_masked(self):
        msgs = [{"role": "system", "content": "правила: сверяй номер карты"},
                {"role": "user", "content": "мой e-mail x@y.ru"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]}]
        out = privacy.mask_messages(msgs)
        self.assertEqual(out[0]["content"], "правила: сверяй номер карты")  # system untouched
        self.assertNotIn("x@y.ru", out[1]["content"])
        self.assertIsNone(out[2]["content"])          # non-string content passes through

    def test_detects_sandbox_dlp_error(self):
        body = '{"status":400,"description":"Personal data is found.","error":"HAS_PERSONAL_DATA"}'
        self.assertTrue(privacy.is_personal_data_error(body))
        self.assertFalse(privacy.is_personal_data_error('{"error":"RATE_LIMIT"}'))


if __name__ == "__main__":
    unittest.main()
