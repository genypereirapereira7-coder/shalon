"""Térmica com porta de rede (IP fixo).

Alternativa ao USB quando a impressora fica longe do PC. Exige **IP fixo** ou
reserva de DHCP no roteador: com IP dinâmico, o endereço muda numa queda de luz
e as comandas param de sair sem ninguém entender por quê.
"""

import logging

from cupom import Cupom
from impressoras.base import ErroImpressao, texto_pra_termica

log = logging.getLogger(__name__)


class EscPosRede:
    def __init__(self, host: str, porta: int = 9100, timeout: int = 10) -> None:
        try:
            from escpos.printer import Network
        except ImportError as erro:  # pragma: no cover - depende do hardware
            raise ErroImpressao(
                "python-escpos não está instalado. Rode: pip install python-escpos"
            ) from erro

        self._Network = Network
        self._host = host
        self._porta_tcp = porta
        # Sem timeout, uma impressora desligada trava a thread de impressão pra
        # sempre e a fila para — silenciosamente, que é o pior jeito.
        self._timeout = timeout
        self._conexao = None

    def _conectar(self):
        if self._conexao is None:
            self._conexao = self._Network(self._host, port=self._porta_tcp, timeout=self._timeout)
        return self._conexao

    def imprimir(self, cupom: Cupom) -> None:
        try:
            conexao = self._conectar()
            conexao.text(texto_pra_termica(cupom))
            conexao.cut()
        except Exception as erro:  # pragma: no cover - depende do hardware
            self.fechar()
            raise ErroImpressao(f"Falha na térmica de rede {self._host}: {erro}") from erro

    def esta_ok(self) -> bool:
        try:
            self._conectar()
            return True
        except Exception:  # pragma: no cover - depende do hardware
            return False

    def fechar(self) -> None:
        if self._conexao is not None:
            try:
                self._conexao.close()
            except Exception:  # pragma: no cover - depende do hardware
                log.debug("erro ao fechar a conexão de rede", exc_info=True)
            self._conexao = None
