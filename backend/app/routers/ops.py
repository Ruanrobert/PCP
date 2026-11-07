"""Consulta detalhada de OPs: identificação, fluxo de etapas, leituras, lançamentos, estados e divergências."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, Contexto, Escrita, Operacional, em_lista, filtro_fabricas
from ..db import agora
from ..fluxo import etapas_do_fluxo
from ..producao_calc import ESTADOS, agregar, considerar, estado_etapa, estado_geral
from .remessas import obter_remessa, registrar_historico

router = APIRouter(prefix="/api", tags=["OPs"])

SELECT_OP = """
SELECT o.*, r.numero AS remessa_numero, r.tipo_remessa, r.descricao AS remessa_descricao, r.data_entrega,
       r.status AS remessa_status, r.fabrica_id, f.codigo AS fabrica_codigo, f.nome AS fabrica_nome
FROM ops o
JOIN remessas r ON r.id = o.remessa_id AND r.empresa_id = o.empresa_id
JOIN fabricas f ON f.id = r.fabrica_id AND f.empresa_id = r.empresa_id
"""


class NovaOP(BaseModel):
    numero: str = Field(min_length=1, max_length=50)
    pedido: str = Field(default="", max_length=60)
    material: str = Field(default="", max_length=200)
    quantidade_prevista: int | None = Field(default=None, ge=0, le=10_000_000)


class EditarOP(BaseModel):
    pedido: str | None = Field(default=None, max_length=60)
    material: str | None = Field(default=None, max_length=200)
    quantidade_prevista: int | None = Field(default=None, ge=0, le=10_000_000)


def _estados_op(op: dict, etapas: list[dict], dados: dict) -> tuple[list[dict], str, str | None]:
    chaves = [(op["remessa_id"], op["id"], e["id"]) for e in etapas]
    op_tem_info = any((dados.get(c) or {}).get("leituras") or (dados.get(c) or {}).get("lancamentos") for c in chaves)
    resultado, ultima = [], None
    for e, chave in zip(etapas, chaves):
        d = dados.get(chave) or {"manual": None, "importado": None, "leituras": 0, "leituras_sem_quantidade": 0,
                                 "lancamentos": 0, "conferir": 0, "ultima_data": None}
        estado, qtd, saldo = estado_etapa(d, op["quantidade_prevista"], bool(op_tem_info))
        _, divergente = considerar(d)
        if d["ultima_data"] and (ultima is None or d["ultima_data"] > ultima):
            ultima = d["ultima_data"]
        resultado.append({"etapa_id": e["id"], "etapa": e["nome"], "codigo": e["codigo"], "cor": e["cor"],
                          "ativa": e["ativa"], "estado": estado, "quantidade_considerada": qtd, "saldo": saldo,
                          "manual": d["manual"], "importado": d["importado"], "divergente_manual_importado": divergente,
                          "leituras": d["leituras"], "leituras_sem_quantidade": d["leituras_sem_quantidade"],
                          "lancamentos": d["lancamentos"], "conferir": d["conferir"], "ultima_data": d["ultima_data"]})
    ativas = [r["estado"] for r in resultado if r["ativa"]]
    return resultado, estado_geral(ativas), ultima


def obter_op(db: sqlite3.Connection, ctx: Contexto, op_id: int) -> dict:
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, None))
    r = db.execute(SELECT_OP + f" WHERE o.id = ? AND o.empresa_id = ? AND {clausula}",
                   [op_id, ctx.empresa_id, *params]).fetchone()
    if r is None:
        raise HTTPException(404, "OP não encontrada.")
    return dict(r)


@router.get("/ops")
def listar_ops(ctx: Operacional, db: DB, fabrica_id: int | None = None, remessa: str = Query("", max_length=50),
               op: str = Query("", max_length=50), pedido: str = Query("", max_length=60),
               material: str = Query("", max_length=200), remessa_id: int | None = None,
               etapa_id: int | None = None, situacao: str | None = Query(None, max_length=40),
               limite: int = Query(500, ge=1, le=5000)):
    if situacao and situacao not in ESTADOS:
        raise HTTPException(422, f"Situação inválida. Use: {', '.join(ESTADOS)}.")
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, fabrica_id))
    sql = SELECT_OP + f" WHERE o.empresa_id = ? AND {clausula}"
    valores: list = [ctx.empresa_id, *params]
    for coluna, valor in (("r.numero", remessa), ("o.numero", op), ("o.pedido", pedido), ("o.material", material)):
        if valor.strip():
            sql += f" AND {coluna} LIKE ?"
            valores.append(f"%{valor.strip()}%")
    if remessa_id is not None:
        sql += " AND o.remessa_id = ?"
        valores.append(remessa_id)
    ops = [dict(r) for r in db.execute(sql + " ORDER BY r.data_entrega IS NULL, r.data_entrega, r.numero, o.numero "
                                       "LIMIT ?", valores + [limite])]
    dados = agregar(db, ctx.empresa_id, sorted({o["remessa_id"] for o in ops}))
    fluxos: dict = {}
    saida = []
    for o in ops:
        if o["fabrica_id"] not in fluxos:
            fluxos[o["fabrica_id"]] = etapas_do_fluxo(db, ctx.empresa_id, o["fabrica_id"], incluir_inativas=True)
        etapas, geral, ultima = _estados_op(o, fluxos[o["fabrica_id"]], dados)
        if situacao and geral != situacao:
            continue
        if etapa_id is not None:
            alvo = next((e for e in etapas if e["etapa_id"] == etapa_id), None)
            if alvo is None:
                continue
            o["estado_etapa_filtrada"] = alvo["estado"]
        o.update({"situacao": geral, "ultima_movimentacao": ultima, "etapas": etapas})
        saida.append(o)
    return saida


@router.get("/ops/{op_id}")
def detalhe_op(op_id: int, ctx: Operacional, db: DB):
    op = obter_op(db, ctx, op_id)
    etapas_fluxo = etapas_do_fluxo(db, ctx.empresa_id, op["fabrica_id"], incluir_inativas=True)
    dados = agregar(db, ctx.empresa_id, [op["remessa_id"]])
    etapas, geral, ultima = _estados_op(op, etapas_fluxo, dados)

    leituras = [dict(r) for r in db.execute(
        """SELECT l.*, e.nome AS etapa_nome, i.nome_arquivo, i.criado_em AS importado_em
           FROM leituras l JOIN etapas e ON e.id = l.etapa_id AND e.empresa_id = l.empresa_id
           LEFT JOIN importacoes i ON i.id = l.importacao_id AND i.empresa_id = l.empresa_id
           WHERE l.empresa_id = ? AND l.op_id = ? ORDER BY l.data_leitura, l.id""", (ctx.empresa_id, op_id))]
    manuais = [dict(r) for r in db.execute(
        """SELECT p.*, e.nome AS etapa_nome, u.nome AS usuario_nome,
                  CASE WHEN p.op_id IS NULL THEN 'remessa' ELSE 'op' END AS nivel
           FROM producao_diaria p JOIN etapas e ON e.id = p.etapa_id AND e.empresa_id = p.empresa_id
           LEFT JOIN usuarios u ON u.id = p.usuario_id
           WHERE p.empresa_id = ? AND (p.op_id = ? OR (p.op_id IS NULL AND p.remessa_id = ?))
           ORDER BY p.data_producao, p.id""", (ctx.empresa_id, op_id, op["remessa_id"]))]
    historico = [dict(r) for r in db.execute(
        """SELECT criado_em, acao, usuario_nome, origem, importacao_id, detalhes, antes, depois FROM auditoria
           WHERE empresa_id = ? AND entidade = 'op' AND entidade_id = ? ORDER BY id DESC LIMIT 200""",
        (ctx.empresa_id, str(op_id)))]
    importacoes = [dict(r) for r in db.execute(
        """SELECT it.importacao_id, it.linha, it.acao, it.mensagem, i.nome_arquivo, i.criado_em
           FROM importacao_itens it JOIN importacoes i ON i.id = it.importacao_id AND i.empresa_id = it.empresa_id
           WHERE it.empresa_id = ? AND it.entidade = 'op' AND it.entidade_id = ? ORDER BY it.id DESC LIMIT 200""",
        (ctx.empresa_id, op_id))]

    divergencias = []
    for e in etapas:
        if e["divergente_manual_importado"]:
            divergencias.append(f"{e['etapa']}: manual ({e['manual']}) diferente do importado ({e['importado']}).")
        if e["conferir"]:
            divergencias.append(f"{e['etapa']}: {e['conferir']} leitura(s) marcada(s) para conferência.")
        if op["quantidade_prevista"] and (e["quantidade_considerada"] or 0) > op["quantidade_prevista"]:
            divergencias.append(f"{e['etapa']}: quantidade ({e['quantidade_considerada']}) maior que a prevista "
                                f"({op['quantidade_prevista']}).")
    if any(m["nivel"] == "remessa" for m in manuais):
        divergencias.append("Há lançamentos manuais no nível da remessa (sem OP); eles não entram no cálculo desta OP.")

    for item in historico:
        for campo in ("antes", "depois"):
            if item[campo]:
                try:
                    item[campo] = json.loads(item[campo])
                except ValueError:
                    pass
    return {"op": op, "situacao": geral, "ultima_movimentacao": ultima, "etapas": etapas, "leituras": leituras,
            "lancamentos": manuais, "historico": historico, "importacoes": importacoes, "divergencias": divergencias}


@router.post("/remessas/{remessa_id}/ops", status_code=201)
def criar_op(remessa_id: int, dados: NovaOP, ctx: Escrita, db: DB):
    remessa = obter_remessa(db, ctx, remessa_id)
    numero = dados.numero.strip()
    if db.execute("SELECT 1 FROM ops WHERE empresa_id = ? AND remessa_id = ? AND numero = ?",
                  (ctx.empresa_id, remessa_id, numero)).fetchone():
        raise HTTPException(409, "Já existe essa OP nesta remessa.")
    momento = agora()
    cur = db.execute(
        """INSERT INTO ops (empresa_id, remessa_id, numero, pedido, material, quantidade_prevista, origem,
               criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, 'manual', ?, ?)""",
        (ctx.empresa_id, remessa_id, numero, dados.pedido.strip(), dados.material.strip(), dados.quantidade_prevista,
         momento, momento))
    op = obter_op(db, ctx, cur.lastrowid)
    registrar_historico(db, ctx, remessa_id, "OP_CRIADA", f"OP {numero} cadastrada")
    auditoria.registrar(db, "OP_CRIADA", ctx=ctx, entidade="op", entidade_id=op["id"],
                        fabrica_id=remessa["fabrica_id"], depois=op)
    return op


@router.put("/ops/{op_id}")
def editar_op(op_id: int, dados: EditarOP, ctx: Escrita, db: DB):
    atual = obter_op(db, ctx, op_id)
    campos = dados.model_fields_set
    novo = {k: atual[k] for k in ("pedido", "material", "quantidade_prevista")}
    for campo in campos:
        valor = getattr(dados, campo)
        novo[campo] = valor.strip() if isinstance(valor, str) else valor
    db.execute("UPDATE ops SET pedido = ?, material = ?, quantidade_prevista = ?, atualizado_em = ? WHERE id = ?",
               (novo["pedido"] or "", novo["material"] or "", novo["quantidade_prevista"], agora(), op_id))
    auditoria.registrar(db, "OP_EDITADA", ctx=ctx, entidade="op", entidade_id=op_id, fabrica_id=atual["fabrica_id"],
                        antes={k: atual[k] for k in novo}, depois=novo)
    return obter_op(db, ctx, op_id)
