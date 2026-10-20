import json
import logging
import os
import re
import secrets
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent

# Local development only: values in .env are loaded if the file exists.
# Real environment variables always take precedence, and production should
# set them through the hosting platform instead of a .env file.
load_dotenv(BASE_DIR / ".env")

app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("SECRET_KEY"):
    # A random key works for local development but changes on every restart
    # and differs between worker processes, so set SECRET_KEY in production.
    app.logger.warning("SECRET_KEY is not set; using a temporary random key.")

app.config["MESSAGES_DIR"] = BASE_DIR / "messages"
app.config["TURNSTILE_SITE_KEY"] = os.environ.get("TURNSTILE_SITE_KEY", "")
app.config["TURNSTILE_SECRET_KEY"] = os.environ.get("TURNSTILE_SECRET_KEY", "")
app.config["TURNSTILE_EXPECTED_HOSTNAMES"] = {
    host.strip().lower()
    for host in os.environ.get("TURNSTILE_EXPECTED_HOSTNAMES", "").split(",")
    if host.strip()
}

# Only trust X-Forwarded-For / X-Forwarded-Proto when the app really sits
# behind a reverse proxy. Set this to the number of proxies in front of the app
# (for example 1 behind a single nginx or hosting-platform load balancer).
# The default of 0 ignores forwarded headers entirely, so clients cannot fake
# their IP address to bypass rate limits.
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "0"))
if TRUSTED_PROXY_COUNT > 0:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXY_COUNT,
        x_proto=TRUSTED_PROXY_COUNT,
    )

csrf = CSRFProtect(app)

# Rate limits for the contact form submission only (POST /contact).
# Adjust with the CONTACT_RATE_LIMIT environment variable, using the
# "<count> per <period>" syntax, separated by semicolons.
CONTACT_RATE_LIMIT = os.environ.get("CONTACT_RATE_LIMIT", "5 per 10 minutes; 20 per day")

# "memory://" is per process and resets on restart, so it only suits local
# development or a single worker. Use a shared store such as
# RATELIMIT_STORAGE_URI=redis://... when running several workers.
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
    strategy="fixed-window",
)

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT_SECONDS = 5
TURNSTILE_TOKEN_MAX_LENGTH = 2048

# Errors that mean our configuration or the service is at fault, not the visitor.
TURNSTILE_SERVICE_ERROR_CODES = {
    "missing-input-secret",
    "invalid-input-secret",
    "bad-request",
    "internal-error",
}

ALLOWED_FIELDS = {"name", "email", "message", "cf-turnstile-response"}
MAX_LENGTHS = {"name": 100, "email": 254, "message": 5000}
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
# Control characters other than tab, newline and carriage return.
CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

logger = logging.getLogger(__name__)


class TurnstileRejected(Exception):
    """Cloudflare says the token is invalid, expired or already used."""


class TurnstileUnavailable(Exception):
    """Verification could not be completed, so the submission must be refused."""


def verify_turnstile(token):
    """Verify a Turnstile token server-side with Cloudflare's Siteverify API.

    Returns None when verification succeeds. Raises TurnstileRejected when
    Cloudflare rejects the token, and TurnstileUnavailable for any other
    failure. Callers must treat both exceptions as "do not save".
    """
    secret = app.config.get("TURNSTILE_SECRET_KEY")
    if not secret:
        raise TurnstileUnavailable("TURNSTILE_SECRET_KEY is not configured")

    body = urllib.parse.urlencode({"secret": secret, "response": token}).encode("utf-8")
    req = urllib.request.Request(
        TURNSTILE_VERIFY_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urllib.request.urlopen(req, timeout=TURNSTILE_TIMEOUT_SECONDS) as response:
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
        if TURNSTILE_SERVICE_ERROR_CODES.intersection(error_codes):
            raise TurnstileUnavailable(f"Siteverify error: {', '.join(map(str, error_codes))}")
        raise TurnstileRejected(f"Token rejected: {', '.join(map(str, error_codes)) or 'no codes'}")

    expected_hostnames = app.config.get("TURNSTILE_EXPECTED_HOSTNAMES") or set()
    if expected_hostnames:
        hostname = str(payload.get("hostname", "")).lower()
        if hostname not in expected_hostnames:
            raise TurnstileRejected("Token was issued for an unexpected hostname")


def validate_contact_form(data):
    """Return (cleaned_values, errors). errors maps field names to messages."""
    errors = {}
    cleaned = {}

    for field in ("name", "email", "message"):
        value = data.get(field)
        if not isinstance(value, str):
            errors[field] = "This field is required."
            continue

        value = value.strip()
        if field == "message":
            # Keep the visitor's line breaks; normalise Windows line endings.
            value = value.replace("\r\n", "\n").replace("\r", "\n")
        if not value:
            errors[field] = "This field is required."
            continue
        if len(value) > MAX_LENGTHS[field]:
            errors[field] = f"Please keep this under {MAX_LENGTHS[field]} characters."
            continue
        if CONTROL_CHARS.search(value):
            errors[field] = "Please remove unsupported characters."
            continue
        if field != "message" and ("\n" in value or "\r" in value):
            errors[field] = "Please enter this on a single line."
            continue

        cleaned[field] = value

    if "email" not in errors and not EMAIL_PATTERN.match(cleaned["email"]):
        errors["email"] = "Please enter a valid email address, e.g. name@example.com."

    return cleaned, errors


@app.context_processor
def inject_turnstile_site_key():
    # The site key is public and is rendered into the page. The secret never is.
    return {"turnstile_site_key": app.config.get("TURNSTILE_SITE_KEY", "")}


@app.route("/")
def home():
    return render_template("home.html")


@app.route("/contact", methods=["GET", "POST"])
@limiter.limit(CONTACT_RATE_LIMIT, methods=["POST"])
def contact():
    if request.method == "POST":
        return handle_contact_submission()

    return render_template("contact.html")


def handle_contact_submission():
    if not request.is_json:
        return jsonify(error="Unsupported request format."), 415

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Invalid submission."), 400

    if not set(data.keys()) <= ALLOWED_FIELDS:
        return jsonify(error="Invalid submission."), 400

    token = data.get("cf-turnstile-response")
    if not isinstance(token, str) or not token.strip():
        return jsonify(
            error="Please complete the security check and try again.",
            retry_turnstile=True,
        ), 400
    token = token.strip()
    if len(token) > TURNSTILE_TOKEN_MAX_LENGTH:
        return jsonify(
            error="The security check could not be verified. Please try again.",
            retry_turnstile=True,
        ), 400

    cleaned, errors = validate_contact_form(data)
    if errors:
        return jsonify(error=" ".join(errors.values()), fields=errors), 400

    # Validation happens before verification so a typo doesn't use up the token.
    # Nothing is saved until the token is verified below.
    try:
        verify_turnstile(token)
    except TurnstileRejected as exc:
        logger.info("Turnstile rejected a contact submission: %s", exc)
        return jsonify(
            error="The security check could not be verified. Please complete it again.",
            retry_turnstile=True,
        ), 400
    except TurnstileUnavailable as exc:
        logger.error("Turnstile verification unavailable: %s", exc)
        return jsonify(
            error="Verification is temporarily unavailable. Please try again shortly."
        ), 503

    now = datetime.now()
    messages_dir = app.config["MESSAGES_DIR"]
    messages_dir.mkdir(exist_ok=True)
    filename = messages_dir / f"message_{now:%Y-%m-%d_%H-%M-%S_%f}_{secrets.token_hex(4)}.txt"
    with filename.open("x", encoding="utf-8") as handle:
        handle.write(
            f"Date: {now:%Y-%m-%d %H:%M:%S}\n"
            f"Name: {cleaned['name']}\n"
            f"Email: {cleaned['email']}\n\n"
            f"Message:\n{cleaned['message']}\n"
        )
    return jsonify(ok=True)


@app.errorhandler(CSRFError)
def handle_csrf_error(error):
    if request.path == "/contact":
        return jsonify(
            error="Your session has expired. Please reload the page and try again.",
        ), 400
    return error


@app.errorhandler(429)
def handle_rate_limit(error):
    if request.path == "/contact":
        return jsonify(
            error="Too many attempts. Please wait a while before trying again.",
        ), 429
    return error


@app.route("/about")
def about():
    return render_template("about.html")


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
