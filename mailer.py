"""Email sending, provider-agnostic.

Selects the implementation via the MAIL_BACKEND env var:
  - "console" (default): logs the email locally — no credentials needed,
    and verification links land in mailer.OUTBOX (used by local dev/tests).
  - "smtp": real SMTP over TLS. Gmail uses smtp.gmail.com:587 with
    MAIL_USER + MAIL_APP_PASSWORD (an App Password — never the Google
    account password). Host/port come from MAIL_HOST (MAIL_SERVER is
    accepted as an alias, with a warning) and MAIL_PORT. For local mail
    preview, point MAIL_HOST at Mailpit (e.g. 127.0.0.1:1025) and leave
    credentials blank.
  - "resend": Resend REST API (RESEND_API_KEY + a verified domain).

Everything funnels through send_email(), so the auth layer never cares
which backend is configured.
"""

import logging
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mailer")
# uvicorn's default logging config installs handlers only on its own
# "uvicorn.*" loggers — the root logger has none, so records propagated from
# this module are dropped below WARNING (Python's lastResort handler) and the
# config line never reaches the logs. Give the mailer logger its own stderr
# handler when nothing upstream is configured, so Render logs show it too.
logger.setLevel(logging.INFO)
if not logger.handlers and not logging.getLogger().handlers:
    _handler = logging.StreamHandler()  # stderr — same place uvicorn logs go
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)

MAIL_BACKEND = os.environ.get("MAIL_BACKEND") or "console"
MAIL_USER = os.environ.get("MAIL_USER") or ""
MAIL_APP_PASSWORD = os.environ.get("MAIL_APP_PASSWORD") or ""

# Console backend keeps the last sent emails so tests/dev can inspect them.
OUTBOX: list[dict] = []


def _smtp_host() -> str:
    """SMTP hostname for the `smtp` backend.

    MAIL_HOST is the documented name; MAIL_SERVER is accepted as an alias
    (a common name for the same setting on hosting dashboards) but logs a
    warning, so a misnamed env var never silently diverges from the docs.
    """
    host = os.environ.get("MAIL_HOST") or os.environ.get("MAIL_SERVER") or "smtp.gmail.com"
    if not os.environ.get("MAIL_HOST") and os.environ.get("MAIL_SERVER"):
        logger.warning(
            "mailer: SMTP host read from MAIL_SERVER=%r — the documented name is "
            "MAIL_HOST; rename it in the hosting dashboard to silence this warning",
            os.environ["MAIL_SERVER"],
        )
    return host


def _log_config() -> None:
    """Log which backend is active and whether credentials are present, so
    Render logs immediately reveal why an email did (or didn't) get sent."""
    if MAIL_BACKEND == "smtp":
        if MAIL_USER and MAIL_APP_PASSWORD:
            logger.info(
                "mailer: backend=smtp user=%r host=%s:%s (credentials present)",
                MAIL_USER,
                _smtp_host(),
                os.environ.get("MAIL_PORT", "587"),
            )
        else:
            logger.warning(
                "mailer: MAIL_BACKEND=smtp but MAIL_USER/MAIL_APP_PASSWORD are not set "
                "— will fall back to console capture"
            )
    elif MAIL_BACKEND == "resend":
        logger.info(
            "mailer: backend=resend key=%s",
            "set" if os.environ.get("RESEND_API_KEY") else "MISSING",
        )
    else:
        logger.info("mailer: backend=console (verification links go to the server logs)")


_log_config()


def _smtp_send(to_email: str, subject: str, html: str) -> None:
    host = _smtp_host()
    port = int(os.environ.get("MAIL_PORT", "587"))
    user = MAIL_USER
    password = MAIL_APP_PASSWORD.replace(" ", "")  # Gmail shows spaces; SMTP doesn't need them
    from_addr = os.environ.get("MAIL_FROM") or (user or "no-reply@notes-saas.local")

    if not user or not password:
        raise RuntimeError("MAIL_USER/MAIL_APP_PASSWORD missing for the smtp backend")

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
        server.ehlo()
        # Opportunistic TLS: upgrade whenever the relay offers STARTTLS
        # (587 and 2525 both do) so credentials never cross the network in
        # plaintext — regardless of which non-SSL port the dashboard is set to.
        if server.has_extn("starttls"):
            server.starttls(context=context)
            server.ehlo()
        if user:
            server.login(user, password)
        server.send_message(msg)


def send_email(to_email: str, subject: str, html: str) -> None:
    """Send one HTML email — the single seam every mail message goes through.

    If a real backend (smtp/resend) is configured but fails — bad credentials,
    SMTP down — we still capture the message in OUTBOX (console) and log the
    real error loudly, so a verification link is never silently lost.
    """
    if MAIL_BACKEND == "smtp":
        try:
            _smtp_send(to_email, subject, html)
            logger.info("email queued to %s via smtp", to_email)
            return
        except Exception:
            logger.exception("SMTP send FAILED — falling back to console capture")
    elif MAIL_BACKEND == "resend":
        try:
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
            return
        except Exception:
            logger.exception("Resend send FAILED — falling back to console capture")

    OUTBOX.append({"to": to_email, "subject": subject, "html": html})
    # The console backend's contract (see module docstring) is that emailed
    # links "go to the server logs" — so log the link itself, not just the
    # subject. Without this there is no way to click the link locally or
    # from Render's log viewer.
    link_match = re.search(r"href=['\"]([^'\"]+)['\"]", html)
    link = link_match.group(1) if link_match else "(no link found in body)"
    logger.warning("[console mail] to=%s subject=%r link=%s", to_email, subject, link)


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


def send_password_reset_email(to_email: str, reset_url: str) -> None:
    """Password-reset email, sent by the /auth/forgot-password endpoint."""
    subject = "Reset your password"
    html = (
        "<h2>Reset your password 🔐</h2>"
        "<p>You (or someone who clicked 'Forgot password') requested a password reset.</p>"
        "<p><a style='background:#2383e2;color:#fff;padding:10px 16px;border-radius:8px;"
        "text-decoration:none;font-weight:600' href='" + reset_url + "'>Reset password</a></p>"
        "<p style='color:#8b8b8b;font-size:12px'>Or paste this link into your browser:<br>"
        + reset_url + "</p>"
        "<p style='color:#8b8b8b;font-size:12px'>This link expires in 24 hours.</p>"
    )
    send_email(to_email, subject, html)