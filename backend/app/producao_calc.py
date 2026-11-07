"""Consolidação da produção por remessa / OP / etapa.

REGRAS (documentadas em docs/REGRAS_PRODUCAO.md):
1. Lançamentos manuais (producao_diaria, não estornados) e leituras importadas (leituras) ficam SEPARADOS.
   Nunca são somados entre si.
2. Leituras do tipo 'movimento' somam; leituras do tipo 'acumulado' valem pelo valor mais recente
   (por data de leitura e, em empate, pela importação mais recente).
3. Quantidade considerada da etapa:
   - só manual → manual; só importado → importado;
   - ambos e iguais → esse valor;
   - ambos e diferentes → NÃO calculada; a etapa fica "Com divergência" para revisão humana.
4. Leitura sem quantidade comprova apenas a passagem (data); não gera quantidade nem saldo.
5. Estados da etapa:
   - Sem leitura: nenhuma informação na OP em nenhuma etapa.
   - Pendente: sem informação nesta etapa, mas a OP já tem informação em outra etapa.
   - Em andamento: há leitura sem quantidade, ou há quantidade mas a quantidade prevista é desconhecida.
   - Parcialmente concluída: 0 < quantidade considerada < prevista.
   - Concluída: quantidade considerada = prevista.
   - Com divergência: manual ≠ importado, quantidade > prevista, ou leitura marcada para conferência.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

from .contexto import em_lista

SEM_LEITURA = "Sem leitura"
PENDENTE = "Pendente"
EM_ANDAMENTO = "Em andamento"
PARCIAL = "Parcialmente concluída"
CONCLUIDA = "Concluída"
DIVERGENCIA = "Com divergência"
ESTADOS = [SEM_LEITURA, PENDENTE, EM_ANDAMENTO, PARCIAL, CONCLUIDA, DIVERGENCIA]


def _vazio() -> dict:
    return {"manual": None, "importado": None, "leituras": 0, "leituras_sem_quantidade": 0, "lancamentos": 0,
            "conferir": 0, "ultima_data": None}


def agregar(db: sqlite3.Connection, empresa_id: int, remessa_ids: list[int]) -> dict:
    """Retorna {(remessa_id, op_id ou None, etapa_id): dados} para as remessas informadas."""
    dados: dict = defaultdict(_vazio)
    if not remessa_ids:
        return dados
    clausula, params = em_lista("remessa_id", remessa_ids)

    for r in db.execute(
            f"""SELECT remessa_id, op_id, etapa_id, SUM(quantidade_pares) AS total, COUNT(*) AS n,
                       MAX(data_producao) AS ultima
                FROM producao_diaria WHERE empresa_id = ? AND estornado_em IS NULL AND {clausula}
                GROUP BY remessa_id, op_id, etapa_id""", [empresa_id, *params]):
        d = dados[(r["remessa_id"], r["op_id"], r["etapa_id"])]
        d["manual"] = r["total"]
        d["lancamentos"] = r["n"]
        d["ultima_data"] = max(filter(None, [d["ultima_data"], r["ultima"]]), default=None)

    ultimos_acumulados: dict = {}
    for r in db.execute(
            f"""SELECT remessa_id, op_id, etapa_id, data_leitura, quantidade, tipo_valor, situacao, ultima_importacao_id, id
                FROM leituras WHERE empresa_id = ? AND {clausula}
                ORDER BY data_leitura, ultima_importacao_id, id""", [empresa_id, *params]):
        chave = (r["remessa_id"], r["op_id"], r["etapa_id"])
        d = dados[chave]
        d["leituras"] += 1
        if r["situacao"] == "conferir":
            d["conferir"] += 1
        if r["data_leitura"]:
            d["ultima_data"] = max(filter(None, [d["ultima_data"], r["data_leitura"][:10]]), default=None)
        if r["quantidade"] is None:
            d["leituras_sem_quantidade"] += 1
        elif r["tipo_valor"] == "acumulado":
            ultimos_acumulados[chave] = r["quantidade"]  # ordenado: o último vence
        else:
            d["importado"] = (d["importado"] or 0) + r["quantidade"]
    for chave, valor in ultimos_acumulados.items():
        d = dados[chave]
        d["importado"] = (d["importado"] or 0) + valor
    return dados


def considerar(d: dict) -> tuple[int | None, bool]:
    """(quantidade considerada, divergente)."""
    manual, importado = d["manual"], d["importado"]
    if manual is not None and importado is not None:
        return (manual, False) if manual == importado else (None, True)
    return (manual if manual is not None else importado), False


def estado_etapa(d: dict, previsto: int | None, op_tem_informacao: bool) -> tuple[str, int | None, int | None]:
    """(estado, quantidade considerada, saldo ou None quando não é calculável com segurança)."""
    qtd, divergente = considerar(d)
    tem_info = d["leituras"] > 0 or d["lancamentos"] > 0
    if divergente or d["conferir"]:
        return DIVERGENCIA, qtd, None
    if not tem_info:
        return (PENDENTE if op_tem_informacao else SEM_LEITURA), None, (previsto if previsto else None)
    if qtd is None:
        return EM_ANDAMENTO, None, None  # só leituras sem quantidade: passagem comprovada, sem saldo
    if qtd == 0:
        return PENDENTE, 0, (previsto if previsto else None)
    if not previsto:
        return EM_ANDAMENTO, qtd, None
    if qtd > previsto:
        return DIVERGENCIA, qtd, None
    if qtd == previsto:
        return CONCLUIDA, qtd, 0
    return PARCIAL, qtd, previsto - qtd


def estado_geral(estados: list[str]) -> str:
    if not estados:
        return SEM_LEITURA
    if DIVERGENCIA in estados:
        return DIVERGENCIA
    if all(e == CONCLUIDA for e in estados):
        return CONCLUIDA
    if all(e == SEM_LEITURA for e in estados):
        return SEM_LEITURA
    if all(e in (SEM_LEITURA, PENDENTE) for e in estados):
        return PENDENTE
    return EM_ANDAMENTO


def somar_nivel_remessa(dados: dict, remessa_id: int, etapa_id: int) -> dict:
    """Soma, para a remessa inteira, as informações de todas as OPs e do nível da remessa."""
    total = _vazio()
    for (rid, _op, eid), d in dados.items():
        if rid != remessa_id or eid != etapa_id:
            continue
        for campo in ("leituras", "leituras_sem_quantidade", "lancamentos", "conferir"):
            total[campo] += d[campo]
        for campo in ("manual", "importado"):
            if d[campo] is not None:
                total[campo] = (total[campo] or 0) + d[campo]
        if d["ultima_data"]:
            total["ultima_data"] = max(filter(None, [total["ultima_data"], d["ultima_data"]]))
    return total
