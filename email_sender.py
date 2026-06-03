"""
ZunoUp Email Sender — powered by Resend API
No SMTP ports needed. Works on Render free tier.
Set RESEND_API_KEY in your Render environment variables.
"""
import os
import json
import urllib.request
import urllib.error


def _make_html(username: str, otp: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f0f2f8;font-family:Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 16px;">
      <table width="480" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:16px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(108,71,255,.12);">
        <tr><td style="background:#6c47ff;padding:28px 32px;">
          <h1 style="margin:0;color:#fff;font-size:26px;">ZunoUp</h1>
          <p style="margin:6px 0 0;color:#d4c8ff;font-size:14px;">DTU Student Community</p>
        </td></tr>
        <tr><td style="padding:32px;">
          <p style="margin:0 0 8px;color:#333;font-size:16px;">Hi <strong>{username}</strong> 👋</p>
          <p style="margin:0 0 24px;color:#666;font-size:14px;line-height:1.6;">
            Use the code below to verify your email. Expires in <strong>10 minutes</strong>.
          </p>
          <div style="background:#f0f2f8;border-radius:12px;padding:24px;
                      text-align:center;margin-bottom:24px;">
            <p style="margin:0 0 8px;color:#888;font-size:12px;
                      text-transform:uppercase;letter-spacing:1px;">Your OTP Code</p>
            <div style="font-size:44px;font-weight:700;letter-spacing:14px;
                        color:#6c47ff;font-family:monospace;">{otp}</div>
          </div>
          <p style="margin:0;color:#aaa;font-size:12px;text-align:center;">
            Didn't sign up? Ignore this email.
          </p>
        </td></tr>
        <tr><td style="background:#f8f7ff;padding:16px 32px;border-top:1px solid #ede9ff;">
          <p style="margin:0;color:#ccc;font-size:11px;text-align:center;">
            ZunoUp &middot; DTU Campus
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_otp_email(sender_email: str, sender_password: str,
                   to_email: str, otp: str, username: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")

    if not api_key:
        print("[EMAIL] ✗ RESEND_API_KEY not set in environment variables!")
        _fallback(to_email, otp)
        return False

    payload = json.dumps({
        "from": "ZunoUp <onboarding@resend.dev>",
        "to": [to_email],
        "subject": "ZunoUp – Your verification code",
        "html": _make_html(username, otp),
        "text": f"Hi {username},\n\nYour ZunoUp OTP is: {otp}\n\nExpires in 10 minutes."
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            print(f"[EMAIL] ✓ Sent via Resend → {to_email} (id: {result.get('id', '?')})")
            return True
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"[EMAIL] ✗ Resend HTTP {e.code}: {body}")
        _fallback(to_email, otp)
        return False
    except Exception as e:
        print(f"[EMAIL] ✗ Resend error: {type(e).__name__}: {e}")
        _fallback(to_email, otp)
        return False


def _fallback(to_email: str, otp: str):
    print(f"\n{'='*44}")
    print(f"  OTP for {to_email}:  {otp}")
    print(f"  (email delivery failed — use this OTP manually)")
    print(f"{'='*44}\n")