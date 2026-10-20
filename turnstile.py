"""Cloudflare Turnstile: the security check on the contact form.

The widget itself lives in templates/contact.html. This module handles the
server side: loading the keys from the environment, verifying each token with
Cloudflare's Siteverify API, and refusing anything it cannot confirm.
"""

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request

from flask import current_app

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TIMEOUT_SECONDS = 5
TOKEN_MAX_LENGTH = 2048

# Errors that mean our configuration or the service is at fault, not the visitor.
SERVICE_ERROR_CODES = {
    "missing-input-secret",
    "invalid-input-secret",
    "bad-request",
    "internal-error",
}


class TurnstileRejected(Exception):
    """Cloudflare says the token is invalid, expired or already used."""


class TurnstileUnavailable(Exception):
    """Verification could not be completed, so the submission must be refused."""


def init_app(app):
    """Load the Turnstile settings and expose the public site key to templates."""
    app.config["TURNSTILE_SITE_KEY"] = os.environ.get("TURNSTILE_SITE_KEY", "")
    app.config["TURNSTILE_SECRET_KEY"] = os.environ.get("TURNSTILE_SECRET_KEY", "")
    app.config["TURNSTILE_EXPECTED_HOSTNAMES"] = {
        host.strip().lower()
        for host in os.environ.get("TURNSTILE_EXPECTED_HOSTNAMES", "").split(",")
        if host.strip()
    }

    @app.context_processor
    def inject_turnstile_site_key():
        # The site key is public and is rendered into the page. The secret never is.
        return {"turnstile_site_key": app.config.get("TURNSTILE_SITE_KEY", "")}


def verify(token):
    """Verify a Turnstile token server-side with Cloudflare's Siteverify API.

    Returns None when verification succeeds. Raises TurnstileRejected when
    Cloudflare rejects the token, and TurnstileUnavailable for any other
    failure. Callers must treat both exceptions as "do not save".
    """
    secret = current_app.config.get("TURNSTILE_SECRET_KEY")
    if not secret:
        raise TurnstileUnavailable("TURNSTILE_SECRET_KEY is not configured")

    body = urllib.parse.urlencode({"secret": secret, "response": token}).encode("utf-8")
    req = urllib.request.Request(
        VERIFY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise TurnstileUnavailable(f"Siteverify returned HTTP {response.status}")
            payload = json.loads(response.read(65536).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise TurnstileUnavailable(f"Siteverify returned HTTP {exc.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise TurnstileUnavailable(f"Siteverify request failed: {type(exc).__name__}") from None
    except ValueError:
        # Covers malformed JSON and invalid UTF-8.
        raise TurnstileUnavailable("Siteverify returned a malformed response") from None

    if not isinstance(payload, dict) or not isinstance(payload.get("success"), bool):
        raise TurnstileUnavailable("Siteverify response is missing a valid success field")

    error_codes = payload.get("error-codes") or []
    if not isinstance(error_codes, list):
        error_codes = []

    if payload["success"] is not True:
        if SERVICE_ERROR_CODES.intersection(error_codes):
            raise TurnstileUnavailable(f"Siteverify error: {', '.join(map(str, error_codes))}")
        raise TurnstileRejected(f"Token rejected: {', '.join(map(str, error_codes)) or 'no codes'}")

    expected_hostnames = current_app.config.get("TURNSTILE_EXPECTED_HOSTNAMES") or set()
    if expected_hostnames:
        hostname = str(payload.get("hostname", "")).lower()
        if hostname not in expected_hostnames:
            raise TurnstileRejected("Token was issued for an unexpected hostname")
