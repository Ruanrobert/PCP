"""Configuração por variáveis de ambiente (arquivo .env opcional).

Nenhum segredo ou credencial fica embutido no código.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv já está no ambiente; uso opcional
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover
    pass

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _bool(nome: str, padrao: bool) -> bool:
    valor = os.getenv(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in {"1", "true", "sim", "yes", "on"}


def _int(nome: str, padrao: int) -> int:
    try:
        return int(os.getenv(nome, padrao))
    except (TypeError, ValueError):
        return padrao


def _lista(nome: str, padrao: str) -> list[str]:
    return [item.strip() for item in os.getenv(nome, padrao).split(",") if item.strip()]


@dataclass
class Config:
    ambiente: str = field(default_factory=lambda: os.getenv("PCP_AMBIENTE", "desenvolvimento"))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("PCP_DB_PATH", str(BACKEND_DIR / "pcp.db"))))
    cors_origins: list[str] = field(
        default_factory=lambda: _lista("PCP_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    )
    app_url: str = field(default_factory=lambda: os.getenv("PCP_APP_URL", "http://localhost:5173"))
    cookie_nome: str = field(default_factory=lambda: os.getenv("PCP_COOKIE_NOME", "pcp_sessao"))
    cookie_secure: bool = field(default_factory=lambda: _bool("PCP_COOKIE_SECURE", False))
    sessao_horas: int = field(default_factory=lambda: _int("PCP_SESSAO_HORAS", 12))
    convite_horas: int = field(default_factory=lambda: _int("PCP_CONVITE_HORAS", 72))
    redefinicao_minutos: int = field(default_factory=lambda: _int("PCP_REDEFINICAO_MINUTOS", 60))
    login_max_falhas: int = field(default_factory=lambda: _int("PCP_LOGIN_MAX_FALHAS", 5))
    login_bloqueio_minutos: int = field(default_factory=lambda: _int("PCP_LOGIN_BLOQUEIO_MINUTOS", 15))
    suporte_max_horas: int = field(default_factory=lambda: _int("PCP_SUPORTE_MAX_HORAS", 4))
    upload_max_mb: int = field(default_factory=lambda: _int("PCP_UPLOAD_MAX_MB", 15))
    dados_dir: Path = field(default_factory=lambda: Path(os.getenv("PCP_DADOS_DIR", str(BACKEND_DIR / "dados"))))
    # E-mail: sem provedor configurado, nada é enviado e a interface informa isso.
    email_provedor: str = field(default_factory=lambda: os.getenv("PCP_EMAIL_PROVEDOR", "").strip().lower())
    smtp_host: str = field(default_factory=lambda: os.getenv("PCP_SMTP_HOST", ""))
    smtp_porta: int = field(default_factory=lambda: _int("PCP_SMTP_PORTA", 587))
    smtp_usuario: str = field(default_factory=lambda: os.getenv("PCP_SMTP_USUARIO", ""))
    smtp_senha: str = field(default_factory=lambda: os.getenv("PCP_SMTP_SENHA", ""))
    smtp_remetente: str = field(default_factory=lambda: os.getenv("PCP_SMTP_REMETENTE", ""))

    @property
    def producao(self) -> bool:
        return self.ambiente == "producao"


config = Config()


def recarregar() -> Config:
    """Usado pelos testes para aplicar variáveis de ambiente alteradas."""
    global config
    config = Config()
    return config
