"""Serviço de e-mail. Sem provedor configurado, NADA é enviado e o chamador é informado."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from . import config as config_mod

log = logging.getLogger("pcp.email")


class EmailService:
    def configurado(self) -> bool:
        c = config_mod.config
        return c.email_provedor == "smtp" and bool(c.smtp_host and c.smtp_remetente)

    def enviar(self, destinatario: str, assunto: str, texto: str) -> bool:
        """Retorna True somente se o servidor aceitou a mensagem."""
        if not self.configurado():
            return False
        c = config_mod.config
        msg = EmailMessage()
        msg["From"] = c.smtp_remetente
        msg["To"] = destinatario
        msg["Subject"] = assunto
        msg.set_content(texto)
        try:
            with smtplib.SMTP(c.smtp_host, c.smtp_porta, timeout=15) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if c.smtp_usuario:
                    smtp.login(c.smtp_usuario, c.smtp_senha)
                smtp.send_message(msg)
            return True
        except (OSError, smtplib.SMTPException) as erro:
            # Sem dados pessoais nem conteúdo no log.
            log.warning("Falha ao enviar e-mail: %s", type(erro).__name__)
            return False


email_servico = EmailService()
