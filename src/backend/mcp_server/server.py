"""Servidor MCP do Sabor da Maria.

    python -m mcp_server        # sobe em http://127.0.0.1:9000/mcp

Esta e a fronteira de determinismo. O LLM nao soma e nao grava: ele chama
uma ferramenta e recebe o resultado pronto.

A regra que sustenta o desafio esta em `prato_aceitar`: ela RECUSA enquanto
o gate apontar pendencia. Nao e instrucao de prompt — e uma funcao que
devolve erro. O modelo nao tem como pular.
"""

from __future__ import annotations

from decimal import Decimal

from fastmcp import FastMCP

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

    try:
        n = len(repo.despensa(limite=1))
        return JSONResponse({"status": "ok", "banco": "ok" if n else "vazio"})
    except Exception as erro:
        return JSONResponse(
            {"status": "degradado", "banco": "indisponivel", "erro": str(erro)[:200]},
            status_code=503,
        )


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
    """
    return repo.json_seguro(repo.perfil_pendente())


# --------------------------------------------------------------------------- #
# Cardapio
# --------------------------------------------------------------------------- #
@mcp.tool
def prato_salvar(
    nome: str,
    ingredientes: list[dict],
    fonte: str = "",
    requisitos: list[dict] | None = None,
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
    r = repo.prato_salvar(nome, fonte or None, ingredientes, requisitos or [])
    return repo.json_seguro(r)


@mcp.tool
def prato_checar(prato_id: int) -> dict:
    """O gate: da para a Dona Maria fazer este prato hoje?

    Devolve `apto` e, quando falso, a lista de pendencias — cada uma ja com
    a PERGUNTA pronta. Quatro coisas travam um prato: utensilio/tecnica nao
    confirmado, unidade que nao da para converter, ingrediente em falta sem
    preco, e compras que estouram o orcamento.
    """
    return repo.json_seguro(_checar(prato_id))


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
def cardapio() -> list[dict]:
    """Os pratos ja aceitos, com CMV e preco."""
    return repo.json_seguro(repo.cardapio())


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

    `completo=False` significa que algum ingrediente nao tem custo conhecido
    e o total esta SUBESTIMADO — veja `sem_custo` e pergunte a ela.
    """
    return repo.json_seguro(_cmv(prato_id))


@mcp.tool
def cenarios(prato_id: int) -> dict:
    """Tres opcoes de preco, com a conta aberta.

    A taxa de 10% incide sobre a VENDA: ela recebe 0,90 x preco. Por isso o
    minimo e CMV/0,90, e nao CMV + 10%.

    Quem escolhe e a Dona Maria. Apresente as tres e deixe ela decidir.
    """
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
