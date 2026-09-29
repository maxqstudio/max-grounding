from __future__ import annotations

import unittest

from max_grounding.network_policy import is_admissible_result_url


class ResultUrlAdmissionTests(unittest.TestCase):
    def test_accepts_public_http_and_https_targets(self) -> None:
        self.assertTrue(is_admissible_result_url("https://example.com/article"))
        self.assertTrue(is_admissible_result_url("http://93.184.216.34/path"))

    def test_rejects_credentials_and_non_http_schemes(self) -> None:
        self.assertFalse(is_admissible_result_url("https://user:pass@example.com/x"))
        self.assertFalse(is_admissible_result_url("file:///etc/passwd"))
        self.assertFalse(is_admissible_result_url("javascript:alert(1)"))

    def test_rejects_localhost_names(self) -> None:
        self.assertFalse(is_admissible_result_url("http://localhost/admin"))
        self.assertFalse(is_admissible_result_url("http://api.localhost/admin"))

    def test_rejects_literal_non_public_ip_targets(self) -> None:
        blocked = [
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://[::ffff:127.0.0.1]/",
            "http://127.1/",
            "http://2130706433/",
            "http://10.0.0.5/",
            "http://172.16.0.5/",
            "http://192.168.1.5/",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",
            "http://192.0.2.1/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                self.assertFalse(is_admissible_result_url(url))

    def test_rejects_malformed_ports(self) -> None:
        self.assertFalse(is_admissible_result_url("https://example.com:99999/x"))


if __name__ == "__main__":
    unittest.main()
