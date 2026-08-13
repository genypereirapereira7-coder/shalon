"""Hash de senha, tokens JWT e trava de tentativas de login."""

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_config
from app.models.usuario import Papel

cfg = get_config()


# ---------------------------------------------------------------- senha / PIN

def gerar_hash(segredo: str) -> str:
    return bcrypt.hashpw(segredo.encode(), bcrypt.gensalt()).decode()


def conferir_hash(segredo: str, hash_guardado: str) -> bool:
    try:
        return bcrypt.checkpw(segredo.encode(), hash_guardado.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------------- JWT

@dataclass(frozen=True)
class Identidade:
    usuario_id: int
    nome: str
    papel: Papel


def criar_token_acesso(usuario_id: int, nome: str, papel: Papel) -> tuple[str, int]:
    """Devolve (token, segundos_de_validade)."""
    expira_em = datetime.now(UTC) + timedelta(minutes=cfg.acesso_expira_min)
    corpo = {
        "sub": str(usuario_id),
        "nome": nome,
        "papel": papel.value,
        "exp": expira_em,
        "iat": datetime.now(UTC),
    }
    token = jwt.encode(corpo, cfg.jwt_segredo, algorithm=cfg.jwt_algoritmo)
    return token, cfg.acesso_expira_min * 60


def ler_token_acesso(token: str) -> Identidade | None:
    try:
        corpo = jwt.decode(token, cfg.jwt_segredo, algorithms=[cfg.jwt_algoritmo])
        return Identidade(
            usuario_id=int(corpo["sub"]),
            nome=corpo.get("nome", ""),
            papel=Papel(corpo["papel"]),
        )
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


# ------------------------------------------------------------ refresh token

def gerar_refresh() -> tuple[str, str]:
    """Devolve (token_em_claro, hash_pra_guardar).

    SHA-256 e não bcrypt: o refresh já é aleatório de 256 bits, não tem o que
    forçar por dicionário, e o /auth/renovar precisa ser rápido.
    """
    bruto = f"{uuid.uuid4()}.{secrets.token_urlsafe(32)}"
    return bruto, hashlib.sha256(bruto.encode()).hexdigest()


def hash_refresh(bruto: str) -> str:
    return hashlib.sha256(bruto.encode()).hexdigest()


# -------------------------------------------------- trava de força bruta

@dataclass
class _Tentativas:
    contagem: int = 0
    bloqueado_ate: float = 0.0


@dataclass
class TravaLogin:
    """PIN de 4 dígitos são 10 mil combinações — bcrypt protege o banco vazado,
    não o endpoint. A trava é o que impede alguém de varrer o PIN.

    Em memória de propósito: a v1 roda com um worker só (seção 9).
    """

    max_tentativas: int = cfg.login_max_tentativas
    bloqueio_seg: int = cfg.login_bloqueio_seg
    _por_chave: dict[str, _Tentativas] = field(default_factory=dict)

    def segundos_restantes(self, chave: str) -> int:
        registro = self._por_chave.get(chave)
        if not registro:
            return 0
        restante = registro.bloqueado_ate - time.monotonic()
        return int(restante) + 1 if restante > 0 else 0

    def registrar_falha(self, chave: str) -> None:
        registro = self._por_chave.setdefault(chave, _Tentativas())
        registro.contagem += 1
        if registro.contagem >= self.max_tentativas:
            registro.bloqueado_ate = time.monotonic() + self.bloqueio_seg
            registro.contagem = 0

    def limpar(self, chave: str) -> None:
        self._por_chave.pop(chave, None)


trava_login = TravaLogin()
