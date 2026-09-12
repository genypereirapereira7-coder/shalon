"""Hash de senha, tokens JWT e trava de tentativas de login."""

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar
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


def validade_do_token(token: str) -> datetime | None:
    """Quando este access token expira.

    Só o `/ws` precisa disto. Uma conexão HTTP morre a cada requisição e
    revalida o token na seguinte; um WebSocket aberto ficaria de pé pra sempre
    com o token que apresentou na entrada — a tela da cozinha do PC nunca
    fecha. Sabendo a validade, a rota derruba o socket no vencimento e o
    cliente reconecta com um token novo.
    """
    try:
        corpo = jwt.decode(token, cfg.jwt_segredo, algorithms=[cfg.jwt_algoritmo])
        return datetime.fromtimestamp(corpo["exp"], UTC)
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError, OSError):
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

    # Acima disto o `_expurgar` limpa o que já não bloqueia. Folgado pra uma
    # loja: são poucos aparelhos e poucos nomes, e este número só é alcançado
    # quando alguém está varrendo.
    MAX_CHAVES: ClassVar[int] = 2048

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
        self._expurgar()
        registro = self._por_chave.setdefault(chave, _Tentativas())
        registro.contagem += 1
        if registro.contagem >= self.max_tentativas:
            registro.bloqueado_ate = time.monotonic() + self.bloqueio_seg
            registro.contagem = 0

    def limpar(self, chave: str) -> None:
        self._por_chave.pop(chave, None)

    def _expurgar(self) -> None:
        """Joga fora o que já não bloqueia ninguém.

        A chave é `IP:nome`, e quem escolhe as duas metades é quem tenta entrar:
        sem expurgo, uma varredura de nomes deixava um registro por tentativa,
        pra sempre, num processo que fica meses de pé. Não é uma invasão — é o
        processo engordando até alguém reiniciar sem saber por quê.

        Roda só quando uma falha é registrada, e só passando do teto: em uso
        normal são meia dúzia de chaves e isto nunca acontece. O que sobrevive
        ao expurgo é exatamente quem está bloqueado agora — perder a contagem
        parcial de quem errou uma vez há uma hora não custa nada.
        """
        if len(self._por_chave) <= self.MAX_CHAVES:
            return
        agora_mono = time.monotonic()
        self._por_chave = {
            chave: registro
            for chave, registro in self._por_chave.items()
            if registro.bloqueado_ate > agora_mono
        }


trava_login = TravaLogin()
