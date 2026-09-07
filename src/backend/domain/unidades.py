"""Conversao de unidade.

A planilha da Dona Maria mistura unidade de MEDIDA com unidade de EMBALAGEM:

    kg          1 kg                    medida pura
    un          1 unidade               medida pura (ovos)
    balde 2kg   1 embalagem = 2 kg      o numero esta escondido no texto
    un 500ml    1 embalagem = 0,5 L

Sem extrair esse numero, `preco_pago / qtd_comprada` devolve R$ por EMBALAGEM.
O balde de alcaparras sairia a R$ 82,00 em vez de R$ 41,00/kg — o dobro.

Este modulo e a unica fonte dessa regra: o ETL usa na carga, o MCP usa quando
precisa casar a unidade de uma receita com a unidade da despensa.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import NamedTuple

__all__ = ["Unidade", "normalizar", "converter", "UnidadeIncompativel"]


class Unidade(NamedTuple):
    """Resultado da normalizacao.

    base   unidade de medida real: 'kg', 'L' ou 'un'
    fator  quanto de `base` cabe em 1 unidade da planilha

    Ambos None quando o texto nao permite extrair a conversao.
    """

    base: str | None
    fator: Decimal | None

    @property
    def conhecida(self) -> bool:
        return self.base is not None and self.fator is not None


# Unidades de medida que reconhecemos, e quanto valem na base canonica.
_BASE: dict[str, tuple[str, Decimal]] = {
    "kg": ("kg", Decimal("1")),
    "g": ("kg", Decimal("0.001")),
    "l": ("L", Decimal("1")),
    "ml": ("L", Decimal("0.001")),
    "un": ("un", Decimal("1")),
}

# Captura "2kg", "500 ml", "400g" dentro de um texto qualquer.
_QTD_NO_TEXTO = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml)\b", re.IGNORECASE)


def normalizar(unidade: str) -> Unidade:
    """Texto da planilha -> (base, fator).

    >>> normalizar("kg")
    Unidade(base='kg', fator=Decimal('1'))
    >>> normalizar("balde 2kg")
    Unidade(base='kg', fator=Decimal('2'))
    >>> normalizar("un 500ml").fator
    Decimal('0.500')
    >>> normalizar("caixa").conhecida
    False
    """
    texto = (unidade or "").strip()
    if not texto:
        return Unidade(None, None)

    direto = _BASE.get(texto.lower())
    if direto:
        return Unidade(*direto)

    achado = _QTD_NO_TEXTO.search(texto)
    if achado:
        valor = Decimal(achado.group(1).replace(",", "."))
        base, mult = _BASE[achado.group(2).lower()]
        return Unidade(base, valor * mult)

    return Unidade(None, None)


class UnidadeIncompativel(Exception):
    """A receita pede numa unidade que nao da para derivar da despensa.

    Nao e erro de programa: e o gatilho da elicitacao. O agente pergunta
    em vez de chutar.
    """

    def __init__(self, de: str, para: str) -> None:
        self.de = de
        self.para = para
        super().__init__(f"nao sei converter '{de}' para '{para}'")


def converter(quantidade: Decimal, de: str, para: str) -> Decimal:
    """Converte uma quantidade entre unidades da MESMA base.

    >>> converter(Decimal("500"), "g", "kg")
    Decimal('0.500')

    Levanta UnidadeIncompativel quando as bases diferem — inclusive no caso
    que interessa: receita pede grama, despensa so sabe 'un'.

    >>> try:
    ...     converter(Decimal("80"), "g", "un")
    ... except UnidadeIncompativel as erro:
    ...     print(erro)
    nao sei converter 'g' para 'un'
    """
    origem = normalizar(de)
    destino = normalizar(para)

    if not origem.conhecida or not destino.conhecida or origem.base != destino.base:
        raise UnidadeIncompativel(de, para)

    return quantidade * origem.fator / destino.fator
