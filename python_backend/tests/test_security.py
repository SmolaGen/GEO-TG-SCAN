import unittest
import sys
import os

# Ensure python_backend is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from main import app
from config import config

class TestSecurityEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = config.auth_token

    def test_missing_token_returns_401(self):
        """API request without Authorization header must return 401"""
        res = self.client.get("/api/status", headers={"Host": "127.0.0.1:8000"})
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.text)

    def test_invalid_token_returns_401(self):
        """API request with incorrect token must return 401"""
        res = self.client.get("/api/status", headers={
            "Host": "127.0.0.1:8000",
            "Authorization": "Bearer wrong_token_value_123"
        })
        self.assertEqual(res.status_code, 401)
        self.assertIn("Unauthorized", res.text)

    def test_valid_token_returns_200(self):
        """API request with matching Bearer token must succeed"""
        res = self.client.get("/api/status", headers={
            "Host": "127.0.0.1:8000",
            "Authorization": f"Bearer {self.token}"
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("token_verified"))

    def test_forbidden_origin_returns_403(self):
        """Request from unauthorized Origin must be rejected with 403"""
        res = self.client.get("/api/status", headers={
            "Host": "127.0.0.1:8000",
            "Origin": "http://evil-attacker-site.com",
            "Authorization": f"Bearer {self.token}"
        })
        self.assertEqual(res.status_code, 403)
        self.assertIn("Forbidden Origin", res.text)

    def test_invalid_host_dns_rebinding_returns_400(self):
        """Request with unauthorized Host header (DNS rebinding) must be rejected with 400"""
        res = self.client.get("/api/status", headers={
            "Host": "evil-rebinding-domain.org",
            "Authorization": f"Bearer {self.token}"
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("DNS Rebinding", res.text)

    def test_static_index_accessible(self):
        """Static index.html is accessible at / for local users"""
        res = self.client.get("/", headers={"Host": "127.0.0.1:8000"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Telegram Live Geo Inspector", res.text)

    def test_html_does_not_contain_token(self):
        """GET / must never contain auth_token in the HTML body"""
        res = self.client.get("/", headers={"Host": "127.0.0.1:8000"})
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(self.token, res.text)
        self.assertNotIn("__BOOTSTRAP_AUTH_TOKEN__", res.text)

    def test_origin_subdomain_bypass_rejected(self):
        """Origin containing localhost or 127.0.0.1 as subdomain must be rejected with 403"""
        for evil_origin in ["https://localhost.evil.com", "http://127.0.0.1.attacker.net", "http://evil-localhost.com"]:
            res = self.client.get("/api/status", headers={
                "Host": "127.0.0.1:8000",
                "Origin": evil_origin,
                "Authorization": f"Bearer {self.token}"
            })
            self.assertEqual(res.status_code, 403, f"Evil origin {evil_origin} must be rejected with 403")

if __name__ == "__main__":
    unittest.main()
