"""Térmica 80mm ligada por USB — o caminho principal da §3.

O `python-escpos` é importado dentro do construtor, não no topo do módulo. É de
propósito: o agente roda em dev sem impressora nenhuma, e um import no topo
faria o programa inteiro morrer no arranque por causa de uma dependência que só
a máquina da cozinha precisa.

Descobrir `vendor_id`/`product_id`: `python -m escpos.cli --help` ou, no
Windows, o Gerenciador de Dispositivos → Detalhes → IDs de hardware
(`USB\\VID_0416&PID_5011` → vendor `0x0416`, produto `0x5011`).
"""

import logging

from cupom import Cupom
from impressoras.base import ErroImpressao, texto_pra_termica

log = logging.getLogger(__name__)


class EscPosUSB:
    def __init__(self, vendor_id: int, product_id: int, perfil: str | None = None) -> None:
        try:
            from escpos.printer import Usb
        except ImportError as erro:  # pragma: no cover - depende do hardware
            raise ErroImpressao(
                "python-escpos não está instalado. Rode: pip install 'python-escpos[usb]'"
            ) from erro

        self._Usb = Usb
        self._vendor_id = vendor_id
        self._product_id = product_id
        self._perfil = perfil
        self._porta = None

    def _conectar(self):
        """Abre a porta na primeira impressão e reaproveita nas seguintes.

        Reabrir a cada cupom é lento e, em algumas térmicas, derruba a porta
        depois de algumas dezenas de aberturas. Se a conexão morreu, o
        `imprimir` a descarta e esta função abre outra.
        """
        if self._porta is None:
            extras = {"profile": self._perfil} if self._perfil else {}
            self._porta = self._Usb(self._vendor_id, self._product_id, **extras)
        return self._porta

    def imprimir(self, cupom: Cupom) -> None:
        try:
            porta = self._conectar()
            porta.text(texto_pra_termica(cupom))
            porta.cut()
        except Exception as erro:  # pragma: no cover - depende do hardware
            # Porta descartada: a próxima tentativa reabre. Uma térmica que caiu
            # e voltou não pode exigir reiniciar o agente pra imprimir de novo.
            self.fechar()
            raise ErroImpressao(f"Falha na térmica USB: {erro}") from erro

    def esta_ok(self) -> bool:
        try:
            porta = self._conectar()
        except Exception:  # pragma: no cover - depende do hardware
            return False

        # `paper_status` existe nos modelos que respondem status pela USB. Nos
        # que não respondem, o `python-escpos` levanta — e nesse caso a única
        # verdade disponível é "a porta abriu".
        try:
            return bool(porta.paper_status())
        except Exception:  # pragma: no cover - depende do hardware
            return True

    def fechar(self) -> None:
        if self._porta is not None:
            try:
                self._porta.close()
            except Exception:  # pragma: no cover - depende do hardware
                log.debug("erro ao fechar a porta USB", exc_info=True)
            self._porta = None
