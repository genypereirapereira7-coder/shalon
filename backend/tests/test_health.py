"""Fase 0: /health responde."""


async def test_health_responde(cliente):
    resposta = await cliente.get("/health")
    assert resposta.status_code == 200

    corpo = resposta.json()
    assert corpo["fuso"] == "America/Sao_Paulo"
    assert corpo["dia_operacional"]
    assert corpo["agora_utc"].endswith("+00:00")


async def test_health_nao_precisa_de_token(cliente):
    assert (await cliente.get("/health")).status_code == 200
