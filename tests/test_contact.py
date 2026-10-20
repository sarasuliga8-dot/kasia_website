"""Tests for the contact form: Turnstile, CSRF, validation, and rate limiting.

Cloudflare's Siteverify endpoint is mocked at the urllib boundary, so no real
network calls or real Turnstile keys are needed. CSRF stays enabled: each test
fetches a real token from the page, exactly as the browser does.
"""

import json
import re
import socket
import urllib.error
from unittest import mock

import pytest

import app as app_module

VALID_BODY = {
    "name": "Test Visitor",
    "email": "visitor@example.com",
    "message": "Hello there, this is a test message.",
    "cf-turnstile-response": "token-from-widget",
}
TEST_SECRET = "test-secret-value-not-real"
TEST_SITE_KEY = "test-site-key-not-real"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self, size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def success_payload(**extra):
    return {"success": True, "hostname": "localhost", "challenge_ts": "2026-10-15T10:00:00Z", **extra}


@pytest.fixture
def messages_dir(tmp_path):
    return tmp_path / "messages"


@pytest.fixture
def client(messages_dir):
    flask_app = app_module.app
    flask_app.config.update(
        TESTING=True,
        TURNSTILE_SITE_KEY=TEST_SITE_KEY,
        TURNSTILE_SECRET_KEY=TEST_SECRET,
        TURNSTILE_EXPECTED_HOSTNAMES=set(),
        MESSAGES_DIR=messages_dir,
    )
    app_module.limiter.reset()
    with flask_app.test_client() as test_client:
        yield test_client


def csrf_token(client):
    html = client.get("/contact").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def submit(client, body, csrf=True):
    headers = {"X-CSRFToken": csrf_token(client)} if csrf else {}
    return client.post("/contact", json=body, headers=headers)


def saved_files(messages_dir):
    return sorted(messages_dir.glob("*.txt")) if messages_dir.exists() else []


def siteverify_returns(payload, status=200):
    return mock.patch("turnstile.urllib.request.urlopen", return_value=FakeResponse(payload, status))


# 1. Valid submission with successful Turnstile verification
def test_valid_submission_with_successful_turnstile_is_saved(client, messages_dir):
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, VALID_BODY)

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert len(saved_files(messages_dir)) == 1
    saved = saved_files(messages_dir)[0].read_text(encoding="utf-8")
    assert "Name: Test Visitor" in saved
    assert "Email: visitor@example.com" in saved
    assert "Hello there, this is a test message." in saved

    # Verification happened server-side, once, with a timeout and the right parameters
    assert urlopen.call_count == 1
    request_obj = urlopen.call_args.args[0]
    assert request_obj.full_url == "https://challenges.cloudflare.com/turnstile/v0/siteverify"
    sent = request_obj.data.decode("utf-8")
    assert "secret=test-secret-value-not-real" in sent
    assert "response=token-from-widget" in sent
    assert urlopen.call_args.kwargs["timeout"] == 5


# 2. Submission with no CAPTCHA token
def test_missing_token_is_rejected_without_calling_cloudflare(client, messages_dir):
    body = {k: v for k, v in VALID_BODY.items() if k != "cf-turnstile-response"}
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, body)

    assert response.status_code == 400
    assert response.get_json()["retry_turnstile"] is True
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


# 3. Invalid or expired token
@pytest.mark.parametrize("code", ["invalid-input-response", "timeout-or-duplicate"])
def test_invalid_or_expired_token_is_rejected(client, messages_dir, code):
    with siteverify_returns({"success": False, "error-codes": [code]}):
        response = submit(client, VALID_BODY)

    assert response.status_code == 400
    assert response.get_json()["retry_turnstile"] is True
    assert saved_files(messages_dir) == []


# 4. Failed or unavailable Cloudflare verification service (must fail closed)
@pytest.mark.parametrize(
    "side_effect",
    [
        urllib.error.URLError("network down"),
        socket.timeout("timed out"),
        urllib.error.HTTPError("https://example", 500, "Server Error", None, None),
    ],
)
def test_unavailable_siteverify_fails_closed(client, messages_dir, side_effect):
    with mock.patch("turnstile.urllib.request.urlopen", side_effect=side_effect):
        response = submit(client, VALID_BODY)

    assert response.status_code == 503
    assert saved_files(messages_dir) == []


def test_malformed_siteverify_response_fails_closed(client, messages_dir):
    with siteverify_returns(b"<html>not json</html>"):
        response = submit(client, VALID_BODY)
    assert response.status_code == 503
    assert saved_files(messages_dir) == []


def test_siteverify_response_without_success_field_fails_closed(client, messages_dir):
    with siteverify_returns({"hostname": "localhost"}):
        response = submit(client, VALID_BODY)
    assert response.status_code == 503
    assert saved_files(messages_dir) == []


def test_siteverify_success_must_be_true_boolean(client, messages_dir):
    with siteverify_returns({"success": "yes"}):
        response = submit(client, VALID_BODY)
    assert response.status_code == 503
    assert saved_files(messages_dir) == []


def test_service_side_error_code_is_treated_as_unavailable(client, messages_dir):
    with siteverify_returns({"success": False, "error-codes": ["invalid-input-secret"]}):
        response = submit(client, VALID_BODY)
    assert response.status_code == 503
    assert saved_files(messages_dir) == []


def test_missing_secret_key_fails_closed_without_network_call(client, messages_dir):
    app_module.app.config["TURNSTILE_SECRET_KEY"] = ""
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, VALID_BODY)

    assert response.status_code == 503
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


def test_hostname_mismatch_is_rejected(client, messages_dir):
    app_module.app.config["TURNSTILE_EXPECTED_HOSTNAMES"] = {"example.org"}
    with siteverify_returns(success_payload(hostname="evil.test")):
        response = submit(client, VALID_BODY)
    assert response.status_code == 400
    assert saved_files(messages_dir) == []


def test_error_responses_never_contain_the_secret(client):
    with siteverify_returns({"success": False, "error-codes": ["invalid-input-response"]}):
        response = submit(client, VALID_BODY)
    assert TEST_SECRET not in response.get_data(as_text=True)


# 5. Invalid or missing required fields
@pytest.mark.parametrize("field", ["name", "email", "message"])
def test_missing_required_field_is_rejected(client, messages_dir, field):
    body = dict(VALID_BODY)
    del body[field]
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, body)

    assert response.status_code == 400
    assert field in response.get_json()["fields"]
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


def test_whitespace_only_fields_are_rejected(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "name": "   ", "message": "\n\t "})
    assert response.status_code == 400
    assert set(response.get_json()["fields"]) == {"name", "message"}
    assert saved_files(messages_dir) == []


def test_non_string_field_is_rejected(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "name": ["a", "b"]})
    assert response.status_code == 400
    assert "name" in response.get_json()["fields"]


# 6. Text fields exceeding permitted lengths
def test_name_over_limit_is_rejected(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "name": "a" * 101})
    assert response.status_code == 400
    assert "name" in response.get_json()["fields"]


def test_message_over_limit_is_rejected(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "message": "a" * 5001})
    assert response.status_code == 400
    assert "message" in response.get_json()["fields"]


def test_fields_at_exact_limit_are_accepted(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "name": "a" * 100, "message": "b" * 5000})
    assert response.status_code == 200


# 7. Invalid email addresses
@pytest.mark.parametrize("email", ["not-an-email", "a@b", "x" * 250 + "@example.com", "two@@example.com"])
def test_invalid_email_is_rejected(client, messages_dir, email):
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, {**VALID_BODY, "email": email})
    assert response.status_code == 400
    assert "email" in response.get_json()["fields"]
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


def test_newline_injection_in_single_line_field_is_rejected(client, messages_dir):
    with siteverify_returns(success_payload()):
        response = submit(client, {**VALID_BODY, "name": "Alice\nEmail: forged@example.com"})
    assert response.status_code == 400
    assert "name" in response.get_json()["fields"]
    assert saved_files(messages_dir) == []


def test_unexpected_fields_are_rejected(client, messages_dir):
    # A client cannot claim Turnstile passed by sending its own flag
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, {**VALID_BODY, "turnstile_success": True, "is_admin": True})
    assert response.status_code == 400
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


def test_non_json_content_type_is_rejected(client, messages_dir):
    response = client.post(
        "/contact",
        data="name=x&email=y&message=z",
        content_type="application/x-www-form-urlencoded",
        headers={"X-CSRFToken": csrf_token(client)},
    )
    assert response.status_code == 415
    assert saved_files(messages_dir) == []


def test_malformed_json_body_is_rejected(client, messages_dir):
    response = client.post(
        "/contact",
        data="{not valid json",
        content_type="application/json",
        headers={"X-CSRFToken": csrf_token(client)},
    )
    assert response.status_code == 400
    assert saved_files(messages_dir) == []


def test_json_array_body_is_rejected(client):
    response = client.post(
        "/contact",
        json=["not", "an", "object"],
        headers={"X-CSRFToken": csrf_token(client)},
    )
    assert response.status_code == 400


# 9. Direct submission to the endpoint without passing CAPTCHA
def test_direct_post_without_captcha_or_csrf_is_blocked(client, messages_dir):
    body = {k: v for k, v in VALID_BODY.items() if k != "cf-turnstile-response"}
    with siteverify_returns(success_payload()) as urlopen:
        response = client.post("/contact", json=body)  # no CSRF token either

    assert response.status_code == 400
    assert "session has expired" in response.get_json()["error"]
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


def test_csrf_token_is_required_even_with_valid_turnstile(client, messages_dir):
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, VALID_BODY, csrf=False)
    assert response.status_code == 400
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []


# 8. Requests exceeding the rate limit
def test_contact_post_rate_limit_is_enforced_and_ignores_spoofed_forwarded_for(client, messages_dir):
    with siteverify_returns(success_payload()):
        for index in range(5):
            response = client.post(
                "/contact",
                json=VALID_BODY,
                headers={"X-CSRFToken": csrf_token(client), "X-Forwarded-For": f"203.0.113.{index}"},
            )
            assert response.status_code == 200

        blocked = client.post(
            "/contact",
            json=VALID_BODY,
            headers={"X-CSRFToken": csrf_token(client), "X-Forwarded-For": "198.51.100.99"},
        )

    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.get_json()["error"]
    assert len(saved_files(messages_dir)) == 5


def test_rate_limit_does_not_apply_to_page_browsing(client):
    for _ in range(30):
        assert client.get("/contact").status_code == 200
        assert client.get("/").status_code == 200


# 10. Normal website navigation and existing form functionality
def test_home_page_still_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "About Me" in html
    assert "Working Together" in html


def test_contact_page_renders_widget_and_csrf_meta_without_secret(client):
    html = client.get("/contact").get_data(as_text=True)
    assert 'id="contactForm"' in html
    assert 'name="csrf-token"' in html
    assert "https://challenges.cloudflare.com/turnstile/v0/api.js" in html
    assert f'sitekey: "{TEST_SITE_KEY}"' in html
    assert TEST_SECRET not in html


def test_contact_page_without_site_key_omits_widget_and_refuses_tokenless_submission(client, messages_dir):
    app_module.app.config["TURNSTILE_SITE_KEY"] = ""
    html = client.get("/contact").get_data(as_text=True)
    assert "challenges.cloudflare.com" not in html

    body = {k: v for k, v in VALID_BODY.items() if k != "cf-turnstile-response"}
    with siteverify_returns(success_payload()) as urlopen:
        response = submit(client, body)
    assert response.status_code == 400
    assert urlopen.call_count == 0
    assert saved_files(messages_dir) == []
