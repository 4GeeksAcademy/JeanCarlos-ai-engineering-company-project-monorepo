import html
import logging
import os
from urllib.parse import urlencode, urlsplit

import resend
from resend.exceptions import ResendError

logger = logging.getLogger(__name__)


def reset_expiration_minutes() -> int:
    try:
        minutes = int(os.environ.get("PASSWORD_RESET_EXPIRE_MINUTES", "30"))
    except ValueError as exc:
        raise RuntimeError("PASSWORD_RESET_EXPIRE_MINUTES must be a positive integer") from exc
    if minutes <= 0:
        raise RuntimeError("PASSWORD_RESET_EXPIRE_MINUTES must be a positive integer")
    return minutes


def send_password_reset_email(email: str, token: str) -> None:
    try:
        api_key = os.environ.get("RESEND_API_KEY")
        sender = os.environ.get("RESEND_FROM_EMAIL")
        base_url = os.environ.get("FRONTEND_BASE_URL", "")
        parsed = urlsplit(base_url)
        valid_scheme = parsed.scheme == "https" or (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
        if (
            not api_key or not sender or not valid_scheme or not parsed.hostname
            or parsed.username or parsed.password or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
        ):
            raise RuntimeError(
                "RESEND_API_KEY, RESEND_FROM_EMAIL and a valid FRONTEND_BASE_URL are required"
            )
        link = f"{base_url.rstrip('/')}/reset-password?{urlencode({'token': token})}"
        resend.api_key = api_key
        resend.Emails.send({
            "from": sender,
            "to": [email],
            "subject": "Restablecer contraseña",
            "html": (
                "<p>Has solicitado restablecer tu contraseña.</p>"
                f'<p><a href="{html.escape(link, quote=True)}">Restablecer contraseña</a></p>'
                "<p>Si no lo solicitaste, ignora este correo.</p>"
            ),
        })
    except RuntimeError as exc:
        logger.error("Password reset email configuration error: %s", exc)
    except (ValueError, ResendError) as exc:
        logger.error("Password reset email delivery failed: %s", type(exc).__name__)
