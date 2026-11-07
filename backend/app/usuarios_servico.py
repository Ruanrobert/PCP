"""Regras de convites e usuários compartilhadas pelo painel da plataforma e pelo admin da empresa."""
from __future__ import annotations

import json
import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, Field

from . import auditoria
from . import config as config_mod
from .contexto import PAPEIS_EMPRESA, Contexto
from .db import agora, agora_mais
from .email_servico import email_servico
from .planos import verificar_limite_usuarios
from .security import hash_token, novo_token


class NovoConvite(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    nome: str = Field(min_length=1, max_length=120)
    papel: str = Field(pattern=r"^(admin_empresa|pcp|consulta)$")
    acesso_todas_fabricas: bool = True
    fabricas: list[int] = Field(default_factory=list, max_length=500)


class AtualizarUsuario(BaseModel):
    nome: str | None = Field(default=None, min_length=1, max_length=120)
    papel: str | None = Field(default=None, pattern=r"^(admin_empresa|pcp|consulta)$")
    acesso_todas_fabricas: bool | None = None
    fabricas: list[int] | None = Field(default=None, max_length=500)


def _validar_fabricas(db: sqlite3.Connection, empresa_id: int, fabricas: list[int]) -> list[int]:
    unicas = sorted(set(fabricas))
    for fabrica_id in unicas:
        if not db.execute("SELECT 1 FROM fabricas WHERE id = ? AND empresa_id = ?", (fabrica_id, empresa_id)).fetchone():
            raise HTTPException(422, f"Fábrica {fabrica_id} não pertence à empresa.")
    return unicas


def criar_convite(db: sqlite3.Connection, ctx: Contexto, empresa: dict, dados: NovoConvite) -> dict:
    from .routers.auth import normalizar_email

    if empresa["tipo"] == "interna":
        raise HTTPException(422, "A área interna de testes não recebe usuários de clientes.")
    email = normalizar_email(dados.email)
    if db.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,)).fetchone():
        raise HTTPException(409, "Já existe uma conta com este e-mail.")
    fabricas = [] if dados.acesso_todas_fabricas else _validar_fabricas(db, empresa["id"], dados.fabricas)
    if not dados.acesso_todas_fabricas and not fabricas:
        raise HTTPException(422, "Selecione ao menos uma fábrica ou conceda acesso a todas.")

    momento = agora()
    anteriores = db.execute(
        "UPDATE convites SET revogado_em = ? WHERE empresa_id = ? AND email = ? AND aceito_em IS NULL "
        "AND revogado_em IS NULL", (momento, empresa["id"], email)).rowcount
    verificar_limite_usuarios(db, empresa, adicionais=1, contar_convites=True)

    token = novo_token()
    expira = agora_mais(hours=config_mod.config.convite_horas)
    cur = db.execute(
        """INSERT INTO convites (empresa_id, email, nome, papel, acesso_todas_fabricas, fabricas, token_hash,
               criado_por, criado_em, expira_em)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (empresa["id"], email, dados.nome.strip(), dados.papel, 1 if dados.acesso_todas_fabricas else 0,
         json.dumps(fabricas), hash_token(token), ctx.usuario["id"], momento, expira),
    )
    convite_id = cur.lastrowid
    link = f"{config_mod.config.app_url}/?convite={token}"
    enviado = email_servico.enviar(
        email, "Convite para o PCP Industrial",
        f"Olá, {dados.nome.strip()}.\n\nVocê foi convidado(a) para acessar o PCP Industrial da empresa "
        f"{empresa['nome']}.\nCrie sua senha em: {link}\nO convite expira em {config_mod.config.convite_horas} horas.",
    )
    if enviado:
        db.execute("UPDATE convites SET email_enviado = 1 WHERE id = ?", (convite_id,))
    auditoria.registrar(db, "CONVITE_CRIADO", ctx=ctx, entidade="convite", entidade_id=convite_id,
                        empresa_id=empresa["id"],
                        depois={"papel": dados.papel, "acesso_todas_fabricas": dados.acesso_todas_fabricas,
                                "fabricas": fabricas, "email_enviado": enviado,
                                "convites_anteriores_revogados": anteriores})
    return {
        "id": convite_id, "email": email, "expira_em": expira, "email_enviado": enviado,
        # O link só é exibido agora, para quem criou o convite; o banco guarda apenas o hash do token.
        "link": link,
        "aviso": None if enviado else "O e-mail NÃO foi enviado (nenhum provedor configurado ou falha no envio). "
                                      "Entregue o link ao convidado por um canal seguro.",
    }


def listar_convites(db: sqlite3.Connection, empresa_id: int) -> list[dict]:
    linhas = db.execute(
        """SELECT id, email, nome, papel, acesso_todas_fabricas, fabricas, criado_em, expira_em, aceito_em,
                  revogado_em, email_enviado FROM convites WHERE empresa_id = ? ORDER BY id DESC""", (empresa_id,))
    resultado = []
    momento = agora()
    for r in linhas:
        item = dict(r)
        item["fabricas"] = json.loads(item["fabricas"] or "[]")
        item["situacao"] = ("aceito" if item["aceito_em"] else "revogado" if item["revogado_em"]
                            else "expirado" if item["expira_em"] <= momento else "pendente")
        resultado.append(item)
    return resultado


def revogar_convite(db: sqlite3.Connection, ctx: Contexto, empresa_id: int, convite_id: int) -> None:
    convite = db.execute("SELECT * FROM convites WHERE id = ? AND empresa_id = ?", (convite_id, empresa_id)).fetchone()
    if convite is None:
        raise HTTPException(404, "Convite não encontrado.")
    if convite["aceito_em"] or convite["revogado_em"]:
        raise HTTPException(409, "O convite já foi utilizado ou revogado.")
    db.execute("UPDATE convites SET revogado_em = ? WHERE id = ?", (agora(), convite_id))
    auditoria.registrar(db, "CONVITE_REVOGADO", ctx=ctx, entidade="convite", entidade_id=convite_id,
                        empresa_id=empresa_id)


def listar_usuarios(db: sqlite3.Connection, empresa_id: int | None = None, busca: str = "") -> list[dict]:
    sql = """SELECT u.id, u.empresa_id, e.nome AS empresa_nome, u.nome, u.email, u.papel, u.acesso_todas_fabricas,
                    u.ativo, u.ultimo_login_em, u.ultima_atividade_em, u.criado_em,
                    CASE WHEN u.bloqueado_ate > ? THEN 1 ELSE 0 END AS bloqueado
             FROM usuarios u LEFT JOIN empresas e ON e.id = u.empresa_id WHERE 1 = 1"""
    params: list = [agora()]
    if empresa_id is not None:
        sql += " AND u.empresa_id = ?"
        params.append(empresa_id)
    if busca:
        sql += " AND (u.nome LIKE ? OR u.email LIKE ? OR e.nome LIKE ?)"
        params += [f"%{busca}%"] * 3
    usuarios = [dict(r) for r in db.execute(sql + " ORDER BY u.nome", params)]
    for u in usuarios:
        u["fabricas"] = [r[0] for r in db.execute(
            "SELECT fabrica_id FROM usuario_fabricas WHERE usuario_id = ?", (u["id"],))]
    return usuarios


def _usuario_da_empresa(db: sqlite3.Connection, empresa_id: int, usuario_id: int) -> dict:
    u = db.execute("SELECT * FROM usuarios WHERE id = ? AND empresa_id = ?", (usuario_id, empresa_id)).fetchone()
    if u is None:
        raise HTTPException(404, "Usuário não encontrado.")
    return dict(u)


def _garantir_outro_admin(db: sqlite3.Connection, empresa_id: int, usuario_id: int) -> None:
    restantes = db.execute(
        "SELECT COUNT(*) FROM usuarios WHERE empresa_id = ? AND papel = 'admin_empresa' AND ativo = 1 AND id <> ?",
        (empresa_id, usuario_id)).fetchone()[0]
    if restantes == 0:
        raise HTTPException(409, "A empresa precisa manter pelo menos um administrador ativo.")


def atualizar_usuario(db: sqlite3.Connection, ctx: Contexto, empresa_id: int, usuario_id: int,
                      dados: AtualizarUsuario) -> dict:
    atual = _usuario_da_empresa(db, empresa_id, usuario_id)
    campos = dados.model_fields_set
    papel = dados.papel if "papel" in campos and dados.papel else atual["papel"]
    if papel not in PAPEIS_EMPRESA:
        raise HTTPException(422, "Papel inválido.")
    if atual["papel"] == "admin_empresa" and papel != "admin_empresa":
        if usuario_id == ctx.usuario["id"]:
            raise HTTPException(409, "Você não pode remover o seu próprio papel de administrador.")
        _garantir_outro_admin(db, empresa_id, usuario_id)
    todas = atual["acesso_todas_fabricas"] if dados.acesso_todas_fabricas is None else int(dados.acesso_todas_fabricas)
    nome = dados.nome.strip() if dados.nome else atual["nome"]
    fabricas_antes = [r[0] for r in db.execute("SELECT fabrica_id FROM usuario_fabricas WHERE usuario_id = ?",
                                               (usuario_id,))]
    fabricas = fabricas_antes
    if dados.fabricas is not None:
        fabricas = _validar_fabricas(db, empresa_id, dados.fabricas)
    if not todas and not fabricas:
        raise HTTPException(422, "Selecione ao menos uma fábrica ou conceda acesso a todas.")
    db.execute("UPDATE usuarios SET nome = ?, papel = ?, acesso_todas_fabricas = ?, atualizado_em = ? WHERE id = ?",
               (nome, papel, todas, agora(), usuario_id))
    db.execute("DELETE FROM usuario_fabricas WHERE usuario_id = ?", (usuario_id,))
    if not todas:
        for fabrica_id in fabricas:
            db.execute("INSERT INTO usuario_fabricas (empresa_id, usuario_id, fabrica_id) VALUES (?, ?, ?)",
                       (empresa_id, usuario_id, fabrica_id))
    auditoria.registrar(db, "USUARIO_PERMISSOES", ctx=ctx, entidade="usuario", entidade_id=usuario_id,
                        empresa_id=empresa_id,
                        antes={"nome": atual["nome"], "papel": atual["papel"],
                               "acesso_todas_fabricas": atual["acesso_todas_fabricas"], "fabricas": fabricas_antes},
                        depois={"nome": nome, "papel": papel, "acesso_todas_fabricas": todas,
                                "fabricas": fabricas if not todas else []})
    return {"ok": True}


def definir_ativo(db: sqlite3.Connection, ctx: Contexto, empresa_id: int, usuario_id: int, ativo: bool) -> dict:
    from .routers.auth import revogar_sessoes

    atual = _usuario_da_empresa(db, empresa_id, usuario_id)
    if not ativo:
        if usuario_id == ctx.usuario["id"]:
            raise HTTPException(409, "Você não pode desativar a sua própria conta.")
        if atual["papel"] == "admin_empresa":
            _garantir_outro_admin(db, empresa_id, usuario_id)
    else:
        empresa = dict(db.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone())
        if not atual["ativo"]:
            verificar_limite_usuarios(db, empresa, adicionais=1, contar_convites=False)
    db.execute("UPDATE usuarios SET ativo = ?, atualizado_em = ? WHERE id = ?", (1 if ativo else 0, agora(), usuario_id))
    revogadas = 0 if ativo else revogar_sessoes(db, usuario_id, "conta desativada")
    auditoria.registrar(db, "USUARIO_ATIVADO" if ativo else "USUARIO_DESATIVADO", ctx=ctx, entidade="usuario",
                        entidade_id=usuario_id, empresa_id=empresa_id,
                        detalhes=f"{revogadas} sessão(ões) encerrada(s)" if revogadas else "")
    return {"ok": True}


def revogar_sessoes_usuario(db: sqlite3.Connection, ctx: Contexto, empresa_id: int | None, usuario_id: int) -> dict:
    from .routers.auth import revogar_sessoes

    if empresa_id is not None:
        _usuario_da_empresa(db, empresa_id, usuario_id)
    total = revogar_sessoes(db, usuario_id, "revogadas pelo administrador")
    auditoria.registrar(db, "SESSOES_REVOGADAS", ctx=ctx, entidade="usuario", entidade_id=usuario_id,
                        empresa_id=empresa_id, detalhes=f"{total} sessão(ões)")
    return {"ok": True, "sessoes_revogadas": total}


def gerar_link_redefinicao(db: sqlite3.Connection, ctx: Contexto, empresa_id: int | None, usuario_id: int) -> dict:
    from .routers.auth import criar_redefinicao

    if empresa_id is not None:
        usuario = _usuario_da_empresa(db, empresa_id, usuario_id)
    else:
        u = db.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if u is None:
            raise HTTPException(404, "Usuário não encontrado.")
        usuario = dict(u)
    if not usuario["ativo"]:
        raise HTTPException(409, "Ative a conta antes de redefinir a senha.")
    token = criar_redefinicao(db, usuario, ctx.usuario["id"])
    link = f"{config_mod.config.app_url}/?redefinir={token}"
    enviado = email_servico.enviar(usuario["email"], "PCP Industrial — redefinição de senha",
                                   f"Para criar uma nova senha, acesse: {link}")
    auditoria.registrar(db, "REDEFINICAO_GERADA", ctx=ctx, entidade="usuario", entidade_id=usuario_id,
                        empresa_id=usuario["empresa_id"], detalhes="e-mail enviado" if enviado else "link entregue manualmente")
    return {"link": link, "email_enviado": enviado, "expira_minutos": config_mod.config.redefinicao_minutos,
            "aviso": None if enviado else "O e-mail NÃO foi enviado. Entregue o link ao usuário por um canal seguro."}
