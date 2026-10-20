# Kasia Suliga Website

Personal website for Katarzyna (Kasia) Suliga Wasielewska, focused on digital strategy and EdTech leadership. Built with Flask.

## Pages

- `/` — Home
- `/about` — About Me
- `/working-together` — Working Together
- `/contact` — Contact form

## Contact form

The contact form is protected by:

- **Cloudflare Turnstile** (`turnstile.py`) — verifies each submission server-side via Cloudflare's Siteverify API before anything is saved.
- **CSRF protection** via Flask-WTF.
- **Rate limiting** via Flask-Limiter (default: 5 submissions per 10 minutes, 20 per day — configurable with `CONTACT_RATE_LIMIT`).

Validated submissions are written as timestamped `.txt` files under the `messages/` directory (created automatically, not committed to git).

## Setup

1. Create a virtual environment and install dependencies:

   ```
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

   For running tests, install dev dependencies instead:

   ```
   pip install -r requirements-dev.txt
   ```

2. Copy `.env.example` to `.env` and fill in your own values (Turnstile keys, secret key, etc.):

   ```
   cp .env.example .env
   ```

3. Run the app:

   ```
   python app.py
   ```

   Set `FLASK_DEBUG=1` in `.env` for local development with the Flask debugger.

## Configuration

Environment variables (see `.env.example` for details):

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs Flask sessions and CSRF tokens. |
| `TURNSTILE_SITE_KEY` / `TURNSTILE_SECRET_KEY` | Cloudflare Turnstile widget keys. |
| `TURNSTILE_EXPECTED_HOSTNAMES` | Comma-separated hostnames the Turnstile widget may run on. |
| `TRUSTED_PROXY_COUNT` | Number of reverse proxies in front of the app; controls trust of `X-Forwarded-*` headers. |
| `RATELIMIT_STORAGE_URI` | Rate limit storage backend (use a shared store like Redis with multiple workers). |
| `CONTACT_RATE_LIMIT` | Rate limit rules for `POST /contact`. |
| `FLASK_DEBUG` | Enables the Flask debugger locally. Keep off in production. |

In production, set these as real environment variables on the hosting platform rather than deploying a `.env` file.

## Tests

```
pytest
```

## Project structure

```
app.py              # Flask app, routes, contact form validation and rate limiting
turnstile.py         # Cloudflare Turnstile server-side verification
templates/           # Jinja2 templates (base layout + pages)
static/              # CSS, JS, and images
tests/               # pytest test suite
```
