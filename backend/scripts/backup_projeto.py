"""Backup independente do PCP Industrial.

Uso (na raiz do projeto):
    backend\\.venv\\Scripts\\python.exe backend\\scripts\\backup_projeto.py <rotulo>

- Cria backups/<data>_<rotulo>_<hora>/ e recusa sobrescrever uma pasta existente.
- Copia o banco com a API sqlite3.backup, abrindo a origem em modo somente-leitura.
- Valida a cópia (integrity_check) e compara as contagens com a origem.
- Copia o código-fonte (sem node_modules, .venv, backups, __pycache__).
- Grava MANIFESTO.json com SHA-256 de cada arquivo, contagens e esquema.
- Força a gravação em disco (fsync) por causa de quedas de energia.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
BANCO = RAIZ / "backend" / "pcp.db"
IGNORAR_DIRS = {"node_modules", ".venv", "venv", "backups", "__pycache__", ".git", "dist"}
IGNORAR_ARQ = {"pcp.db", "pcp.db-wal", "pcp.db-shm", "pcp.db-journal"}


def sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(65536), b""):
            h.update(bloco)
    return h.hexdigest()


def fsync_arquivo(caminho: Path) -> None:
    with open(caminho, "rb+") as f:
        f.flush()
        os.fsync(f.fileno())


def contagens(conexao: sqlite3.Connection) -> dict[str, int]:
    tabelas = [r[0] for r in conexao.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: conexao.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tabelas}


def main() -> int:
    rotulo = (sys.argv[1] if len(sys.argv) > 1 else "manual").strip().replace(" ", "-")
    agora = datetime.now()
    destino = RAIZ / "backups" / f"{agora:%Y-%m-%d}_{rotulo}_{agora:%H%M%S}"
    if destino.exists():
        print(f"ERRO: {destino} já existe; nada foi sobrescrito.")
        return 1
    destino.mkdir(parents=True)

    # 1) Banco: cópia consistente com a origem aberta somente-leitura.
    origem = sqlite3.connect(f"file:{BANCO.as_posix()}?mode=ro", uri=True)
    copia_path = destino / "pcp.db"
    copia = sqlite3.connect(copia_path)
    origem.backup(copia)
    cont_origem = contagens(origem)
    esquema = [r[0] for r in origem.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]
    user_version = origem.execute("PRAGMA user_version").fetchone()[0]
    dados_reais = {
        t: [dict(zip([d[0] for d in cur.description], row)) for row in cur.fetchall()]
        for t in ("remessas", "producao_diaria", "historico_remessas")
        for cur in [origem.execute(f'SELECT * FROM "{t}" ORDER BY id')]
    }
    origem.close()
    copia.close()
    fsync_arquivo(copia_path)

    verif = sqlite3.connect(f"file:{copia_path.as_posix()}?mode=ro", uri=True)
    integridade = verif.execute("PRAGMA integrity_check").fetchone()[0]
    cont_copia = contagens(verif)
    verif.close()
    if integridade != "ok" or cont_copia != cont_origem:
        print("ERRO: cópia do banco não confere com a origem.", integridade, cont_origem, cont_copia)
        return 2

    # 2) Código-fonte.
    arquivos: dict[str, str] = {}
    for caminho in RAIZ.rglob("*"):
        rel = caminho.relative_to(RAIZ)
        if any(p in IGNORAR_DIRS for p in rel.parts) or not caminho.is_file():
            continue
        if caminho.name in IGNORAR_ARQ or caminho.suffix == ".pyc":
            continue
        alvo = destino / "projeto" / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(caminho, alvo)
        h_origem, h_copia = sha256(caminho), sha256(alvo)
        if h_origem != h_copia:
            print(f"ERRO: hash divergente em {rel}")
            return 3
        arquivos[rel.as_posix()] = h_origem

    manifesto = {
        "criado_em": datetime.now(timezone.utc).isoformat(),
        "rotulo": rotulo,
        "banco": {
            "origem": str(BANCO),
            "sha256_original_no_momento": sha256(BANCO),
            "sha256_copia": sha256(copia_path),
            "integrity_check": integridade,
            "user_version": user_version,
            "contagens": cont_origem,
            "esquema": esquema,
            "dados": dados_reais,
        },
        "arquivos": arquivos,
    }
    man_path = destino / "MANIFESTO.json"
    man_path.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding="utf-8")
    fsync_arquivo(man_path)

    print(f"OK: {destino}")
    print(f"  banco: integrity={integridade} contagens={cont_origem}")
    print(f"  arquivos copiados e conferidos: {len(arquivos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
