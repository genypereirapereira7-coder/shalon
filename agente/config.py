"""Leitura do `config.ini`.

Arquivo `.ini` e não variáveis de ambiente: quem vai mexer nisto é a dona da
sorveteria ou o técnico que instalou o PC, com o Bloco de Notas, e não alguém
com um terminal aberto.
"""

import configparser
from dataclasses import dataclass, field
from pathlib import Path

PADRAO = "config.ini"


@dataclass(frozen=True)
class Config:
    url: str
    usuario_id: int
    pin: str

    impressora: str
    opcoes_impressora: dict[str, str] = field(default_factory=dict)

    fuso: str = "America/Sao_Paulo"
    largura: int = 48

    abrir_cozinha: bool = False
    navegador: str = ""

    @property
    def url_ws(self) -> str:
        """`http://x/…` → `ws://x/ws`, `https://` → `wss://`.

        Derivada e não configurada à parte: são sempre o mesmo servidor, e duas
        chaves pra apontar pro mesmo lugar é uma chance de apontarem pra
        lugares diferentes.
        """
        base = self.url.rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://") :] + "/ws"
        return "ws://" + base.removeprefix("http://") + "/ws"

    @property
    def url_cozinha(self) -> str:
        return self.url.rstrip("/") + "/cozinha/"


def carregar(caminho: str | Path = PADRAO) -> Config:
    caminho = Path(caminho)
    if not caminho.exists():
        raise SystemExit(
            f"Não achei o {caminho}. Copie o config.ini.exemplo e ajuste os valores."
        )

    ini = configparser.ConfigParser()
    ini.read(caminho, encoding="utf-8")

    servidor = ini["servidor"] if ini.has_section("servidor") else {}
    impressora = ini["impressora"] if ini.has_section("impressora") else {}
    loja = ini["loja"] if ini.has_section("loja") else {}
    tela = ini["cozinha"] if ini.has_section("cozinha") else {}

    faltando = [c for c in ("url", "usuario_id", "pin") if not servidor.get(c)]
    if faltando:
        raise SystemExit(f"config.ini: falta {', '.join(faltando)} na seção [servidor]")

    return Config(
        url=servidor.get("url").strip(),
        usuario_id=int(servidor.get("usuario_id")),
        pin=servidor.get("pin").strip(),
        impressora=impressora.get("tipo", "fake").strip(),
        # Tudo que não é `tipo` vai pro driver escolhido. Assim uma impressora
        # nova traz suas chaves próprias sem mexer neste arquivo.
        opcoes_impressora={c: v for c, v in impressora.items() if c != "tipo"},
        fuso=loja.get("fuso", "America/Sao_Paulo").strip(),
        largura=int(loja.get("largura", 48)),
        abrir_cozinha=tela.get("abrir", "nao").strip().lower() in {"sim", "s", "yes", "true", "1"},
        navegador=tela.get("navegador", "").strip(),
    )
