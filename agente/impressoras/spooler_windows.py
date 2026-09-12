"""Impressora comum pela fila do Windows.

A saída de emergência da §3: serve pra qualquer impressora que já esteja
instalada no Windows, inclusive uma laser de escritório, enquanto a térmica não
chega ou quando ela quebra num sábado.

Manda **texto cru** (`RAW`) pro spooler. Não gera PDF: PDF exigiria um
renderizador e um leitor associado, e o cupom já é texto monoespaçado — que é
exatamente o que uma térmica e o modo texto de qualquer impressora entendem.

Depende de `pywin32`, que só existe no Windows. Como todos os outros drivers, o
import fica no construtor pra não quebrar o agente em quem roda Linux.
"""

import logging

from cupom import Cupom
from impressoras.base import ErroImpressao, texto_pra_termica

log = logging.getLogger(__name__)


class SpoolerWindows:
    def __init__(self, nome_impressora: str | None = None) -> None:
        try:
            import win32print
        except ImportError as erro:  # pragma: no cover - só Windows
            raise ErroImpressao(
                "pywin32 não está instalado. Rode: pip install pywin32"
            ) from erro

        self._win32print = win32print
        # Sem nome, usa a impressora padrão do Windows — que é o que a dona da
        # sorveteria vai ter configurado sem saber que configurou.
        self._nome = nome_impressora or win32print.GetDefaultPrinter()

    def imprimir(self, cupom: Cupom) -> None:
        # cp850 cobre os acentos do português no modo texto da maioria das
        # impressoras. `replace` porque perder o cedilha é mil vezes melhor do
        # que não sair papel nenhum.
        dados = texto_pra_termica(cupom).encode("cp850", errors="replace")

        try:
            alca = self._win32print.OpenPrinter(self._nome)
            try:
                trabalho = self._win32print.StartDocPrinter(
                    alca, 1, (f"Comanda #{cupom.numero}", None, "RAW")
                )
                try:
                    self._win32print.StartPagePrinter(alca)
                    self._win32print.WritePrinter(alca, dados)
                    self._win32print.EndPagePrinter(alca)
                finally:
                    self._win32print.EndDocPrinter(alca)
                log.info("comanda #%s enviada ao spooler (trabalho %s)", cupom.numero, trabalho)
            finally:
                self._win32print.ClosePrinter(alca)
        except Exception as erro:  # pragma: no cover - só Windows
            raise ErroImpressao(f"Falha no spooler '{self._nome}': {erro}") from erro

    def esta_ok(self) -> bool:
        try:
            alca = self._win32print.OpenPrinter(self._nome)
            try:
                info = self._win32print.GetPrinter(alca, 2)
            finally:
                self._win32print.ClosePrinter(alca)
        except Exception:  # pragma: no cover - só Windows
            return False

        # Status 0 = pronta. Qualquer bit ligado é sem papel, offline, atolada
        # ou pausada — todos motivos pra não confirmar impressão pro servidor.
        return info.get("Status", 0) == 0

    def fechar(self) -> None:
        pass
