"""O gate: um prato so entra no cardapio se a Dona Maria conseguir produzi-lo.

    "O agente nao pode deixar ela comprar ingredientes e descobrir depois
     que nao consegue cozinhar."          -- enunciado, secao 2.2

Isso nao e uma instrucao de prompt: e uma funcao que devolve `apto=False` e
uma lista de perguntas. Quem chama (o MCP) recusa a gravacao enquanto houver
pendencia — o modelo nao tem como pular.

Quatro coisas podem travar um prato:

    utensilio/tecnica  ela nao tem, ou ainda nao foi perguntado
    unidade            a receita pede grama e a despensa so sabe 'un'
    estoque            falta ingrediente e nao da para comprar
    orcamento          as compras nao cabem nos R$ 80

Toda pendencia carrega a PERGUNTA pronta. O agente nao precisa inventar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from .unidades import UnidadeIncompativel, converter

__all__ = [
    "RequisitoPerfil",
    "FatoPerfil",
    "ItemDespensa",
    "ItemReceita",
    "Pendencia",
    "Compra",
    "Viabilidade",
    "possui",
    "avaliar",
]

_SIM = {"sim", "tem", "possui", "s", "true", "1"}
_NAO = {"nao", "não", "nao tem", "não tem", "n", "false", "0", "nenhum"}


def possui(resposta: str | None) -> bool | None:
    """Interpreta a resposta gravada no perfil.

    True = tem · False = nao tem · None = nao sabemos ainda.

    Respostas livres ("so 2 bocas", "uma panela pequena") contam como
    afirmativas: ela descreveu o que tem. So nega quem nega explicitamente.

    >>> possui("sim"), possui("nao tem"), possui(None)
    (True, False, None)
    >>> possui("so 2 bocas")
    True
    """
    if resposta is None:
        return None
    texto = resposta.strip().lower()
    if not texto:
        return None
    if texto in _NAO:
        return False
    if texto in _SIM:
        return True
    return True


# --------------------------------------------------------------------------- #
# Entradas
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RequisitoPerfil:
    """Algo que o prato exige da cozinha ou da cozinheira."""

    categoria: str  # utensilio | tecnica | restricao
    item: str
    obrigatorio: bool = True


@dataclass(frozen=True)
class FatoPerfil:
    """O que ja se sabe sobre ela. Espelha a tabela `perfil`."""

    categoria: str
    item: str
    resposta: str | None = None
    status: str = "pendente"  # pendente | confirmado


@dataclass(frozen=True)
class ItemDespensa:
    """Uma linha da despensa, ja com o comprometido descontado."""

    nome: str
    unidade_base: str | None
    disponivel: Decimal
    custo_unitario: Decimal | None


@dataclass(frozen=True)
class ItemReceita:
    """Um ingrediente como a RECEITA pede — pode estar em outra unidade."""

    ingrediente: str
    quantidade: Decimal
    unidade: str


# --------------------------------------------------------------------------- #
# Saidas
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Pendencia:
    tipo: str  # utensilio | tecnica | restricao | unidade | estoque | orcamento
    chave: str
    pergunta: str
    detalhe: str = ""


@dataclass(frozen=True)
class Compra:
    """O que falta comprar de um ingrediente."""

    ingrediente: str
    quantidade: Decimal
    unidade_base: str
    custo: Decimal


@dataclass(frozen=True)
class Viabilidade:
    apto: bool
    pendencias: tuple[Pendencia, ...] = ()
    compras: tuple[Compra, ...] = ()
    custo_compras: Decimal = Decimal("0")
    disponiveis: tuple[str, ...] = field(default=())

    def perguntas(self) -> tuple[str, ...]:
        """So as perguntas, na ordem — e o que o agente vai falar."""
        return tuple(p.pergunta for p in self.pendencias)


# --------------------------------------------------------------------------- #
def _checar_perfil(
    requisitos: Iterable[RequisitoPerfil],
    perfil: Iterable[FatoPerfil],
) -> list[Pendencia]:
    conhecido = {(f.categoria, f.item): f for f in perfil}
    pendencias: list[Pendencia] = []

    for req in requisitos:
        if not req.obrigatorio:
            continue
        fato = conhecido.get((req.categoria, req.item))

        if fato is None or fato.status != "confirmado":
            pendencias.append(
                Pendencia(
                    tipo=req.categoria,
                    chave=req.item,
                    pergunta=f"A senhora tem {req.item}?"
                    if req.categoria == "utensilio"
                    else f"A senhora sabe fazer {req.item}?",
                )
            )
            continue

        if possui(fato.resposta) is False:
            pendencias.append(
                Pendencia(
                    tipo=req.categoria,
                    chave=req.item,
                    pergunta=f"Este prato precisa de {req.item}, que a senhora nao tem. "
                    "Quer que eu procure outra receita?",
                    detalhe="impedimento confirmado",
                )
            )
    return pendencias


def _checar_ingredientes(
    receita: Iterable[ItemReceita],
    despensa: Iterable[ItemDespensa],
) -> tuple[list[Pendencia], list[Compra], list[str]]:
    estoque = {i.nome: i for i in despensa}
    pendencias: list[Pendencia] = []
    compras: list[Compra] = []
    tem: list[str] = []

    for item in receita:
        na_despensa = estoque.get(item.ingrediente)

        if na_despensa is None:
            pendencias.append(
                Pendencia(
                    tipo="estoque",
                    chave=item.ingrediente,
                    pergunta=f"{item.ingrediente} nao esta na despensa. "
                    f"Quanto custa {item.quantidade} {item.unidade} onde a senhora compra?",
                )
            )
            continue

        if not na_despensa.unidade_base:
            pendencias.append(
                Pendencia(
                    tipo="unidade",
                    chave=item.ingrediente,
                    pergunta=f"Quanto pesa/rende uma unidade de {item.ingrediente}? "
                    f"A receita pede {item.quantidade} {item.unidade}.",
                )
            )
            continue

        try:
            precisa = converter(item.quantidade, item.unidade, na_despensa.unidade_base)
        except UnidadeIncompativel:
            pendencias.append(
                Pendencia(
                    tipo="unidade",
                    chave=item.ingrediente,
                    pergunta=f"A receita pede {item.quantidade} {item.unidade} de "
                    f"{item.ingrediente}, mas a despensa registra em "
                    f"'{na_despensa.unidade_base}'. Quanto isso da em "
                    f"{na_despensa.unidade_base}?",
                    detalhe=f"{item.unidade} -> {na_despensa.unidade_base}",
                )
            )
            continue

        falta = precisa - na_despensa.disponivel
        if falta <= 0:
            tem.append(item.ingrediente)
            continue

        if na_despensa.custo_unitario is None:
            pendencias.append(
                Pendencia(
                    tipo="estoque",
                    chave=item.ingrediente,
                    pergunta=f"Faltam {falta} {na_despensa.unidade_base} de "
                    f"{item.ingrediente} e nao sei o preco. Quanto custa?",
                )
            )
            continue

        compras.append(
            Compra(
                ingrediente=item.ingrediente,
                quantidade=falta,
                unidade_base=na_despensa.unidade_base,
                custo=falta * na_despensa.custo_unitario,
            )
        )

    return pendencias, compras, tem


def avaliar(
    receita: Sequence[ItemReceita],
    despensa: Sequence[ItemDespensa],
    requisitos: Sequence[RequisitoPerfil] = (),
    perfil: Sequence[FatoPerfil] = (),
    orcamento_restante: Decimal = Decimal("0"),
) -> Viabilidade:
    """Responde: da para fazer este prato hoje?

    Um prato viavel, tudo na despensa:

    >>> despensa = [ItemDespensa("Feijao preto", "kg", Decimal("1"), Decimal("9.60"))]
    >>> receita = [ItemReceita("Feijao preto", Decimal("120"), "g")]
    >>> avaliar(receita, despensa).apto
    True

    Falta utensilio que ninguem perguntou ainda:

    >>> r = avaliar(receita, despensa,
    ...             requisitos=[RequisitoPerfil("utensilio", "panela de pressao")])
    >>> r.apto
    False
    >>> r.perguntas()
    ('A senhora tem panela de pressao?',)

    A receita pede grama, a despensa so sabe unidade — vira pergunta,
    nao chute:

    >>> choc = [ItemDespensa("Cobertura de chocolate", "un", Decimal("1"), Decimal("79.90"))]
    >>> r = avaliar([ItemReceita("Cobertura de chocolate", Decimal("80"), "g")], choc)
    >>> r.pendencias[0].tipo
    'unidade'

    Falta ingrediente e a compra nao cabe no orcamento:

    >>> pouco = [ItemDespensa("Bacon", "kg", Decimal("0"), Decimal("23.90"))]
    >>> r = avaliar([ItemReceita("Bacon", Decimal("2"), "kg")], pouco,
    ...             orcamento_restante=Decimal("10.00"))
    >>> r.apto, r.custo_compras
    (False, Decimal('47.80'))
    >>> r.pendencias[0].tipo
    'orcamento'
    """
    pendencias = _checar_perfil(requisitos, perfil)
    p_ing, compras, tem = _checar_ingredientes(receita, despensa)
    pendencias += p_ing

    custo = sum((c.custo for c in compras), Decimal("0"))
    if custo > orcamento_restante:
        pendencias.append(
            Pendencia(
                tipo="orcamento",
                chave="complementos",
                pergunta=f"As compras deste prato dao R$ {custo:.2f}, mas restam "
                f"R$ {orcamento_restante:.2f} do orcamento. Quer reduzir a "
                "porcao ou trocar algum ingrediente?",
                detalhe=f"faltam R$ {custo - orcamento_restante:.2f}",
            )
        )

    return Viabilidade(
        apto=not pendencias,
        pendencias=tuple(pendencias),
        compras=tuple(compras),
        custo_compras=custo,
        disponiveis=tuple(tem),
    )
