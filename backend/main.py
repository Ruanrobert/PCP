from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "pcp.db"

app = FastAPI(title="PCP Industrial API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def conectar_banco():
    """Abre e fecha uma conexão com o banco SQLite."""
    conexao = sqlite3.connect(DB_PATH)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        yield conexao
        conexao.commit()
    except Exception:
        conexao.rollback()
        raise
    finally:
        conexao.close()


def agora():
    return datetime.now(timezone.utc).isoformat()


def inicializar_banco():
    with conectar_banco() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sistema_info (
                id INTEGER PRIMARY KEY,
                nome TEXT NOT NULL,
                versao TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS remessas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                numero TEXT NOT NULL UNIQUE,
                descricao TEXT NOT NULL DEFAULT '',
                data_entrega TEXT,
                quantidade_pares INTEGER NOT NULL CHECK (
                    quantidade_pares >= 0
                ),
                status TEXT NOT NULL DEFAULT 'A programar',
                criado_em TEXT NOT NULL,
                atualizado_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS historico_remessas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remessa_id INTEGER NOT NULL,
                acao TEXT NOT NULL,
                detalhes TEXT NOT NULL,
                registrado_em TEXT NOT NULL,
                FOREIGN KEY (remessa_id)
                    REFERENCES remessas(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS producao_diaria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                remessa_id INTEGER NOT NULL,
                etapa TEXT NOT NULL CHECK (etapa IN ('Corte', 'Costura', 'Montagem')),
                data_producao TEXT NOT NULL,
                quantidade_pares INTEGER NOT NULL CHECK (quantidade_pares > 0),
                observacao TEXT NOT NULL DEFAULT '',
                registrado_em TEXT NOT NULL,
                FOREIGN KEY (remessa_id) REFERENCES remessas(id)
            );
            CREATE INDEX IF NOT EXISTS idx_producao_remessa_data
                ON producao_diaria(remessa_id, data_producao);
        """)

        db.execute(
            """
            INSERT OR IGNORE INTO sistema_info (id, nome, versao)
            VALUES (1, ?, ?)
            """,
            ("PCP Industrial", app.version),
        )


class NovaRemessa(BaseModel):
    numero: str = Field(min_length=1, max_length=50)
    descricao: str = Field(min_length=1, max_length=200)
    data_entrega: str | None = None
    quantidade_pares: int = Field(ge=0)
    status: str = Field(default="A programar", min_length=1, max_length=50)


class AtualizarRemessa(BaseModel):
    numero: str | None = Field(default=None, min_length=1, max_length=50)
    descricao: str | None = Field(default=None, min_length=1, max_length=200)
    data_entrega: str | None = None
    quantidade_pares: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, min_length=1, max_length=50)


class NovaProducao(BaseModel):
    remessa_id: int = Field(gt=0)
    etapa: str = Field(pattern=r"^(Corte|Costura|Montagem)$")
    data_producao: str = Field(min_length=10, max_length=10)
    quantidade_pares: int = Field(gt=0)
    observacao: str = Field(default="", max_length=500)


@app.get("/")
def inicio():
    return {"sistema": "PCP Industrial API", "versao": app.version}


@app.get("/api/health")
def health():
    with conectar_banco() as db:
        db.execute("SELECT 1")

    return {"api": "online", "banco_de_dados": "conectado"}


@app.get("/api/info")
def informacoes():
    return {
        "sistema": "PCP Industrial",
        "versao": app.version,
        "banco": "SQLite",
    }


@app.get("/api/remessas")
def listar_remessas():
    with conectar_banco() as db:
        remessas = db.execute(
            "SELECT * FROM remessas ORDER BY id DESC"
        ).fetchall()

    return [dict(remessa) for remessa in remessas]


@app.post("/api/remessas", status_code=201)
def criar_remessa(remessa: NovaRemessa):
    numero = remessa.numero.strip()
    descricao = remessa.descricao.strip()
    status = remessa.status.strip()

    if not numero or not descricao:
        raise HTTPException(
            status_code=422,
            detail="Número e descrição são obrigatórios.",
        )

    try:
        with conectar_banco() as db:
            momento_atual = agora()

            cursor = db.execute(
                """
                INSERT INTO remessas (
                    numero, descricao, data_entrega,
                    quantidade_pares, status, criado_em, atualizado_em
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    numero,
                    descricao,
                    remessa.data_entrega,
                    remessa.quantidade_pares,
                    status,
                    momento_atual,
                    momento_atual,
                ),
            )

            remessa_id = cursor.lastrowid

            db.execute(
                """
                INSERT INTO historico_remessas (
                    remessa_id, acao, detalhes, registrado_em
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    remessa_id,
                    "CRIACAO",
                    f"Remessa {numero} cadastrada",
                    momento_atual,
                ),
            )

            registro = db.execute(
                "SELECT * FROM remessas WHERE id = ?",
                (remessa_id,),
            ).fetchone()

        return dict(registro)

    except sqlite3.IntegrityError as erro:
        if "UNIQUE constraint failed: remessas.numero" in str(erro):
            raise HTTPException(
                status_code=409,
                detail="Já existe uma remessa com esse número.",
            ) from erro
        raise


@app.put("/api/remessas/{remessa_id}")
def atualizar_remessa(remessa_id: int, remessa: AtualizarRemessa):
    campos_enviados = remessa.model_fields_set

    try:
        with conectar_banco() as db:
            atual = db.execute(
                "SELECT * FROM remessas WHERE id = ?",
                (remessa_id,),
            ).fetchone()

            if atual is None:
                raise HTTPException(
                    status_code=404,
                    detail="Remessa não encontrada.",
                )

            numero = (
                remessa.numero.strip()
                if "numero" in campos_enviados and remessa.numero is not None
                else atual["numero"]
            )
            descricao = (
                remessa.descricao.strip()
                if "descricao" in campos_enviados and remessa.descricao is not None
                else atual["descricao"]
            )
            data_entrega = (
                remessa.data_entrega
                if "data_entrega" in campos_enviados
                else atual["data_entrega"]
            )
            quantidade_pares = (
                remessa.quantidade_pares
                if "quantidade_pares" in campos_enviados
                and remessa.quantidade_pares is not None
                else atual["quantidade_pares"]
            )
            status = (
                remessa.status.strip()
                if "status" in campos_enviados and remessa.status is not None
                else atual["status"]
            )

            if not numero or not descricao or not status:
                raise HTTPException(
                    status_code=422,
                    detail="Número, descrição e status são obrigatórios.",
                )

            momento_atual = agora()

            db.execute(
                """
                UPDATE remessas
                SET numero = ?,
                    descricao = ?,
                    data_entrega = ?,
                    quantidade_pares = ?,
                    status = ?,
                    atualizado_em = ?
                WHERE id = ?
                """,
                (
                    numero,
                    descricao,
                    data_entrega,
                    quantidade_pares,
                    status,
                    momento_atual,
                    remessa_id,
                ),
            )

            db.execute(
                """
                INSERT INTO historico_remessas (
                    remessa_id, acao, detalhes, registrado_em
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    remessa_id,
                    "EDICAO",
                    f"Remessa {numero} atualizada (status: {status}, pares: {quantidade_pares})",
                    momento_atual,
                ),
            )

            registro = db.execute(
                "SELECT * FROM remessas WHERE id = ?",
                (remessa_id,),
            ).fetchone()

        return dict(registro)

    except sqlite3.IntegrityError as erro:
        if "UNIQUE constraint failed: remessas.numero" in str(erro):
            raise HTTPException(
                status_code=409,
                detail="Já existe uma remessa com esse número.",
            ) from erro
        raise


@app.delete("/api/remessas/{remessa_id}")
def excluir_remessa(remessa_id: int):
    with conectar_banco() as db:
        atual = db.execute(
            "SELECT * FROM remessas WHERE id = ?",
            (remessa_id,),
        ).fetchone()

        if atual is None:
            raise HTTPException(
                status_code=404,
                detail="Remessa não encontrada.",
            )

        momento_atual = agora()

        db.execute(
            """
            UPDATE remessas
            SET status = 'Excluída',
                atualizado_em = ?
            WHERE id = ?
            """,
            (momento_atual, remessa_id),
        )

        db.execute(
            """
            INSERT INTO historico_remessas (
                remessa_id, acao, detalhes, registrado_em
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                remessa_id,
                "EXCLUSAO",
                f"Remessa {atual['numero']} marcada como excluída",
                momento_atual,
            ),
        )

        registro = db.execute(
            "SELECT * FROM remessas WHERE id = ?",
            (remessa_id,),
        ).fetchone()

    return dict(registro)


@app.get("/api/remessas/{remessa_id}/historico")
def historico_remessa(remessa_id: int):
    with conectar_banco() as db:
        existe = db.execute(
            "SELECT id FROM remessas WHERE id = ?",
            (remessa_id,),
        ).fetchone()

        if existe is None:
            raise HTTPException(
                status_code=404,
                detail="Remessa não encontrada.",
            )

        historico = db.execute(
            """
            SELECT * FROM historico_remessas
            WHERE remessa_id = ?
            ORDER BY id DESC
            """,
            (remessa_id,),
        ).fetchall()

    return [dict(item) for item in historico]


inicializar_banco()

@app.get("/api/producao")
def listar_producao(remessa_id: int | None = None, data_producao: str | None = None, etapa: str | None = None):
    filtros = []
    parametros = []
    if remessa_id is not None:
        filtros.append("p.remessa_id = ?")
        parametros.append(remessa_id)
    if data_producao:
        filtros.append("p.data_producao = ?")
        parametros.append(data_producao)
    if etapa:
        if etapa not in ("Corte", "Costura", "Montagem"):
            raise HTTPException(status_code=422, detail="Etapa inválida.")
        filtros.append("p.etapa = ?")
        parametros.append(etapa)
    where = (" WHERE " + " AND ".join(filtros)) if filtros else ""
    with conectar_banco() as db:
        rows = db.execute(
            """SELECT p.*, r.numero AS remessa_numero, r.descricao AS remessa_descricao,
                      r.quantidade_pares AS total_pares_remessa, r.status AS status_remessa
               FROM producao_diaria p JOIN remessas r ON r.id = p.remessa_id"""
            + where + " ORDER BY p.data_producao DESC, p.id DESC",
            parametros,
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/producao/resumo")
def resumo_producao():
    with conectar_banco() as db:
        rows = db.execute(
            """SELECT r.id AS remessa_id, r.numero, r.descricao, r.quantidade_pares,
                      COALESCE(SUM(CASE WHEN p.etapa = 'Corte' THEN p.quantidade_pares ELSE 0 END), 0) AS corte,
                      COALESCE(SUM(CASE WHEN p.etapa = 'Costura' THEN p.quantidade_pares ELSE 0 END), 0) AS costura,
                      COALESCE(SUM(CASE WHEN p.etapa = 'Montagem' THEN p.quantidade_pares ELSE 0 END), 0) AS montagem
               FROM remessas r LEFT JOIN producao_diaria p ON p.remessa_id = r.id
               WHERE r.status != 'Excluída'
               GROUP BY r.id ORDER BY r.id DESC"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.post("/api/producao", status_code=201)
def registrar_producao(registro: NovaProducao):
    try:
        datetime.strptime(registro.data_producao, "%Y-%m-%d")
    except ValueError as erro:
        raise HTTPException(status_code=422, detail="Data inválida. Use AAAA-MM-DD.") from erro
    with conectar_banco() as db:
        remessa = db.execute(
            "SELECT * FROM remessas WHERE id = ?", (registro.remessa_id,)
        ).fetchone()
        if remessa is None:
            raise HTTPException(status_code=404, detail="Remessa não encontrada.")
        if remessa["status"] == "Excluída":
            raise HTTPException(status_code=409, detail="Não é possível registrar produção em remessa excluída.")
        momento = agora()
        cursor = db.execute(
            """INSERT INTO producao_diaria
               (remessa_id, etapa, data_producao, quantidade_pares, observacao, registrado_em)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (registro.remessa_id, registro.etapa, registro.data_producao,
             registro.quantidade_pares, registro.observacao.strip(), momento),
        )
        db.execute(
            """INSERT INTO historico_remessas (remessa_id, acao, detalhes, registrado_em)
               VALUES (?, ?, ?, ?)""",
            (registro.remessa_id, "PRODUCAO", f"{registro.etapa}: {registro.quantidade_pares} pares em {registro.data_producao}", momento),
        )
        row = db.execute(
            """SELECT p.*, r.numero AS remessa_numero, r.descricao AS remessa_descricao
               FROM producao_diaria p JOIN remessas r ON r.id = p.remessa_id WHERE p.id = ?""",
            (cursor.lastrowid,),
        ).fetchone()
    return dict(row)


@app.delete("/api/producao/{producao_id}")
def excluir_registro_producao(producao_id: int):
    with conectar_banco() as db:
        row = db.execute("SELECT * FROM producao_diaria WHERE id = ?", (producao_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Registro de produção não encontrado.")
        db.execute("DELETE FROM producao_diaria WHERE id = ?", (producao_id,))
        db.execute(
            """INSERT INTO historico_remessas (remessa_id, acao, detalhes, registrado_em)
               VALUES (?, ?, ?, ?)""",
            (row["remessa_id"], "EXCLUSAO_PRODUCAO", f"Registro de produção #{producao_id} removido ({row['etapa']}, {row['quantidade_pares']} pares)", agora()),
        )
    return {"ok": True, "id": producao_id}
