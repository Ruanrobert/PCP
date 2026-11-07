"""Importação de relatórios: envio → prévia → (ajuste do mapeamento) → confirmação transacional."""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import auditoria
from .. import config as config_mod
from ..contexto import DB, Contexto, Escrita, Operacional, fabricas_visiveis
from ..db import abrir, agora
from ..fluxo import catalogo
from ..importador import (ErroArquivo, amostra_aba, analisar, detalhar_para_previa, detectar_cabecalho, ler_arquivo,
                          sugerir_mapeamento)
from ..planos import verificar_funcionalidade

router = APIRouter(prefix="/api/importacoes", tags=["importação"])
log = logging.getLogger("pcp.importacao")


class Mapeamento(BaseModel):
    aba: str = Field(max_length=200)
    mapeamento: dict


class Confirmacao(BaseModel):
    ignorar_linhas_com_erro: bool = False
    aplicar_alteracoes_op: bool = True


def _arquivo(empresa_id: int, importacao_id: int) -> Path:
    pasta = config_mod.config.dados_dir / "importacoes" / str(int(empresa_id))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta / f"{int(importacao_id)}.bin"


def _importacao(db: sqlite3.Connection, ctx: Contexto, importacao_id: int) -> dict:
    r = db.execute("SELECT * FROM importacoes WHERE id = ? AND empresa_id = ?", (importacao_id, ctx.empresa_id)).fetchone()
    if r is None:
        raise HTTPException(404, "Importação não encontrada.")
    d = dict(r)
    d["mapeamento"] = json.loads(d["mapeamento"] or "{}")
    d["resumo"] = json.loads(d["resumo"] or "{}")
    return d


def _carregar(ctx: Contexto, imp: dict) -> dict:
    caminho = _arquivo(ctx.empresa_id, imp["id"])
    if not caminho.exists():
        raise HTTPException(410, "O arquivo desta importação não está mais disponível. Envie-o novamente.")
    return ler_arquivo(imp["nome_arquivo"], caminho.read_bytes())


def _aba(lido: dict, nome: str | None) -> dict:
    if nome:
        for a in lido["abas"]:
            if a["nome"] == nome:
                return a
        raise HTTPException(422, f"Aba {nome!r} não encontrada no arquivo.")
    return max(lido["abas"], key=lambda a: len(a["linhas"]))


def _resposta(db, ctx, imp: dict, lido: dict, aba: dict, mapeamento: dict, analise: dict) -> dict:
    anteriores = [dict(r) for r in db.execute(
        "SELECT id, criado_em, confirmado_em FROM importacoes WHERE empresa_id = ? AND sha256 = ? AND estado = 'confirmada' "
        "AND id <> ?", (ctx.empresa_id, imp["sha256"], imp["id"]))]
    avisos = list(analise["avisos"])
    if anteriores:
        avisos.insert(0, f"Este mesmo arquivo já foi importado {len(anteriores)} vez(es) "
                         f"(última em {anteriores[-1]['confirmado_em'][:16]}). Reimportar não duplica leituras.")
    return {
        "importacao": {k: imp[k] for k in ("id", "nome_arquivo", "sha256", "tamanho_bytes", "formato", "estado", "criado_em")},
        "abas": [{"nome": a["nome"], "linhas": len(a["linhas"])} for a in lido["abas"]],
        "aba": aba["nome"], "amostra": amostra_aba(aba, 25), "mapeamento": mapeamento,
        "etapas": catalogo(db, ctx.empresa_id, incluir_inativas=False),
        "fabricas": [{"id": f["id"], "codigo": f["codigo"], "nome": f["nome"]} for f in fabricas_visiveis(db, ctx, False)],
        "resumo": analise["resumo"], "erros": analise["erros"], "avisos": avisos,
        "duplicidades": analise["duplicidades"], "ambiguas": analise["ambiguas"],
        "detalhes": detalhar_para_previa(analise), "importacoes_anteriores_mesmo_arquivo": anteriores,
    }


@router.get("")
def historico(ctx: Operacional, db: DB):
    return [dict(r, resumo=json.loads(r["resumo"] or "{}")) for r in db.execute(
        """SELECT i.id, i.nome_arquivo, i.sha256, i.formato, i.aba, i.estado, i.resumo, i.erro, i.criado_em,
                  i.confirmado_em, u.nome AS usuario_nome
           FROM importacoes i LEFT JOIN usuarios u ON u.id = i.usuario_id
           WHERE i.empresa_id = ? ORDER BY i.id DESC LIMIT 200""", (ctx.empresa_id,))]


@router.post("", status_code=201)
async def enviar(ctx: Escrita, db: DB, arquivo: UploadFile = File(...), aba: str | None = Form(None)):
    verificar_funcionalidade(db, ctx.empresa, "importacao")
    limite = config_mod.config.upload_max_mb * 1024 * 1024
    conteudo = await arquivo.read(limite + 1)
    if len(conteudo) > limite:
        raise HTTPException(413, f"Arquivo maior que {config_mod.config.upload_max_mb} MB.")
    nome = Path(arquivo.filename or "arquivo").name[:200]
    try:
        lido = ler_arquivo(nome, conteudo)
    except ErroArquivo as erro:
        raise HTTPException(422, str(erro)) from None
    escolhida = _aba(lido, aba)
    etapas = catalogo(db, ctx.empresa_id, incluir_inativas=False)
    mapeamento = sugerir_mapeamento(escolhida["linhas"], detectar_cabecalho(escolhida["linhas"]), etapas)
    fabricas = fabricas_visiveis(db, ctx, False)
    if len(fabricas) == 1:
        mapeamento["fabrica_padrao_id"] = fabricas[0]["id"]
    cur = db.execute(
        """INSERT INTO importacoes (empresa_id, usuario_id, nome_arquivo, sha256, tamanho_bytes, formato, aba, mapeamento,
               estado, criado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'previa', ?)""",
        (ctx.empresa_id, ctx.usuario["id"], nome, lido["sha256"], len(conteudo), lido["formato"], escolhida["nome"],
         json.dumps(mapeamento), agora()))
    imp = _importacao(db, ctx, cur.lastrowid)
    _arquivo(ctx.empresa_id, imp["id"]).write_bytes(conteudo)
    analise = analisar(db, ctx.empresa_id, fabricas, escolhida, mapeamento, nome)
    db.execute("UPDATE importacoes SET resumo = ? WHERE id = ?", (json.dumps(analise["resumo"]), imp["id"]))
    auditoria.registrar(db, "IMPORTACAO_ENVIADA", ctx=ctx, entidade="importacao", entidade_id=imp["id"],
                        importacao_id=imp["id"], depois={"arquivo": nome, "sha256": lido["sha256"], "resumo": analise["resumo"]})
    return _resposta(db, ctx, imp, lido, escolhida, mapeamento, analise)


@router.get("/{importacao_id}")
def detalhe(importacao_id: int, ctx: Operacional, db: DB):
    imp = _importacao(db, ctx, importacao_id)
    itens = [dict(r) for r in db.execute(
        "SELECT linha, tipo, acao, entidade, entidade_id, mensagem FROM importacao_itens WHERE importacao_id = ? "
        "AND empresa_id = ? ORDER BY id LIMIT 2000", (importacao_id, ctx.empresa_id))]
    return {"importacao": imp, "itens": itens}


@router.post("/{importacao_id}/analisar")
def reanalisar(importacao_id: int, dados: Mapeamento, ctx: Escrita, db: DB):
    imp = _importacao(db, ctx, importacao_id)
    if imp["estado"] != "previa":
        raise HTTPException(409, "Esta importação já foi finalizada.")
    lido = _carregar(ctx, imp)
    aba = _aba(lido, dados.aba)
    mapeamento = dados.mapeamento
    if aba["nome"] != imp["aba"] and "linha_cabecalho" not in mapeamento:
        mapeamento = sugerir_mapeamento(aba["linhas"], detectar_cabecalho(aba["linhas"]),
                                        catalogo(db, ctx.empresa_id, incluir_inativas=False))
    analise = analisar(db, ctx.empresa_id, fabricas_visiveis(db, ctx, False), aba, mapeamento, imp["nome_arquivo"])
    db.execute("UPDATE importacoes SET aba = ?, mapeamento = ?, resumo = ? WHERE id = ?",
               (aba["nome"], json.dumps(mapeamento), json.dumps(analise["resumo"]), importacao_id))
    return _resposta(db, ctx, imp, lido, aba, mapeamento, analise)


@router.post("/{importacao_id}/sugerir")
def resugerir(importacao_id: int, dados: Mapeamento, ctx: Escrita, db: DB):
    """Recalcula a sugestão automática para a aba (e linha de cabeçalho, se informada)."""
    imp = _importacao(db, ctx, importacao_id)
    lido = _carregar(ctx, imp)
    aba = _aba(lido, dados.aba)
    linha = dados.mapeamento.get("linha_cabecalho")
    linha = int(linha) if isinstance(linha, int) and 0 <= linha < len(aba["linhas"]) else detectar_cabecalho(aba["linhas"])
    mapeamento = sugerir_mapeamento(aba["linhas"], linha, catalogo(db, ctx.empresa_id, incluir_inativas=False))
    fabricas = fabricas_visiveis(db, ctx, False)
    if len(fabricas) == 1:
        mapeamento["fabrica_padrao_id"] = fabricas[0]["id"]
    analise = analisar(db, ctx.empresa_id, fabricas, aba, mapeamento, imp["nome_arquivo"])
    return _resposta(db, ctx, imp, lido, aba, mapeamento, analise)


@router.post("/{importacao_id}/cancelar")
def cancelar(importacao_id: int, ctx: Escrita, db: DB):
    imp = _importacao(db, ctx, importacao_id)
    if imp["estado"] != "previa":
        raise HTTPException(409, "Esta importação já foi finalizada.")
    db.execute("UPDATE importacoes SET estado = 'cancelada' WHERE id = ?", (importacao_id,))
    auditoria.registrar(db, "IMPORTACAO_CANCELADA", ctx=ctx, entidade="importacao", entidade_id=importacao_id,
                        importacao_id=importacao_id)
    return {"ok": True}


@router.post("/{importacao_id}/confirmar")
def confirmar(importacao_id: int, dados: Confirmacao, ctx: Escrita, db: DB):
    imp = _importacao(db, ctx, importacao_id)
    if imp["estado"] != "previa":
        raise HTTPException(409, "Esta importação já foi finalizada.")
    lido = _carregar(ctx, imp)
    aba = _aba(lido, imp["aba"])
    # A análise é refeita sobre o estado ATUAL do banco, dentro da mesma transação da gravação.
    analise = analisar(db, ctx.empresa_id, fabricas_visiveis(db, ctx, False), aba, imp["mapeamento"], imp["nome_arquivo"])
    erros_estruturais = [e for e in analise["erros"] if e["linha"] is None or e["coluna"] is None]
    if erros_estruturais:
        raise HTTPException(422, "Corrija o mapeamento antes de confirmar: " + erros_estruturais[0]["mensagem"])
    if analise["erros"] and not dados.ignorar_linhas_com_erro:
        raise HTTPException(422, f"Há {len(analise['erros'])} erro(s) em linhas do arquivo. Corrija o mapeamento "
                                 "ou confirme explicitamente que as linhas com erro serão ignoradas.")
    try:
        resultado = _aplicar(db, ctx, imp, analise, dados)
    except HTTPException:
        raise
    except Exception as erro:
        db.rollback()
        log.exception("Falha ao gravar importação %s", importacao_id)
        _marcar_falha(ctx.empresa_id, importacao_id, type(erro).__name__)
        raise HTTPException(500, "A importação falhou e NENHUM dado foi gravado (operação revertida).") from None
    return resultado


def _marcar_falha(empresa_id: int, importacao_id: int, motivo: str) -> None:
    con = abrir()
    try:
        con.execute("UPDATE importacoes SET estado = 'falhou', erro = ? WHERE id = ? AND empresa_id = ?",
                    (motivo[:200], importacao_id, empresa_id))
        con.commit()
    finally:
        con.close()


def _aplicar(db: sqlite3.Connection, ctx: Contexto, imp: dict, analise: dict, dados: Confirmacao) -> dict:
    interno = analise["_interno"]
    ops, comp = interno["ops"], interno["comparacao"]
    linhas_erro = set(analise["linhas_com_erro"])
    momento = agora()
    imp_id = imp["id"]
    contagem = {"remessas_criadas": 0, "ops_criadas": 0, "ops_atualizadas": 0, "leituras_adicionadas": 0,
                "leituras_ja_conhecidas": 0, "leituras_para_conferencia": 0, "linhas_ignoradas_por_erro": len(linhas_erro)}

    def item(linha, tipo, acao, entidade="", entidade_id=None, mensagem="", extra=None):
        db.execute("""INSERT INTO importacao_itens (empresa_id, importacao_id, linha, tipo, acao, entidade, entidade_id,
                      mensagem, dados) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                   (ctx.empresa_id, imp_id, linha, tipo, acao, entidade, entidade_id, mensagem,
                    json.dumps(extra or {}, ensure_ascii=False, default=str)))

    # 1) Remessas novas
    remessa_ids: dict[tuple, int] = {}
    for r in comp["remessas_novas"]:
        materiais = sorted(r["materiais"])
        descricao = (", ".join(materiais)[:200]) if materiais else f"Importada de {imp['nome_arquivo']}"[:200]
        cur = db.execute(
            """INSERT INTO remessas (empresa_id, fabrica_id, numero, tipo_remessa, descricao, data_entrega, quantidade_pares,
                   status, origem, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, NULL, ?, 'A programar', 'importacao', ?, ?)""",
            (ctx.empresa_id, r["fabrica_id"], r["numero"], r["tipo_remessa"], descricao, r["quantidade"], momento, momento))
        rid = cur.lastrowid
        remessa_ids[(r["fabrica_id"], r["tipo_remessa"], r["numero"])] = rid
        db.execute("""INSERT INTO historico_remessas (empresa_id, remessa_id, acao, detalhes, registrado_em, usuario_id)
                      VALUES (?, ?, 'IMPORTACAO', ?, ?, ?)""",
                   (ctx.empresa_id, rid, f"Remessa criada pela importação #{imp_id} ({imp['nome_arquivo']})", momento,
                    ctx.usuario["id"]))
        item(None, "remessa", "novo", "remessa", rid, f"Remessa {r['numero']}")
        contagem["remessas_criadas"] += 1
    for r in comp["remessas_existentes"]:
        remessa_ids[(r["fabrica_id"], r["tipo_remessa"], r["numero"])] = r["id"]

    # 2) OPs
    for chave, o in ops.items():
        o["remessa_id"] = remessa_ids[(o["fabrica_id"], o["tipo_remessa"], o["remessa"])]
        if not o["op"] or all(l in linhas_erro for l in o["linhas"]):
            continue
        if o.get("op_id") is None:
            cur = db.execute(
                """INSERT INTO ops (empresa_id, remessa_id, numero, pedido, material, quantidade_prevista, origem,
                       importacao_id, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, 'importacao', ?, ?, ?)""",
                (ctx.empresa_id, o["remessa_id"], o["op"], o["pedido"], o["material"], o["quantidade_total"], imp_id,
                 momento, momento))
            o["op_id"] = cur.lastrowid
            item(o["linhas"][0], "op", "novo", "op", o["op_id"], f"OP {o['op']}")
            auditoria.registrar(db, "OP_IMPORTADA", ctx=ctx, entidade="op", entidade_id=o["op_id"],
                                fabrica_id=o["fabrica_id"], origem="importacao", importacao_id=imp_id,
                                depois={k: o[k] for k in ("remessa", "op", "pedido", "material", "quantidade_total")})
            contagem["ops_criadas"] += 1
        elif o.get("mudancas"):
            if dados.aplicar_alteracoes_op:
                novos = {c: v["depois"] for c, v in o["mudancas"].items()}
                sets = ", ".join(f"{c} = ?" for c in novos)
                db.execute(f"UPDATE ops SET {sets}, atualizado_em = ? WHERE id = ? AND empresa_id = ?",
                           [*novos.values(), momento, o["op_id"], ctx.empresa_id])
                item(o["linhas"][0], "op", "atualizado", "op", o["op_id"], "Dados da OP atualizados", o["mudancas"])
                auditoria.registrar(db, "OP_ATUALIZADA_IMPORTACAO", ctx=ctx, entidade="op", entidade_id=o["op_id"],
                                    fabrica_id=o["fabrica_id"], origem="importacao", importacao_id=imp_id,
                                    antes={c: v["antes"] for c, v in o["mudancas"].items()}, depois=novos)
                contagem["ops_atualizadas"] += 1
            else:
                item(o["linhas"][0], "op", "conferir", "op", o["op_id"], "Alterações detectadas e não aplicadas",
                     o["mudancas"])
        else:
            item(o["linhas"][0], "op", "ignorado", "op", o["op_id"], "OP já conhecida, sem alterações")

    # 3) Leituras
    for l in comp["leituras_novas"]:
        if l["linha"] in linhas_erro:
            continue
        o = ops[l["chave_op"]]
        cur = db.execute(
            """INSERT INTO leituras (empresa_id, remessa_id, op_id, etapa_id, data_leitura, quantidade, tipo_valor,
                   chave_origem, id_origem, importacao_id, ultima_importacao_id, linha_origem, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ctx.empresa_id, o["remessa_id"], o.get("op_id"), l["etapa_id"], l["data"], l["quantidade"],
             imp["mapeamento"].get("tipo_valor", "movimento"), l["chave"], l["id_mov"], imp_id, imp_id, l["linha"],
             momento, momento))
        item(l["linha"], "leitura", "novo", "op" if o.get("op_id") else "remessa", o.get("op_id") or o["remessa_id"],
             f"Leitura etapa {l['etapa_id']} {l['data'] or 'sem data'} qtd {l['quantidade'] if l['quantidade'] is not None else 'não informada'}")
        contagem["leituras_adicionadas"] += 1
    for l in comp["leituras_conhecidas"]:
        db.execute("UPDATE leituras SET ultima_importacao_id = ? WHERE id = ? AND empresa_id = ?",
                   (imp_id, l["leitura_id"], ctx.empresa_id))
        contagem["leituras_ja_conhecidas"] += 1
    for l in comp["leituras_alteradas"]:
        if l["linha"] in linhas_erro:
            continue
        # Não sobrescreve: marca para conferência e guarda o valor novo no item da importação.
        db.execute("UPDATE leituras SET situacao = 'conferir', observacao = ?, ultima_importacao_id = ?, atualizado_em = ? "
                   "WHERE id = ? AND empresa_id = ?",
                   (f"Importação #{imp_id} trouxe quantidade {l['quantidade']} e data {l['data']} "
                    f"(antes {l['antes']['quantidade']} em {l['antes']['data']}).", imp_id, momento, l["leitura_id"],
                    ctx.empresa_id))
        o = ops[l["chave_op"]]
        item(l["linha"], "leitura", "conferir", "op" if o.get("op_id") else "remessa", o.get("op_id") or o["remessa_id"],
             "Leitura existente com valor diferente: enviada para conferência", {"antes": l["antes"],
                                                                                 "depois": {"quantidade": l["quantidade"], "data": l["data"]}})
        contagem["leituras_para_conferencia"] += 1
    for e in analise["erros"]:
        item(e["linha"], "linha", "erro", mensagem=e["mensagem"])
    for a in analise["ambiguas"]:
        item(a["linhas"][0], "leitura", "conferir", mensagem=a["mensagem"], extra=a)

    resumo = dict(analise["resumo"], resultado=contagem)
    db.execute("UPDATE importacoes SET estado = 'confirmada', resumo = ?, confirmado_em = ?, confirmado_por = ? WHERE id = ?",
               (json.dumps(resumo), momento, ctx.usuario["id"], imp_id))
    auditoria.registrar(db, "IMPORTACAO_CONFIRMADA", ctx=ctx, entidade="importacao", entidade_id=imp_id,
                        importacao_id=imp_id, origem="importacao", depois=contagem,
                        detalhes=f"{imp['nome_arquivo']} ({imp['sha256'][:12]})")
    return {"ok": True, "importacao_id": imp_id, "resultado": contagem, "resumo": resumo}
