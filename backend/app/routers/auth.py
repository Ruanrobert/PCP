"""Rotas de autenticação: login, sessão, convites, redefinição de senha e contexto do administrador."""
from __future__ import annotations

import json
import re
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auditoria
from .. import config as config_mod
from ..contexto import DB, PAPEIS_EMPRESA, Contexto, Sessao, estado_efetivo, fabricas_visiveis, ip_cliente
from ..db import agora, agora_mais, parse_iso
from ..email_servico import email_servico
from ..planos import plano_da_empresa, uso_da_empresa, verificar_limite_usuarios
from ..security import (hash_email, hash_senha, hash_token, novo_token, validar_politica_senha,
                        verificar_senha)

router = APIRouter(prefix="/api/auth", tags=["autenticação"])

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CSRF_COOKIE = "pcp_csrf"
MAX_FALHAS_IP = 30  # por janela de bloqueio


def normalizar_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(422, "Informe um e-mail válido.")
    return email


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    senha: str = Field(min_length=1, max_length=256)


class AceitarConvite(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    senha: str = Field(min_length=1, max_length=256)
    nome: str | None = Field(default=None, max_length=120)


class Token(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class Redefinir(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    senha: str = Field(min_length=1, max_length=256)


class Esqueci(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TrocarSenha(BaseModel):
    senha_atual: str = Field(min_length=1, max_length=256)
    nova_senha: str = Field(min_length=1, max_length=256)


class MudarContexto(BaseModel):
    empresa_id: int | None = None
    motivo: str = Field(default="", max_length=300)
    horas: int = Field(default=1, ge=1, le=24)


# --------------------------------------------------------------------------------------
# Sessões
# --------------------------------------------------------------------------------------
def criar_sessao(db: sqlite3.Connection, usuario: dict, request: Request, response: Response) -> None:
    cfg = config_mod.config
    token = novo_token()
    csrf = novo_token()
    momento = agora()
    db.execute(
        """INSERT INTO sessoes (usuario_id, token_hash, criado_em, expira_em, ultimo_uso_em, ip, user_agent)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (usuario["id"], hash_token(token), momento, agora_mais(hours=cfg.sessao_horas), momento,
         ip_cliente(request), (request.headers.get("user-agent") or "")[:200]),
    )
    db.execute("UPDATE usuarios SET falhas_login = 0, bloqueado_ate = NULL, ultimo_login_em = ?, "
               "ultima_atividade_em = ? WHERE id = ?", (momento, momento, usuario["id"]))
    idade = cfg.sessao_horas * 3600
    response.set_cookie(cfg.cookie_nome, token, max_age=idade, httponly=True, secure=cfg.cookie_secure,
                        samesite="lax", path="/")
    # Cookie legível pelo JavaScript para o envio do cabeçalho X-CSRF-Token (double submit).
    response.set_cookie(CSRF_COOKIE, csrf, max_age=idade, httponly=False, secure=cfg.cookie_secure,
                        samesite="lax", path="/")


def limpar_cookies(response: Response) -> None:
    response.delete_cookie(config_mod.config.cookie_nome, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def revogar_sessoes(db: sqlite3.Connection, usuario_id: int, motivo: str, exceto_id: int | None = None) -> int:
    sql = "UPDATE sessoes SET revogada_em = ?, motivo_revogacao = ? WHERE usuario_id = ? AND revogada_em IS NULL"
    params: list = [agora(), motivo, usuario_id]
    if exceto_id is not None:
        sql += " AND id <> ?"
        params.append(exceto_id)
    return db.execute(sql, params).rowcount


def verificar_empresa_permite_login(db: sqlite3.Connection, usuario: dict) -> None:
    if usuario["papel"] == "admin_geral":
        return
    empresa = db.execute("SELECT * FROM empresas WHERE id = ?", (usuario["empresa_id"],)).fetchone()
    if empresa is None or empresa["tipo"] == "interna":
        raise HTTPException(403, "Conta sem empresa válida.")
    estado = estado_efetivo(dict(empresa))
    if estado in ("suspensa", "cancelada"):
        raise HTTPException(403, f"O acesso da empresa está {estado}. Entre em contato com o suporte.")


# --------------------------------------------------------------------------------------
# Login / logout / sessão
# --------------------------------------------------------------------------------------
@router.post("/login")
def login(dados: Login, request: Request, response: Response, db: DB):
    cfg = config_mod.config
    ip = ip_cliente(request)
    email = (dados.email or "").strip().lower()
    janela = agora_mais(minutes=-cfg.login_bloqueio_minutos)
    falhas_ip = db.execute(
        "SELECT COUNT(*) FROM tentativas_login WHERE ip = ? AND sucesso = 0 AND criado_em > ?", (ip, janela)
    ).fetchone()[0]
    if falhas_ip >= MAX_FALHAS_IP:
        raise HTTPException(429, "Muitas tentativas a partir deste endereço. Aguarde alguns minutos.")

    usuario = db.execute("SELECT * FROM usuarios WHERE email = ?", (email,)).fetchone()
    usuario = dict(usuario) if usuario else None
    if usuario and usuario["bloqueado_ate"] and parse_iso(usuario["bloqueado_ate"]) > parse_iso(agora()):
        raise HTTPException(429, "Conta temporariamente bloqueada por excesso de tentativas. Tente mais tarde.")

    senha_ok = verificar_senha(dados.senha, usuario["senha_hash"] if usuario else None)
    if not usuario or not senha_ok or not usuario["ativo"]:
        db.execute("INSERT INTO tentativas_login (email_hash, ip, sucesso, criado_em) VALUES (?, ?, 0, ?)",
                   (hash_email(email), ip, agora()))
        if usuario and usuario["ativo"]:
            falhas = usuario["falhas_login"] + 1
            bloqueio = agora_mais(minutes=cfg.login_bloqueio_minutos) if falhas >= cfg.login_max_falhas else None
            db.execute("UPDATE usuarios SET falhas_login = ?, bloqueado_ate = ? WHERE id = ?",
                       (falhas, bloqueio, usuario["id"]))
            auditoria.registrar(db, "LOGIN_FALHA", usuario=usuario, entidade="usuario", entidade_id=usuario["id"],
                                empresa_id=usuario["empresa_id"], ip=ip, origem="seguranca",
                                detalhes="Conta bloqueada temporariamente" if bloqueio else "")
        # Commit explícito: as falhas precisam ser gravadas mesmo com a resposta de erro.
        db.commit()
        raise HTTPException(401, "E-mail ou senha inválidos.")

    verificar_empresa_permite_login(db, usuario)
    criar_sessao(db, usuario, request, response)
    db.execute("INSERT INTO tentativas_login (email_hash, ip, sucesso, criado_em) VALUES (?, ?, 1, ?)",
               (hash_email(email), ip, agora()))
    auditoria.registrar(db, "LOGIN", usuario=usuario, entidade="usuario", entidade_id=usuario["id"],
                        empresa_id=usuario["empresa_id"], ip=ip, origem="seguranca")
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(config_mod.config.cookie_nome)
    if token:
        db.execute("UPDATE sessoes SET revogada_em = ?, motivo_revogacao = 'logout' "
                   "WHERE token_hash = ? AND revogada_em IS NULL", (agora(), hash_token(token)))
    limpar_cookies(response)
    return {"ok": True}


def descrever_sessao(db: sqlite3.Connection, ctx: Contexto) -> dict:
    from ..contexto import contexto_operacional  # import local evita ciclo

    u = ctx.usuario
    dados = {
        "usuario": {"id": u["id"], "nome": u["nome"], "email": u["email"], "papel": u["papel"],
                    "acesso_todas_fabricas": bool(u["acesso_todas_fabricas"])},
        "empresa": None, "estado_empresa": None, "somente_leitura": False, "motivos_leitura": [],
        "suporte": None, "fabricas": [], "plano": None, "uso": None,
        "email_configurado": email_servico.configurado(),
    }
    try:
        op = contexto_operacional(ctx, db)
    except HTTPException as erro:
        dados["contexto_erro"] = erro.detail
        return dados
    emp = op.empresa
    dados.update({
        "empresa": {"id": emp["id"], "nome": emp["nome"], "tipo": emp["tipo"]},
        "estado_empresa": op.estado_empresa,
        "somente_leitura": op.somente_leitura,
        "motivos_leitura": op.motivos_leitura,
        "fabricas": [{"id": f["id"], "codigo": f["codigo"], "nome": f["nome"], "ativa": bool(f["ativa"])}
                     for f in fabricas_visiveis(db, op)],
    })
    if op.suporte:
        dados["suporte"] = {"motivo": ctx.sessao.get("suporte_motivo"), "expira_em": ctx.sessao.get("suporte_expira_em")}
    plano = plano_da_empresa(db, emp)
    if plano:
        dados["plano"] = {k: plano[k] for k in ("id", "nome", "limite_usuarios", "limite_fabricas", "funcionalidades")}
    if u["papel"] in ("admin_empresa", "admin_geral"):
        dados["uso"] = uso_da_empresa(db, emp["id"])
    return dados


@router.get("/sessao")
def sessao(ctx: Sessao, db: DB):
    return descrever_sessao(db, ctx)


@router.post("/senha")
def trocar_senha(dados: TrocarSenha, ctx: Sessao, db: DB):
    if not verificar_senha(dados.senha_atual, ctx.usuario["senha_hash"]):
        raise HTTPException(422, "Senha atual incorreta.")
    erro = validar_politica_senha(dados.nova_senha, ctx.usuario["email"])
    if erro:
        raise HTTPException(422, erro)
    db.execute("UPDATE usuarios SET senha_hash = ?, senha_alterada_em = ?, atualizado_em = ? WHERE id = ?",
               (hash_senha(dados.nova_senha), agora(), agora(), ctx.usuario["id"]))
    revogadas = revogar_sessoes(db, ctx.usuario["id"], "troca de senha", exceto_id=ctx.sessao["id"])
    auditoria.registrar(db, "SENHA_ALTERADA", usuario=ctx.usuario, entidade="usuario",
                        entidade_id=ctx.usuario["id"], empresa_id=ctx.usuario["empresa_id"], ip=ctx.ip,
                        origem="seguranca", detalhes=f"{revogadas} outra(s) sessão(ões) encerrada(s)")
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Convites
# --------------------------------------------------------------------------------------
def _convite_valido(db: sqlite3.Connection, token: str) -> dict:
    convite = db.execute("SELECT * FROM convites WHERE token_hash = ?", (hash_token(token),)).fetchone()
    if convite is None:
        raise HTTPException(404, "Convite inválido.")
    convite = dict(convite)
    if convite["revogado_em"]:
        raise HTTPException(410, "Este convite foi revogado.")
    if convite["aceito_em"]:
        raise HTTPException(410, "Este convite já foi utilizado.")
    if parse_iso(convite["expira_em"]) <= parse_iso(agora()):
        raise HTTPException(410, "Este convite expirou. Peça um novo convite ao administrador.")
    return convite


@router.post("/convite/validar")
def validar_convite(dados: Token, db: DB):
    convite = _convite_valido(db, dados.token)
    empresa = db.execute("SELECT nome FROM empresas WHERE id = ?", (convite["empresa_id"],)).fetchone()
    return {"email": convite["email"], "nome": convite["nome"], "papel": convite["papel"],
            "empresa": empresa["nome"] if empresa else "", "expira_em": convite["expira_em"]}


@router.post("/convite/aceitar")
def aceitar_convite(dados: AceitarConvite, request: Request, response: Response, db: DB):
    convite = _convite_valido(db, dados.token)
    empresa = db.execute("SELECT * FROM empresas WHERE id = ?", (convite["empresa_id"],)).fetchone()
    if empresa is None:
        raise HTTPException(404, "Empresa não encontrada.")
    empresa = dict(empresa)
    if estado_efetivo(empresa) in ("suspensa", "cancelada"):
        raise HTTPException(403, "O acesso desta empresa está bloqueado.")
    if convite["papel"] not in PAPEIS_EMPRESA:
        raise HTTPException(422, "Papel inválido no convite.")
    erro = validar_politica_senha(dados.senha, convite["email"])
    if erro:
        raise HTTPException(422, erro)
    if db.execute("SELECT 1 FROM usuarios WHERE email = ?", (convite["email"],)).fetchone():
        raise HTTPException(409, "Já existe uma conta com este e-mail.")
    # O próprio convite já ocupa uma vaga; conta apenas usuários ativos.
    verificar_limite_usuarios(db, empresa, adicionais=1, contar_convites=False)

    momento = agora()
    nome = (dados.nome or convite["nome"]).strip() or convite["nome"]
    cur = db.execute(
        """INSERT INTO usuarios (empresa_id, nome, email, senha_hash, papel, acesso_todas_fabricas, ativo,
               senha_alterada_em, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)""",
        (empresa["id"], nome, convite["email"], hash_senha(dados.senha), convite["papel"],
         convite["acesso_todas_fabricas"], momento, momento, momento),
    )
    usuario_id = cur.lastrowid
    if not convite["acesso_todas_fabricas"]:
        for fabrica_id in json.loads(convite["fabricas"] or "[]"):
            existe = db.execute("SELECT 1 FROM fabricas WHERE id = ? AND empresa_id = ?",
                                (fabrica_id, empresa["id"])).fetchone()
            if existe:
                db.execute("INSERT OR IGNORE INTO usuario_fabricas (empresa_id, usuario_id, fabrica_id) VALUES (?, ?, ?)",
                           (empresa["id"], usuario_id, fabrica_id))
    db.execute("UPDATE convites SET aceito_em = ?, usuario_id = ? WHERE id = ?", (momento, usuario_id, convite["id"]))
    usuario = dict(db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone())
    auditoria.registrar(db, "CONVITE_ACEITO", usuario=usuario, entidade="usuario", entidade_id=usuario_id,
                        empresa_id=empresa["id"], ip=ip_cliente(request), origem="seguranca",
                        depois={"papel": usuario["papel"], "convite_id": convite["id"]})
    criar_sessao(db, usuario, request, response)
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Redefinição de senha
# --------------------------------------------------------------------------------------
def criar_redefinicao(db: sqlite3.Connection, usuario: dict, criado_por: int | None) -> str:
    token = novo_token()
    db.execute("UPDATE redefinicoes_senha SET revogado_em = ? WHERE usuario_id = ? AND usado_em IS NULL "
               "AND revogado_em IS NULL", (agora(), usuario["id"]))
    db.execute(
        "INSERT INTO redefinicoes_senha (usuario_id, token_hash, criado_por, criado_em, expira_em) VALUES (?, ?, ?, ?, ?)",
        (usuario["id"], hash_token(token), criado_por, agora(),
         agora_mais(minutes=config_mod.config.redefinicao_minutos)),
    )
    return token


@router.post("/esqueci")
def esqueci_senha(dados: Esqueci, request: Request, db: DB):
    mensagem = ("Se o e-mail estiver cadastrado e o envio de e-mails estiver configurado, você receberá um link. "
                "Caso não receba, peça ao administrador da sua empresa um link de redefinição.")
    try:
        email = normalizar_email(dados.email)
    except HTTPException:
        return {"mensagem": mensagem}
    usuario = db.execute("SELECT * FROM usuarios WHERE email = ? AND ativo = 1", (email,)).fetchone()
    if usuario and email_servico.configurado():
        token = criar_redefinicao(db, dict(usuario), None)
        link = f"{config_mod.config.app_url}/?redefinir={token}"
        enviado = email_servico.enviar(email, "PCP Industrial — redefinição de senha",
                                       f"Para criar uma nova senha, acesse: {link}\n"
                                       f"O link expira em {config_mod.config.redefinicao_minutos} minutos.")
        auditoria.registrar(db, "REDEFINICAO_SOLICITADA", usuario=dict(usuario), entidade="usuario",
                            entidade_id=usuario["id"], empresa_id=usuario["empresa_id"],
                            ip=ip_cliente(request), origem="seguranca",
                            detalhes="e-mail enviado" if enviado else "falha no envio do e-mail")
    return {"mensagem": mensagem}


@router.post("/redefinir/validar")
def validar_redefinicao(dados: Token, db: DB):
    _redefinicao_valida(db, dados.token)
    return {"ok": True}


def _redefinicao_valida(db: sqlite3.Connection, token: str) -> dict:
    red = db.execute("SELECT * FROM redefinicoes_senha WHERE token_hash = ?", (hash_token(token),)).fetchone()
    if red is None or red["revogado_em"] or red["usado_em"] or parse_iso(red["expira_em"]) <= parse_iso(agora()):
        raise HTTPException(410, "Link de redefinição inválido ou expirado.")
    return dict(red)


@router.post("/redefinir")
def redefinir_senha(dados: Redefinir, request: Request, db: DB):
    red = _redefinicao_valida(db, dados.token)
    usuario = dict(db.execute("SELECT * FROM usuarios WHERE id = ?", (red["usuario_id"],)).fetchone())
    if not usuario["ativo"]:
        raise HTTPException(403, "Conta desativada.")
    erro = validar_politica_senha(dados.senha, usuario["email"])
    if erro:
        raise HTTPException(422, erro)
    momento = agora()
    db.execute("UPDATE usuarios SET senha_hash = ?, senha_alterada_em = ?, falhas_login = 0, bloqueado_ate = NULL, "
               "atualizado_em = ? WHERE id = ?", (hash_senha(dados.senha), momento, momento, usuario["id"]))
    db.execute("UPDATE redefinicoes_senha SET usado_em = ? WHERE id = ?", (momento, red["id"]))
    revogar_sessoes(db, usuario["id"], "redefinição de senha")
    auditoria.registrar(db, "SENHA_REDEFINIDA", usuario=usuario, entidade="usuario", entidade_id=usuario["id"],
                        empresa_id=usuario["empresa_id"], ip=ip_cliente(request), origem="seguranca")
    return {"ok": True}


# --------------------------------------------------------------------------------------
# Contexto do administrador geral (área interna / suporte assistido)
# --------------------------------------------------------------------------------------
@router.post("/contexto")
def mudar_contexto(dados: MudarContexto, ctx: Sessao, db: DB):
    if not ctx.admin_geral:
        raise HTTPException(403, "Acesso restrito ao administrador da plataforma.")
    sessao_id = ctx.sessao["id"]
    if dados.empresa_id is None:
        if ctx.sessao.get("suporte_expira_em"):
            auditoria.registrar(db, "SUPORTE_FIM", ctx=ctx, entidade="empresa",
                                entidade_id=ctx.sessao.get("contexto_empresa_id"),
                                empresa_id=ctx.sessao.get("contexto_empresa_id"), origem="suporte")
        db.execute("UPDATE sessoes SET contexto_empresa_id = NULL, suporte_motivo = NULL, suporte_expira_em = NULL "
                   "WHERE id = ?", (sessao_id,))
        return {"ok": True}

    empresa = db.execute("SELECT * FROM empresas WHERE id = ?", (dados.empresa_id,)).fetchone()
    if empresa is None:
        raise HTTPException(404, "Empresa não encontrada.")
    if empresa["tipo"] == "interna":
        db.execute("UPDATE sessoes SET contexto_empresa_id = ?, suporte_motivo = NULL, suporte_expira_em = NULL "
                   "WHERE id = ?", (empresa["id"], sessao_id))
        auditoria.registrar(db, "CONTEXTO_AREA_INTERNA", ctx=ctx, entidade="empresa", entidade_id=empresa["id"],
                            empresa_id=empresa["id"], origem="admin")
        return {"ok": True}

    motivo = dados.motivo.strip()
    if len(motivo) < 10:
        raise HTTPException(422, "Descreva o motivo do acesso de suporte (mínimo de 10 caracteres).")
    autorizado = parse_iso(empresa["suporte_autorizado_ate"])
    if autorizado is None or autorizado <= parse_iso(agora()):
        raise HTTPException(403, "O cliente não autorizou acesso de suporte no momento.")
    horas = min(dados.horas, config_mod.config.suporte_max_horas)
    fim = min(parse_iso(agora_mais(hours=horas)), autorizado).isoformat()
    db.execute("UPDATE sessoes SET contexto_empresa_id = ?, suporte_motivo = ?, suporte_expira_em = ? WHERE id = ?",
               (empresa["id"], motivo, fim, sessao_id))
    auditoria.registrar(db, "SUPORTE_INICIO", ctx=ctx, entidade="empresa", entidade_id=empresa["id"],
                        empresa_id=empresa["id"], origem="suporte", detalhes=motivo, depois={"expira_em": fim})
    return {"ok": True, "expira_em": fim}
