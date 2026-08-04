"""DLP masking for the Sandbox (privacy.py).

Contract per the Sandbox team: the DLP checks ONLY bank account numbers and card numbers —
the standard pass masks exactly that and keeps everything else verbatim. The strict pass
(automatic retry) applies the full arsenal in case the documented rules prove incomplete.
"""
import unittest

import privacy


class TestMaskStandard(unittest.TestCase):
    """Standard pass: card/account numbers masked, everything else untouched."""

    def test_card_and_account_numbers_masked(self):
        s = privacy.mask("карта 4276380012345678, р/с 40703810529100000006, "
                         "к/с №30101810200000000824")
        for number in ("4276380012345678", "40703810529100000006", "30101810200000000824"):
            self.assertNotIn(number, s)

    def test_grouped_card_number_masked(self):
        s = privacy.mask("карта 4276 3800 1234 5678 или 4276-3800-1234-5678")
        self.assertNotIn("4276 3800 1234 5678", s)
        self.assertNotIn("4276-3800-1234-5678", s)

    def test_everything_else_is_verbatim(self):
        # not in the DLP rules → must reach the judge unchanged
        text = ("OPERATOR: Вам поможет Анжелика\n"
                "Здравствуйте, Никита! Ваш e-mail ivan@mail.ru, тел +7 (905) 621-67-53\n"
                "CVV не называйте. ИНН 1841012345/КПП 184101001, БИК 042202824\n"
                "списание 299 ₽ 12 июля со счёта 40817810***7020, подписка «Альфа-Смарт»")
        self.assertEqual(privacy.mask(text), text)

    def test_short_and_masked_numbers_survive(self):
        s = privacy.mask("код подтверждения 123456, сумма 10 638 ₽, счёт 40817810***7020")
        self.assertIn("123456", s)
        self.assertIn("10 638", s)
        self.assertIn("40817810***7020", s)


class TestMaskStrict(unittest.TestCase):
    """Strict retry: the full arsenal — for rows the standard pass could not save."""

    def test_all_digits_and_topics_masked(self):
        s = privacy.mask("долг по счету 12345, задолженность 500 ₽", strict=True)
        self.assertFalse(any(ch.isdigit() for ch in s))
        self.assertNotIn("счету", s)
        self.assertNotIn("задолженность", s)

    def test_names_emails_phones_masked(self):
        s = privacy.mask("Вам поможет Анжелика. Здравствуйте, Никита! "
                         "Пишите на ivan@mail.ru, звоните +7 (905) 621-67-53", strict=True)
        for pd in ("Анжелика", "Никита", "ivan@mail.ru", "621-67-53"):
            self.assertNotIn(pd, s)
        self.assertIn("Вам поможет специалист", s)
        self.assertIn("Здравствуйте, клиент", s)

    def test_org_identities_masked(self):
        s = privacy.mask('СНТ Север, ТОВАРИЩЕСТВО "СЕВЕР", АО «АЛЬФА-БАНК», '
                         'Филиал «Нижегородский»', strict=True)
        for org in ("Север", "СЕВЕР", "АЛЬФА-БАНК", "Нижегородский"):
            self.assertNotIn(org, s)
        self.assertIn("организация", s)

    def test_product_names_survive(self):
        # org masking is anchored on org-type words — subscriptions stay recognizable
        s = privacy.mask("подписка «Альфа-Смарт» за 299 ₽", strict=True)
        self.assertIn("Альфа-Смарт", s)

    def test_newlines_kept(self):
        s = privacy.mask("CLIENT: Да\nOPERATOR:   Подписку   отключила", strict=True)
        self.assertEqual(s.count("\n"), 1)           # transcript structure survives
        self.assertIn("Подписку отключила", s)       # runs of spaces collapsed


class TestMaskMessages(unittest.TestCase):
    def test_system_verbatim_others_masked(self):
        msgs = [{"role": "system", "content": "правила: сверяй карту 4276380012345678"},
                {"role": "user", "content": "карта 4276380012345678"},
                {"role": "assistant", "content": None, "tool_calls": [{"id": "t1"}]}]
        out = privacy.mask_messages(msgs)
        self.assertIn("4276380012345678", out[0]["content"])   # system untouched
        self.assertNotIn("4276380012345678", out[1]["content"])
        self.assertIsNone(out[2]["content"])                   # non-string passes through

    def test_detects_sandbox_dlp_error(self):
        body = '{"status":400,"description":"Personal data is found.","error":"HAS_PERSONAL_DATA"}'
        self.assertTrue(privacy.is_personal_data_error(body))
        self.assertFalse(privacy.is_personal_data_error('{"error":"RATE_LIMIT"}'))


if __name__ == "__main__":
    unittest.main()
