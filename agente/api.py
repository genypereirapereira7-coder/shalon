"""Cliente REST do agente.

Só as quatro chamadas de que ele precisa. O agente não tem tela, então o que
importa aqui é diferente do `frontend/comum/api.js`: ninguém vai ver uma
mensagem de erro e tentar de novo — se algo falhar, quem tem que insistir é o
programa.

**O refresh dura 60 dias** (§ da config do backend). É o que permite ao PC da
cozinha ser desligado no domingo e voltar na terça sem alguém digitar PIN.
"""

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class ErroApi(Exception):
    """O servidor recusou. Repetir a mesma chamada não muda nada."""


class Api:
    def __init__(self, url: str, usuario_id: int, pin: str) -> None:
        self._url = url.rstrip("/")
        self._usuario_id = usuario_id
        self._pin = pin
        self._acesso: str | None = None
        self._refresh: str | None = None
        self._http = httpx.AsyncClient(base_url=self._url, timeout=15)
        # Duas impressões terminando juntas dariam dois refresh ao mesmo tempo,
        # e a rotação do backend trata refresh reutilizado como token roubado —
        # derrubando todas as sessões. Uma renovação por vez.
        self._renovando = asyncio.Lock()

    @property
    def acesso(self) -> str | None:
        return self._acesso

    async def entrar(self) -> None:
        resposta = await self._http.post(
            "/auth/login",
            json={
                "usuario_id": self._usuario_id,
                "segredo": self._pin,
                "dispositivo": "agente-de-impressao",
            },
        )
        if resposta.status_code != 200:
            raise ErroApi(f"Login recusado ({resposta.status_code}): {resposta.text}")

        corpo = resposta.json()
        if corpo.get("papel") != "AGENTE":
            # Rodar o agente com o PIN do dono funcionaria, e seria péssimo: as
            # comandas sairiam assinadas por ele e o PIN do dono ficaria em
            # texto plano num .ini do PC da cozinha.
            raise ErroApi(
                f"O usuário {self._usuario_id} tem papel {corpo.get('papel')}, não AGENTE."
            )

        self._acesso = corpo["acesso"]
        self._refresh = corpo["refresh"]
        log.info("agente autenticado como %s", corpo.get("nome"))

    async def renovar(self) -> bool:
        """Troca o refresh por um par novo. `False` = precisa logar de novo."""
        async with self._renovando:
            if not self._refresh:
                return False
            resposta = await self._http.post("/auth/renovar", json={"refresh": self._refresh})
            if resposta.status_code != 200:
                log.warning("refresh recusado (%s); vou logar de novo", resposta.status_code)
                self._acesso = self._refresh = None
                return False

            corpo = resposta.json()
            self._acesso = corpo["acesso"]
            self._refresh = corpo["refresh"]
            return True

    async def nao_impressos(self) -> list[dict]:
        """O que ficou pra trás. Chamado a cada (re)conexão — é a rede de
        segurança de quando o WebSocket esteve fora do ar."""
        return (await self._pedir("GET", "/pedidos/nao-impressos")).json()

    async def confirmar_impressao(self, pedido_id: str) -> None:
        """ACK: saiu papel. Idempotente do lado do servidor."""
        await self._pedir("POST", f"/pedidos/{pedido_id}/impresso")

    async def fechar(self) -> None:
        await self._http.aclose()

    # ---------------------------------------------------------------- internos

    async def _pedir(self, metodo: str, caminho: str) -> httpx.Response:
        """Faz a chamada e, no 401, renova o token uma vez antes de desistir."""
        resposta = await self._http.request(metodo, caminho, headers=self._cabecalho())

        if resposta.status_code == 401:
            renovado = await self.renovar() or await self._logar_de_novo()
            if renovado:
                resposta = await self._http.request(metodo, caminho, headers=self._cabecalho())

        if resposta.status_code >= 400:
            raise ErroApi(f"{metodo} {caminho} → {resposta.status_code}: {resposta.text[:200]}")
        return resposta

    async def _logar_de_novo(self) -> bool:
        try:
            await self.entrar()
            return True
        except (ErroApi, httpx.HTTPError):
            log.exception("não consegui logar de novo")
            return False

    def _cabecalho(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._acesso}"} if self._acesso else {}
