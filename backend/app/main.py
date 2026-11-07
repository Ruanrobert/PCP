"""Aplicação FastAPI do PCP Industrial SaaS.

Iniciar (na pasta backend):  .venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8000
O servidor NÃO migra o banco: se a versão do banco não for a esperada, as rotas respondem 503.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import config as config_mod
from .db import abrir, versao
from .migrations import VERSAO_ALVO
from .routers import admin, auth, configuracao, empresa, importacao, ops, producao, relatorios, remessas
from .routers.auth import CSRF_COOKIE
from .security import iguais

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pcp")

app = FastAPI(title="PCP Industrial API", version="1.0.0",
              description="API multiempresa do PCP Industrial. Autenticação por cookie de sessão + cabeçalho "
                          "X-CSRF-Token nas operações de escrita.")

cfg = config_mod.config
if cfg.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cfg.cors_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token"])

ROTAS_SEM_CSRF = {"/api/auth/login", "/api/auth/convite/aceitar", "/api/auth/convite/validar",
                  "/api/auth/esqueci", "/api/auth/redefinir", "/api/auth/redefinir/validar"}
METODOS_ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}

_janela: dict[str, deque] = defaultdict(deque)
_trava = threading.Lock()
LIMITE_REQUISICOES = 600  # por IP por minuto (proteção básica; em produção complementar no proxy)


def _banco_pronto() -> tuple[bool, str]:
    try:
        con = abrir()
        try:
            v = versao(con)
        finally:
            con.close()
    except FileNotFoundError as erro:
        return False, str(erro)
    if v != VERSAO_ALVO:
        return False, f"Banco na versão {v}; esperado {VERSAO_ALVO}. Execute a migração (python -m app.cli migrar)."
    return True, ""


@app.middleware("http")
async def protecoes(request: Request, call_next):
    caminho = request.url.path
    if caminho.startswith("/api/"):
        ip = request.client.host if request.client else ""
        agora_s = time.monotonic()
        with _trava:
            fila = _janela[ip]
            while fila and agora_s - fila[0] > 60:
                fila.popleft()
            if len(fila) >= LIMITE_REQUISICOES:
                return JSONResponse({"detail": "Muitas requisições. Aguarde um instante."}, status_code=429)
            fila.append(agora_s)

        if request.method in METODOS_ESCRITA and caminho not in ROTAS_SEM_CSRF:
            if not iguais(request.headers.get("x-csrf-token"), request.cookies.get(CSRF_COOKIE)):
                return JSONResponse({"detail": "Verificação de segurança (CSRF) falhou. Recarregue a página."},
                                    status_code=403)
        if caminho != "/api/health":
            pronto, motivo = _banco_pronto()
            if not pronto:
                return JSONResponse({"detail": motivo}, status_code=503)

    resposta = await call_next(request)
    resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
    resposta.headers.setdefault("X-Frame-Options", "DENY")
    resposta.headers.setdefault("Referrer-Policy", "same-origin")
    if caminho.startswith("/api/"):
        resposta.headers.setdefault("Cache-Control", "no-store")
    if cfg.producao:
        resposta.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resposta


@app.exception_handler(Exception)
async def erro_inesperado(request: Request, erro: Exception):
    # Sem detalhes internos para o cliente; o log registra só o tipo e a rota.
    log.exception("Erro inesperado em %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Erro interno. A operação não foi concluída."}, status_code=500)


@app.get("/api/health")
def health():
    pronto, motivo = _banco_pronto()
    return {"api": "online", "banco_de_dados": "conectado" if pronto else "indisponível",
            "versao_api": app.version, **({"motivo": motivo} if motivo else {})}


@app.get("/api/info")
def info():
    return {"sistema": "PCP Industrial", "versao": app.version, "banco": "SQLite", "ambiente": cfg.ambiente}


for modulo in (auth, admin, empresa, configuracao, remessas, producao, ops, importacao, relatorios):
    app.include_router(modulo.router)
