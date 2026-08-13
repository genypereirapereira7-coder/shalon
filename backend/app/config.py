"""Configuração via variáveis de ambiente (prefixo SHALON_)."""

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHALON_", env_file=".env", extra="ignore")

    ambiente: str = "dev"

    database_url: str = "postgresql+asyncpg://shalon:shalon@localhost:5432/shalon"

    # Autenticação
    jwt_segredo: str = "dev-inseguro-troque-em-producao"
    jwt_algoritmo: str = "HS256"
    acesso_expira_min: int = 30          # token curto, renovado pelo refresh
    refresh_expira_dias: int = 60        # o celular da loja não pode deslogar no meio do expediente
    login_max_tentativas: int = 5        # PIN de 4 dígitos é fraco: a trava é aqui, não no bcrypt
    login_bloqueio_seg: int = 300

    # Dia operacional — o fuso PRECISA ser explícito: a VPS roda em UTC e a
    # virada das 04h sairia errada se dependesse do relógio do sistema.
    fuso: str = "America/Sao_Paulo"
    hora_virada_dia: int = 4

    # Um pedido que ficou na fila offline por mais que isso é recusado:
    # provavelmente é celular com relógio errado, não venda atrasada.
    atraso_max_horas: int = 12

    cors_origens: list[str] = ["*"]

    # Onde ficam os PWAs; em dev o próprio FastAPI serve os arquivos.
    dir_frontend: str = "../frontend"

    senha_dono: str = "shalon123"  # usada só pelo seed inicial

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.fuso)

    @property
    def producao(self) -> bool:
        return self.ambiente.lower() in {"prod", "producao", "production"}


@lru_cache
def get_config() -> Config:
    return Config()
