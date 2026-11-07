"""Registro de auditoria (append-only). Nunca grava senhas, tokens ou hashes."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import agora

_CAMPOS_PROIBIDOS = ("senha", "token", "hash", "secret", "segredo", "csrf")


def _limpar(valor: Any) -> Any:
    if isinstance(valor, sqlite3.Row):
        valor = dict(valor)
    if isinstance(valor, dict):
        return {k: ("[omitido]" if any(p in k.lower() for p in _CAMPOS_PROIBIDOS) else _limpar(v))
                for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpar(v) for v in valor]
    return valor


def registrar(
    con: sqlite3.Connection,
    acao: str,
    *,
    ctx=None,
    entidade: str = "",
    entidade_id: Any = "",
    empresa_id: int | None = None,
    fabrica_id: int | None = None,
    origem: str | None = None,
    importacao_id: int | None = None,
    antes: Any = None,
    depois: Any = None,
    detalhes: str = "",
    usuario: dict | None = None,
    ip: str = "",
) -> None:
    usuario = usuario or (ctx.usuario if ctx else None) or {}
    if empresa_id is None and ctx is not None and ctx.empresa:
        empresa_id = ctx.empresa["id"]
    suporte = bool(ctx and ctx.suporte)
    if origem is None:
        origem = "suporte" if suporte else ("admin" if usuario.get("papel") == "admin_geral" else "manual")
    con.execute(
        """INSERT INTO auditoria (criado_em, usuario_id, usuario_nome, papel, empresa_id, fabrica_id, acao,
               entidade, entidade_id, origem, importacao_id, suporte, antes, depois, detalhes, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            agora(), usuario.get("id"), usuario.get("nome", ""), usuario.get("papel", ""), empresa_id, fabrica_id,
            acao, entidade, str(entidade_id if entidade_id is not None else ""), origem, importacao_id,
            1 if suporte else 0,
            json.dumps(_limpar(antes), ensure_ascii=False, default=str) if antes is not None else None,
            json.dumps(_limpar(depois), ensure_ascii=False, default=str) if depois is not None else None,
            detalhes, ip or (ctx.ip if ctx else ""),
        ),
    )
