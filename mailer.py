"""Email sending, provider-agnostic.

Selects the implementation via the MAIL_BACKEND env var:
  - "console" (default): logs the email locally — no credentials needed,
    and verification links land in mailer.OUTBOX (used by local dev/tests).
  - "smtp": real SMTP over TLS. Gmail uses smtp.gmail.com:587 with
    MAIL_USER + MAIL_APP_PASSWORD (an App Password — never the Google
    account password). For local mail preview, point MAIL_HOST at Mailpit
    (e.g. 127.0.0.1:1025) and leave credentials blank.
  - "resend": Resend REST API (RESEND_API_KEY + a verified domain).

Everything funnels through send_email(), so the auth layer never cares
which backend is configured.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mailer")

MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")

# Console backend keeps the last sent emails so tests/dev can inspect them.
OUTBOX: list[dict] = []


def _smtp_send(to_email: str, subject: str, html: str) -> None:
    host = os.environ.get("MAIL_HOST", "smtp.gmail.com")
    port = int(os.environ.get("MAIL_PORT", "587"))
    user = os.environ.get("MAIL_USER", "")
    password = os.environ.get("MAIL_APP_PASSWORD", "")
    from_addr = os.environ.get("MAIL_FROM", user or "no-reply@notes-saas.local")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content("Please view this email with HTML enabled.")
    msg.add_alternative(html, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            if user:
                server.login(user, password)
            server.send_message(msg)
        return

    with smtplib.SMTP(host, port, timeout=15) as server:
        if port == 587:
            server.starttls(context=context)
        if user:
            server.login(user, password)
        server.send_message(msg)


def send_email(to_email: str, subject: str, html: str) -> None:
    """Send one HTML email — the single seam every mail message goes through."""
    if MAIL_BACKEND == "smtp":
        _smtp_send(to_email, subject, html)
        logger.info("email queued to %s via smtp", to_email)
    elif MAIL_BACKEND == "resend":
        import requests
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {os.environ.get('RESEND_API_KEY')}"},
            json={
                "from": os.environ.get("MAIL_FROM", "Notes <no-reply@example.com>"),
                "to": [to_email],
                "subject": subject,
                "html": html,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("email queued to %s via resend", to_email)
    else:
        OUTBOX.append({"to": to_email, "subject": subject, "html": html})
        logger.info("[console mail] to=%s subject=%r", to_email, subject)


def send_verification_email(to_email: str, verify_url: str) -> None:
    """One purpose-built email, used by signup and resend-verification."""
    subject = "Confirm your email"
    html = (
        "<h2>Welcome to Notes Workspace 👋</h2>"
        "<p>Please confirm your email address to finish setting up your account.</p>"
        "<p><a style='background:#2383e2;color:#fff;padding:10px 16px;border-radius:8px;"
        "text-decoration:none;font-weight:600' href='" + verify_url + "'>Verify email</a></p>"
        "<p style='color:#8b8b8b;font-size:12px'>Or paste this link into your browser:<br>"
        + verify_url + "</p>"
    )
    send_email(to_email, subject, html)