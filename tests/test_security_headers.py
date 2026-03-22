"""
Oura Edge — Security Header Unit Tests
Verifies that the Flask API returns all required security headers
on every route, including error responses.

Run: cd /Users/saintlydigital-clawbot/oura-claude && python -m pytest tests/test_security_headers.py -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

import pytest
from index import app

REQUIRED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options":        "DENY",
    "Referrer-Policy":        "strict-origin-when-cross-origin",
    "Permissions-Policy":     "geolocation=(), microphone=(), camera=()",
    "Cache-Control":          "no-store",
}

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


class TestSecurityHeaders:
    """Every API response must include all security headers."""

    def _assert_security_headers(self, response):
        for header, expected_value in REQUIRED_HEADERS.items():
            actual = response.headers.get(header)
            assert actual is not None, f"Missing security header: {header}"
            assert actual == expected_value, (
                f"Header {header}: expected '{expected_value}', got '{actual}'"
            )

    def test_validate_endpoint_missing_token(self, client):
        """Missing token on /api/validate must return 4xx and security headers."""
        r = client.get("/api/validate")
        assert r.status_code in (400, 401)
        self._assert_security_headers(r)

    def test_validate_endpoint_bad_token(self, client):
        """Bad token → 401, but headers still present."""
        r = client.get("/api/validate", headers={"X-Oura-Token": "badtoken123"})
        # Will get 401 or 200 depending on network; just check headers exist
        self._assert_security_headers(r)

    def test_data_endpoint_missing_token(self, client):
        """Missing token on /api/data → 401 with security headers."""
        r = client.get("/api/data")
        assert r.status_code == 401
        self._assert_security_headers(r)

    def test_demo_endpoint_has_security_headers(self, client):
        """Demo data endpoint must also return security headers."""
        r = client.get("/api/demo")
        assert r.status_code == 200
        self._assert_security_headers(r)

    def test_unknown_api_route_has_security_headers(self, client):
        """404 from unknown API path must still have security headers."""
        r = client.get("/api/nonexistent-endpoint-xyz")
        self._assert_security_headers(r)

    def test_no_sensitive_data_in_error_response(self, client):
        """Error responses must not leak token, stack traces, or internal paths."""
        r = client.get("/api/data")
        body = r.get_json() or {}

        # Must not contain Python-specific error internals
        body_str = str(body)
        assert "Traceback" not in body_str
        assert "File \"" not in body_str
        assert "line " not in body_str.lower() or "baseline" in body_str.lower()

        # Must not echo back an actual token value (header name in help text is OK)
        assert "Bearer " not in body_str

    def test_error_response_is_generic(self, client):
        """Error messages must be user-safe strings, not str(exception)."""
        r = client.get("/api/data")
        body = r.get_json() or {}
        error_msg = body.get("error", "")

        # Should be a clean error code, not a Python exception string
        assert "urlopen error" not in error_msg
        assert "ConnectionRefusedError" not in error_msg
        assert "TimeoutError" not in error_msg
        assert len(error_msg) < 100, f"Error message suspiciously long: {error_msg}"

    def test_cors_wildcard_not_present_on_api(self, client):
        """API routes should not broadcast Access-Control-Allow-Origin: * """
        r = client.get("/api/data")
        cors = r.headers.get("Access-Control-Allow-Origin", "")
        assert cors != "*", "API must not allow wildcard CORS on data endpoints"

    def test_no_server_version_leakage(self, client):
        """Server header must not expose Flask/Werkzeug version details."""
        r = client.get("/api/validate")
        server = r.headers.get("Server", "")
        assert "Werkzeug" not in server, f"Server header leaks runtime: {server}"
        assert "Python" not in server


class TestTokenHandling:
    """Verify token validation behaviour."""

    def test_empty_token_returns_401(self, client):
        r = client.get("/api/data", headers={"X-Oura-Token": ""})
        assert r.status_code == 401

    def test_whitespace_only_token_returns_401(self, client):
        r = client.get("/api/data", headers={"X-Oura-Token": "   "})
        assert r.status_code == 401

    def test_no_token_header_returns_401(self, client):
        r = client.get("/api/data")
        assert r.status_code == 401

    def test_demo_endpoint_requires_no_token(self, client):
        """Demo endpoint must be accessible without a token."""
        r = client.get("/api/demo")
        assert r.status_code == 200

    def test_response_body_never_echoes_token(self, client):
        """Any token sent in the header must never appear in the response body."""
        test_token = "TESTTOKENVALUE12345"
        r = client.get("/api/data", headers={"X-Oura-Token": test_token})
        body_str = r.get_data(as_text=True)
        assert test_token not in body_str, "Token was echoed back in response body!"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
