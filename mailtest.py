"""Diagnose why verification emails aren't arriving.

Run from the project folder:
    venv\\Scripts\\python mailtest.py                 # checks config + SMTP login
    venv\\Scripts\\python mailtest.py --send you@gmail.com

It reads the same config as the app: env vars, or the local `.env` file
(gitignored). Fill MAIL_USER (Gmail address or Brevo SMTP login) and
MAIL_APP_PASSWORD (Gmail App Password or Brevo SMTP key) there first.
"""

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

backend = os.environ.get("MAIL_BACKEND") or "console"
user = os.environ.get("MAIL_USER") or ""
password_raw = os.environ.get("MAIL_APP_PASSWORD") or ""
password = password_raw.replace(" ", "")
is_brevo = password_raw.strip().lower().startswith("xsmtpsib-")
# Same resolution as the app (mailer._smtp_host): MAIL_HOST is the documented
# name, MAIL_SERVER accepted as an alias so this diagnostic matches real
# behavior on the hosting dashboard. A Brevo SMTP key defaults the host to
# smtp-relay.brevo.com, exactly like the app does.
host = (
    os.environ.get("MAIL_HOST")
    or os.environ.get("MAIL_SERVER")
    or ("smtp-relay.brevo.com" if is_brevo else "smtp.gmail.com")
)
port = int(os.environ.get("MAIL_PORT", "587"))
from_addr = os.environ.get("MAIL_FROM") or user or "(not set)"
provider = "Brevo" if is_brevo else ("Gmail" if "gmail" in host.lower() else "other")

print("=== mail config ===")
print(f"MAIL_BACKEND        = {backend}")
print(f"Provider            = {provider}")
print(f"MAIL_USER           = {user or '(not set)'}")
if password:
    kind = "chars, Brevo SMTP key" if is_brevo else "chars"
    print(f"MAIL_APP_PASSWORD   = set ({len(password)} {kind})")
else:
    print("MAIL_APP_PASSWORD   = (NOT SET)")
print(f"MAIL_HOST / PORT    = {host}:{port}")
print(f"MAIL_FROM           = {from_addr}")
if not os.environ.get("MAIL_HOST") and os.environ.get("MAIL_SERVER"):
    print("(note: SMTP host came from MAIL_SERVER — the app documents MAIL_HOST;")
    print("  both work, but rename it to MAIL_HOST in the dashboard to match the docs)")
if is_brevo:
    print("(Brevo: MAIL_USER must be the SMTP login from Brevo → Settings → SMTP & API →")
    print("  SMTP tab — an email-format identifier, NOT your account password. The")
    print("  xsmtpsib-… key above is the password only. The From address (default")
    print("  MAIL_USER) must be a verified sender in Brevo, or Brevo rejects the mail.)")
print()

if backend != "smtp":
    print("Result: backend is NOT smtp. To send real email, set")
    print("  MAIL_BACKEND=smtp  plus MAIL_USER + MAIL_APP_PASSWORD")
    print("(On Render: Environment -> Add from .env. Locally: .env file.)")
    sys.exit(1)

if not user or not password:
    print("Result: missing MAIL_USER or MAIL_APP_PASSWORD for smtp.")
    sys.exit(2)

print(f"Connecting to {host}:{port}...")
try:
    context = ssl.create_default_context()
    if port == 465:
        server = smtplib.SMTP_SSL(host, port, context=context, timeout=20)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
        server.ehlo()
        # Upgrade to TLS whenever the relay offers STARTTLS (587 and 2525
        # both do) so the login is never sent in plaintext.
        if server.has_extn("starttls"):
            server.starttls(context=context)
            server.ehlo()
    print("Connected ✓")
    server.login(user, password)
    print("SMTP login OK ✓ — the credentials are valid.")

    if "--send" in sys.argv:
        target = sys.argv[sys.argv.index("--send") + 1]
        msg = EmailMessage()
        msg["Subject"] = "Notes workspace — mail test"
        msg["From"] = from_addr
        msg["To"] = target
        msg.set_content(
            "This is a test email from your Notes workspace. "
            "If you're reading this, SMTP delivery works. 🎉"
        )
        server.send_message(msg)
        print(f"Test email sent to {target} ✓ (check inbox + Spam)")
    server.quit()
    print("DONE — email delivery is configured correctly.")
except smtplib.SMTPAuthenticationError as err:
    print("SMTP login FAILED — credentials rejected.")
    print(f"  ({err.smtp_code}) {err.smtp_error.decode(errors='replace')}")
    if is_brevo:
        print("Brevo causes: (1) MAIL_USER is not the SMTP login from Brevo →")
        print("  Settings → SMTP & API → SMTP tab. The login is a separate")
        print("  email-format identifier — NOT your Brevo account password, and")
        print("  NOT the SMTP key. Copy the value from the 'Login' field there.")
        print("  (2) An API key (xkeysib-…) is set instead of an SMTP key (xsmtpsib-…).")
        print("  (3) The key contains a trailing space or line break.")
    else:
        print("Likely causes: wrong App Password / SMTP login, or 2-Step Verification")
        print("  is off (Gmail), or a stray space in the key.")
    sys.exit(3)
except Exception as err:
    print("Mail test FAILED:")
    print(f"  {type(err).__name__}: {err}")
    sys.exit(4)
