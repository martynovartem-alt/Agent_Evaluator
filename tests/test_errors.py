"""Typed error hierarchy: where/what_to_do carried through to diagnoses."""
import json
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
