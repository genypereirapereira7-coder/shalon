"""Escolhe a implementação de `Impressora` pelo que está no `config.ini`.

A fábrica mora aqui pra que o `main.py` não conheça driver nenhum: ele pede uma
impressora e recebe algo que cumpre o protocolo. Trocar a térmica quebrada pela
laser do escritório num sábado de movimento é editar uma linha e reiniciar.
"""

from impressoras.base import ErroImpressao, Impressora

TIPOS = ("fake", "escpos_usb", "escpos_rede", "spooler_windows")


def criar(tipo: str, opcoes: dict[str, str]) -> Impressora:
    tipo = (tipo or "fake").strip().lower()

    if tipo == "fake":
        from impressoras.fake import ImpressoraFake

        return ImpressoraFake(opcoes.get("arquivo", "cupons.txt"))

    if tipo == "escpos_usb":
        from impressoras.escpos_usb import EscPosUSB

        return EscPosUSB(
            vendor_id=_inteiro(opcoes, "vendor_id"),
            product_id=_inteiro(opcoes, "product_id"),
            perfil=opcoes.get("perfil") or None,
        )

    if tipo == "escpos_rede":
        from impressoras.escpos_rede import EscPosRede

        host = opcoes.get("host")
        if not host:
            raise ErroImpressao("impressora escpos_rede exige `host` no config.ini")
        return EscPosRede(host=host, porta=int(opcoes.get("porta", 9100)))

    if tipo == "spooler_windows":
        from impressoras.spooler_windows import SpoolerWindows

        return SpoolerWindows(opcoes.get("nome") or None)

    raise ErroImpressao(f"Impressora '{tipo}' não existe. Use uma de: {', '.join(TIPOS)}")


def _inteiro(opcoes: dict[str, str], chave: str) -> int:
    """Aceita `0x0416` e `1046` — os IDs de USB são publicados nas duas formas."""
    bruto = opcoes.get(chave)
    if not bruto:
        raise ErroImpressao(f"impressora escpos_usb exige `{chave}` no config.ini")
    try:
        return int(bruto, 0)
    except ValueError as erro:
        raise ErroImpressao(f"`{chave}` inválido no config.ini: {bruto!r}") from erro
