"""Produção: lançamentos manuais (com estorno lógico), leituras importadas e resumo por etapa."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, Escrita, Operacional, em_lista, filtro_fabricas
from ..db import agora
from ..fluxo import etapa_da_empresa, etapas_do_fluxo
from ..producao_calc import agregar, considerar, somar_nivel_remessa
from .remessas import STATUS_EXCLUIDA, obter_remessa, registrar_historico, validar_data

router = APIRouter(prefix="/api", tags=["produção"])


class NovaProducao(BaseModel):
    remessa_id: int = Field(gt=0)
    etapa_id: int | None = Field(default=None, gt=0)
    etapa: str | None = Field(default=None, max_length=60)  # compatibilidade: nome da etapa
    op_id: int | None = Field(default=None, gt=0)
    data_producao: str = Field(min_length=10, max_length=10)
    quantidade_pares: int = Field(gt=0, le=1_000_000)
    observacao: str = Field(default="", max_length=500)


class Estorno(BaseModel):
    motivo: str = Field(min_length=5, max_length=500)


SELECT_LANC = """
SELECT p.*, r.numero AS remessa_numero, r.descricao AS remessa_descricao, r.quantidade_pares AS total_pares_remessa,
       r.status AS status_remessa, r.fabrica_id, f.codigo AS fabrica_codigo, o.numero AS op_numero,
       o.pedido AS op_pedido, o.material AS op_material, e.nome AS etapa_nome, e.cor AS etapa_cor,
       u.nome AS usuario_nome, 'manual' AS origem
FROM producao_diaria p
JOIN remessas r ON r.id = p.remessa_id AND r.empresa_id = p.empresa_id
JOIN fabricas f ON f.id = r.fabrica_id AND f.empresa_id = r.empresa_id
JOIN etapas e ON e.id = p.etapa_id AND e.empresa_id = p.empresa_id
LEFT JOIN ops o ON o.id = p.op_id AND o.empresa_id = p.empresa_id
LEFT JOIN usuarios u ON u.id = p.usuario_id
"""


@router.get("/producao")
def listar_producao(ctx: Operacional, db: DB, fabrica_id: int | None = None, remessa_id: int | None = None,
                    op_id: int | None = None, data_producao: str | None = None, data_inicio: str | None = None,
                    data_fim: str | None = None, etapa_id: int | None = None,
                    etapa: str | None = Query(None, max_length=60), incluir_estornados: bool = False):
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, fabrica_id))
    sql = SELECT_LANC + f" WHERE p.empresa_id = ? AND {clausula}"
    valores: list = [ctx.empresa_id, *params]
    if etapa and etapa_id is None:
        encontrada = etapa_da_empresa(db, ctx.empresa_id, nome=etapa)
        if encontrada is None:
            raise HTTPException(422, "Etapa inválida.")
        etapa_id = encontrada["id"]
    for coluna, valor in (("p.remessa_id", remessa_id), ("p.op_id", op_id), ("p.etapa_id", etapa_id)):
        if valor is not None:
            sql += f" AND {coluna} = ?"
            valores.append(valor)
    if data_producao:
        sql += " AND p.data_producao = ?"
        valores.append(validar_data(data_producao))
    if data_inicio:
        sql += " AND p.data_producao >= ?"
        valores.append(validar_data(data_inicio))
    if data_fim:
        sql += " AND p.data_producao <= ?"
        valores.append(validar_data(data_fim))
    if not incluir_estornados:
        sql += " AND p.estornado_em IS NULL"
    return [dict(r) for r in db.execute(sql + " ORDER BY p.data_producao DESC, p.id DESC", valores)]


@router.get("/producao/resumo")
def resumo(ctx: Operacional, db: DB, fabrica_id: int | None = None):
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, fabrica_id))
    remessas = [dict(r) for r in db.execute(
        f"""SELECT r.id AS remessa_id, r.numero, r.descricao, r.quantidade_pares, r.fabrica_id, f.codigo AS fabrica_codigo
            FROM remessas r JOIN fabricas f ON f.id = r.fabrica_id AND f.empresa_id = r.empresa_id
            WHERE r.empresa_id = ? AND r.status <> ? AND {clausula} ORDER BY r.id DESC""",
        [ctx.empresa_id, STATUS_EXCLUIDA, *params])]
    dados = agregar(db, ctx.empresa_id, [r["remessa_id"] for r in remessas])
    etapas = etapas_do_fluxo(db, ctx.empresa_id, fabrica_id, incluir_inativas=True)
    for r in remessas:
        por_etapa = {}
        for e in etapas:
            d = somar_nivel_remessa(dados, r["remessa_id"], e["id"])
            qtd, divergente = considerar(d)
            por_etapa[str(e["id"])] = {"manual": d["manual"] or 0, "importado": d["importado"],
                                       "considerado": qtd, "divergente": divergente,
                                       "leituras": d["leituras"], "lancamentos": d["lancamentos"],
                                       "ultima_data": d["ultima_data"]}
            # Compatibilidade com a interface anterior (corte/costura/montagem = total manual).
            r[e["codigo"].lower()] = d["manual"] or 0
        r["por_etapa"] = por_etapa
    return remessas


@router.post("/producao", status_code=201)
def registrar(dados: NovaProducao, ctx: Escrita, db: DB):
    data = validar_data(dados.data_producao, "data")
    remessa = obter_remessa(db, ctx, dados.remessa_id)
    if remessa["status"] == STATUS_EXCLUIDA:
        raise HTTPException(409, "Não é possível registrar produção em remessa excluída.")
    etapa = etapa_da_empresa(db, ctx.empresa_id, dados.etapa_id, None if dados.etapa_id else dados.etapa)
    if etapa is None:
        raise HTTPException(422, "Etapa inválida.")
    if not etapa["ativa"]:
        raise HTTPException(409, "A etapa está desativada.")
    if dados.op_id is not None:
        op = db.execute("SELECT id FROM ops WHERE id = ? AND empresa_id = ? AND remessa_id = ?",
                        (dados.op_id, ctx.empresa_id, remessa["id"])).fetchone()
        if op is None:
            raise HTTPException(404, "OP não encontrada nesta remessa.")
    momento = agora()
    cur = db.execute(
        """INSERT INTO producao_diaria (empresa_id, remessa_id, op_id, etapa_id, etapa, data_producao, quantidade_pares,
               observacao, registrado_em, usuario_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (ctx.empresa_id, remessa["id"], dados.op_id, etapa["id"], etapa["nome"], data, dados.quantidade_pares,
         dados.observacao.strip(), momento, ctx.usuario["id"]))
    registrar_historico(db, ctx, remessa["id"], "PRODUCAO",
                        f"{etapa['nome']}: {dados.quantidade_pares} pares em {data}")
    lanc = dict(db.execute(SELECT_LANC + " WHERE p.id = ?", (cur.lastrowid,)).fetchone())
    auditoria.registrar(db, "PRODUCAO_REGISTRADA", ctx=ctx, entidade="producao", entidade_id=lanc["id"],
                        fabrica_id=remessa["fabrica_id"], depois=lanc)
    return lanc


@router.post("/producao/{producao_id}/estorno")
def estornar(producao_id: int, dados: Estorno, ctx: Escrita, db: DB):
    """Correção de lançamento: o registro original permanece; fica marcado como estornado."""
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, None))
    lanc = db.execute(SELECT_LANC + f" WHERE p.id = ? AND p.empresa_id = ? AND {clausula}",
                      [producao_id, ctx.empresa_id, *params]).fetchone()
    if lanc is None:
        raise HTTPException(404, "Lançamento não encontrado.")
    if lanc["estornado_em"]:
        raise HTTPException(409, "Lançamento já estornado.")
    db.execute("UPDATE producao_diaria SET estornado_em = ?, estornado_por = ?, motivo_estorno = ? WHERE id = ?",
               (agora(), ctx.usuario["id"], dados.motivo.strip(), producao_id))
    registrar_historico(db, ctx, lanc["remessa_id"], "ESTORNO_PRODUCAO",
                        f"Lançamento #{producao_id} estornado ({lanc['etapa']}, {lanc['quantidade_pares']} pares): "
                        f"{dados.motivo.strip()}")
    auditoria.registrar(db, "PRODUCAO_ESTORNADA", ctx=ctx, entidade="producao", entidade_id=producao_id,
                        fabrica_id=lanc["fabrica_id"], antes=dict(lanc), detalhes=dados.motivo.strip())
    return {"ok": True, "id": producao_id}


@router.delete("/producao/{producao_id}")
def excluir_fisico(producao_id: int):
    raise HTTPException(405, "Lançamentos de produção não são apagados. Use POST /api/producao/{id}/estorno.")


@router.get("/leituras")
def listar_leituras(ctx: Operacional, db: DB, fabrica_id: int | None = None, remessa_id: int | None = None,
                    op_id: int | None = None, etapa_id: int | None = None, importacao_id: int | None = None,
                    data_inicio: str | None = None, data_fim: str | None = None,
                    limite: int = Query(1000, ge=1, le=10000)):
    clausula, params = em_lista("r.fabrica_id", filtro_fabricas(db, ctx, fabrica_id))
    sql = f"""SELECT l.*, r.numero AS remessa_numero, r.fabrica_id, f.codigo AS fabrica_codigo, o.numero AS op_numero,
                     o.pedido AS op_pedido, o.material AS op_material, e.nome AS etapa_nome, e.cor AS etapa_cor,
                     i.nome_arquivo, 'importado' AS origem
              FROM leituras l
              JOIN remessas r ON r.id = l.remessa_id AND r.empresa_id = l.empresa_id
              JOIN fabricas f ON f.id = r.fabrica_id AND f.empresa_id = r.empresa_id
              JOIN etapas e ON e.id = l.etapa_id AND e.empresa_id = l.empresa_id
              LEFT JOIN ops o ON o.id = l.op_id AND o.empresa_id = l.empresa_id
              LEFT JOIN importacoes i ON i.id = l.importacao_id AND i.empresa_id = l.empresa_id
              WHERE l.empresa_id = ? AND {clausula}"""
    valores: list = [ctx.empresa_id, *params]
    for coluna, valor in (("l.remessa_id", remessa_id), ("l.op_id", op_id), ("l.etapa_id", etapa_id),
                          ("l.importacao_id", importacao_id)):
        if valor is not None:
            sql += f" AND {coluna} = ?"
            valores.append(valor)
    if data_inicio:
        sql += " AND l.data_leitura >= ?"
        valores.append(validar_data(data_inicio))
    if data_fim:
        sql += " AND substr(l.data_leitura, 1, 10) <= ?"
        valores.append(validar_data(data_fim))
    sql += " ORDER BY l.data_leitura DESC, l.id DESC LIMIT ?"
    return [dict(r) for r in db.execute(sql, valores + [limite])]
