"""Contexto de autorização: quem é o usuário, em qual empresa opera e quais fábricas pode ver.

Regra central do isolamento multiempresa: o empresa_id usado nas consultas vem SEMPRE da sessão
validada no servidor — nunca do corpo, da URL ou de cabeçalhos enviados pelo navegador.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from . import config as config_mod
from .db import agora, conexao_requisicao, parse_iso
from .security import hash_token

DB = Annotated[sqlite3.Connection, Depends(conexao_requisicao, scope="function")]

PAPEIS_EMPRESA = ("admin_empresa", "pcp", "consulta")


@dataclass
class Contexto:
    usuario: dict
    sessao: dict
    ip: str
    empresa: dict | None = None
    estado_empresa: str | None = None
    fabricas_permitidas: list[int] | None = None  # None = todas as fábricas da empresa
    somente_leitura: bool = False
    suporte: bool = False
    motivos_leitura: list[str] = field(default_factory=list)

    @property
    def papel(self) -> str:
        return self.usuario["papel"]

    @property
    def admin_geral(self) -> bool:
        return self.papel == "admin_geral"

    @property
    def empresa_id(self) -> int:
        if not self.empresa:
            raise HTTPException(409, "Nenhuma empresa selecionada para operar.")
        return self.empresa["id"]

    def pode_escrever(self) -> bool:
        return not self.somente_leitura and self.papel in ("admin_geral", "admin_empresa", "pcp")


def ip_cliente(request: Request) -> str:
    # Atrás de um proxy reverso confiável, configure o uvicorn com --proxy-headers.
    return request.client.host if request.client else ""


def estado_efetivo(empresa: dict, hoje: date | None = None) -> str:
    """Estado considerando datas: teste/assinatura vencidos viram 'vencida' (sem alterar o banco)."""
    if empresa["tipo"] == "interna":
        return "ativa"
    estado = empresa["estado"]
    hoje = hoje or date.today()
    carencia = timedelta(days=int(empresa.get("carencia_dias") or 0))

    def passou(valor: str | None) -> bool:
        if not valor:
            return False
        try:
            return hoje > date.fromisoformat(valor[:10]) + carencia
        except ValueError:
            return False

    if estado == "teste" and passou(empresa.get("teste_ate")):
        return "vencida"
    if estado == "ativa" and passou(empresa.get("vencimento_em")):
        return "vencida"
    return estado


# --------------------------------------------------------------------------------------
# Sessão
# --------------------------------------------------------------------------------------
def sessao_atual(request: Request, db: DB) -> Contexto:
    token = request.cookies.get(config_mod.config.cookie_nome)
    if not token:
        raise HTTPException(401, "Sessão não encontrada. Faça login.")
    sessao = db.execute("SELECT * FROM sessoes WHERE token_hash = ?", (hash_token(token),)).fetchone()
    if sessao is None or sessao["revogada_em"]:
        raise HTTPException(401, "Sessão encerrada. Faça login novamente.")
    expira = parse_iso(sessao["expira_em"])
    if expira is None or expira <= parse_iso(agora()):
        raise HTTPException(401, "Sessão expirada. Faça login novamente.")
    usuario = db.execute("SELECT * FROM usuarios WHERE id = ?", (sessao["usuario_id"],)).fetchone()
    if usuario is None or not usuario["ativo"]:
        raise HTTPException(401, "Conta desativada.")
    momento = agora()
    # Atualiza no máximo a cada minuto, para não gravar em toda requisição.
    ultimo = parse_iso(sessao["ultimo_uso_em"])
    if ultimo is None or (parse_iso(momento) - ultimo).total_seconds() > 60:
        db.execute("UPDATE sessoes SET ultimo_uso_em = ? WHERE id = ?", (momento, sessao["id"]))
        db.execute("UPDATE usuarios SET ultima_atividade_em = ? WHERE id = ?", (momento, usuario["id"]))
    return Contexto(usuario=dict(usuario), sessao=dict(sessao), ip=ip_cliente(request))


Sessao = Annotated[Contexto, Depends(sessao_atual)]


def exigir_admin_geral(ctx: Sessao) -> Contexto:
    if not ctx.admin_geral:
        raise HTTPException(403, "Acesso restrito ao administrador da plataforma.")
    return ctx


AdminGeral = Annotated[Contexto, Depends(exigir_admin_geral)]


# --------------------------------------------------------------------------------------
# Contexto operacional (empresa + fábricas)
# --------------------------------------------------------------------------------------
def contexto_operacional(ctx: Sessao, db: DB) -> Contexto:
    if ctx.admin_geral:
        empresa_id = ctx.sessao.get("contexto_empresa_id")
        if not empresa_id:
            raise HTTPException(409, "Selecione a área interna ou um acesso de suporte para operar.")
        empresa = db.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if empresa is None:
            raise HTTPException(404, "Empresa do contexto não encontrada.")
        empresa = dict(empresa)
        if empresa["tipo"] != "interna":
            # Suporte assistido: temporário, autorizado pelo cliente, somente leitura e auditado.
            fim = parse_iso(ctx.sessao.get("suporte_expira_em"))
            autorizado = parse_iso(empresa.get("suporte_autorizado_ate"))
            agora_dt = parse_iso(agora())
            if fim is None or fim <= agora_dt or autorizado is None or autorizado <= agora_dt:
                raise HTTPException(403, "O acesso de suporte expirou ou não está autorizado pelo cliente.")
            ctx.suporte = True
            ctx.somente_leitura = True
            ctx.motivos_leitura.append("Acesso de suporte (somente leitura).")
        ctx.empresa = empresa
        ctx.estado_empresa = estado_efetivo(empresa)
        ctx.fabricas_permitidas = None
        return ctx

    empresa = db.execute("SELECT * FROM empresas WHERE id = ?", (ctx.usuario["empresa_id"],)).fetchone()
    if empresa is None:
        raise HTTPException(403, "Empresa não encontrada.")
    empresa = dict(empresa)
    estado = estado_efetivo(empresa)
    if empresa["tipo"] == "interna":
        raise HTTPException(403, "Área restrita ao administrador da plataforma.")
    if estado in ("suspensa", "cancelada"):
        raise HTTPException(403, f"O acesso da empresa está {estado}. Entre em contato com o suporte.")
    ctx.empresa = empresa
    ctx.estado_empresa = estado
    if estado == "vencida":
        ctx.somente_leitura = True
        ctx.motivos_leitura.append("Assinatura vencida: consulta e exportação liberadas, alterações bloqueadas.")
    if not ctx.usuario["acesso_todas_fabricas"]:
        ctx.fabricas_permitidas = [
            r[0] for r in db.execute(
                "SELECT fabrica_id FROM usuario_fabricas WHERE usuario_id = ? AND empresa_id = ?",
                (ctx.usuario["id"], empresa["id"]))
        ]
    return ctx


Operacional = Annotated[Contexto, Depends(contexto_operacional)]


def exigir_escrita(ctx: Operacional) -> Contexto:
    if ctx.somente_leitura:
        raise HTTPException(403, " ".join(ctx.motivos_leitura) or "Acesso somente leitura.")
    if ctx.papel not in ("admin_geral", "admin_empresa", "pcp"):
        raise HTTPException(403, "Seu perfil permite apenas consulta.")
    return ctx


Escrita = Annotated[Contexto, Depends(exigir_escrita)]


def exigir_admin_empresa(ctx: Operacional) -> Contexto:
    if ctx.papel not in ("admin_geral", "admin_empresa"):
        raise HTTPException(403, "Ação restrita ao administrador da empresa.")
    if ctx.suporte:
        raise HTTPException(403, "O acesso de suporte é somente leitura.")
    if ctx.somente_leitura:
        raise HTTPException(403, " ".join(ctx.motivos_leitura))
    return ctx


AdminEmpresa = Annotated[Contexto, Depends(exigir_admin_empresa)]


# --------------------------------------------------------------------------------------
# Fábricas
# --------------------------------------------------------------------------------------
def fabricas_visiveis(db: sqlite3.Connection, ctx: Contexto, incluir_inativas: bool = True) -> list[dict]:
    sql = "SELECT * FROM fabricas WHERE empresa_id = ?"
    params: list = [ctx.empresa_id]
    if not incluir_inativas:
        sql += " AND ativa = 1"
    if ctx.fabricas_permitidas is not None:
        if not ctx.fabricas_permitidas:
            return []
        sql += f" AND id IN ({','.join('?' * len(ctx.fabricas_permitidas))})"
        params += ctx.fabricas_permitidas
    return [dict(r) for r in db.execute(sql + " ORDER BY codigo", params)]


def filtro_fabricas(db: sqlite3.Connection, ctx: Contexto, fabrica_id: int | None) -> list[int]:
    """IDs de fábrica que a consulta pode usar. fabrica_id=None significa 'Todas as permitidas'.

    Uma fábrica de outra empresa, ou não autorizada para o usuário, responde 404 (não revela existência).
    """
    visiveis = [f["id"] for f in fabricas_visiveis(db, ctx)]
    if fabrica_id is None:
        return visiveis
    if fabrica_id not in visiveis:
        raise HTTPException(404, "Fábrica não encontrada.")
    return [fabrica_id]


def validar_fabrica_escrita(db: sqlite3.Connection, ctx: Contexto, fabrica_id: int) -> dict:
    for fabrica in fabricas_visiveis(db, ctx):
        if fabrica["id"] == fabrica_id:
            if not fabrica["ativa"]:
                raise HTTPException(409, "A fábrica está desativada.")
            return fabrica
    raise HTTPException(404, "Fábrica não encontrada.")


def em_lista(coluna: str, ids: list[int]) -> tuple[str, list[int]]:
    """Cláusula SQL segura para 'coluna IN (...)'; lista vazia nunca retorna linhas."""
    if not ids:
        return "0 = 1", []
    return f"{coluna} IN ({','.join('?' * len(ids))})", list(ids)
