"""Modelo opcional de etapas para novas empresas (lista informada pelo proprietário em 18/10/2026).

Só é aplicado quando o administrador marca "criar etapas padrão" ao cadastrar a empresa.
Os sinônimos ajudam a reconhecer cabeçalhos abreviados nos relatórios (ex.: "Mont.", "Distrib.").
"""
from __future__ import annotations

import json
import sqlite3

from .db import agora

MODELO = [
    ("CORTE", "Corte", "#2563eb", []),
    ("COSTURA", "Costura", "#7c3aed", []),
    ("PALMILHA", "Palmilha", "#db2777", ["Palm"]),
    ("PRE", "Pré", "#ea580c", ["Pre"]),
    ("SOLA", "Sola", "#ca8a04", []),
    ("MONTAGEM", "Montagem", "#059669", ["Mont", "Mont."]),
    ("DISTRIBUICAO", "Distribuição", "#0891b2", ["Distrib", "Distrib."]),
    ("EXPEDICAO", "Expedição", "#475569", ["Exped", "Exped."]),
]


def criar_etapas_padrao(db: sqlite3.Connection, empresa_id: int) -> int:
    momento = agora()
    criadas = 0
    for ordem, (codigo, nome, cor, sinonimos) in enumerate(MODELO, start=1):
        cur = db.execute(
            """INSERT OR IGNORE INTO etapas (empresa_id, codigo, nome, cor, ordem, sinonimos, criado_em, atualizado_em)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (empresa_id, codigo, nome, cor, ordem * 10, json.dumps(sinonimos, ensure_ascii=False), momento, momento),
        )
        criadas += cur.rowcount
    return criadas
