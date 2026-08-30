"""Os cabeçalhos de segurança que o Caddy punha e a hospedagem gerenciada não.

Existem como teste porque a perda foi silenciosa da primeira vez: eles moravam
no `Caddyfile`, a loja saiu da VPS pro serviço gerenciado, e as proteções
sumiram junto com o proxy sem nada quebrar e sem ninguém notar.
"""

from app.config import get_config

cfg = get_config()


async def test_cabecalhos_basicos(cliente):
    resposta = await cliente.get("/health")
    assert resposta.headers["X-Content-Type-Options"] == "nosniff"
    assert resposta.headers["X-Frame-Options"] == "DENY"
    assert resposta.headers["Referrer-Policy"] == "same-origin"
    assert "camera=()" in resposta.headers["Permissions-Policy"]


async def test_csp_presente_e_fechada(cliente):
    csp = (await cliente.get("/health")).headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # Nada de `unsafe-inline`: os dois PWAs carregam o script por `src`, e
    # abrir a exceção aqui devolveria de graça o XSS que a política existe
    # pra fechar.
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp


async def test_csp_deixa_o_rawbt_passar(cliente):
    """O item que não pode cair.

    A comanda chega na térmica por um iframe que navega pra `intent:…`
    (`frontend/vendas/rawbt.js`). Sem `intent:` no `frame-src`, o navegador
    bloqueia o quadro, a impressão para de sair e o servidor não acusa nada —
    o erro aparece só no console do celular do balcão.
    """
    csp = (await cliente.get("/health")).headers["Content-Security-Policy"]
    assert "frame-src 'self' intent:" in csp


async def test_websocket_liberado_no_connect_src(cliente):
    """`connect-src` sem `ws:` derruba o WebSocket — e as telas passam a
    depender só do polling, ficando lentas sem erro nenhum aparecer."""
    csp = (await cliente.get("/health")).headers["Content-Security-Policy"]
    assert "connect-src 'self' ws: wss:" in csp


async def test_sem_hsts_fora_de_producao(cliente):
    """Em desenvolvimento o HSTS seria um tiro no pé: o navegador passaria a
    exigir HTTPS de `127.0.0.1` e a tela pararia de abrir — inclusive depois de
    o desenvolvedor desfazer o que causou isso."""
    assert cfg.producao is False  # o conftest fixa SHALON_AMBIENTE=teste
    resposta = await cliente.get("/health")
    assert "Strict-Transport-Security" not in resposta.headers


async def test_service_worker_continua_sem_cache(cliente):
    """A regra antiga não pode ter sido atropelada pelo middleware novo."""
    resposta = await cliente.get("/vendas/sw.js")
    if resposta.status_code == 200:
        assert "no-store" in resposta.headers.get("Cache-Control", "")
