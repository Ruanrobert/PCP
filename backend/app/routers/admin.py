"""Painel do proprietário da plataforma (somente admin_geral)."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auditoria
from ..contexto import DB, AdminGeral, estado_efetivo
from ..db import agora
from ..etapas_padrao import criar_etapas_padrao
from ..planos import uso_da_empresa
from ..usuarios_servico import (AtualizarUsuario, NovoConvite, atualizar_usuario, criar_convite, definir_ativo,
                                gerar_link_redefinicao, listar_convites, listar_usuarios, revogar_convite,
                                revogar_sessoes_usuario)

router = APIRouter(prefix="/api/admin", tags=["administração da plataforma"])

ESTADOS = ("teste", "ativa", "vencida", "suspensa", "cancelada")
DATA = r"^\d{4}-\d{2}-\d{2}$"


class FabricaInicial(BaseModel):
    codigo: str = Field(min_length=1, max_length=30)
    nome: str = Field(min_length=1, max_length=120)


class DadosEmpresa(BaseModel):
    nome: str = Field(min_length=2, max_length=160)
    documento: str = Field(default="", max_length=30)
    email_contato: str = Field(default="", max_length=254)
    telefone: str = Field(default="", max_length=40)
    observacoes: str = Field(default="", max_length=2000)
    plano_id: int | None = None
    estado: str = Field(default="teste", pattern=r"^(teste|ativa)$")
    inicio_em: str | None = Field(default=None, pattern=DATA)
    teste_ate: str | None = Field(default=None, pattern=DATA)
    vencimento_em: str | None = Field(default=None, pattern=DATA)
    carencia_dias: int = Field(default=0, ge=0, le=365)
    criar_etapas_padrao: bool = False
    fabricas: list[FabricaInicial] = Field(default_factory=list, max_length=100)


class EditarEmpresa(BaseModel):
    nome: str | None = Field(default=None, min_length=2, max_length=160)
    documento: str | None = Field(default=None, max_length=30)
    email_contato: str | None = Field(default=None, max_length=254)
    telefone: str | None = Field(default=None, max_length=40)
    observacoes: str | None = Field(default=None, max_length=2000)
    plano_id: int | None = None
    inicio_em: str | None = Field(default=None, pattern=DATA)
    teste_ate: str | None = Field(default=None, pattern=DATA)
    vencimento_em: str | None = Field(default=None, pattern=DATA)
    carencia_dias: int | None = Field(default=None, ge=0, le=365)


class MudarEstado(BaseModel):
    estado: str = Field(pattern=r"^(teste|ativa|vencida|suspensa|cancelada)$")
    motivo: str = Field(default="", max_length=500)


class DadosPlano(BaseModel):
    codigo: str = Field(min_length=2, max_length=40, pattern=r"^[A-Za-z0-9_\-]+$")
    nome: str = Field(min_length=2, max_length=80)
    descricao: str = Field(default="", max_length=500)
    preco_mensal_centavos: int | None = Field(default=None, ge=0)
    limite_usuarios: int | None = Field(default=None, ge=0)
    limite_fabricas: int | None = Field(default=None, ge=0)
    funcionalidades: list[str] = Field(default_factory=list, max_length=50)
    dias_teste: int = Field(default=0, ge=0, le=365)
    ativo: bool = True


class Ativo(BaseModel):
    ativo: bool


def _empresa(db, empresa_id: int) -> dict:
    e = db.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if e is None:
        raise HTTPException(404, "Empresa não encontrada.")
    return dict(e)


def _validar_plano(db, plano_id: int | None) -> None:
    if plano_id is not None and not db.execute("SELECT 1 FROM planos WHERE id = ?", (plano_id,)).fetchone():
        raise HTTPException(422, "Plano não encontrado.")


def _descrever(db, e: dict) -> dict:
    item = {k: e[k] for k in ("id", "nome", "documento", "email_contato", "telefone", "observacoes", "tipo",
                              "estado", "motivo_estado", "plano_id", "inicio_em", "teste_ate", "vencimento_em",
                              "carencia_dias", "pagamento_confirmado_em", "suporte_autorizado_ate", "criado_em",
                              "atualizado_em")}
    item["estado_efetivo"] = estado_efetivo(e)
    item["uso"] = uso_da_empresa(db, e["id"])
    item["ultima_atividade_em"] = db.execute(
        "SELECT MAX(ultima_atividade_em) FROM usuarios WHERE empresa_id = ?", (e["id"],)).fetchone()[0]
    plano = db.execute("SELECT nome FROM planos WHERE id = ?", (e["plano_id"],)).fetchone() if e["plano_id"] else None
    item["plano_nome"] = plano["nome"] if plano else None
    return item


# ------------------------------------------------------------------ resumo
@router.get("/resumo")
def resumo(ctx: AdminGeral, db: DB):
    empresas = [dict(r) for r in db.execute("SELECT * FROM empresas WHERE tipo = 'cliente'")]
    por_estado: dict[str, int] = {e: 0 for e in ESTADOS}
    for e in empresas:
        por_estado[estado_efetivo(e)] += 1
    return {
        "empresas": len(empresas), "por_estado": por_estado,
        "usuarios_ativos": db.execute("SELECT COUNT(*) FROM usuarios WHERE ativo = 1 AND papel <> 'admin_geral'").fetchone()[0],
        "convites_pendentes": db.execute(
            "SELECT COUNT(*) FROM convites WHERE aceito_em IS NULL AND revogado_em IS NULL AND expira_em > ?",
            (agora(),)).fetchone()[0],
        "area_interna": [dict(r) for r in db.execute("SELECT id, nome FROM empresas WHERE tipo = 'interna'")],
    }


# ------------------------------------------------------------------ empresas
@router.get("/empresas")
def listar_empresas(ctx: AdminGeral, db: DB, busca: str = Query("", max_length=100),
                    estado: str | None = Query(None, pattern=r"^(teste|ativa|vencida|suspensa|cancelada)$"),
                    incluir_internas: bool = False):
    sql = "SELECT * FROM empresas WHERE 1 = 1"
    params: list = []
    if not incluir_internas:
        sql += " AND tipo = 'cliente'"
    if busca:
        sql += " AND (nome LIKE ? OR documento LIKE ? OR email_contato LIKE ?)"
        params += [f"%{busca}%"] * 3
    itens = [_descrever(db, dict(r)) for r in db.execute(sql + " ORDER BY nome", params)]
    if estado:
        itens = [i for i in itens if i["estado_efetivo"] == estado]
    return itens


@router.post("/empresas", status_code=201)
def criar_empresa(dados: DadosEmpresa, ctx: AdminGeral, db: DB):
    _validar_plano(db, dados.plano_id)
    momento = agora()
    cur = db.execute(
        """INSERT INTO empresas (nome, documento, email_contato, telefone, observacoes, tipo, estado, plano_id,
               inicio_em, teste_ate, vencimento_em, carencia_dias, criado_em, atualizado_em)
           VALUES (?, ?, ?, ?, ?, 'cliente', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (dados.nome.strip(), dados.documento.strip(), dados.email_contato.strip(), dados.telefone.strip(),
         dados.observacoes.strip(), dados.estado, dados.plano_id, dados.inicio_em or momento[:10],
         dados.teste_ate, dados.vencimento_em, dados.carencia_dias, momento, momento),
    )
    empresa_id = cur.lastrowid
    codigos = set()
    for f in dados.fabricas:
        codigo = f.codigo.strip()
        if codigo.upper() in codigos:
            raise HTTPException(422, f"Código de fábrica repetido: {codigo}")
        codigos.add(codigo.upper())
        db.execute("INSERT INTO fabricas (empresa_id, codigo, nome, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
                   (empresa_id, codigo, f.nome.strip(), momento, momento))
    if dados.criar_etapas_padrao:
        criar_etapas_padrao(db, empresa_id)
    empresa = _empresa(db, empresa_id)
    auditoria.registrar(db, "EMPRESA_CRIADA", ctx=ctx, entidade="empresa", entidade_id=empresa_id,
                        empresa_id=empresa_id, depois=_descrever(db, empresa))
    return _descrever(db, empresa)


@router.get("/empresas/{empresa_id}")
def obter_empresa(empresa_id: int, ctx: AdminGeral, db: DB):
    e = _empresa(db, empresa_id)
    item = _descrever(db, e)
    item["fabricas"] = [dict(r) for r in db.execute(
        "SELECT id, codigo, nome, ativa FROM fabricas WHERE empresa_id = ? ORDER BY codigo", (empresa_id,))]
    item["usuarios"] = listar_usuarios(db, empresa_id)
    item["convites"] = listar_convites(db, empresa_id)
    return item


@router.put("/empresas/{empresa_id}")
def editar_empresa(empresa_id: int, dados: EditarEmpresa, ctx: AdminGeral, db: DB):
    atual = _empresa(db, empresa_id)
    campos = dados.model_fields_set
    if "plano_id" in campos:
        _validar_plano(db, dados.plano_id)
    novo = dict(atual)
    for campo in campos:
        valor = getattr(dados, campo)
        novo[campo] = valor.strip() if isinstance(valor, str) else valor
    db.execute(
        """UPDATE empresas SET nome = ?, documento = ?, email_contato = ?, telefone = ?, observacoes = ?, plano_id = ?,
               inicio_em = ?, teste_ate = ?, vencimento_em = ?, carencia_dias = ?, atualizado_em = ? WHERE id = ?""",
        (novo["nome"], novo["documento"], novo["email_contato"], novo["telefone"], novo["observacoes"],
         novo["plano_id"], novo["inicio_em"], novo["teste_ate"], novo["vencimento_em"], novo["carencia_dias"],
         agora(), empresa_id),
    )
    auditoria.registrar(db, "EMPRESA_EDITADA", ctx=ctx, entidade="empresa", entidade_id=empresa_id,
                        empresa_id=empresa_id, antes={c: atual[c] for c in campos}, depois={c: novo[c] for c in campos})
    return _descrever(db, _empresa(db, empresa_id))


@router.post("/empresas/{empresa_id}/estado")
def mudar_estado(empresa_id: int, dados: MudarEstado, ctx: AdminGeral, db: DB):
    from .auth import revogar_sessoes

    atual = _empresa(db, empresa_id)
    if atual["tipo"] == "interna":
        raise HTTPException(409, "O estado da área interna não pode ser alterado.")
    if dados.estado in ("suspensa", "cancelada") and len(dados.motivo.strip()) < 5:
        raise HTTPException(422, "Informe o motivo da suspensão/cancelamento.")
    db.execute("UPDATE empresas SET estado = ?, motivo_estado = ?, atualizado_em = ? WHERE id = ?",
               (dados.estado, dados.motivo.strip(), agora(), empresa_id))
    revogadas = 0
    if dados.estado in ("suspensa", "cancelada"):
        # Política: bloqueia login e encerra sessões; NENHUM dado é apagado.
        for (uid,) in db.execute("SELECT id FROM usuarios WHERE empresa_id = ?", (empresa_id,)).fetchall():
            revogadas += revogar_sessoes(db, uid, f"empresa {dados.estado}")
        db.execute("UPDATE empresas SET suporte_autorizado_ate = NULL WHERE id = ?", (empresa_id,))
    auditoria.registrar(db, "EMPRESA_ESTADO", ctx=ctx, entidade="empresa", entidade_id=empresa_id,
                        empresa_id=empresa_id, antes={"estado": atual["estado"]},
                        depois={"estado": dados.estado}, detalhes=f"{dados.motivo.strip()} — {revogadas} sessão(ões) encerrada(s)")
    return _descrever(db, _empresa(db, empresa_id))


# ------------------------------------------------------------------ usuários e convites (qualquer empresa)
@router.get("/usuarios")
def usuarios(ctx: AdminGeral, db: DB, empresa_id: int | None = None, busca: str = Query("", max_length=100)):
    return listar_usuarios(db, empresa_id, busca)


@router.put("/empresas/{empresa_id}/usuarios/{usuario_id}")
def editar_usuario(empresa_id: int, usuario_id: int, dados: AtualizarUsuario, ctx: AdminGeral, db: DB):
    _empresa(db, empresa_id)
    return atualizar_usuario(db, ctx, empresa_id, usuario_id, dados)


@router.post("/empresas/{empresa_id}/usuarios/{usuario_id}/ativo")
def ativar_usuario(empresa_id: int, usuario_id: int, dados: Ativo, ctx: AdminGeral, db: DB):
    _empresa(db, empresa_id)
    return definir_ativo(db, ctx, empresa_id, usuario_id, dados.ativo)


@router.post("/usuarios/{usuario_id}/revogar-sessoes")
def revogar_sessoes_rota(usuario_id: int, ctx: AdminGeral, db: DB):
    if not db.execute("SELECT 1 FROM usuarios WHERE id = ?", (usuario_id,)).fetchone():
        raise HTTPException(404, "Usuário não encontrado.")
    return revogar_sessoes_usuario(db, ctx, None, usuario_id)


@router.post("/usuarios/{usuario_id}/redefinicao")
def redefinicao(usuario_id: int, ctx: AdminGeral, db: DB):
    return gerar_link_redefinicao(db, ctx, None, usuario_id)


@router.get("/empresas/{empresa_id}/convites")
def convites(empresa_id: int, ctx: AdminGeral, db: DB):
    _empresa(db, empresa_id)
    return listar_convites(db, empresa_id)


@router.post("/empresas/{empresa_id}/convites", status_code=201)
def convidar(empresa_id: int, dados: NovoConvite, ctx: AdminGeral, db: DB):
    return criar_convite(db, ctx, _empresa(db, empresa_id), dados)


@router.post("/empresas/{empresa_id}/convites/{convite_id}/revogar")
def revogar(empresa_id: int, convite_id: int, ctx: AdminGeral, db: DB):
    revogar_convite(db, ctx, empresa_id, convite_id)
    return {"ok": True}


# ------------------------------------------------------------------ planos
def _plano_saida(r) -> dict:
    p = dict(r)
    p["funcionalidades"] = json.loads(p["funcionalidades"] or "[]")
    p["ativo"] = bool(p["ativo"])
    return p


@router.get("/planos")
def listar_planos(ctx: AdminGeral, db: DB):
    return [_plano_saida(r) for r in db.execute("SELECT * FROM planos ORDER BY nome")]


@router.post("/planos", status_code=201)
def criar_plano(dados: DadosPlano, ctx: AdminGeral, db: DB):
    if db.execute("SELECT 1 FROM planos WHERE codigo = ?", (dados.codigo,)).fetchone():
        raise HTTPException(409, "Já existe um plano com esse código.")
    momento = agora()
    cur = db.execute(
        """INSERT INTO planos (codigo, nome, descricao, preco_mensal_centavos, limite_usuarios, limite_fabricas,
               funcionalidades, dias_teste, ativo, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (dados.codigo, dados.nome.strip(), dados.descricao.strip(), dados.preco_mensal_centavos, dados.limite_usuarios,
         dados.limite_fabricas, json.dumps(dados.funcionalidades), dados.dias_teste, 1 if dados.ativo else 0,
         momento, momento))
    plano = _plano_saida(db.execute("SELECT * FROM planos WHERE id = ?", (cur.lastrowid,)).fetchone())
    auditoria.registrar(db, "PLANO_CRIADO", ctx=ctx, entidade="plano", entidade_id=plano["id"], depois=plano)
    return plano


@router.put("/planos/{plano_id}")
def editar_plano(plano_id: int, dados: DadosPlano, ctx: AdminGeral, db: DB):
    atual = db.execute("SELECT * FROM planos WHERE id = ?", (plano_id,)).fetchone()
    if atual is None:
        raise HTTPException(404, "Plano não encontrado.")
    if db.execute("SELECT 1 FROM planos WHERE codigo = ? AND id <> ?", (dados.codigo, plano_id)).fetchone():
        raise HTTPException(409, "Já existe um plano com esse código.")
    db.execute(
        """UPDATE planos SET codigo = ?, nome = ?, descricao = ?, preco_mensal_centavos = ?, limite_usuarios = ?,
               limite_fabricas = ?, funcionalidades = ?, dias_teste = ?, ativo = ?, atualizado_em = ? WHERE id = ?""",
        (dados.codigo, dados.nome.strip(), dados.descricao.strip(), dados.preco_mensal_centavos, dados.limite_usuarios,
         dados.limite_fabricas, json.dumps(dados.funcionalidades), dados.dias_teste, 1 if dados.ativo else 0,
         agora(), plano_id))
    plano = _plano_saida(db.execute("SELECT * FROM planos WHERE id = ?", (plano_id,)).fetchone())
    auditoria.registrar(db, "PLANO_EDITADO", ctx=ctx, entidade="plano", entidade_id=plano_id,
                        antes=_plano_saida(atual), depois=plano)
    return plano


# ------------------------------------------------------------------ auditoria
@router.get("/auditoria")
def listar_auditoria(ctx: AdminGeral, db: DB, empresa_id: int | None = None,
                     acao: str = Query("", max_length=60), desde: str | None = Query(None, pattern=DATA),
                     ate: str | None = Query(None, pattern=DATA), limite: int = Query(200, ge=1, le=1000),
                     deslocamento: int = Query(0, ge=0)):
    sql = """SELECT a.*, e.nome AS empresa_nome FROM auditoria a LEFT JOIN empresas e ON e.id = a.empresa_id
             WHERE 1 = 1"""
    params: list = []
    if empresa_id is not None:
        sql += " AND a.empresa_id = ?"
        params.append(empresa_id)
    if acao:
        sql += " AND a.acao LIKE ?"
        params.append(f"%{acao.upper()}%")
    if desde:
        sql += " AND a.criado_em >= ?"
        params.append(desde)
    if ate:
        sql += " AND a.criado_em < date(?, '+1 day')"
        params.append(ate)
    sql += " ORDER BY a.id DESC LIMIT ? OFFSET ?"
    params += [limite, deslocamento]
    return [dict(r) for r in db.execute(sql, params)]
