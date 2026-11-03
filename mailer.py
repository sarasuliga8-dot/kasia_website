"""Email notifications for contact form messages, sent through an SMTP service.

Any SMTP provider works (for example Gmail with an app password, Brevo,
Mailgun or SendGrid). The settings come from environment variables, so no
credentials live in the code.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from flask import current_app

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 10


class MailUnavailable(Exception):
    """The notification email could not be sent."""


def _env_bool(name, default):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def init_app(app):
    """Load the SMTP settings from the environment."""
    username = os.environ.get("SMTP_USERNAME", "")
    app.config["SMTP_HOST"] = os.environ.get("SMTP_HOST", "")
    app.config["SMTP_PORT"] = int(os.environ.get("SMTP_PORT", "587"))
    app.config["SMTP_USERNAME"] = username
    app.config["SMTP_PASSWORD"] = os.environ.get("SMTP_PASSWORD", "")
    # True for port 465 (implicit TLS). Otherwise the connection upgrades with STARTTLS.
    app.config["SMTP_USE_SSL"] = _env_bool("SMTP_USE_SSL", False)
    # Where contact form messages are delivered.
    app.config["CONTACT_EMAIL_TO"] = os.environ.get("CONTACT_EMAIL_TO", "")
    # The sender address. Most providers require it to be a verified address.
    app.config["CONTACT_EMAIL_FROM"] = os.environ.get("CONTACT_EMAIL_FROM", "") or username


def is_configured():
    config = current_app.config
    return bool(config["SMTP_HOST"] and config["CONTACT_EMAIL_TO"] and config["CONTACT_EMAIL_FROM"])


def send_contact_email(name, email, message, sent_at):
    """Email one contact form submission to the site owner.

    Returns False when SMTP is not configured, so local development works without
    a mail account. Raises MailUnavailable if the server cannot send the email.
    The visitor's address goes in Reply-To, so replying goes straight to them.
    """
    if not is_configured():
        logger.info("SMTP is not configured; contact email was not sent.")
        return False

    config = current_app.config
    mail = EmailMessage()
    mail["Subject"] = f"New contact form message from {name}"
    mail["From"] = config["CONTACT_EMAIL_FROM"]
    mail["To"] = config["CONTACT_EMAIL_TO"]
    mail["Reply-To"] = formataddr((name, email))
    # A unique Message-ID, tied to the sending domain, is expected in legitimate mail
    mail["Message-ID"] = make_msgid(domain=config["CONTACT_EMAIL_FROM"].rpartition("@")[2] or None)
    mail.set_content(
        f"Date: {sent_at:%Y-%m-%d %H:%M:%S}\n"
        f"Name: {name}\n"
        f"Email: {email}\n\n"
        f"Message:\n{message}\n"
    )

    tls_context = ssl.create_default_context()
    try:
        if config["SMTP_USE_SSL"]:
            server = smtplib.SMTP_SSL(
                config["SMTP_HOST"], config["SMTP_PORT"], timeout=TIMEOUT_SECONDS, context=tls_context
            )
        else:
            server = smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=TIMEOUT_SECONDS)
        with server:
            if not config["SMTP_USE_SSL"]:
                server.starttls(context=tls_context)
            if config["SMTP_USERNAME"]:
                server.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
            server.send_message(mail)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailUnavailable(f"SMTP send failed: {type(exc).__name__}") from None

    return True
