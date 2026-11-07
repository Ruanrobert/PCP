"""Limites dos planos comerciais (usuários, fábricas, funcionalidades)."""
from __future__ import annotations

import json
import sqlite3

from fastapi import HTTPException

from .db import agora


def plano_da_empresa(db: sqlite3.Connection, empresa: dict) -> dict | None:
    if not empresa.get("plano_id"):
        return None
    plano = db.execute("SELECT * FROM planos WHERE id = ?", (empresa["plano_id"],)).fetchone()
    if plano is None:
        return None
    plano = dict(plano)
    try:
        plano["funcionalidades"] = json.loads(plano["funcionalidades"] or "[]")
    except ValueError:
        plano["funcionalidades"] = []
    return plano


def uso_da_empresa(db: sqlite3.Connection, empresa_id: int) -> dict:
    usuarios = db.execute(
        "SELECT COUNT(*) FROM usuarios WHERE empresa_id = ? AND ativo = 1", (empresa_id,)).fetchone()[0]
    convites = db.execute(
        """SELECT COUNT(*) FROM convites WHERE empresa_id = ? AND aceito_em IS NULL AND revogado_em IS NULL
           AND expira_em > ?""", (empresa_id, agora())).fetchone()[0]
    fabricas = db.execute(
        "SELECT COUNT(*) FROM fabricas WHERE empresa_id = ? AND ativa = 1", (empresa_id,)).fetchone()[0]
    return {"usuarios_ativos": usuarios, "convites_pendentes": convites, "fabricas_ativas": fabricas}


def verificar_limite_usuarios(db: sqlite3.Connection, empresa: dict, adicionais: int = 1,
                              contar_convites: bool = True) -> None:
    if empresa["tipo"] == "interna":
        return
    plano = plano_da_empresa(db, empresa)
    if not plano or plano["limite_usuarios"] is None:
        return
    uso = uso_da_empresa(db, empresa["id"])
    total = uso["usuarios_ativos"] + (uso["convites_pendentes"] if contar_convites else 0) + adicionais
    if total > plano["limite_usuarios"]:
        raise HTTPException(409, f"O plano {plano['nome']} permite até {plano['limite_usuarios']} usuário(s) "
                                 f"(ativos + convites pendentes).")


def verificar_limite_fabricas(db: sqlite3.Connection, empresa: dict, adicionais: int = 1) -> None:
    if empresa["tipo"] == "interna":
        return
    plano = plano_da_empresa(db, empresa)
    if not plano or plano["limite_fabricas"] is None:
        return
    uso = uso_da_empresa(db, empresa["id"])
    if uso["fabricas_ativas"] + adicionais > plano["limite_fabricas"]:
        raise HTTPException(409, f"O plano {plano['nome']} permite até {plano['limite_fabricas']} fábrica(s) ativa(s).")


def verificar_funcionalidade(db: sqlite3.Connection, empresa: dict, codigo: str) -> None:
    """Planos sem lista de funcionalidades liberam tudo; com lista, só o que estiver nela."""
    if empresa["tipo"] == "interna":
        return
    plano = plano_da_empresa(db, empresa)
    if plano and plano["funcionalidades"] and codigo not in plano["funcionalidades"]:
        raise HTTPException(403, f"A funcionalidade '{codigo}' não está incluída no plano {plano['nome']}.")
