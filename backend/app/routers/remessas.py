"""Remessas: mesmas URLs do sistema anterior, agora isoladas por empresa e por fábrica autorizada."""
from __future__ import annotations

import sqlite3
from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, Contexto, Escrita, Operacional, em_lista, filtro_fabricas, validar_fabrica_escrita
from ..db import agora

router = APIRouter(prefix="/api/remessas", tags=["remessas"])

STATUS_EXCLUIDA = "Excluída"

SELECT_REMESSA = """
SELECT r.*, f.codigo AS fabrica_codigo, f.nome AS fabrica_nome,
       (SELECT COUNT(*) FROM ops o WHERE o.remessa_id = r.id AND o.empresa_id = r.empresa_id) AS total_ops,
       (SELECT group_concat(DISTINCT o.pedido) FROM ops o
         WHERE o.remessa_id = r.id AND o.empresa_id = r.empresa_id AND o.pedido <> '') AS pedidos,
       (SELECT group_concat(DISTINCT o.material) FROM ops o
         WHERE o.remessa_id = r.id AND o.empresa_id = r.empresa_id AND o.material <> '') AS materiais
FROM remessas r JOIN fabricas f ON f.id = r.fabrica_id AND f.empresa_id = r.empresa_id
"""


def validar_data(valor: str | None, campo: str = "data") -> str | None:
    if valor in (None, ""):
        return None
    try:
        return date.fromisoformat(valor).isoformat()
    except (TypeError, ValueError):
        raise HTTPException(422, f"{campo.capitalize()} inválida. Use AAAA-MM-DD.") from None


class NovaRemessa(BaseModel):
    fabrica_id: int = Field(gt=0)
    numero: str = Field(min_length=1, max_length=50)
    tipo_remessa: str = Field(default="", max_length=30)
    descricao: str = Field(min_length=1, max_length=200)
    data_entrega: str | None = None
    quantidade_pares: int = Field(ge=0, le=10_000_000)
    status: str = Field(default="A programar", min_length=1, max_length=50)


class AtualizarRemessa(BaseModel):
    fabrica_id: int | None = Field(default=None, gt=0)
    numero: str | None = Field(default=None, min_length=1, max_length=50)
    tipo_remessa: str | None = Field(default=None, max_length=30)
    descricao: str | None = Field(default=None, min_length=1, max_length=200)
    data_entrega: str | None = None
    quantidade_pares: int | None = Field(default=None, ge=0, le=10_000_000)
    status: str | None = Field(default=None, min_length=1, max_length=50)


def obter_remessa(db: sqlite3.Connection, ctx: Contexto, remessa_id: int) -> dict:
    """Remessa da empresa da sessão, em fábrica autorizada; caso contrário 404."""
    fabricas = filtro_fabricas(db, ctx, None)
    clausula, params = em_lista("r.fabrica_id", fabricas)
    r = db.execute(SELECT_REMESSA + f" WHERE r.id = ? AND r.empresa_id = ? AND {clausula}",
                   [remessa_id, ctx.empresa_id, *params]).fetchone()
    if r is None:
        raise HTTPException(404, "Remessa não encontrada.")
    return dict(r)


def registrar_historico(db: sqlite3.Connection, ctx: Contexto, remessa_id: int, acao: str, detalhes: str) -> None:
    db.execute("""INSERT INTO historico_remessas (empresa_id, remessa_id, acao, detalhes, registrado_em, usuario_id)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (ctx.empresa_id, remessa_id, acao, detalhes, agora(), ctx.usuario["id"]))


def _erro_integridade(erro: sqlite3.IntegrityError):
    if "UNIQUE" in str(erro):
        raise HTTPException(409, "Já existe uma remessa com esse número nesta fábrica.") from erro
    raise erro


@router.get("")
def listar(ctx: Operacional, db: DB, fabrica_id: int | None = None, busca: str = Query("", max_length=100),
           incluir_excluidas: bool = True):
    fabricas = filtro_fabricas(db, ctx, fabrica_id)
    clausula, params = em_lista("r.fabrica_id", fabricas)
    sql = SELECT_REMESSA + f" WHERE r.empresa_id = ? AND {clausula}"
    valores: list = [ctx.empresa_id, *params]
    if not incluir_excluidas:
        sql += " AND r.status <> ?"
        valores.append(STATUS_EXCLUIDA)
    if busca.strip():
        termo = f"%{busca.strip()}%"
        sql += """ AND (r.numero LIKE ? OR r.descricao LIKE ? OR r.tipo_remessa LIKE ? OR f.codigo LIKE ?
                   OR EXISTS (SELECT 1 FROM ops o WHERE o.remessa_id = r.id AND o.empresa_id = r.empresa_id
                              AND (o.numero LIKE ? OR o.pedido LIKE ? OR o.material LIKE ?)))"""
        valores += [termo] * 7
    return [dict(r) for r in db.execute(sql + " ORDER BY r.id DESC", valores)]


@router.post("", status_code=201)
def criar(dados: NovaRemessa, ctx: Escrita, db: DB):
    fabrica = validar_fabrica_escrita(db, ctx, dados.fabrica_id)
    numero, descricao, status = dados.numero.strip(), dados.descricao.strip(), dados.status.strip()
    if not numero or not descricao:
        raise HTTPException(422, "Número e descrição são obrigatórios.")
    data_entrega = validar_data(dados.data_entrega, "data de entrega")
    momento = agora()
    try:
        cur = db.execute(
            """INSERT INTO remessas (empresa_id, fabrica_id, numero, tipo_remessa, descricao, data_entrega,
                   quantidade_pares, status, origem, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
            (ctx.empresa_id, fabrica["id"], numero, dados.tipo_remessa.strip(), descricao, data_entrega,
             dados.quantidade_pares, status, momento, momento))
    except sqlite3.IntegrityError as erro:
        _erro_integridade(erro)
    remessa_id = cur.lastrowid
    registrar_historico(db, ctx, remessa_id, "CRIACAO", f"Remessa {numero} cadastrada (fábrica {fabrica['codigo']})")
    remessa = obter_remessa(db, ctx, remessa_id)
    auditoria.registrar(db, "REMESSA_CRIADA", ctx=ctx, entidade="remessa", entidade_id=remessa_id,
                        fabrica_id=fabrica["id"], depois=remessa)
    return remessa


@router.put("/{remessa_id}")
def atualizar(remessa_id: int, dados: AtualizarRemessa, ctx: Escrita, db: DB):
    atual = obter_remessa(db, ctx, remessa_id)
    campos = dados.model_fields_set
    novo = dict(atual)
    if "fabrica_id" in campos and dados.fabrica_id:
        novo["fabrica_id"] = validar_fabrica_escrita(db, ctx, dados.fabrica_id)["id"]
    for campo in ("numero", "tipo_remessa", "descricao", "status"):
        valor = getattr(dados, campo)
        if campo in campos and valor is not None:
            novo[campo] = valor.strip()
    if "quantidade_pares" in campos and dados.quantidade_pares is not None:
        novo["quantidade_pares"] = dados.quantidade_pares
    if "data_entrega" in campos:
        novo["data_entrega"] = validar_data(dados.data_entrega, "data de entrega")
    if not novo["numero"] or not novo["descricao"] or not novo["status"]:
        raise HTTPException(422, "Número, descrição e status são obrigatórios.")
    try:
        db.execute(
            """UPDATE remessas SET fabrica_id = ?, numero = ?, tipo_remessa = ?, descricao = ?, data_entrega = ?,
                   quantidade_pares = ?, status = ?, atualizado_em = ? WHERE id = ? AND empresa_id = ?""",
            (novo["fabrica_id"], novo["numero"], novo["tipo_remessa"], novo["descricao"], novo["data_entrega"],
             novo["quantidade_pares"], novo["status"], agora(), remessa_id, ctx.empresa_id))
    except sqlite3.IntegrityError as erro:
        _erro_integridade(erro)
    registrar_historico(db, ctx, remessa_id, "EDICAO",
                        f"Remessa {novo['numero']} atualizada (status: {novo['status']}, pares: {novo['quantidade_pares']})")
    depois = obter_remessa(db, ctx, remessa_id)
    chaves = ("fabrica_id", "numero", "tipo_remessa", "descricao", "data_entrega", "quantidade_pares", "status")
    auditoria.registrar(db, "REMESSA_EDITADA", ctx=ctx, entidade="remessa", entidade_id=remessa_id,
                        fabrica_id=depois["fabrica_id"], antes={k: atual[k] for k in chaves},
                        depois={k: depois[k] for k in chaves})
    return depois


@router.delete("/{remessa_id}")
def excluir(remessa_id: int, ctx: Escrita, db: DB):
    """Exclusão LÓGICA (status → Excluída), como no sistema anterior. Nada é apagado."""
    atual = obter_remessa(db, ctx, remessa_id)
    db.execute("UPDATE remessas SET status = ?, atualizado_em = ? WHERE id = ? AND empresa_id = ?",
               (STATUS_EXCLUIDA, agora(), remessa_id, ctx.empresa_id))
    registrar_historico(db, ctx, remessa_id, "EXCLUSAO", f"Remessa {atual['numero']} marcada como excluída")
    auditoria.registrar(db, "REMESSA_EXCLUIDA", ctx=ctx, entidade="remessa", entidade_id=remessa_id,
                        fabrica_id=atual["fabrica_id"], antes={"status": atual["status"]},
                        depois={"status": STATUS_EXCLUIDA})
    return obter_remessa(db, ctx, remessa_id)


@router.get("/{remessa_id}/historico")
def historico(remessa_id: int, ctx: Operacional, db: DB):
    obter_remessa(db, ctx, remessa_id)
    return [dict(r) for r in db.execute(
        """SELECT h.*, u.nome AS usuario_nome FROM historico_remessas h LEFT JOIN usuarios u ON u.id = h.usuario_id
           WHERE h.remessa_id = ? AND h.empresa_id = ? ORDER BY h.id DESC""", (remessa_id, ctx.empresa_id))]
