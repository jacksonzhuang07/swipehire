"""Pluggable outbound mail for sign-in codes.

Modes (first match wins):
  1. Resend  — env RESEND_API_KEY set: POST https://api.resend.com/emails,
               From = env SWIPEHIRE_MAIL_FROM or onboarding@resend.dev.
  2. SMTP    — env SMTP_HOST set (SMTP_PORT/SMTP_USER/SMTP_PASS/SMTP_FROM,
               SMTP_TLS=1 for STARTTLS).
  3. Demo    — nothing configured: NO email is sent. The caller surfaces the
               code in the UI in a clearly-labelled demo box instead. This is
               a deliberate demo tradeoff, documented in README/BUILD_NOTES.

Test hook: when env SWIPEHIRE_MAIL_CAPTURE is set, messages are appended to
LAST_SENT (and not actually delivered) so tests can read the code.
"""
import json, os, smtplib, urllib.request, urllib.error
from email.message import EmailMessage

LAST_SENT = []


def mail_mode():
    if os.environ.get("RESEND_API_KEY"):
        return "resend"
    if os.environ.get("SMTP_HOST"):
        return "smtp"
    return "demo"


def _send_resend(to, subject, text):
    payload = {
        "from": os.environ.get("SWIPEHIRE_MAIL_FROM") or "onboarding@resend.dev",
        "to": [to],
        "subject": subject,
        "text": text,
    }
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}",
                 "Content-Type": "application/json",
                 "User-Agent": "SwipeHire/4.0"},
        method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status in (200, 201)


def _send_smtp(to, subject, text):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ.get("SMTP_USER") or ""
    pw = os.environ.get("SMTP_PASS") or ""
    sender = os.environ.get("SMTP_FROM") or user or "no-reply@swipehire.app"
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(text)
    with smtplib.SMTP(host, port, timeout=15) as s:
        if os.environ.get("SMTP_TLS", "1") != "0":
            s.starttls()
        if user:
            s.login(user, pw)
        s.send_message(msg)
    return True


def send_email(to, subject, text):
    """Returns (mode, delivered). Never raises: delivery failure falls back to
    demo-style surfacing by the caller when mode becomes 'demo'."""
    mode = mail_mode()
    if os.environ.get("SWIPEHIRE_MAIL_CAPTURE"):
        LAST_SENT.append({"to": to, "subject": subject, "text": text, "mode": mode})
        return mode, True
    if mode == "demo":
        return "demo", False
    try:
        ok = _send_resend(to, subject, text) if mode == "resend" else _send_smtp(to, subject, text)
        return mode, bool(ok)
    except Exception:
        return "demo", False


def send_signin_code(to, code):
    subject = "Your SwipeHire sign-in code"
    text = (f"Your SwipeHire sign-in code is {code}\n\n"
            f"It expires in 10 minutes and can only be used once.\n"
            f"If you didn't request this, you can ignore this email.")
    return send_email(to, subject, text)
