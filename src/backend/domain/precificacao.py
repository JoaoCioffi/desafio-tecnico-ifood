"""CMV e preco de venda.

Toda a matematica do desafio mora aqui. Funcoes puras: recebem numero,
devolvem numero. Sem banco, sem rede, sem LLM.

    CMV          somatorio de (quantidade usada x custo unitario)
    taxa         10% sobre a VENDA — a Dona Maria recebe 0,90 x P
    preco minimo P >= CMV / 0,90
    lucro        0,90 x P - CMV

A regra de arredondamento: precisao cheia durante a conta, arredonda uma
vez so no fim. Arredondar cada ingrediente antes de somar desviaria o total
em centavos.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Iterable, Sequence

__all__ = [
    "TAXA_PLATAFORMA",
    "ItemCusto",
    "LinhaCMV",
    "CMV",
    "Cenario",
    "calcular_cmv",
    "preco_minimo",
    "lucro",
    "montar_cenarios",
]

TAXA_PLATAFORMA = Decimal("0.10")

_CENTAVO = Decimal("0.01")


def _reais(valor: Decimal) -> Decimal:
    """Arredonda para centavos. Use so na fronteira, nunca no meio da conta."""
    return valor.quantize(_CENTAVO, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# CMV
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ItemCusto:
    """Um ingrediente dentro de um prato, ja na unidade base da despensa."""

    ingrediente: str
    quantidade: Decimal
    custo_unitario: Decimal | None  # None = a despensa nao sabe o custo
    unidade_base: str = ""


@dataclass(frozen=True)
class LinhaCMV:
    ingrediente: str
    quantidade: Decimal
    unidade_base: str
    custo_unitario: Decimal
    custo: Decimal


@dataclass(frozen=True)
class CMV:
    total: Decimal
    linhas: tuple[LinhaCMV, ...]
    sem_custo: tuple[str, ...]

    @property
    def completo(self) -> bool:
        """False quando algum ingrediente nao tem custo — o total esta subestimado."""
        return not self.sem_custo


def calcular_cmv(itens: Iterable[ItemCusto]) -> CMV:
    """Soma quantidade x custo_unitario de cada ingrediente.

    Ingrediente sem custo_unitario nao entra na soma e e devolvido em
    `sem_custo`. Nao inventamos valor: o agente pergunta.

    >>> cmv = calcular_cmv([
    ...     ItemCusto("Feijao preto", Decimal("0.120"), Decimal("9.60"), "kg"),
    ...     ItemCusto("Bacon",        Decimal("0.040"), Decimal("23.90"), "kg"),
    ... ])
    >>> cmv.total
    Decimal('2.11')
    >>> cmv.completo
    True
    """
    linhas: list[LinhaCMV] = []
    sem_custo: list[str] = []
    total = Decimal("0")

    for item in itens:
        if item.custo_unitario is None:
            sem_custo.append(item.ingrediente)
            continue
        custo = item.quantidade * item.custo_unitario
        total += custo
        linhas.append(
            LinhaCMV(
                ingrediente=item.ingrediente,
                quantidade=item.quantidade,
                unidade_base=item.unidade_base,
                custo_unitario=item.custo_unitario,
                custo=_reais(custo),
            )
        )

    return CMV(total=_reais(total), linhas=tuple(linhas), sem_custo=tuple(sem_custo))


# --------------------------------------------------------------------------- #
# Preco
# --------------------------------------------------------------------------- #
def preco_minimo(cmv: Decimal, taxa: Decimal = TAXA_PLATAFORMA) -> Decimal:
    """Abaixo disso ela paga para trabalhar.

    A taxa incide sobre a VENDA, nao sobre o lucro — por isso divide, nao soma.

    Arredonda para CIMA, nao para o mais proximo. Com CMV 7,12 a conta da
    7,9111...; cobrar 7,91 devolveria 0,90 x 7,91 = 7,119 — um decimo de
    centavo A MENOS que o CMV. Um minimo que nao cobre o custo nao e minimo.

    >>> preco_minimo(Decimal("7.12"))
    Decimal('7.92')
    >>> lucro(preco_minimo(Decimal("7.12")), Decimal("7.12")) >= 0
    True
    """
    return (cmv / (Decimal("1") - taxa)).quantize(_CENTAVO, rounding=ROUND_CEILING)


def lucro(preco: Decimal, cmv: Decimal, taxa: Decimal = TAXA_PLATAFORMA) -> Decimal:
    """O que sobra no bolso: 0,90 x P - CMV.

    >>> lucro(Decimal("26.90"), Decimal("7.12"))
    Decimal('17.09')
    """
    return _reais(preco * (Decimal("1") - taxa) - cmv)


@dataclass(frozen=True)
class Cenario:
    rotulo: str
    preco: Decimal
    taxa: Decimal
    recebe: Decimal
    cmv: Decimal
    lucro: Decimal
    margem: Decimal  # % do preco que sobra para ela
    cmv_pct: Decimal  # % do preco que e comida

    @property
    def viavel(self) -> bool:
        return self.lucro > 0


def montar_cenarios(
    cmv: Decimal,
    precos: Sequence[tuple[str, Decimal]],
    taxa: Decimal = TAXA_PLATAFORMA,
) -> tuple[Cenario, ...]:
    """Monta os cenarios de preco com a conta aberta.

    Quem escolhe o preco e a Dona Maria — a funcao so mostra o que cada
    opcao significa.

    >>> cs = montar_cenarios(Decimal("7.12"), [("Equilibrio", Decimal("26.90"))])
    >>> cs[0].recebe, cs[0].lucro, cs[0].margem
    (Decimal('24.21'), Decimal('17.09'), Decimal('63.5'))
    """
    cenarios = []
    for rotulo, preco in precos:
        recebe = preco * (Decimal("1") - taxa)
        resultado = recebe - cmv
        cenarios.append(
            Cenario(
                rotulo=rotulo,
                preco=_reais(preco),
                taxa=_reais(preco * taxa),
                recebe=_reais(recebe),
                cmv=_reais(cmv),
                lucro=_reais(resultado),
                margem=(resultado / preco * 100).quantize(Decimal("0.1")),
                cmv_pct=(cmv / preco * 100).quantize(Decimal("0.1")),
            )
        )
    return tuple(cenarios)
