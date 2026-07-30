"""Sandbox API client contract: rate-limit slot math + required headers + TLS (no network)."""
import io
import json
import ssl
import unittest
import urllib.error
from unittest import mock

import config
import errors
import oai


def _spec(system_id: str = "", rps: float = 0.0,
          base_url: str = "https://sandbox.test/internal/llm/v1",
          insecure: bool = False) -> config.AgentSpec:
    return config.AgentSpec(role="resolution", provider="openai", mode="llm",
                            base_url=base_url, api_key="uuid-key",
                            model="test-model", effort="medium", prompt="prompts/resolution.md",
                            system_id=system_id, rps=rps, insecure=insecure)


def _fake_urlopen(captured: list, contexts: list | None = None):
    def fake(req, timeout=None, context=None):
        captured.append(req)
        if contexts is not None:
            contexts.append(context)
        body = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}}]})
        resp = mock.MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: False
        resp.read = lambda: body.encode()
        return resp
    return fake


class TestReserveSlot(unittest.TestCase):
    def test_slots_spaced_by_rps(self):
        # 0.2 RPS → 5s between slots; first call sends immediately, next two queue behind it.
        self.assertEqual(oai._reserve_slot("ep-a", 0.2, now=100.0), 0.0)
        self.assertEqual(oai._reserve_slot("ep-a", 0.2, now=100.0), 5.0)
        self.assertEqual(oai._reserve_slot("ep-a", 0.2, now=100.0), 10.0)

    def test_idle_endpoint_sends_immediately(self):
        oai._reserve_slot("ep-b", 0.2, now=200.0)
        self.assertEqual(oai._reserve_slot("ep-b", 0.2, now=300.0), 0.0)  # past slot expired

    def test_endpoints_are_independent(self):
        oai._reserve_slot("ep-c", 0.2, now=100.0)
        self.assertEqual(oai._reserve_slot("ep-d", 0.2, now=100.0), 0.0)

    def test_no_rps_no_wait(self):
        self.assertEqual(oai._reserve_slot("ep-e", 0.0, now=100.0), 0.0)
        self.assertEqual(oai._reserve_slot("ep-e", 0.0, now=100.0), 0.0)


class TestThrottleThroughChat(unittest.TestCase):
    """The rate limit must be wired through chat() itself, not just exist as a helper."""

    def _run_chats(self, specs):
        sleeps, captured = [], []
        with mock.patch.object(oai.urllib.request, "urlopen", side_effect=_fake_urlopen(captured)), \
             mock.patch.object(oai.time, "sleep", side_effect=sleeps.append), \
             mock.patch.object(oai.time, "monotonic", return_value=1000.0):
            for spec in specs:
                oai.chat(spec, [{"role": "user", "content": "hi"}])
        return sleeps

    def test_chat_sleeps_between_sandbox_calls(self):
        spec = _spec(rps=0.2, base_url="https://ep-chat.test/v1")
        # first call sends immediately (no sleep); the next two queue 5s apart
        self.assertEqual(self._run_chats([spec, spec, spec]), [5.0, 10.0])

    def test_base_url_slash_variants_share_one_schedule(self):
        a = _spec(rps=0.2, base_url="https://ep-slash.test/v1")
        b = _spec(rps=0.2, base_url="https://ep-slash.test/v1/")  # same endpoint, other spelling
        self.assertEqual(self._run_chats([a, b]), [5.0])

    def test_no_rps_never_sleeps(self):
        spec = _spec(rps=0.0, base_url="https://ep-free.test/v1")
        self.assertEqual(self._run_chats([spec, spec]), [])


class TestSandboxHeaders(unittest.TestCase):
    def _chat(self, spec):
        """Run oai.chat with urlopen mocked; return the captured urllib Request."""
        captured = []
        with mock.patch.object(oai.urllib.request, "urlopen", side_effect=_fake_urlopen(captured)):
            msg = oai.chat(spec, [{"role": "user", "content": "hi"}])
        self.assertEqual(msg["content"], "ok")
        return captured[0]

    def test_systemid_and_unique_messageid_sent(self):
        req1 = self._chat(_spec(system_id="sanduser"))
        req2 = self._chat(_spec(system_id="sanduser"))
        self.assertEqual(req1.get_header("Systemid"), "sanduser")
        self.assertTrue(req1.get_header("Messageid"))
        # a repeated messageid is a documented Sandbox 400 — must be fresh per request
        self.assertNotEqual(req1.get_header("Messageid"), req2.get_header("Messageid"))
        self.assertEqual(req1.get_header("Authorization"), "Bearer uuid-key")

    def test_headers_absent_without_system_id(self):
        req = self._chat(_spec(system_id=""))
        self.assertIsNone(req.get_header("Systemid"))
        self.assertIsNone(req.get_header("Messageid"))

    def test_url_is_chat_completions(self):
        req = self._chat(_spec(system_id="sanduser"))
        self.assertEqual(req.full_url, "https://sandbox.test/internal/llm/v1/chat/completions")


class TestTlsContext(unittest.TestCase):
    """insecure = true → verification off (curl -k, the Sandbox self-signed cert)."""

    def _chat_context(self, spec):
        """Run oai.chat with urlopen mocked; return the context urlopen received."""
        contexts = []
        with mock.patch.object(oai.urllib.request, "urlopen",
                               side_effect=_fake_urlopen([], contexts)):
            oai.chat(spec, [{"role": "user", "content": "hi"}])
        return contexts[0]

    def test_default_context_is_none(self):
        # no TLS knobs → urlopen gets context=None, i.e. stock certificate verification
        self.assertIsNone(self._chat_context(_spec()))

    def test_insecure_disables_verification(self):
        ctx = self._chat_context(_spec(insecure=True))
        self.assertEqual(ctx.verify_mode, ssl.CERT_NONE)
        self.assertFalse(ctx.check_hostname)


class TestCertErrorRemediation(unittest.TestCase):
    """A certificate failure must point at insecure/ca_bundle, not the VPN hint."""

    def _chat_error(self, exc):
        with mock.patch.object(oai.urllib.request, "urlopen", side_effect=exc):
            with self.assertRaises(errors.NetworkError) as cm:
                oai.chat(_spec(), [{"role": "user", "content": "hi"}])
        return cm.exception

    def test_cert_failure_gets_tls_fix(self):
        reason = ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate")
        e = self._chat_error(urllib.error.URLError(reason))
        self.assertIn("insecure = true", e.what_to_do)
        self.assertIn("SSL_CERT_FILE", e.what_to_do)

    def test_other_network_failure_keeps_vpn_hint(self):
        e = self._chat_error(urllib.error.URLError(OSError("no route to host")))
        self.assertIn("VPN", e.what_to_do)


if __name__ == "__main__":
    unittest.main()
