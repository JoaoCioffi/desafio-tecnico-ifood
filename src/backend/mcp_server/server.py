"""Servidor MCP do Sabor da Maria.

    python -m mcp_server        # sobe em http://127.0.0.1:9000/mcp

Esta e a fronteira de determinismo. O LLM nao soma e nao grava: ele chama
uma ferramenta e recebe o resultado pronto.

A regra que sustenta o desafio esta em `prato_aceitar`: ela RECUSA enquanto
o gate apontar pendencia. Nao e instrucao de prompt — e uma funcao que
devolve erro. O modelo nao tem como pular.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from decimal import Decimal
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from pydantic import BaseModel, ConfigDict, Field

from domain.precificacao import (
    ItemCusto,
    calcular_cmv,
    lucro,
    montar_cenarios,
    preco_minimo,
)
from domain.viabilidade import (
    FatoPerfil,
    ItemDespensa,
    ItemReceita,
    RequisitoPerfil,
    avaliar,
)

from . import repo

_SUBIU = time.monotonic()

mcp = FastMCP(
    "sabor-da-maria",
    instructions=(
        "Ferramentas da consultora de cardapio da Dona Maria. "
        "Use-as para qualquer numero: nunca calcule CMV ou preco de cabeca. "
        "Um prato so entra no cardapio via prato_aceitar, que recusa "
        "enquanto prato_checar apontar pendencia."
    ),
)


# --------------------------------------------------------------------------- #
# Saude
# --------------------------------------------------------------------------- #
#  A rota /mcp so aceita POST com JSON-RPC, ou GET com Accept: text/event-stream
#  — e a especificacao do transporte streamable-http. Health check que manda
#  GET ou HEAD simples recebe 400 / 405, o que e correto mas enche o log de
#  erro em requisicao legitima.
#
#  Este endpoint da a esses probes um lugar proprio, e serve ao healthcheck do
#  docker-compose e ao cockpit saberem se o MCP esta vivo.
# --------------------------------------------------------------------------- #
@mcp.custom_route("/health", methods=["GET", "HEAD"])
async def health(request):
    from starlette.responses import JSONResponse

    # O painel le DAQUI, por HTTP, e nao de arquivo no disco. A tentativa
    # anterior lia o config do Hermes direto do sistema de arquivos e falhava
    # com FileNotFoundError num arquivo que existia — o processo do painel
    # simplesmente nao o enxergava. HTTP nao tem esse problema.
    corpo = {
        "status": "ok",
        "servidor": mcp.name,
        "no_ar_ha": round(time.monotonic() - _SUBIU),
    }

    # CADA leitura vai no seu try. Um /health que estoura 500 e pior que nao
    # existir: ele so e consultado quando alguem ja esta desconfiado de algo,
    # e ai some justamente a informacao que diria o que esta errado. Foi o que
    # aconteceu — uma chamada a uma API que nao existe derrubou o endpoint
    # inteiro, inclusive a parte que funcionava.
    try:
        corpo["ferramentas"] = sorted(t.name for t in await mcp.list_tools())
    except Exception as erro:
        corpo["ferramentas_erro"] = f"{type(erro).__name__}: {erro}"[:120]

    try:
        corpo["banco"] = "ok" if repo.despensa(limite=1) else "vazio"
    except Exception as erro:
        corpo |= {"status": "degradado", "banco": "indisponivel", "erro": str(erro)[:200]}
        return JSONResponse(corpo, status_code=503)

    return JSONResponse(corpo)


# --------------------------------------------------------------------------- #
# Sequencia
#
# O gate existia em UM lugar: escrito a mao dentro do prato_aceitar. As outras
# nove ferramentas aceitavam qualquer coisa em qualquer ordem, e o agente
# aproveitou — numa rodada real ele foi de `prato_salvar` direto para `cmv`,
# pulou o `prato_checar` e apresentou preco de tres pratos que nunca foram
# verificados. Deu certo por sorte: rodando o gate depois, os tres passavam.
#
# Cada etapa pulada assim virava um `if` novo dentro de uma ferramenta nova.
# Aqui a regra fica DECLARADA: ferramenta que precise de pre-condicao ganha
# uma linha nesta tabela, nao um remendo dentro de si.
#
# E toda resposta de prato leva `proximo_passo`. Nao e enfeite: o modelo pode
# nao ler uma instrucao no prompt, mas SEMPRE le o resultado da ferramenta que
# acabou de chamar. E o unico canal que ele nao pula — entao e o servidor que
# conduz a sequencia, em vez de torcer para ele lembrar dela.
# --------------------------------------------------------------------------- #
EXIGE_APTO = ("cmv", "cenarios", "prato_aceitar")


class Recusa(ToolError):
    """O servidor disse nao, e isso e resultado previsto — nao defeito.

    Herda de ToolError para a mensagem chegar ao modelo como resultado de
    ferramenta. O `log_level` em DEBUG e o que falta: sem ele o FastMCP
    imprime `Error calling tool 'x'` no console para uma recusa que funcionou
    exatamente como projetado, e quem le o log aprende a ignorar a palavra
    "Error" — ate o dia em que ela significa alguma coisa.

    O rastro completo da recusa continua indo para a tabela `evento`, com
    fase propria. O que sai do console e o alarme falso, nao a informacao.
    """

    # O FastMCPError grava `self.log_level` no __init__, entao atributo de
    # classe nao adianta: a instancia sobrescreve com ERROR.
    def __init__(self, *args: object) -> None:
        super().__init__(*args, log_level=logging.DEBUG)


class Bloqueado(Recusa):
    """Pre-condicao de sequencia nao satisfeita: gate, fonte, preco minimo."""


def _exigir_apto(ferramenta: str, prato_id: int) -> dict:
    """Roda o gate e recusa se houver pendencia. Devolve o resultado do gate."""
    check = _checar(prato_id)
    if not check["apto"]:
        perguntas = "\n  - ".join(check["perguntas"]) or "(sem perguntas)"
        raise Bloqueado(
            f"`{ferramenta}` exige o prato aprovado no gate, e o prato {prato_id} "
            f"tem {len(check['pendencias'])} pendencia(s).\n"
            "Precificar antes de saber se ela consegue cozinhar e o erro que este "
            "projeto existe para evitar.\n"
            f"Faca estas perguntas a ela, uma por vez, grave com perfil_gravar e "
            f"rode prato_checar de novo:\n  - {perguntas}"
        )
    return check


def _proximo_passo(prato_id: int) -> str:
    """O que o servidor espera a seguir para ESTE prato."""
    try:
        prato = repo.prato(prato_id)
        if prato is None:
            return "prato nao encontrado"
        if prato["status"] == "aceito":
            return "prato fechado no cardapio. Va para o proximo, ou cardapio()"
        check = _checar(prato_id)
        if not check["apto"]:
            return (
                f"prato_checar({prato_id}) tem {len(check['pendencias'])} pendencia(s): "
                "faca as perguntas a ela, uma por vez, e grave com perfil_gravar"
            )
        return (
            f"cenarios({prato_id}) para as tres opcoes de preco — e deixe a "
            "Dona Maria escolher antes de prato_aceitar"
        )
    except Exception:
        return ""


def _com_passo(prato_id: int, dados: dict) -> dict:
    passo = _proximo_passo(prato_id)
    return {**dados, "proximo_passo": passo} if passo else dados


# --------------------------------------------------------------------------- #
# Observabilidade
#
# Um middleware, e nao um decorador em cada uma das nove ferramentas: assim
# ferramenta nova ja nasce observada, sem ninguem lembrar de anotar.
# --------------------------------------------------------------------------- #
def _resumir(resultado: Any, limite: int = 400) -> str:
    """Recorte legivel do que a ferramenta devolveu. Nunca levanta."""
    try:
        conteudo = (
            getattr(resultado, "structured_content", None)
            or getattr(resultado, "content", None)
            or resultado
        )
        if isinstance(conteudo, list) and len(conteudo) == 1:
            conteudo = getattr(conteudo[0], "text", conteudo[0])
        texto = conteudo if isinstance(conteudo, str) else json.dumps(
            conteudo, ensure_ascii=False, default=str
        )
    except Exception:
        texto = "(irrepresentavel)"
    texto = " ".join(str(texto).split())
    return texto[:limite] + ("..." if len(texto) > limite else "")


class Observador(Middleware):
    """Grava toda chamada na tabela `evento`: quem, o que entrou, o que saiu.

    Sem impressao no console. A tentativa de tracar cada chamada ali poluiu
    mais do que ajudou — a informacao e a mesma que vai para a tabela, e o
    lugar de le-la e o painel do run_services, que junta isto ao resto.
    """

    async def on_call_tool(self, context, call_next):
        nome = getattr(context.message, "name", "?")
        args = getattr(context.message, "arguments", None) or {}
        repo.registrar(nome, "inicio", f"chamou {nome}", args)
        inicio = time.monotonic()
        try:
            resultado = await call_next(context)
        except BaseException as erro:
            # BaseException, nao Exception: CancelledError nao herda de
            # Exception, e o Hermes cancela chamada quando o turno estoura.
            # Com o except estreito isso ficava INVISIVEL — `inicio` gravado,
            # nenhum fim, e so contando os dois lados dava para notar.
            if isinstance(erro, ToolError):
                fase = "recusa"  # resultado previsto, nao defeito
            elif isinstance(erro, asyncio.CancelledError):
                fase = "cancelado"  # o cliente desistiu; a ferramenta estava viva
            else:
                fase = "erro"
            repo.registrar(
                nome,
                fase,
                f"{type(erro).__name__}: {erro}",
                {"ms": round((time.monotonic() - inicio) * 1000)},
            )
            raise
        repo.registrar(nome, "fim", f"{nome} respondeu", {
            "ms": round((time.monotonic() - inicio) * 1000),
            "resposta": _resumir(resultado),
        })
        return resultado


mcp.add_middleware(Observador())


# --------------------------------------------------------------------------- #
# Despensa
# --------------------------------------------------------------------------- #
@mcp.tool
def despensa(
    ingrediente: str = "",
    ordenar_por: str = "nome",
    limite: int = 0,
) -> list[dict]:
    """Consulta a despensa: o que tem, quanto sobra e quanto custa a unidade.

    FILTRE. Chamar sem argumento devolve os 37 ingredientes, e ler a despensa
    inteira para responder sobre um item custa tempo em toda rodada.

        despensa(ingrediente="bacon")               -> so o bacon
        despensa(ordenar_por="custo", limite=3)     -> os 3 mais caros
        despensa(ordenar_por="disponivel")          -> o que tem mais sobrando
        despensa()                                  -> tudo (use so quando precisar)

    ingrediente  busca parcial, ignora acento e maiuscula ("feijao" acha
                 "Feijão preto")
    ordenar_por  'nome' | 'custo' | 'disponivel'
    limite       0 = sem limite

    `disponivel` ja desconta o que os pratos aceitos comprometeram.
    `custo_unitario` e por `unidade_base` (kg, L ou un) — ja normalizado,
    entao alcaparra sai a R$ 41,00/kg e nao a R$ 82,00 pelo balde.
    """
    return repo.json_seguro(
        repo.despensa(
            ingrediente=ingrediente or None,
            ordenar_por=ordenar_por,
            limite=limite or None,
        )
    )


# --------------------------------------------------------------------------- #
# Elicitacao
# --------------------------------------------------------------------------- #
@mcp.tool
def perfil_ler() -> list[dict]:
    """O que ja se sabe da Dona Maria: utensilios, tecnicas e restricoes."""
    return repo.json_seguro(repo.perfil())


@mcp.tool
def perfil_gravar(categoria: str, item: str, resposta: str) -> dict:
    """Registra uma resposta dela.

    categoria: 'utensilio', 'tecnica' ou 'restricao'
    item:      'panela de pressao', 'massa fresca', 'espaco na geladeira'
    resposta:  'sim', 'nao tem', ou o detalhe que ela deu ('so 2 bocas')
    """
    if categoria not in ("utensilio", "tecnica", "restricao"):
        raise ValueError("categoria deve ser utensilio, tecnica ou restricao")
    return repo.json_seguro(repo.perfil_gravar(categoria, item, resposta))


@mcp.tool
def perfil_pendente() -> list[dict]:
    """O que os pratos exigem e ela ainda nao respondeu.

    Esta e a agenda de elicitacao: nao pergunta nada, devolve o que FALTA
    perguntar. Consulte antes de conversar para nao repetir pergunta ja feita.

    Cada item vem com `como_gravar`: a chamada pronta para registrar a
    resposta dela. Devolver so o dado nao bastou — numa rodada real o agente
    leu a pendencia da panela de pressao, escreveu "agora que sabemos que ela
    tem panela de pressao" e seguiu sem gravar nada. Entregar a chamada e o
    mesmo remedio que o `prato_checar` usa ao devolver a pergunta pronta.
    """
    pendentes = repo.perfil_pendente()
    for p in pendentes:
        p["como_gravar"] = (
            f"perfil_gravar(categoria=\"{p['categoria']}\", item=\"{p['item']}\", "
            "resposta=<o que ela respondeu>)"
        )
    return repo.json_seguro(pendentes)


@mcp.tool
def ingrediente_preco(
    ingrediente: str, preco_pago: float, quantidade: float, unidade: str
) -> dict:
    """Grava quanto a Dona Maria paga por um ingrediente.

    Use quando o gate pedir o preco de algo que falta. Registre EXATAMENTE o
    que ela disse — o preco que ela pagou, pela quantidade que ela comprou, na
    embalagem que ela comprou:

        "R$ 24 o quilo"          -> preco_pago=24, quantidade=1, unidade="kg"
        "R$ 12 o pacote de 500g" -> preco_pago=12, quantidade=1, unidade="pacote 500g"
        "R$ 82 o balde de 2kg"   -> preco_pago=82, quantidade=1, unidade="balde 2kg"

    NAO divida na cabeca para "converter para o quilo". O servidor faz isso, e
    devolve `custo_unitario` ja normalizado — R$ 41,00/kg no caso do balde.
    Fazer essa conta de cabeca e o erro que este projeto existe para evitar.
    """
    try:
        r = repo.ingrediente_preco(
            ingrediente, Decimal(str(preco_pago)), Decimal(str(quantidade)), unidade
        )
    except ValueError as recusa:
        raise Recusa(str(recusa)) from None
    return repo.json_seguro(r)


# --------------------------------------------------------------------------- #
# Cardapio
# --------------------------------------------------------------------------- #
# `list[dict]` publica o schema `{"type": "array", "items": {"type": "object"}}`:
# um objeto sem propriedade nenhuma. O modelo entao adivinha o nome da chave
# pela frase do prompt, e ja mandou `"quantidade em kg por porcao"` como nome
# de campo. Declarar o tipo poe os nomes e as descricoes no schema, que e onde
# o modelo procura.
#
# `extra="allow"` de proposito: o schema ORIENTA, nao barra. O que escapar
# chega inteiro no `normalizar_itens` do repo, que tolera as grafias e, quando
# nao da, devolve um erro que ensina o formato. Barrar aqui trocaria essa
# mensagem por um traceback de validacao.
class Ingrediente(BaseModel):
    """Um ingrediente da receita, na quantidade de UMA porcao."""

    model_config = ConfigDict(extra="allow")

    ingrediente: str | None = Field(
        None, description="nome do ingrediente, como na receita — ex: 'Feijao preto'"
    )
    quantidade: float | None = Field(
        None,
        description="quantidade de uma porcao em kg, L ou un — numero decimal, ex: 0.12",
    )


class Requisito(BaseModel):
    """O que a receita exige da cozinha da Dona Maria. Alimenta o gate."""

    model_config = ConfigDict(extra="allow")

    categoria: Literal["utensilio", "tecnica", "restricao"] | None = Field(
        None, description="utensilio, tecnica ou restricao"
    )
    item: str | None = Field(
        None,
        description="so o objeto, sem repetir a categoria — 'panela de pressao'",
    )


@mcp.tool
def prato_salvar(
    nome: str,
    ingredientes: list[Ingrediente],
    fonte: str = "",
    requisitos: list[Requisito] | None = None,
) -> dict:
    """Guarda uma receita candidata.

    Chame UMA VEZ, com a lista completa: a gravacao SUBSTITUI os ingredientes
    do prato. Duas chamadas nao somam — a segunda apaga a primeira.

    ingredientes: [{"ingrediente": "Feijao preto", "quantidade": 0.12}]
                  quantidade sempre na unidade_base da despensa (kg, L, un)
    requisitos:   [{"categoria": "utensilio", "item": "panela de pressao"}]
                  o que a receita exige da cozinha — alimenta o gate

    Ingrediente que a despensa nao tem NAO e descartado: entra como item de
    estoque zero e custo desconhecido, e volta em `fora_da_despensa`. O
    prato_checar vai cobrar o preco dele antes de deixar o prato passar.
    Confira `total_ingredientes` contra a receita que voce leu.
    """
    try:
        r = repo.prato_salvar(
            nome,
            fonte or None,
            [i.model_dump() for i in ingredientes],
            [q.model_dump() for q in requisitos or []],
        )
    except ValueError as recusa:
        raise Recusa(str(recusa)) from None
    return repo.json_seguro(_com_passo(r["id"], r))


@mcp.tool
def prato_checar(prato_id: int) -> dict:
    """O gate: da para a Dona Maria fazer este prato hoje?

    Devolve `apto` e, quando falso, a lista de pendencias — cada uma ja com
    a PERGUNTA pronta. Quatro coisas travam um prato: utensilio/tecnica nao
    confirmado, unidade que nao da para converter, ingrediente em falta sem
    preco, e compras que estouram o orcamento.
    """
    resultado = _checar(prato_id)
    # Cada pendencia leva a chamada que a resolve. Devolver so a pergunta ja
    # provou nao bastar: o agente leu a pendencia da panela de pressao, disse
    # "agora que sabemos que ela tem" e seguiu sem gravar nada.
    # A pendencia serializada usa `item`, nao `chave` — o nome do campo muda na
    # fronteira. Escrevi `chave` aqui e o KeyError derrubava TODA chamada de
    # prato_checar, transformando o gate inteiro em erro. Passou despercebido
    # porque o agente perguntava a coisa certa mesmo assim: ele lia a recusa,
    # que continha a pergunta, e seguia. O rastro so denunciou quando a fase
    # `recusa` passou a ser gravavel.
    for p in resultado.get("pendencias", []):
        alvo = p.get("item", "")
        if p["tipo"] == "estoque":
            p["como_gravar"] = (
                f'ingrediente_preco(ingrediente="{alvo}", preco_pago=<R$>, '
                'quantidade=<quanto ela compra>, unidade="<embalagem, ex: kg ou pacote 500g>")'
            )
        elif p["tipo"] in ("utensilio", "tecnica", "restricao"):
            p["como_gravar"] = (
                f'perfil_gravar(categoria="{p["tipo"]}", item="{alvo}", '
                "resposta=<o que ela respondeu>)"
            )
    return repo.json_seguro(_com_passo(prato_id, resultado))


def _checar(prato_id: int) -> dict:
    prato = repo.prato(prato_id)
    if prato is None:
        raise ValueError(f"prato {prato_id} nao encontrado")

    itens = repo.prato_ingredientes(prato_id)
    receita = [
        ItemReceita(i["ingrediente"], Decimal(str(i["quantidade"])), i["unidade_base"] or "un")
        for i in itens
    ]
    estoque = [
        ItemDespensa(
            i["ingrediente"],
            i["unidade_base"],
            Decimal(str(i["disponivel"])),
            Decimal(str(i["custo_unitario"])) if i["custo_unitario"] is not None else None,
        )
        for i in itens
    ]
    requisitos = [
        RequisitoPerfil(r.get("categoria", "utensilio"), r["item"])
        for r in (prato.get("requisitos") or [])
    ]
    perfil = [
        FatoPerfil(f["categoria"], f["item"], f["resposta"], f["status"])
        for f in repo.perfil()
    ]
    restante = Decimal(str(repo.orcamento()["restante"]))

    v = avaliar(receita, estoque, requisitos, perfil, restante)
    return {
        "prato": prato["nome"],
        "apto": v.apto,
        "pendencias": [
            {"tipo": p.tipo, "item": p.chave, "pergunta": p.pergunta, "detalhe": p.detalhe}
            for p in v.pendencias
        ],
        "perguntas": list(v.perguntas()),
        "compras": [
            {
                "ingrediente": c.ingrediente,
                "quantidade": c.quantidade,
                "unidade": c.unidade_base,
                "custo": c.custo,
            }
            for c in v.compras
        ],
        "custo_compras": v.custo_compras,
        "orcamento_restante": restante,
    }


@mcp.tool
def prato_aceitar(prato_id: int, preco: float) -> dict:
    """Fecha o prato no cardapio ao preco escolhido pela Dona Maria.

    RECUSA se prato_checar apontar qualquer pendencia — a validacao roda
    aqui no servidor, nao no prompt. Tambem recusa preco abaixo do minimo,
    que daria prejuizo depois da taxa de 10%.
    """
    check = _checar(prato_id)
    if not check["apto"]:
        return repo.json_seguro(
            {
                "aceito": False,
                "motivo": "o prato ainda tem pendencia",
                "pendencias": check["pendencias"],
                "perguntas": check["perguntas"],
            }
        )

    detalhe = _cmv(prato_id)
    total = Decimal(str(detalhe["cmv"]))
    minimo = preco_minimo(total)
    escolhido = Decimal(str(preco))

    if escolhido < minimo:
        return repo.json_seguro(
            {
                "aceito": False,
                "motivo": f"R$ {escolhido:.2f} fica abaixo do minimo de R$ {minimo:.2f}",
                "cmv": total,
                "preco_minimo": minimo,
            }
        )

    linha = repo.prato_aceitar(prato_id, escolhido, total, check["compras"])
    return repo.json_seguro(
        {
            "aceito": True,
            "prato": linha["nome"],
            "cmv": total,
            "preco": escolhido,
            "lucro": lucro(escolhido, total),
        }
    )


@mcp.tool
def cardapio(apenas_aceitos: bool = False) -> list[dict]:
    """Todos os pratos e o estado de cada um.

    Por padrao inclui os `sugerido` — os que ja foram salvos e ainda nao
    passaram no gate ou nao tiveram preco escolhido. Lista vazia aqui
    significa que NENHUM prato foi salvo ainda, e nao que o cardapio esta
    fechado sem itens.

    `apenas_aceitos=True` devolve so o que ja esta fechado no cardapio.
    """
    return repo.json_seguro(repo.cardapio(apenas_aceitos))


# --------------------------------------------------------------------------- #
# Dinheiro
# --------------------------------------------------------------------------- #
def _cmv(prato_id: int) -> dict:
    itens = repo.prato_ingredientes(prato_id)
    r = calcular_cmv(
        ItemCusto(
            i["ingrediente"],
            Decimal(str(i["quantidade"])),
            Decimal(str(i["custo_unitario"])) if i["custo_unitario"] is not None else None,
            i["unidade_base"] or "",
        )
        for i in itens
    )
    return {
        "cmv": r.total,
        "completo": r.completo,
        "sem_custo": list(r.sem_custo),
        "linhas": [
            {
                "ingrediente": l.ingrediente,
                "quantidade": l.quantidade,
                "unidade": l.unidade_base,
                "custo_unitario": l.custo_unitario,
                "custo": l.custo,
            }
            for l in r.linhas
        ],
    }


@mcp.tool
def cmv(prato_id: int) -> dict:
    """CMV do prato, aberto por ingrediente.

    EXIGE o prato aprovado no gate: nao se precifica o que ainda nao se sabe
    se da para cozinhar.

    `completo=False` significa que algum ingrediente nao tem custo conhecido
    e o total esta SUBESTIMADO — veja `sem_custo` e pergunte a ela.
    """
    _exigir_apto("cmv", prato_id)
    return repo.json_seguro(_com_passo(prato_id, _cmv(prato_id)))


@mcp.tool
def cenarios(prato_id: int) -> dict:
    """Tres opcoes de preco, com a conta aberta.

    A taxa de 10% incide sobre a VENDA: ela recebe 0,90 x preco. Por isso o
    minimo e CMV/0,90, e nao CMV + 10%.

    EXIGE o prato aprovado no gate.

    Quem escolhe e a Dona Maria. Apresente as tres e deixe ela decidir.
    """
    _exigir_apto("cenarios", prato_id)
    total = Decimal(str(_cmv(prato_id)["cmv"]))
    minimo = preco_minimo(total)
    cs = montar_cenarios(
        total,
        [
            ("Volume", (total * 3).quantize(Decimal("1")) - Decimal("0.10")),
            ("Equilibrio", (total * 4).quantize(Decimal("1")) - Decimal("0.10")),
            ("Premium", (total * 5).quantize(Decimal("1")) - Decimal("0.10")),
        ],
    )
    return repo.json_seguro(
        {
            "cmv": total,
            "preco_minimo": minimo,
            "taxa_plataforma": "10% sobre a venda",
            "cenarios": [
                {
                    "rotulo": c.rotulo,
                    "preco": c.preco,
                    "taxa": c.taxa,
                    "recebe": c.recebe,
                    "lucro": c.lucro,
                    "margem_pct": c.margem,
                    "cmv_pct": c.cmv_pct,
                }
                for c in cs
            ],
        }
    )
