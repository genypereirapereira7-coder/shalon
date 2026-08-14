"""Impressora de desenvolvimento: escreve o cupom num arquivo de texto.

Não é só pra teste. Enquanto a térmica não chega, é com ela que o fluxo inteiro
da §6 — venda no celular, evento no WebSocket, cupom formatado, ACK de volta —
roda de ponta a ponta. O único pedaço que fica sem exercício é o cabo USB.
"""

import logging
from pathlib import Path

from cupom import Cupom
from impressoras.base import ErroImpressao

log = logging.getLogger(__name__)


class ImpressoraFake:
    def __init__(self, destino: str = "cupons.txt") -> None:
        self.caminho = Path(destino)

    def imprimir(self, cupom: Cupom) -> None:
        try:
            self.caminho.parent.mkdir(parents=True, exist_ok=True)
            # Append e não overwrite: o arquivo vira o rolo de papel do dia, e
            # dá pra conferir a ordem em que as comandas saíram.
            with self.caminho.open("a", encoding="utf-8") as papel:
                # Sem carimbo de hora aqui: o cupom já traz a hora da loja, e o
                # `datetime.now()` deste PC pode estar em outro fuso — duas
                # horas diferentes no mesmo arquivo confundem quem confere.
                papel.write(f"\n\n{'=' * 12} corte {'=' * 12}\n")
                papel.write(cupom.texto)
                papel.write("\n")
        except OSError as erro:
            raise ErroImpressao(f"Não deu pra escrever em {self.caminho}: {erro}") from erro

        log.info("cupom do pedido #%s gravado em %s", cupom.numero, self.caminho)

    def esta_ok(self) -> bool:
        return True

    def fechar(self) -> None:
        pass
