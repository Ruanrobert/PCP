"""Autoadministração da empresa cliente (admin_empresa): usuários, convites, suporte e auditoria própria."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, AdminEmpresa, Operacional
from ..db import agora, agora_mais
from ..usuarios_servico import (AtualizarUsuario, NovoConvite, atualizar_usuario, criar_convite, definir_ativo,
                                gerar_link_redefinicao, listar_convites, listar_usuarios, revogar_convite,
                                revogar_sessoes_usuario)

router = APIRouter(prefix="/api/empresa", tags=["empresa"])


class Ativo(BaseModel):
    ativo: bool


class AutorizarSuporte(BaseModel):
    horas: int = Field(ge=1, le=72)


def _admin_cliente(ctx) -> None:
    # O admin geral administra clientes pelo painel da plataforma, não por esta área.
    if ctx.admin_geral and ctx.empresa and ctx.empresa["tipo"] != "interna":
        raise HTTPException(403, "Use o painel da plataforma para administrar clientes.")


@router.get("/usuarios")
def usuarios(ctx: Operacional, db: DB):
    if ctx.papel not in ("admin_empresa", "admin_geral"):
        raise HTTPException(403, "Ação restrita ao administrador da empresa.")
    return {"usuarios": listar_usuarios(db, ctx.empresa_id), "convites": listar_convites(db, ctx.empresa_id)}


@router.post("/convites", status_code=201)
def convidar(dados: NovoConvite, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    return criar_convite(db, ctx, ctx.empresa, dados)


@router.post("/convites/{convite_id}/revogar")
def revogar(convite_id: int, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    revogar_convite(db, ctx, ctx.empresa_id, convite_id)
    return {"ok": True}


@router.put("/usuarios/{usuario_id}")
def editar(usuario_id: int, dados: AtualizarUsuario, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    return atualizar_usuario(db, ctx, ctx.empresa_id, usuario_id, dados)


@router.post("/usuarios/{usuario_id}/ativo")
def ativar(usuario_id: int, dados: Ativo, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    return definir_ativo(db, ctx, ctx.empresa_id, usuario_id, dados.ativo)


@router.post("/usuarios/{usuario_id}/revogar-sessoes")
def revogar_sessoes(usuario_id: int, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    return revogar_sessoes_usuario(db, ctx, ctx.empresa_id, usuario_id)


@router.post("/usuarios/{usuario_id}/redefinicao")
def redefinicao(usuario_id: int, ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    return gerar_link_redefinicao(db, ctx, ctx.empresa_id, usuario_id)


@router.post("/suporte")
def autorizar_suporte(dados: AutorizarSuporte, ctx: AdminEmpresa, db: DB):
    """O cliente autoriza, por tempo limitado, o acesso de suporte (somente leitura) da plataforma."""
    _admin_cliente(ctx)
    ate = agora_mais(hours=dados.horas)
    db.execute("UPDATE empresas SET suporte_autorizado_ate = ?, atualizado_em = ? WHERE id = ?",
               (ate, agora(), ctx.empresa_id))
    auditoria.registrar(db, "SUPORTE_AUTORIZADO", ctx=ctx, entidade="empresa", entidade_id=ctx.empresa_id,
                        depois={"ate": ate})
    return {"suporte_autorizado_ate": ate}


@router.delete("/suporte")
def revogar_suporte(ctx: AdminEmpresa, db: DB):
    _admin_cliente(ctx)
    db.execute("UPDATE empresas SET suporte_autorizado_ate = NULL, atualizado_em = ? WHERE id = ?",
               (agora(), ctx.empresa_id))
    auditoria.registrar(db, "SUPORTE_REVOGADO", ctx=ctx, entidade="empresa", entidade_id=ctx.empresa_id)
    return {"ok": True}


@router.get("/auditoria")
def auditoria_empresa(ctx: Operacional, db: DB, limite: int = Query(200, ge=1, le=1000),
                      deslocamento: int = Query(0, ge=0), acao: str = Query("", max_length=60)):
    if ctx.papel not in ("admin_empresa", "admin_geral"):
        raise HTTPException(403, "Ação restrita ao administrador da empresa.")
    sql = "SELECT * FROM auditoria WHERE empresa_id = ?"
    params: list = [ctx.empresa_id]
    if acao:
        sql += " AND acao LIKE ?"
        params.append(f"%{acao.upper()}%")
    return [dict(r) for r in db.execute(sql + " ORDER BY id DESC LIMIT ? OFFSET ?", params + [limite, deslocamento])]
