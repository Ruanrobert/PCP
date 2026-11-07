"""Comandos administrativos locais do PCP Industrial.

  python -m app.cli status   [--banco CAMINHO]
  python -m app.cli migrar   [--banco CAMINHO]     (faz backup verificado antes)
  python -m app.cli criar-admin --email EMAIL --nome NOME [--banco CAMINHO]   (senha digitada no terminal)

O administrador geral só pode ser criado por este comando local — nenhuma rota da API concede esse papel.
"""
from __future__ import annotations

import argparse
import getpass
import hashlib
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from . import config as config_mod
from .db import agora
from .migrations import VERSAO_ALVO, migrar
from .security import hash_senha, validar_politica_senha


def _banco(caminho: str | None) -> Path:
    p = Path(caminho) if caminho else config_mod.config.db_path
    if not p.exists():
        sys.exit(f"Banco não encontrado: {p}")
    return p


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _contagens(con: sqlite3.Connection) -> dict:
    return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def backup_verificado(origem: Path, rotulo: str) -> Path:
    destino_dir = origem.parent / "backups_banco"
    destino_dir.mkdir(exist_ok=True)
    destino = destino_dir / f"{origem.stem}_{rotulo}_{datetime.now():%Y%m%d_%H%M%S}.db"
    if destino.exists():
        sys.exit(f"Backup já existe: {destino}")
    src = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(destino)
    src.backup(dst)
    cont_src = _contagens(src)
    src.close()
    dst.close()
    chk = sqlite3.connect(f"file:{destino.as_posix()}?mode=ro", uri=True)
    ok = chk.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and _contagens(chk) == cont_src
    chk.close()
    if not ok:
        sys.exit("Backup não confere com a origem; migração cancelada.")
    return destino


def cmd_status(args) -> None:
    p = _banco(args.banco)
    con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    print(f"Banco: {p}\nSHA-256: {_sha256(p)}\nuser_version: {con.execute('PRAGMA user_version').fetchone()[0]} "
          f"(esperado {VERSAO_ALVO})\nintegrity_check: {con.execute('PRAGMA integrity_check').fetchone()[0]}")
    for t, n in _contagens(con).items():
        print(f"  {t}: {n}")
    con.close()


def cmd_migrar(args) -> None:
    p = _banco(args.banco)
    con = sqlite3.connect(p)
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v >= VERSAO_ALVO:
        print(f"Nada a fazer: banco já está na versão {v}.")
        return
    con.close()
    copia = backup_verificado(p, f"antes_v{VERSAO_ALVO}")
    print(f"Backup verificado: {copia}  (SHA-256 {_sha256(copia)})")
    con = sqlite3.connect(p)
    try:
        resultado = migrar(con)
    except Exception as erro:
        con.close()
        sys.exit(f"MIGRAÇÃO CANCELADA (nada foi gravado): {erro}")
    print("Migração concluída:", resultado)
    print("integrity_check:", con.execute("PRAGMA integrity_check").fetchone()[0])
    con.close()


def cmd_criar_admin(args) -> None:
    p = _banco(args.banco)
    email = args.email.strip().lower()
    senha = getpass.getpass("Senha do administrador geral: ")
    if getpass.getpass("Repita a senha: ") != senha:
        sys.exit("As senhas não conferem.")
    erro = validar_politica_senha(senha, email)
    if erro:
        sys.exit(erro)
    con = sqlite3.connect(p)
    con.execute("PRAGMA foreign_keys = ON")
    if con.execute("PRAGMA user_version").fetchone()[0] != VERSAO_ALVO:
        sys.exit("Migre o banco antes de criar o administrador.")
    if con.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,)).fetchone():
        sys.exit("Já existe um usuário com este e-mail.")
    momento = agora()
    with con:
        cur = con.execute(
            """INSERT INTO usuarios (empresa_id, nome, email, senha_hash, papel, acesso_todas_fabricas, ativo,
                   senha_alterada_em, criado_em, atualizado_em) VALUES (NULL, ?, ?, ?, 'admin_geral', 1, 1, ?, ?, ?)""",
            (args.nome.strip(), email, hash_senha(senha), momento, momento, momento))
        con.execute("""INSERT INTO auditoria (criado_em, usuario_id, usuario_nome, papel, acao, entidade, entidade_id,
                       origem, detalhes) VALUES (?, ?, ?, 'admin_geral', 'ADMIN_GERAL_CRIADO', 'usuario', ?, 'sistema',
                       'Criado pelo comando local app.cli')""", (momento, cur.lastrowid, args.nome.strip(), cur.lastrowid))
    con.close()
    print(f"Administrador geral criado: {email}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="comando", required=True)
    for nome in ("status", "migrar"):
        s = sub.add_parser(nome)
        s.add_argument("--banco")
    s = sub.add_parser("criar-admin")
    s.add_argument("--banco")
    s.add_argument("--email", required=True)
    s.add_argument("--nome", required=True)
    args = parser.parse_args()
    {"status": cmd_status, "migrar": cmd_migrar, "criar-admin": cmd_criar_admin}[args.comando](args)


if __name__ == "__main__":
    main()
