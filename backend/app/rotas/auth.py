"""Login por nome e senha, renovação de token, logout e sessões abertas."""

import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, select

from app.config import get_config
from app.dependencias import IdentidadeDep, SessaoDep, SoDono
from app.models.base import agora
from app.models.usuario import Papel, SessaoAuth, Usuario
from app.schemas.auth import (
    LoginEntrada,
    RenovarEntrada,
    SessaoAtiva,
    TokensSaida,
    UsuarioPublico,
)
from app.schemas.usuario import CadastroEntrada, CadastroSaida
from app.seguranca import (
    conferir_hash,
    criar_token_acesso,
    gerar_hash,
    gerar_refresh,
    hash_refresh,
    trava_login,
)
from app.servicos.eventos import Evento, publicar_apos_commit

cfg = get_config()
rotas = APIRouter(prefix="/auth", tags=["auth"])


@rotas.post("/login", response_model=TokensSaida)
async def login(dados: LoginEntrada, request: Request, sessao: SessaoDep):
    """Entra com nome de usuário e senha.

    Não existe mais rota que liste os usuários: a tela de login não mostra
    quem existe, e descobrir isso passa a ser problema de quem tentar entrar.

    O nome não diferencia maiúscula de minúscula e ignora espaço nas pontas —
    quem digita está de pé, com pressa, e "Vanusa " com um espaço a mais não
    pode virar "usuário ou senha inválidos".
    """
    nome = dados.usuario.strip()
    chave = f"{request.client.host if request.client else '?'}:{nome.lower()}"

    bloqueio = trava_login.segundos_restantes(chave)
    if bloqueio:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Muitas tentativas. Tente de novo em {bloqueio}s.",
        )

    consulta = select(Usuario).where(func.lower(Usuario.nome) == nome.lower()).limit(1)
    usuario = (await sessao.execute(consulta)).scalars().first()

    # Mesma resposta pra usuário inexistente e senha errada: não entregamos de
    # graça a informação de quais nomes existem.
    if usuario is None or not usuario.ativo or not conferir_hash(dados.segredo, usuario.pin_hash):
        trava_login.registrar_falha(chave)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário ou senha inválidos")

    trava_login.limpar(chave)
    return await _emitir_tokens(sessao, usuario, dados.dispositivo)


@rotas.post("/cadastro", response_model=CadastroSaida, status_code=status.HTTP_201_CREATED)
async def cadastro(dados: CadastroEntrada, request: Request, sessao: SessaoDep):
    """O funcionário pede uma conta. Quem abre a porta é o dono.

    Sempre nasce FUNCIONARIO — quem vira DONO é gente que já existe no banco
    antes de o sistema subir, não alguém que digitou um PIN na tela de vendas.
    O nome não pode repetir um que já exista (dono incluso): o login busca por
    nome sem saber o papel, e dois donos com o mesmo nome tornariam o login um
    sorteio de qual conta entra.

    **A conta nasce inativa, e esta rota não devolve token.** Antes ela
    devolvia a sessão pronta: criar a conta *era* entrar. Isso funcionava
    enquanto o sistema só existia dentro da loja, onde alcançar a tela já
    exigia estar atrás do balcão. Num endereço público a mesma porta atende
    qualquer um que descubra o link — e uma conta de funcionário enxerga o
    cardápio, lança pedido e imprime comanda. O dono libera pela tela dele; até
    lá o login recusa, porque já checa `ativo`.

    Tem trava de tentativa igual à do login, pela mesma razão que ela existe
    lá: sem isso, um laço cria mil contas em um minuto e a tela do dono vira
    uma lista impossível de auditar — cada uma delas esperando um toque
    distraído.
    """
    nome = dados.nome.strip()
    chave = f"cadastro:{request.client.host if request.client else '?'}"

    bloqueio = trava_login.segundos_restantes(chave)
    if bloqueio:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Muitas contas criadas daqui. Tente de novo em {bloqueio}s.",
        )

    existe = (
        await sessao.execute(
            select(Usuario).where(func.lower(Usuario.nome) == nome.lower())
        )
    ).scalar_one_or_none()
    if existe is not None:
        # Conta o nome repetido como tentativa: é por aí que alguém varreria a
        # lista de quem trabalha na loja, um nome por vez.
        trava_login.registrar_falha(chave)
        raise HTTPException(status.HTTP_409_CONFLICT, "Esse nome já está em uso")

    usuario = Usuario(
        nome=nome,
        pin_hash=gerar_hash(dados.senha),
        papel=Papel.FUNCIONARIO,
        ativo=False,
        aprovado_em=None,
    )
    sessao.add(usuario)
    await sessao.flush()

    trava_login.registrar_falha(chave)

    await publicar_apos_commit(
        sessao, Evento.USUARIO_PENDENTE, {"usuario_id": usuario.id, "nome": usuario.nome}
    )
    return CadastroSaida(nome=usuario.nome)


@rotas.post("/renovar", response_model=TokensSaida)
async def renovar(dados: RenovarEntrada, sessao: SessaoDep):
    """Troca o refresh por um par novo.

    O refresh antigo é revogado na troca (rotação). Se um refresh já usado
    aparecer de novo, é sinal de token roubado — derrubamos todas as sessões
    daquele usuário.
    """
    consulta = select(SessaoAuth).where(SessaoAuth.refresh_hash == hash_refresh(dados.refresh))
    sessao_auth = (await sessao.execute(consulta)).scalar_one_or_none()

    if sessao_auth is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh inválido")

    if sessao_auth.usado_em is not None:
        await _revogar_todas(sessao, sessao_auth.usuario_id)
        # Commit explícito: a dependência de sessão faz rollback em qualquer
        # exceção — inclusive HTTPException. Sem isto a revogação seria desfeita
        # e o token roubado continuaria valendo.
        await sessao.commit()
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Refresh reutilizado — sessões encerradas"
        )

    if not sessao_auth.valida:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sessão expirada")

    usuario = await sessao.get(Usuario, sessao_auth.usuario_id)
    if usuario is None or not usuario.ativo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário inativo")

    sessao_auth.usado_em = agora()
    sessao_auth.revogado_em = agora()
    return await _emitir_tokens(sessao, usuario, sessao_auth.dispositivo)


@rotas.post("/sair", status_code=status.HTTP_204_NO_CONTENT)
async def sair(dados: RenovarEntrada, sessao: SessaoDep):
    consulta = select(SessaoAuth).where(SessaoAuth.refresh_hash == hash_refresh(dados.refresh))
    sessao_auth = (await sessao.execute(consulta)).scalar_one_or_none()
    if sessao_auth and sessao_auth.revogado_em is None:
        sessao_auth.revogado_em = agora()


@rotas.get("/sessoes", response_model=list[SessaoAtiva])
async def sessoes_abertas(sessao: SessaoDep, dono: SoDono):
    """Quem está logado agora, do login mais recente pro mais antigo.

    Só o dono vê. É a lista de aparelhos com acesso ao sistema — saber que
    existe um login de dezembro num celular que ninguém reconhece é o ponto.

    Sessão revogada ou vencida não aparece: a tela existe pra responder "quem
    entra hoje", e um histórico de logins antigos misturado só faria a resposta
    demorar mais.
    """
    consulta = (
        select(SessaoAuth, Usuario)
        .join(Usuario, Usuario.id == SessaoAuth.usuario_id)
        .where(SessaoAuth.revogado_em.is_(None), SessaoAuth.expira_em > agora())
        .order_by(SessaoAuth.criado_em.desc())
    )

    return [
        SessaoAtiva(
            id=s.id,
            usuario_id=usuario.id,
            usuario_nome=usuario.nome,
            papel=usuario.papel,
            dispositivo=s.dispositivo,
            criado_em=s.criado_em,
            expira_em=s.expira_em,
            meu_usuario=usuario.id == dono.usuario_id,
        )
        for s, usuario in (await sessao.execute(consulta)).all()
    ]


@rotas.delete("/sessoes/{sessao_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revogar_sessao(sessao_id: uuid.UUID, sessao: SessaoDep, _: SoDono):
    """Tira o acesso de um aparelho.

    O refresh morre na hora, então o aparelho não consegue mais renovar. O
    token de acesso que ele já tem na mão continua valendo até vencer — são
    até 30 minutos (`acesso_expira_min`). Isso é consequência de o token ser
    assinado e não consultado: validá-lo contra o banco custaria uma consulta
    em toda chamada de toda tela, o dia inteiro, pra cobrir um caso que
    acontece uma vez por ano.

    Quem precisa cortar **agora** — celular roubado — desativa o usuário, e não
    só a sessão. A tela avisa dessa janela em vez de prometer o que a rota não
    entrega.
    """
    aberta = await sessao.get(SessaoAuth, sessao_id)
    if aberta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sessão não existe")

    # Já revogada não é erro: dois toques no mesmo botão, ou duas abas abertas.
    if aberta.revogado_em is None:
        aberta.revogado_em = agora()


@rotas.get("/eu", response_model=UsuarioPublico)
async def eu(ident: IdentidadeDep, sessao: SessaoDep):
    usuario = await sessao.get(Usuario, ident.usuario_id)
    if usuario is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuário não existe mais")
    return usuario


# ------------------------------------------------------------------ internos

async def _emitir_tokens(sessao, usuario: Usuario, dispositivo: str | None) -> TokensSaida:
    acesso, validade = criar_token_acesso(usuario.id, usuario.nome, usuario.papel)
    bruto, guardado = gerar_refresh()

    sessao.add(
        SessaoAuth(
            usuario_id=usuario.id,
            refresh_hash=guardado,
            dispositivo=dispositivo,
            expira_em=agora() + timedelta(days=cfg.refresh_expira_dias),
        )
    )

    return TokensSaida(
        acesso=acesso,
        refresh=bruto,
        expira_em=validade,
        usuario_id=usuario.id,
        nome=usuario.nome,
        papel=usuario.papel,
    )


async def _revogar_todas(sessao, usuario_id: int) -> None:
    consulta = select(SessaoAuth).where(
        SessaoAuth.usuario_id == usuario_id, SessaoAuth.revogado_em.is_(None)
    )
    for s in (await sessao.execute(consulta)).scalars():
        s.revogado_em = agora()
