"""Configuração operacional: fábricas, etapas (seções) e fluxo de etapas por fábrica."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, AdminEmpresa, Operacional, fabricas_visiveis, filtro_fabricas
from ..db import agora
from ..fluxo import catalogo, codigo_de, etapa_da_empresa, etapas_do_fluxo, fluxo_personalizado, normalizar
from ..planos import verificar_limite_fabricas

router = APIRouter(prefix="/api", tags=["configuração"])

COR = r"^#[0-9a-fA-F]{6}$"


class DadosFabrica(BaseModel):
    codigo: str = Field(min_length=1, max_length=30)
    nome: str = Field(min_length=1, max_length=120)


class EditarFabrica(BaseModel):
    codigo: str | None = Field(default=None, min_length=1, max_length=30)
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    ativa: bool | None = None


class DadosEtapa(BaseModel):
    nome: str = Field(min_length=1, max_length=60)
    codigo: str | None = Field(default=None, max_length=30)
    cor: str = Field(default="#64748b", pattern=COR)
    sinonimos: list[str] = Field(default_factory=list, max_length=20)


class EditarEtapa(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=60)
    codigo: str | None = Field(default=None, min_length=1, max_length=30)
    cor: str | None = Field(default=None, pattern=COR)
    ativa: bool | None = None
    sinonimos: list[str] | None = Field(default=None, max_length=20)


class Ordem(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)


class ItemFluxo(BaseModel):
    etapa_id: int
    ativa: bool = True


class Fluxo(BaseModel):
    personalizado: bool
    itens: list[ItemFluxo] = Field(default_factory=list, max_length=200)


# ------------------------------------------------------------------ fábricas
@router.get("/fabricas")
def listar_fabricas(ctx: Operacional, db: DB):
    return [dict(f, ativa=bool(f["ativa"]), fluxo_personalizado=fluxo_personalizado(db, ctx.empresa_id, f["id"]))
            for f in fabricas_visiveis(db, ctx)]


def _fabrica(db, ctx, fabrica_id: int) -> dict:
    r = db.execute("SELECT * FROM fabricas WHERE id = ? AND empresa_id = ?", (fabrica_id, ctx.empresa_id)).fetchone()
    if r is None:
        raise HTTPException(404, "Fábrica não encontrada.")
    return dict(r)


def _codigo_livre(db, empresa_id: int, codigo: str, exceto: int | None = None) -> None:
    sql = "SELECT 1 FROM fabricas WHERE empresa_id = ? AND upper(codigo) = upper(?)"
    params: list = [empresa_id, codigo]
    if exceto:
        sql += " AND id <> ?"
        params.append(exceto)
    if db.execute(sql, params).fetchone():
        raise HTTPException(409, "Já existe uma fábrica com esse código.")


@router.post("/fabricas", status_code=201)
def criar_fabrica(dados: DadosFabrica, ctx: AdminEmpresa, db: DB):
    codigo = dados.codigo.strip()
    _codigo_livre(db, ctx.empresa_id, codigo)
    verificar_limite_fabricas(db, ctx.empresa)
    momento = agora()
    cur = db.execute("INSERT INTO fabricas (empresa_id, codigo, nome, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
                     (ctx.empresa_id, codigo, dados.nome.strip(), momento, momento))
    fabrica = _fabrica(db, ctx, cur.lastrowid)
    auditoria.registrar(db, "FABRICA_CRIADA", ctx=ctx, entidade="fabrica", entidade_id=fabrica["id"],
                        fabrica_id=fabrica["id"], depois=fabrica)
    return fabrica


@router.put("/fabricas/{fabrica_id}")
def editar_fabrica(fabrica_id: int, dados: EditarFabrica, ctx: AdminEmpresa, db: DB):
    atual = _fabrica(db, ctx, fabrica_id)
    codigo = dados.codigo.strip() if dados.codigo else atual["codigo"]
    nome = dados.nome.strip() if dados.nome else atual["nome"]
    ativa = atual["ativa"] if dados.ativa is None else int(dados.ativa)
    if codigo != atual["codigo"]:
        _codigo_livre(db, ctx.empresa_id, codigo, exceto=fabrica_id)
    if ativa and not atual["ativa"]:
        verificar_limite_fabricas(db, ctx.empresa)
    db.execute("UPDATE fabricas SET codigo = ?, nome = ?, ativa = ?, atualizado_em = ? WHERE id = ?",
               (codigo, nome, ativa, agora(), fabrica_id))
    novo = _fabrica(db, ctx, fabrica_id)
    auditoria.registrar(db, "FABRICA_EDITADA", ctx=ctx, entidade="fabrica", entidade_id=fabrica_id,
                        fabrica_id=fabrica_id, antes=atual, depois=novo)
    return novo


@router.get("/fabricas/{fabrica_id}/fluxo")
def obter_fluxo(fabrica_id: int, ctx: Operacional, db: DB):
    filtro_fabricas(db, ctx, fabrica_id)
    personalizado = fluxo_personalizado(db, ctx.empresa_id, fabrica_id)
    return {"personalizado": personalizado,
            "etapas": etapas_do_fluxo(db, ctx.empresa_id, fabrica_id, incluir_inativas=True)}


@router.put("/fabricas/{fabrica_id}/fluxo")
def salvar_fluxo(fabrica_id: int, dados: Fluxo, ctx: AdminEmpresa, db: DB):
    _fabrica(db, ctx, fabrica_id)
    antes = etapas_do_fluxo(db, ctx.empresa_id, fabrica_id, incluir_inativas=True)
    db.execute("DELETE FROM fabrica_etapas WHERE empresa_id = ? AND fabrica_id = ?", (ctx.empresa_id, fabrica_id))
    if dados.personalizado:
        vistos = set()
        for i, item in enumerate(dados.itens, start=1):
            if item.etapa_id in vistos:
                raise HTTPException(422, "Etapa repetida no fluxo.")
            vistos.add(item.etapa_id)
            if etapa_da_empresa(db, ctx.empresa_id, item.etapa_id) is None:
                raise HTTPException(422, "Etapa não pertence à empresa.")
            db.execute("INSERT INTO fabrica_etapas (empresa_id, fabrica_id, etapa_id, ordem, ativa) VALUES (?, ?, ?, ?, ?)",
                       (ctx.empresa_id, fabrica_id, item.etapa_id, i * 10, 1 if item.ativa else 0))
        if not vistos:
            raise HTTPException(422, "Um fluxo personalizado precisa de pelo menos uma etapa.")
    depois = etapas_do_fluxo(db, ctx.empresa_id, fabrica_id, incluir_inativas=True)
    auditoria.registrar(db, "FLUXO_ETAPAS", ctx=ctx, entidade="fabrica", entidade_id=fabrica_id, fabrica_id=fabrica_id,
                        antes=[(e["nome"], e["ativa"]) for e in antes], depois=[(e["nome"], e["ativa"]) for e in depois])
    return {"personalizado": dados.personalizado, "etapas": depois}


# ------------------------------------------------------------------ etapas
@router.get("/etapas")
def listar_etapas(ctx: Operacional, db: DB, fabrica_id: int | None = None, incluir_inativas: bool = False):
    if fabrica_id is not None:
        filtro_fabricas(db, ctx, fabrica_id)
    etapas = etapas_do_fluxo(db, ctx.empresa_id, fabrica_id, incluir_inativas)
    if incluir_inativas:
        for e in etapas:
            e["lancamentos"] = db.execute("SELECT COUNT(*) FROM producao_diaria WHERE empresa_id = ? AND etapa_id = ?",
                                          (ctx.empresa_id, e["id"])).fetchone()[0]
            e["leituras"] = db.execute("SELECT COUNT(*) FROM leituras WHERE empresa_id = ? AND etapa_id = ?",
                                       (ctx.empresa_id, e["id"])).fetchone()[0]
    return etapas


def _verificar_nome_livre(db, empresa_id: int, nome: str, codigo: str, sinonimos: list[str], exceto: int | None):
    alvos = {normalizar(nome), normalizar(codigo), *[normalizar(s) for s in sinonimos]} - {""}
    for e in catalogo(db, empresa_id, incluir_inativas=True):
        if e["id"] == exceto:
            continue
        existentes = {normalizar(e["nome"]), normalizar(e["codigo"]), *[normalizar(s) for s in e["sinonimos"]]}
        if alvos & existentes:
            raise HTTPException(409, f"Nome, código ou sinônimo já usado pela etapa '{e['nome']}'.")


def _limpar_sinonimos(lista: list[str]) -> list[str]:
    return [s.strip()[:40] for s in lista if s and s.strip()]


@router.post("/etapas", status_code=201)
def criar_etapa(dados: DadosEtapa, ctx: AdminEmpresa, db: DB):
    nome = dados.nome.strip()
    codigo = (dados.codigo or "").strip().upper() or codigo_de(nome)
    if not re.match(r"^[A-Z0-9_]+$", codigo):
        raise HTTPException(422, "O código deve conter apenas letras, números e _.")
    sinonimos = _limpar_sinonimos(dados.sinonimos)
    _verificar_nome_livre(db, ctx.empresa_id, nome, codigo, sinonimos, None)
    ordem = (db.execute("SELECT MAX(ordem) FROM etapas WHERE empresa_id = ?", (ctx.empresa_id,)).fetchone()[0] or 0) + 10
    momento = agora()
    cur = db.execute(
        """INSERT INTO etapas (empresa_id, codigo, nome, cor, ordem, sinonimos, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (ctx.empresa_id, codigo, nome, dados.cor, ordem, json.dumps(sinonimos, ensure_ascii=False), momento, momento))
    etapa = etapa_da_empresa(db, ctx.empresa_id, cur.lastrowid)
    auditoria.registrar(db, "ETAPA_CRIADA", ctx=ctx, entidade="etapa", entidade_id=etapa["id"], depois=etapa)
    return etapa


@router.put("/etapas/{etapa_id}")
def editar_etapa(etapa_id: int, dados: EditarEtapa, ctx: AdminEmpresa, db: DB):
    atual = etapa_da_empresa(db, ctx.empresa_id, etapa_id)
    if atual is None:
        raise HTTPException(404, "Etapa não encontrada.")
    nome = dados.nome.strip() if dados.nome else atual["nome"]
    codigo = dados.codigo.strip().upper() if dados.codigo else atual["codigo"]
    if not re.match(r"^[A-Z0-9_]+$", codigo):
        raise HTTPException(422, "O código deve conter apenas letras, números e _.")
    sinonimos = _limpar_sinonimos(dados.sinonimos) if dados.sinonimos is not None else atual["sinonimos"]
    _verificar_nome_livre(db, ctx.empresa_id, nome, codigo, sinonimos, etapa_id)
    cor = dados.cor or atual["cor"]
    ativa = atual["ativa"] if dados.ativa is None else dados.ativa
    # Desativar não apaga nada: lançamentos e leituras continuam ligados à etapa.
    db.execute("UPDATE etapas SET nome = ?, codigo = ?, cor = ?, ativa = ?, sinonimos = ?, atualizado_em = ? WHERE id = ?",
               (nome, codigo, cor, 1 if ativa else 0, json.dumps(sinonimos, ensure_ascii=False), agora(), etapa_id))
    nova = etapa_da_empresa(db, ctx.empresa_id, etapa_id)
    auditoria.registrar(db, "ETAPA_EDITADA", ctx=ctx, entidade="etapa", entidade_id=etapa_id, antes=atual, depois=nova)
    return nova


@router.post("/etapas/ordem")
def reordenar(dados: Ordem, ctx: AdminEmpresa, db: DB):
    ids = list(dict.fromkeys(dados.ids))
    existentes = {e["id"] for e in catalogo(db, ctx.empresa_id, incluir_inativas=True)}
    if set(ids) - existentes:
        raise HTTPException(422, "Lista contém etapas que não pertencem à empresa.")
    restantes = [i for i in sorted(existentes) if i not in ids]
    for posicao, etapa_id in enumerate(ids + restantes, start=1):
        db.execute("UPDATE etapas SET ordem = ?, atualizado_em = ? WHERE id = ? AND empresa_id = ?",
                   (posicao * 10, agora(), etapa_id, ctx.empresa_id))
    auditoria.registrar(db, "ETAPAS_REORDENADAS", ctx=ctx, entidade="etapa", depois=ids)
    return catalogo(db, ctx.empresa_id, incluir_inativas=True)
