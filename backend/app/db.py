"""Acesso ao banco. SQL portável sempre que possível (facilita migrar para PostgreSQL)."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as config_mod


def agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def agora_mais(**delta) -> str:
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def parse_iso(valor: str | None) -> datetime | None:
    if not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def abrir(caminho: Path | str | None = None) -> sqlite3.Connection:
    caminho = Path(caminho or config_mod.config.db_path)
    if not caminho.exists():
        # Nunca cria um banco vazio silenciosamente no lugar do banco real.
        raise FileNotFoundError(f"Banco de dados não encontrado: {caminho}")
    # check_same_thread=False: o FastAPI pode abrir a conexão e usar o endpoint em threads diferentes;
    # cada requisição tem sua própria conexão, então não há compartilhamento entre requisições.
    conexao = sqlite3.connect(caminho, timeout=10, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 10000")
    return conexao


@contextmanager
def transacao(caminho: Path | str | None = None):
    conexao = abrir(caminho)
    try:
        yield conexao
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def versao(conexao: sqlite3.Connection) -> int:
    return conexao.execute("PRAGMA user_version").fetchone()[0]


def conexao_requisicao():
    """Dependência FastAPI: uma transação por requisição (commit no sucesso, rollback em qualquer erro).

    Deve ser usada com scope="function" para que o commit aconteça ANTES de a resposta ser enviada.
    """
    conexao = abrir()
    try:
        yield conexao
        conexao.commit()
    except BaseException:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def linhas(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def linha(cursor) -> dict | None:
    r = cursor.fetchone()
    return dict(r) if r is not None else None
