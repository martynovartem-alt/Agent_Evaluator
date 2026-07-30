"""Typed error hierarchy: where/what_to_do carried through to diagnoses."""
import json
import ssl
import unittest

import errors


class TestHierarchy(unittest.TestCase):
    def test_where_per_class(self):
        self.assertEqual(errors.DatasetError("x").where, "dataset")
        self.assertEqual(errors.ConfigError("x").where, "config")
        self.assertEqual(errors.ApiError("x").where, "api")
        self.assertEqual(errors.NetworkError("x").where, "api")
        self.assertEqual(errors.LlmOutputError("x").where, "llm_output")

    def test_info_shape(self):
        info = errors.NetworkError("model cannot reach url").info()
        self.assertEqual(info["where"], "api")
        self.assertIn("VPN", info["what_to_do"])
        self.assertIn("cannot reach", info["detail"])


class TestApiRemediations(unittest.TestCase):
    def test_sandbox_error_table(self):
        # the codes documented in "4. Sandbox API.pdf" map to specific fixes
        self.assertIn("systemid", errors.ApiError.from_http(406, "no systemid", "m").what_to_do)
        self.assertIn("rps", errors.ApiError.from_http(429, "too many", "m").what_to_do)
        self.assertIn("key", errors.ApiError.from_http(401, "unauthorized", "m").what_to_do)
        e = errors.ApiError.from_http(418, "teapot", "m")
        self.assertEqual(e.status, 418)
        self.assertEqual(e.what_to_do, errors.ApiError.default_fix)  # unknown code → generic


class TestNetworkRemediations(unittest.TestCase):
    def test_cert_failure_points_at_tls_knobs(self):
        # cert errors happen ON the VPN — the VPN hint would mislead; point at insecure/ca_bundle
        reason = ssl.SSLCertVerificationError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: self-signed certificate")
        e = errors.NetworkError.from_url_error(reason, "m", "https://sandbox.test/v1")
        self.assertEqual(e.where, "api")
        self.assertIn("cannot reach", str(e))
        self.assertIn("insecure = true", e.what_to_do)
        self.assertIn("ca_bundle", e.what_to_do)

    def test_cert_failure_detected_from_reason_text(self):
        # some urllib paths stringify the SSL error into reason — detect by marker too
        e = errors.NetworkError.from_url_error(
            "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate", "m", "u")
        self.assertIn("insecure = true", e.what_to_do)

    def test_non_cert_failure_keeps_vpn_hint(self):
        e = errors.NetworkError.from_url_error(OSError("no route to host"), "m", "u")
        self.assertEqual(e.what_to_do, errors.NetworkError.default_fix)
        self.assertIn("VPN", e.what_to_do)


class TestErrorInfo(unittest.TestCase):
    def test_typed_passthrough(self):
        info = errors.error_info(errors.ConfigError("no key"))
        self.assertEqual(info["where"], "config")

    def test_json_decode_is_llm_output(self):
        try:
            json.loads("{broken")
        except json.JSONDecodeError as e:
            info = errors.error_info(e)
        self.assertEqual(info["where"], "llm_output")

    def test_foreign_exception_defaults_to_api(self):
        info = errors.error_info(TimeoutError("timed out"))
        self.assertEqual(info["where"], "api")
        self.assertIn("TimeoutError", info["detail"])


if __name__ == "__main__":
    unittest.main()
