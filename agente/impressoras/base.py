"""O contrato que toda impressora cumpre.

A impressora ainda não foi comprada (§3 da arquitetura), e o agente precisava
funcionar antes dela chegar. Por isso o resto do programa só conhece este
protocolo: trocar `ImpressoraFake` por `EscPosUSB` é uma linha no `config.ini`,
não uma alteração no laço principal.

`imprimir` é **síncrona de propósito**. Falar com uma térmica por USB é
bloqueante e não existe biblioteca async decente pra isso; fingir o contrário
esconderia que a chamada trava. Quem cuida de não travar o laço é o `main.py`,
que chama isto numa thread.
"""

from typing import Protocol, runtime_checkable

from cupom import Cupom


class ErroImpressao(Exception):
    """Não saiu papel.

    Quem levanta isto está dizendo "não confirme pro servidor": o pedido
    continua na fila de não-impressos e a tela da cozinha acende o alerta em
    15s. Errar pro lado de reimprimir é barato; errar pro lado de dar a comanda
    por impressa custa um cliente esperando um sorvete que ninguém começou.
    """


@runtime_checkable
class Impressora(Protocol):
    def imprimir(self, cupom: Cupom) -> None:
        """Põe o cupom no papel. Levanta `ErroImpressao` se não conseguir."""
        ...

    def esta_ok(self) -> bool:
        """Dá pra imprimir agora? Sem papel, offline ou tampa aberta = False."""
        ...

    def fechar(self) -> None:
        """Solta a porta/conexão. Chamado quando o agente encerra."""
        ...


# Avanço final: linhas em branco pra comanda passar da serrilha.
#
# O `spooler_windows` já mandava as suas; as térmicas ESC/POS chamavam `cut()`
# logo depois do texto, e numa impressora sem guilhotina — ou com a guilhotina
# desligada no perfil — o cupom era rasgado em cima do TOTAL.
AVANCO_FINAL = "\n\n\n"


def texto_pra_termica(cupom: Cupom) -> str:
    """O texto do cupom pronto pra sair no papel.

    Troca o `…` do `_cortar` por três pontos: ele não existe na CP437/CP850 que
    a térmica usa, e sai como um caractere qualquer no meio de um nome de
    produto. O `spooler_windows` já fazia esta troca sozinho; as duas ESC/POS
    não faziam, e a mesma comanda saía diferente conforme o cabo.
    """
    return cupom.texto.replace("…", "...") + AVANCO_FINAL
