"""Configuração via variáveis de ambiente (prefixo SHALON_)."""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# O que o `sslmode` do libpq quer dizer pro asyncpg, que usa outro nome e outro
# vocabulário. "disable" fica de fora de propósito: não vira parâmetro nenhum,
# porque não exigir TLS já é o padrão do asyncpg.
_SSL_EQUIVALENTE = {
    "allow": "prefer",
    "prefer": "prefer",
    "require": "require",
    "verify-ca": "verify-ca",
    "verify-full": "verify-full",
}


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SHALON_", env_file=".env", extra="ignore")

    ambiente: str = "dev"

    # Duas variáveis, nesta ordem: a nossa primeiro, `DATABASE_URL` depois.
    #
    # Serviço de banco gerenciado publica a string de conexão como
    # `DATABASE_URL` — é o nome que o Railway, o Heroku e o Fly usam, e é o que
    # a referência `${{Postgres.DATABASE_URL}}` do Railway preenche sozinha.
    # Aceitar o nome de lá economiza o passo mais fácil de esquecer na hora de
    # publicar, e o mais difícil de diagnosticar depois: sem ele o serviço sobe
    # normalmente, com a URL de dev do padrão abaixo, e só falha quando alguém
    # aperta um botão.
    #
    # `SHALON_DATABASE_URL` continua ganhando quando as duas existem: é a que
    # alguém definiu de propósito.
    database_url: str = Field(
        default="postgresql+asyncpg://shalon:shalon@localhost:5432/shalon",
        validation_alias=AliasChoices("SHALON_DATABASE_URL", "DATABASE_URL"),
    )

    # Autenticação
    jwt_segredo: str = "dev-inseguro-troque-em-producao"
    jwt_algoritmo: str = "HS256"
    acesso_expira_min: int = 30          # token curto, renovado pelo refresh

    # Um ano, e deslizante: cada renovação abre uma sessão nova com o prazo
    # cheio. Na prática a senha é digitada uma vez e nunca mais, que é o que o
    # balcão precisa — funcionário deslogado no meio de um sábado é fila
    # parada. Quem tira o acesso de alguém é o dono, pela tela de sessões
    # (`/auth/sessoes`), e não o relógio.
    refresh_expira_dias: int = 365

    login_max_tentativas: int = 5        # a trava é aqui, não no bcrypt
    login_bloqueio_seg: int = 300

    # Dia operacional — o fuso PRECISA ser explícito: a VPS roda em UTC e a
    # virada das 04h sairia errada se dependesse do relógio do sistema.
    fuso: str = "America/Sao_Paulo"
    hora_virada_dia: int = 4

    # Um pedido que ficou na fila offline por mais que isso é recusado:
    # provavelmente é celular com relógio errado, não venda atrasada.
    atraso_max_horas: int = 12

    # Os dois PWAs e a API vivem na mesma origem, então o navegador nunca
    # precisa de CORS pra usar o sistema. `*` estava aqui desde o começo e é
    # inerte na prática (o navegador recusa `*` junto de credenciais), mas
    # inerte por acidente não é o mesmo que fechado: basta alguém trocar o
    # `allow_credentials` um dia pra virar buraco. Lista vazia = só a própria
    # origem.
    cors_origens: list[str] = []

    # ------------------------------------------------------- cabeçalhos

    # A política de conteúdo. Tudo vem da própria origem porque não há CDN,
    # fonte externa nem script de terceiro em lugar nenhum do frontend.
    #
    # `frame-src 'self' intent:` é o item que não pode cair: o RawBT recebe a
    # comanda por um iframe que navega pra `intent:…`
    # (`frontend/vendas/rawbt.js`). Sem o `intent:` aqui, a política bloqueia
    # o quadro, a impressão para de sair e nada no servidor acusa — o erro
    # aparece só no console do celular do balcão.
    #
    # É variável de ambiente pra que uma política errada seja um valor a
    # corrigir no painel, e não um deploy a refazer com a loja aberta.
    # `SHALON_CSP=""` desliga.
    csp: str = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "manifest-src 'self'; "
        "worker-src 'self'; "
        "frame-src 'self' intent:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    # HSTS: um ano, e só em produção e só sobre HTTPS. Sem `preload` e sem
    # `includeSubDomains` de propósito — os dois são difíceis de desfazer, e
    # o domínio aqui é o do serviço gerenciado, não um que a loja controle.
    hsts_max_age: int = 31536000

    # Onde ficam os PWAs; em dev o próprio FastAPI serve os arquivos.
    dir_frontend: str = "../frontend"

    # Só o dono nasce pelo seed. Fica aqui como padrão pra que a loja funcione
    # sem ninguém decorar variável de ambiente — e continua sendo variável pra
    # que trocar a senha não exija editar código.
    senha_dono: str = "adriano212121"

    @field_validator("database_url")
    @classmethod
    def _url_para_asyncpg(cls, valor: str) -> str:
        """Aceita a URL que o provedor de banco entrega, sem edição manual.

        O Render (e o Heroku, e o Railway) publica a string de conexão no
        formato do libpq — `postgresql://…`, às vezes ainda com o `postgres://`
        antigo, e a externa vem com `?sslmode=require` pendurado. Nada disso
        serve pra engine async: sem o `+asyncpg` o SQLAlchemy carrega o driver
        síncrono e morre com "the greenlet library is required"; e o asyncpg
        não conhece `sslmode`, então rejeita a conexão por parâmetro
        desconhecido antes mesmo de tentar.

        Colar a URL na variável de ambiente é o gesto natural de quem está
        configurando o serviço, e é o gesto que falhava. Traduzir aqui custa
        vinte linhas e evita um deploy que sobe verde e responde 500 em tudo.
        """
        valor = valor.strip()
        if not valor:
            return valor

        esquema, resto = (valor.split("://", 1) + [""])[:2] if "://" in valor else (valor, None)
        if resto is None:
            return valor
        if esquema in {"postgres", "postgresql"}:
            esquema = "postgresql+asyncpg"
        elif esquema.startswith(("postgres+", "postgresql+")) and "asyncpg" not in esquema:
            # `postgresql+psycopg2` etc.: o driver síncrono não roda aqui.
            esquema = "postgresql+asyncpg"
        valor = f"{esquema}://{resto}"

        if esquema != "postgresql+asyncpg":
            return valor

        partes = urlsplit(valor)
        consulta = parse_qsl(partes.query, keep_blank_values=True)
        traduzida: list[tuple[str, str]] = []
        for chave, item in consulta:
            if chave == "sslmode":
                modo = item.lower()
                if modo == "disable":
                    continue  # o padrão do asyncpg já é não exigir TLS
                traduzida.append(("ssl", _SSL_EQUIVALENTE.get(modo, "require")))
            elif chave == "channel_binding":
                continue  # só o libpq entende; o asyncpg reclamaria
            else:
                traduzida.append((chave, item))
        return urlunsplit(partes._replace(query=urlencode(traduzida)))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.fuso)

    @property
    def producao(self) -> bool:
        return self.ambiente.lower() in {"prod", "producao", "production"}


@lru_cache
def get_config() -> Config:
    return Config()
