import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger("porcitech.email")


def _send_smtp_sync(to_email: str, subject: str, text_body: str, html_body: str) -> Tuple[bool, str]:
    """Envía un correo transaccional usando SMTP estándar si SMTP_HOST está configurado."""
    if not settings.SMTP_HOST or not settings.SMTP_HOST.strip():
        logger.info(
            "[HU02][SMTP no configurado] Correo de recuperación preparado para %s | Asunto: %s",
            to_email,
            subject,
        )
        return False, "smtp_not_configured"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.SMTP_HOST.strip(), settings.SMTP_PORT, timeout=10) as server:
            if settings.SMTP_USE_TLS:
                server.starttls()
            if settings.SMTP_USER and settings.SMTP_PASSWORD:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.sendmail(settings.SMTP_FROM_EMAIL, [to_email], msg.as_string())
        logger.info("[HU02] Correo de recuperación enviado exitosamente a %s", to_email)
        return True, "sent"
    except Exception as exc:
        logger.warning("[HU02] Fallo enviando correo SMTP a %s: %s", to_email, exc)
        return False, str(exc)


async def send_password_reset_email(
    to_email: str,
    user_name: str,
    reset_url: str,
    expires_minutes: int,
) -> Tuple[bool, str]:
    """Prepara y despacha en segundo plano el correo de restablecimiento de contraseña."""
    subject = "PorciTech — Recuperación de contraseña"
    text_body = (
        f"Hola {user_name},\n\n"
        f"Recibimos una solicitud para restablecer tu contraseña en PorciTech.\n"
        f"Usa el siguiente enlace (válido por {expires_minutes} minutos y de un solo uso):\n\n"
        f"{reset_url}\n\n"
        f"Si no realizaste esta solicitud, puedes ignorar este mensaje.\n"
        f"— Equipo PorciTech"
    )
    html_body = f"""
    <div style="font-family: Arial, sans-serif; max-width: 560px; margin: 0 auto; color: #0f172a;">
      <div style="background: #047857; color: #ffffff; padding: 20px 24px; border-radius: 14px 14px 0 0;">
        <h2 style="margin: 0; font-size: 20px;">PorciTech — Recuperación de acceso</h2>
      </div>
      <div style="border: 1px solid #e2e8f0; border-top: none; padding: 24px; border-radius: 0 0 14px 14px;">
        <p>Hola <strong>{user_name}</strong>,</p>
        <p>Recibimos una solicitud para restablecer la contraseña de tu cuenta en <strong>PorciTech</strong>.</p>
        <p style="margin: 24px 0;">
          <a href="{reset_url}" style="background: #059669; color: #ffffff; padding: 12px 22px; text-decoration: none; border-radius: 10px; font-weight: bold;">
            Restablecer mi contraseña
          </a>
        </p>
        <p style="font-size: 13px; color: #475569;">
          Este enlace caduca en <strong>{expires_minutes} minutos</strong> y queda invalidado automáticamente tras su uso.
        </p>
        <p style="font-size: 12px; color: #64748b; word-break: break-all;">
          Si el botón no funciona, copia y pega esta URL en tu navegador:<br/>{reset_url}
        </p>
      </div>
    </div>
    """
    return await asyncio.to_thread(_send_smtp_sync, to_email, subject, text_body, html_body)
