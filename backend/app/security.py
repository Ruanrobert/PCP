"""Senhas, tokens e utilidades de segurança (somente biblioteca padrão)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# scrypt: N=2^14, r=8, p=1 (~16 MB por verificação) — recomendado pela OWASP para scrypt.
_N, _R, _P = 2**14, 8, 1
SENHA_MIN = 10
SENHA_MAX = 128


def _b64(dados: bytes) -> str:
    return base64.urlsafe_b64encode(dados).decode().rstrip("=")


def _unb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    derivada = hashlib.scrypt(senha.encode("utf-8"), salt=sal, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(sal)}${_b64(derivada)}"


def verificar_senha(senha: str, armazenado: str | None) -> bool:
    if not armazenado:
        # Custo equivalente para não revelar, pelo tempo de resposta, se o usuário existe.
        hashlib.scrypt(senha.encode("utf-8"), salt=b"0" * 16, n=_N, r=_R, p=_P, dklen=32)
        return False
    try:
        algoritmo, n, r, p, sal, esperado = armazenado.split("$")
        if algoritmo != "scrypt":
            return False
        derivada = hashlib.scrypt(senha.encode("utf-8"), salt=_unb64(sal), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(derivada, _unb64(esperado))
    except (ValueError, TypeError):
        return False


def validar_politica_senha(senha: str, email: str = "") -> str | None:
    """Retorna a mensagem de erro, ou None se a senha for aceitável."""
    if len(senha) < SENHA_MIN:
        return f"A senha precisa ter pelo menos {SENHA_MIN} caracteres."
    if len(senha) > SENHA_MAX:
        return f"A senha pode ter no máximo {SENHA_MAX} caracteres."
    if senha.strip() != senha or not senha.strip():
        return "A senha não pode começar ou terminar com espaços."
    if len(set(senha)) < 4:
        return "A senha é muito repetitiva."
    if email and email.split("@")[0].lower() in senha.lower() and len(email.split("@")[0]) >= 4:
        return "A senha não pode conter o seu e-mail."
    return None


def novo_token() -> str:
    """Token aleatório de 256 bits para sessões, convites e redefinições."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Somente o hash do token fica no banco: um vazamento do banco não expõe sessões."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def iguais(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a.encode(), b.encode())
