"""Migrações do banco do PCP Industrial (controladas por PRAGMA user_version).

Regras de segurança:
- Nada é executado ao importar este módulo. Use `python -m app.cli migrar --banco <arquivo>`.
- Antes de migrar, o CLI cria um backup verificado (sqlite3.backup).
- Cada migração roda numa única transação: se qualquer validação falhar, nada é gravado.
- IDs, datas e valores dos registros existentes são preservados.
- Reversão: restaurar o backup gerado antes da migração (ver docs/MIGRACOES.md).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

VERSAO_ALVO = 1

# Empresa interna que recebe os dados existentes (decisão do proprietário em 18/10/2026:
# "são dados de teste: manter numa área interna de testes só do administrador").
EMPRESA_INTERNA_NOME = "Área interna de testes (dados anteriores ao SaaS)"
FABRICA_LEGADA_CODIGO = "LEGADO"
FABRICA_LEGADA_NOME = "Dados anteriores ao SaaS (sem fábrica informada)"
ETAPAS_LEGADAS = [  # etapas que existiam fixas no sistema anterior, mesma ordem e cores do layout
    ("CORTE", "Corte", "#2563eb", 10),
    ("COSTURA", "Costura", "#7c3aed", 20),
    ("MONTAGEM", "Montagem", "#059669", 30),
]


class ErroMigracao(RuntimeError):
    pass


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _tabelas(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _snapshot_legado(con: sqlite3.Connection) -> dict:
    """Fotografia dos dados existentes, usada para validar a migração."""
    snap = {}
    for tabela in ("remessas", "producao_diaria", "historico_remessas"):
        if tabela in _tabelas(con):
            cur = con.execute(f'SELECT * FROM "{tabela}" ORDER BY id')
            cols = [d[0] for d in cur.description]
            snap[tabela] = [dict(zip(cols, r)) for r in cur.fetchall()]
        else:
            snap[tabela] = []
    return snap


# --------------------------------------------------------------------------------------
# Esquema v1 (multiempresa). As chaves estrangeiras compostas (empresa_id, x_id) garantem,
# no próprio banco, que um registro nunca aponte para dados de outra empresa.
# --------------------------------------------------------------------------------------
ESQUEMA_V1_NOVAS = """
CREATE TABLE planos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    descricao TEXT NOT NULL DEFAULT '',
    preco_mensal_centavos INTEGER CHECK (preco_mensal_centavos IS NULL OR preco_mensal_centavos >= 0),
    limite_usuarios INTEGER CHECK (limite_usuarios IS NULL OR limite_usuarios >= 0),
    limite_fabricas INTEGER CHECK (limite_fabricas IS NULL OR limite_fabricas >= 0),
    funcionalidades TEXT NOT NULL DEFAULT '[]',
    dias_teste INTEGER NOT NULL DEFAULT 0 CHECK (dias_teste >= 0),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE empresas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    documento TEXT NOT NULL DEFAULT '',
    email_contato TEXT NOT NULL DEFAULT '',
    telefone TEXT NOT NULL DEFAULT '',
    observacoes TEXT NOT NULL DEFAULT '',
    tipo TEXT NOT NULL DEFAULT 'cliente' CHECK (tipo IN ('cliente', 'interna')),
    -- Estado do acesso/assinatura controlado pelo administrador geral.
    estado TEXT NOT NULL DEFAULT 'teste'
        CHECK (estado IN ('teste', 'ativa', 'vencida', 'suspensa', 'cancelada')),
    motivo_estado TEXT NOT NULL DEFAULT '',
    plano_id INTEGER REFERENCES planos(id),
    inicio_em TEXT,
    teste_ate TEXT,
    vencimento_em TEXT,
    carencia_dias INTEGER NOT NULL DEFAULT 0 CHECK (carencia_dias >= 0),
    -- Só pode ser preenchido por integração futura com provedor de pagamento (nunca pela interface).
    pagamento_confirmado_em TEXT,
    pagamento_referencia TEXT,
    suporte_autorizado_ate TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL
);

CREATE TABLE fabricas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, codigo),
    UNIQUE (empresa_id, id)
);

CREATE TABLE etapas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    codigo TEXT NOT NULL,
    nome TEXT NOT NULL,
    cor TEXT NOT NULL DEFAULT '#64748b',
    ordem INTEGER NOT NULL DEFAULT 0,
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    sinonimos TEXT NOT NULL DEFAULT '[]',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, codigo),
    UNIQUE (empresa_id, nome),
    UNIQUE (empresa_id, id)
);

-- Fluxo por fábrica: ordem e uso de cada etapa. Sem linhas = usa o catálogo da empresa.
CREATE TABLE fabrica_etapas (
    empresa_id INTEGER NOT NULL,
    fabrica_id INTEGER NOT NULL,
    etapa_id INTEGER NOT NULL,
    ordem INTEGER NOT NULL DEFAULT 0,
    ativa INTEGER NOT NULL DEFAULT 1 CHECK (ativa IN (0, 1)),
    PRIMARY KEY (fabrica_id, etapa_id),
    FOREIGN KEY (empresa_id, fabrica_id) REFERENCES fabricas(empresa_id, id),
    FOREIGN KEY (empresa_id, etapa_id) REFERENCES etapas(empresa_id, id)
);

CREATE TABLE usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER REFERENCES empresas(id),
    nome TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash TEXT,
    papel TEXT NOT NULL CHECK (papel IN ('admin_geral', 'admin_empresa', 'pcp', 'consulta')),
    acesso_todas_fabricas INTEGER NOT NULL DEFAULT 1 CHECK (acesso_todas_fabricas IN (0, 1)),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    falhas_login INTEGER NOT NULL DEFAULT 0,
    bloqueado_ate TEXT,
    ultimo_login_em TEXT,
    ultima_atividade_em TEXT,
    senha_alterada_em TEXT,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, id),
    -- O administrador geral nunca pertence a uma empresa; os demais sempre pertencem.
    CHECK ((papel = 'admin_geral' AND empresa_id IS NULL) OR (papel <> 'admin_geral' AND empresa_id IS NOT NULL))
);

CREATE TABLE usuario_fabricas (
    empresa_id INTEGER NOT NULL,
    usuario_id INTEGER NOT NULL,
    fabrica_id INTEGER NOT NULL,
    PRIMARY KEY (usuario_id, fabrica_id),
    FOREIGN KEY (empresa_id, usuario_id) REFERENCES usuarios(empresa_id, id),
    FOREIGN KEY (empresa_id, fabrica_id) REFERENCES fabricas(empresa_id, id)
);

CREATE TABLE sessoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    token_hash TEXT NOT NULL UNIQUE,
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    ultimo_uso_em TEXT,
    revogada_em TEXT,
    motivo_revogacao TEXT,
    ip TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    -- Contexto operacional do administrador geral (área interna ou suporte assistido).
    contexto_empresa_id INTEGER REFERENCES empresas(id),
    suporte_motivo TEXT,
    suporte_expira_em TEXT
);
CREATE INDEX idx_sessoes_usuario ON sessoes(usuario_id);

CREATE TABLE convites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    email TEXT NOT NULL COLLATE NOCASE,
    nome TEXT NOT NULL,
    papel TEXT NOT NULL CHECK (papel IN ('admin_empresa', 'pcp', 'consulta')),
    acesso_todas_fabricas INTEGER NOT NULL DEFAULT 1,
    fabricas TEXT NOT NULL DEFAULT '[]',
    token_hash TEXT NOT NULL UNIQUE,
    criado_por INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    aceito_em TEXT,
    revogado_em TEXT,
    usuario_id INTEGER REFERENCES usuarios(id),
    email_enviado INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_convites_empresa ON convites(empresa_id);

CREATE TABLE redefinicoes_senha (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
    token_hash TEXT NOT NULL UNIQUE,
    criado_por INTEGER REFERENCES usuarios(id),
    criado_em TEXT NOT NULL,
    expira_em TEXT NOT NULL,
    usado_em TEXT,
    revogado_em TEXT
);

-- Tentativas de login: guarda apenas o hash do e-mail (sem dados pessoais em claro).
CREATE TABLE tentativas_login (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_hash TEXT NOT NULL,
    ip TEXT NOT NULL DEFAULT '',
    sucesso INTEGER NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX idx_tentativas_ip ON tentativas_login(ip, criado_em);

CREATE TABLE ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    remessa_id INTEGER NOT NULL,
    numero TEXT NOT NULL,
    pedido TEXT NOT NULL DEFAULT '',
    material TEXT NOT NULL DEFAULT '',
    quantidade_prevista INTEGER CHECK (quantidade_prevista IS NULL OR quantidade_prevista >= 0),
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'importacao')),
    importacao_id INTEGER,
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, remessa_id, numero),
    UNIQUE (empresa_id, id),
    FOREIGN KEY (empresa_id, remessa_id) REFERENCES remessas(empresa_id, id)
);
CREATE INDEX idx_ops_busca ON ops(empresa_id, numero);
CREATE INDEX idx_ops_pedido ON ops(empresa_id, pedido);

CREATE TABLE importacoes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    usuario_id INTEGER REFERENCES usuarios(id),
    nome_arquivo TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    tamanho_bytes INTEGER NOT NULL,
    formato TEXT NOT NULL,
    aba TEXT NOT NULL DEFAULT '',
    mapeamento TEXT NOT NULL DEFAULT '{}',
    estado TEXT NOT NULL DEFAULT 'previa'
        CHECK (estado IN ('previa', 'confirmada', 'cancelada', 'falhou')),
    resumo TEXT NOT NULL DEFAULT '{}',
    erro TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL,
    confirmado_em TEXT,
    confirmado_por INTEGER REFERENCES usuarios(id),
    UNIQUE (empresa_id, id)
);
CREATE INDEX idx_importacoes_sha ON importacoes(empresa_id, sha256);

CREATE TABLE importacao_itens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL,
    importacao_id INTEGER NOT NULL,
    linha INTEGER,
    tipo TEXT NOT NULL,
    acao TEXT NOT NULL,
    entidade TEXT NOT NULL DEFAULT '',
    entidade_id INTEGER,
    mensagem TEXT NOT NULL DEFAULT '',
    dados TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (empresa_id, importacao_id) REFERENCES importacoes(empresa_id, id)
);
CREATE INDEX idx_importacao_itens ON importacao_itens(importacao_id);

-- Leituras importadas dos relatórios. chave_origem é determinística: a reimportação
-- do mesmo evento encontra a mesma chave e não duplica a produção.
CREATE TABLE leituras (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    remessa_id INTEGER NOT NULL,
    op_id INTEGER,
    etapa_id INTEGER NOT NULL,
    data_leitura TEXT,
    quantidade INTEGER CHECK (quantidade IS NULL OR quantidade >= 0),
    tipo_valor TEXT NOT NULL DEFAULT 'movimento' CHECK (tipo_valor IN ('movimento', 'acumulado')),
    chave_origem TEXT NOT NULL,
    id_origem TEXT NOT NULL DEFAULT '',
    importacao_id INTEGER NOT NULL,
    ultima_importacao_id INTEGER NOT NULL,
    linha_origem INTEGER,
    situacao TEXT NOT NULL DEFAULT 'ok' CHECK (situacao IN ('ok', 'conferir')),
    observacao TEXT NOT NULL DEFAULT '',
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, chave_origem),
    FOREIGN KEY (empresa_id, remessa_id) REFERENCES remessas(empresa_id, id),
    FOREIGN KEY (empresa_id, op_id) REFERENCES ops(empresa_id, id),
    FOREIGN KEY (empresa_id, etapa_id) REFERENCES etapas(empresa_id, id),
    FOREIGN KEY (empresa_id, importacao_id) REFERENCES importacoes(empresa_id, id)
);
CREATE INDEX idx_leituras_op ON leituras(empresa_id, op_id, etapa_id);
CREATE INDEX idx_leituras_remessa ON leituras(empresa_id, remessa_id, etapa_id);

-- Auditoria append-only (sem senhas, tokens ou segredos).
CREATE TABLE auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    criado_em TEXT NOT NULL,
    usuario_id INTEGER,
    usuario_nome TEXT NOT NULL DEFAULT '',
    papel TEXT NOT NULL DEFAULT '',
    empresa_id INTEGER,
    fabrica_id INTEGER,
    acao TEXT NOT NULL,
    entidade TEXT NOT NULL DEFAULT '',
    entidade_id TEXT NOT NULL DEFAULT '',
    origem TEXT NOT NULL DEFAULT 'manual',
    importacao_id INTEGER,
    suporte INTEGER NOT NULL DEFAULT 0,
    antes TEXT,
    depois TEXT,
    detalhes TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_auditoria_empresa ON auditoria(empresa_id, criado_em);
CREATE TRIGGER auditoria_sem_update BEFORE UPDATE ON auditoria
BEGIN SELECT RAISE(ABORT, 'auditoria é somente inclusão'); END;
CREATE TRIGGER auditoria_sem_delete BEFORE DELETE ON auditoria
BEGIN SELECT RAISE(ABORT, 'auditoria é somente inclusão'); END;
"""

# Tabelas existentes reconstruídas (o SQLite não permite alterar UNIQUE/CHECK/FK no lugar).
ESQUEMA_V1_RECONSTRUIDAS = """
CREATE TABLE remessas_v1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    fabrica_id INTEGER NOT NULL,
    numero TEXT NOT NULL,
    tipo_remessa TEXT NOT NULL DEFAULT '',
    descricao TEXT NOT NULL DEFAULT '',
    data_entrega TEXT,
    quantidade_pares INTEGER NOT NULL CHECK (quantidade_pares >= 0),
    status TEXT NOT NULL DEFAULT 'A programar',
    origem TEXT NOT NULL DEFAULT 'manual' CHECK (origem IN ('manual', 'importacao')),
    criado_em TEXT NOT NULL,
    atualizado_em TEXT NOT NULL,
    UNIQUE (empresa_id, fabrica_id, tipo_remessa, numero),
    UNIQUE (empresa_id, id),
    FOREIGN KEY (empresa_id, fabrica_id) REFERENCES fabricas(empresa_id, id)
);

CREATE TABLE producao_diaria_v1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    remessa_id INTEGER NOT NULL,
    op_id INTEGER,
    etapa_id INTEGER NOT NULL,
    etapa TEXT NOT NULL,
    data_producao TEXT NOT NULL,
    quantidade_pares INTEGER NOT NULL CHECK (quantidade_pares > 0),
    observacao TEXT NOT NULL DEFAULT '',
    registrado_em TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id),
    estornado_em TEXT,
    estornado_por INTEGER REFERENCES usuarios(id),
    motivo_estorno TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (empresa_id, remessa_id) REFERENCES remessas(empresa_id, id),
    FOREIGN KEY (empresa_id, etapa_id) REFERENCES etapas(empresa_id, id),
    FOREIGN KEY (empresa_id, op_id) REFERENCES ops(empresa_id, id)
);

CREATE TABLE historico_remessas_v1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    remessa_id INTEGER NOT NULL,
    acao TEXT NOT NULL,
    detalhes TEXT NOT NULL,
    registrado_em TEXT NOT NULL,
    usuario_id INTEGER REFERENCES usuarios(id),
    FOREIGN KEY (empresa_id, remessa_id) REFERENCES remessas(empresa_id, id)
);
"""

POS_V1 = """
CREATE INDEX idx_remessas_fabrica ON remessas(empresa_id, fabrica_id, status);
CREATE INDEX idx_producao_remessa_data ON producao_diaria(remessa_id, data_producao);
CREATE INDEX idx_producao_empresa ON producao_diaria(empresa_id, etapa_id, data_producao);
CREATE INDEX idx_historico_remessa ON historico_remessas(empresa_id, remessa_id);
CREATE TRIGGER historico_sem_update BEFORE UPDATE ON historico_remessas
BEGIN SELECT RAISE(ABORT, 'histórico é somente inclusão'); END;
CREATE TRIGGER historico_sem_delete BEFORE DELETE ON historico_remessas
BEGIN SELECT RAISE(ABORT, 'histórico é somente inclusão'); END;
-- Lançamentos de produção nunca são apagados; correções usam estorno lógico.
CREATE TRIGGER producao_sem_delete BEFORE DELETE ON producao_diaria
BEGIN SELECT RAISE(ABORT, 'lançamentos de produção não podem ser apagados; use estorno'); END;
CREATE TRIGGER producao_valores_imutaveis BEFORE UPDATE OF
    empresa_id, remessa_id, etapa_id, etapa, data_producao, quantidade_pares, registrado_em ON producao_diaria
BEGIN SELECT RAISE(ABORT, 'valores de lançamentos de produção são imutáveis; use estorno'); END;
"""


def _criar_esquema_vazio_legado(con: sqlite3.Connection) -> None:
    """Esquema anterior ao SaaS (idêntico ao backend/main.py), para bancos novos de teste."""
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS sistema_info (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, versao TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS remessas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, numero TEXT NOT NULL UNIQUE,
            descricao TEXT NOT NULL DEFAULT '', data_entrega TEXT,
            quantidade_pares INTEGER NOT NULL CHECK (quantidade_pares >= 0),
            status TEXT NOT NULL DEFAULT 'A programar', criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS historico_remessas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, remessa_id INTEGER NOT NULL, acao TEXT NOT NULL,
            detalhes TEXT NOT NULL, registrado_em TEXT NOT NULL, FOREIGN KEY (remessa_id) REFERENCES remessas(id));
        CREATE TABLE IF NOT EXISTS producao_diaria (
            id INTEGER PRIMARY KEY AUTOINCREMENT, remessa_id INTEGER NOT NULL,
            etapa TEXT NOT NULL CHECK (etapa IN ('Corte', 'Costura', 'Montagem')),
            data_producao TEXT NOT NULL, quantidade_pares INTEGER NOT NULL CHECK (quantidade_pares > 0),
            observacao TEXT NOT NULL DEFAULT '', registrado_em TEXT NOT NULL,
            FOREIGN KEY (remessa_id) REFERENCES remessas(id));
        """
    )


def migrar_para_v1(con: sqlite3.Connection) -> dict:
    """Converte o banco legado para o esquema multiempresa v1 (uma única transação)."""
    if con.execute("PRAGMA user_version").fetchone()[0] != 0:
        raise ErroMigracao("O banco não está na versão 0.")
    tabelas = _tabelas(con)
    if "empresas" in tabelas:
        raise ErroMigracao("Tabela 'empresas' já existe em um banco versão 0; verifique manualmente.")
    if "remessas" not in tabelas:
        _criar_esquema_vazio_legado(con)

    antes = _snapshot_legado(con)
    momento = _agora()

    con.isolation_level = None  # transação controlada manualmente abaixo
    con.execute("PRAGMA foreign_keys = OFF")  # exigido para reconstruir tabelas
    con.execute("BEGIN IMMEDIATE")
    try:
        for comando in _dividir(ESQUEMA_V1_NOVAS):
            con.execute(comando)

        empresa_id = None
        fabrica_id = None
        etapas_por_nome: dict[str, int] = {}
        existe_legado = bool(antes["remessas"] or antes["producao_diaria"] or antes["historico_remessas"])
        if existe_legado:
            cur = con.execute(
                """INSERT INTO empresas (nome, tipo, estado, motivo_estado, observacoes, inicio_em, criado_em, atualizado_em)
                   VALUES (?, 'interna', 'ativa', '', ?, ?, ?, ?)""",
                (EMPRESA_INTERNA_NOME,
                 "Criada pela migração v1 para guardar os dados de teste existentes antes do SaaS. "
                 "Acessível somente pelo administrador geral.", momento, momento, momento),
            )
            empresa_id = cur.lastrowid
            cur = con.execute(
                "INSERT INTO fabricas (empresa_id, codigo, nome, criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?)",
                (empresa_id, FABRICA_LEGADA_CODIGO, FABRICA_LEGADA_NOME, momento, momento),
            )
            fabrica_id = cur.lastrowid
            for codigo, nome, cor, ordem in ETAPAS_LEGADAS:
                cur = con.execute(
                    """INSERT INTO etapas (empresa_id, codigo, nome, cor, ordem, criado_em, atualizado_em)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (empresa_id, codigo, nome, cor, ordem, momento, momento),
                )
                etapas_por_nome[nome] = cur.lastrowid

        for comando in _dividir(ESQUEMA_V1_RECONSTRUIDAS):
            con.execute(comando)

        for r in antes["remessas"]:
            con.execute(
                """INSERT INTO remessas_v1 (id, empresa_id, fabrica_id, numero, tipo_remessa, descricao, data_entrega,
                       quantidade_pares, status, origem, criado_em, atualizado_em)
                   VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, 'manual', ?, ?)""",
                (r["id"], empresa_id, fabrica_id, r["numero"], r["descricao"], r["data_entrega"],
                 r["quantidade_pares"], r["status"], r["criado_em"], r["atualizado_em"]),
            )
        for p in antes["producao_diaria"]:
            if p["etapa"] not in etapas_por_nome:
                raise ErroMigracao(f"Etapa desconhecida no lançamento {p['id']}: {p['etapa']!r}")
            con.execute(
                """INSERT INTO producao_diaria_v1 (id, empresa_id, remessa_id, op_id, etapa_id, etapa, data_producao,
                       quantidade_pares, observacao, registrado_em, usuario_id)
                   VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL)""",
                (p["id"], empresa_id, p["remessa_id"], etapas_por_nome[p["etapa"]], p["etapa"],
                 p["data_producao"], p["quantidade_pares"], p["observacao"], p["registrado_em"]),
            )
        for h in antes["historico_remessas"]:
            con.execute(
                """INSERT INTO historico_remessas_v1 (id, empresa_id, remessa_id, acao, detalhes, registrado_em, usuario_id)
                   VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                (h["id"], empresa_id, h["remessa_id"], h["acao"], h["detalhes"], h["registrado_em"]),
            )

        for tabela in ("producao_diaria", "historico_remessas", "remessas"):
            con.execute(f'DROP TABLE "{tabela}"')
        con.execute("DROP INDEX IF EXISTS idx_producao_remessa_data")
        for tabela in ("remessas", "producao_diaria", "historico_remessas"):
            con.execute(f'ALTER TABLE "{tabela}_v1" RENAME TO "{tabela}"')
        for comando in _dividir(POS_V1):
            con.execute(comando)

        if existe_legado:
            con.execute(
                """INSERT INTO auditoria (criado_em, acao, entidade, entidade_id, origem, empresa_id, detalhes, depois)
                   VALUES (?, 'MIGRACAO_V1', 'banco', '1', 'sistema', ?, ?, ?)""",
                (momento, empresa_id,
                 "Dados existentes associados à área interna de testes (decisão do proprietário em 18/10/2026).",
                 json.dumps({"remessas": len(antes["remessas"]), "producao_diaria": len(antes["producao_diaria"]),
                             "historico_remessas": len(antes["historico_remessas"])})),
            )

        _validar_v1(con, antes, empresa_id)
        con.execute("PRAGMA user_version = 1")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.execute("PRAGMA foreign_keys = ON")

    problemas = con.execute("PRAGMA foreign_key_check").fetchall()
    if problemas:
        raise ErroMigracao(f"foreign_key_check falhou após a migração: {problemas}")
    return {"empresa_interna_id": empresa_id, "fabrica_legada_id": fabrica_id,
            "contagens": {k: len(v) for k, v in antes.items()}}


def _validar_v1(con: sqlite3.Connection, antes: dict, empresa_id: int | None) -> None:
    """Compara campo a campo os dados legados com os dados migrados."""
    depois = _snapshot_legado(con)
    for tabela, linhas_antes in antes.items():
        linhas_depois = depois[tabela]
        if len(linhas_antes) != len(linhas_depois):
            raise ErroMigracao(f"{tabela}: {len(linhas_antes)} registros antes, {len(linhas_depois)} depois")
        for a, d in zip(linhas_antes, linhas_depois):
            for campo, valor in a.items():
                if d.get(campo) != valor:
                    raise ErroMigracao(f"{tabela} id={a['id']}: campo {campo} mudou ({valor!r} → {d.get(campo)!r})")
            if d["empresa_id"] != empresa_id:
                raise ErroMigracao(f"{tabela} id={a['id']}: empresa_id inesperado")
    problemas = con.execute("PRAGMA foreign_key_check").fetchall()
    if problemas:
        raise ErroMigracao(f"foreign_key_check: {problemas}")


def _dividir(script: str) -> list[str]:
    """Divide um script SQL em comandos, respeitando blocos BEGIN...END de triggers."""
    comandos, atual, em_trigger = [], [], False
    for linha in script.splitlines():
        limpa = linha.strip()
        if not limpa or limpa.startswith("--"):
            continue
        atual.append(linha)
        if limpa.upper().startswith("CREATE TRIGGER"):
            em_trigger = True
        if em_trigger:
            if limpa.upper().endswith("END;"):
                comandos.append("\n".join(atual).rstrip().rstrip(";"))
                atual, em_trigger = [], False
        elif limpa.endswith(";"):
            comandos.append("\n".join(atual).rstrip().rstrip(";"))
            atual = []
    if atual:
        comandos.append("\n".join(atual))
    return comandos


MIGRACOES = {1: migrar_para_v1}


def migrar(con: sqlite3.Connection) -> list[dict]:
    """Aplica todas as migrações pendentes, em ordem."""
    resultados = []
    while True:
        versao = con.execute("PRAGMA user_version").fetchone()[0]
        if versao >= VERSAO_ALVO:
            return resultados
        func = MIGRACOES.get(versao + 1)
        if func is None:
            raise ErroMigracao(f"Migração {versao + 1} não encontrada")
        resultados.append({"versao": versao + 1, **func(con)})