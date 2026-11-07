"""Resolução do fluxo de etapas por fábrica.

Regra: se a fábrica tiver um fluxo próprio (linhas em fabrica_etapas), ele define ordem e uso.
Caso contrário, vale o catálogo de etapas da empresa (ordem e ativa da tabela etapas).
Para "Todas as fábricas" usa-se o catálogo da empresa.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e sem pontuação — usado para casar nomes de etapas e cabeçalhos."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return "".join(c for c in texto.lower() if c.isalnum())


def codigo_de(nome: str) -> str:
    texto = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().upper()
    return "".join(c if c.isalnum() else "_" for c in texto).strip("_")[:30] or "ETAPA"


def _saida(r) -> dict:
    e = dict(r)
    e["ativa"] = bool(e["ativa"])
    try:
        e["sinonimos"] = json.loads(e.get("sinonimos") or "[]")
    except ValueError:
        e["sinonimos"] = []
    return e


def catalogo(db: sqlite3.Connection, empresa_id: int, incluir_inativas: bool = False) -> list[dict]:
    sql = "SELECT * FROM etapas WHERE empresa_id = ?"
    if not incluir_inativas:
        sql += " AND ativa = 1"
    return [_saida(r) for r in db.execute(sql + " ORDER BY ordem, nome", (empresa_id,))]


def fluxo_personalizado(db: sqlite3.Connection, empresa_id: int, fabrica_id: int) -> bool:
    return db.execute("SELECT 1 FROM fabrica_etapas WHERE empresa_id = ? AND fabrica_id = ? LIMIT 1",
                      (empresa_id, fabrica_id)).fetchone() is not None


def etapas_do_fluxo(db: sqlite3.Connection, empresa_id: int, fabrica_id: int | None,
                    incluir_inativas: bool = False) -> list[dict]:
    if fabrica_id is None or not fluxo_personalizado(db, empresa_id, fabrica_id):
        return catalogo(db, empresa_id, incluir_inativas)
    sql = """SELECT e.*, fe.ordem AS ordem_fluxo, fe.ativa AS ativa_fluxo
             FROM fabrica_etapas fe JOIN etapas e ON e.id = fe.etapa_id AND e.empresa_id = fe.empresa_id
             WHERE fe.empresa_id = ? AND fe.fabrica_id = ?"""
    itens = []
    for r in db.execute(sql + " ORDER BY fe.ordem, e.nome", (empresa_id, fabrica_id)):
        e = _saida(r)
        e["ativa"] = bool(r["ativa"]) and bool(r["ativa_fluxo"])
        e["ordem"] = r["ordem_fluxo"]
        if incluir_inativas or e["ativa"]:
            itens.append(e)
    return itens


def etapa_da_empresa(db: sqlite3.Connection, empresa_id: int, etapa_id: int | None = None,
                     nome: str | None = None) -> dict | None:
    if etapa_id is not None:
        r = db.execute("SELECT * FROM etapas WHERE id = ? AND empresa_id = ?", (etapa_id, empresa_id)).fetchone()
        return _saida(r) if r else None
    if nome:
        alvo = normalizar(nome)
        for e in catalogo(db, empresa_id, incluir_inativas=True):
            if alvo in {normalizar(e["nome"]), normalizar(e["codigo"]), *[normalizar(s) for s in e["sinonimos"]]}:
                return e
    return None
