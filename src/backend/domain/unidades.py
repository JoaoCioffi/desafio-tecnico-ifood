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


def consolidar(itens: list[dict]) -> tuple[list[dict], list[str]]:
    """Junta linhas repetidas do mesmo ingrediente, somando as quantidades.

    Receita longa repete ingrediente, e nao por engano: a lasanha leva parmesao
    no molho E para gratinar, o azeite entra na massa E para untar. Sao duas
    linhas legitimas do mesmo item.

    O banco discorda: `pratos_ingredientes` tem chave primaria
    (prato_id, ingrediente), e a segunda linha estourava com `duplicate key` —
    a ferramenta inteira falhava, e o que se via era o agente tentando de novo
    e escrevendo outra receita. Somar e o que uma cozinheira faz.

    A soma so vale entre linhas na MESMA unidade base. Bases diferentes para o
    mesmo nome nao viram um numero: viram aviso, pelo mesmo canal do resto do
    que nao da para converter. Somar 0,2 kg com 3 un daria um numero que parece
    certo e nao e — o erro mais caro que existe aqui.

    Preserva a ordem da primeira aparicao: a receita continua legivel na ordem
    em que foi escrita.

    >>> itens = [{"ingrediente": "Parmesao", "quantidade": Decimal("0.2"),
    ...           "unidade_base": "kg", "comprar": False, "custo_compra": None},
    ...          {"ingrediente": "Sal", "quantidade": Decimal("0.01"),
    ...           "unidade_base": "kg", "comprar": False, "custo_compra": None},
    ...          {"ingrediente": "Parmesao", "quantidade": Decimal("0.05"),
    ...           "unidade_base": "kg", "comprar": False, "custo_compra": None}]
    >>> juntos, avisos = consolidar(itens)
    >>> [(i["ingrediente"], str(i["quantidade"])) for i in juntos]
    [('Parmesao', '0.25'), ('Sal', '0.01')]
    >>> avisos
    []

    Comprar em qualquer linha faz o item consolidado ser compra, e os custos
    somam — sao duas partes do mesmo desembolso:

    >>> a = {"ingrediente": "Creme", "quantidade": Decimal("0.2"),
    ...      "unidade_base": "L", "comprar": True, "custo_compra": Decimal("2.98")}
    >>> b = {"ingrediente": "Creme", "quantidade": Decimal("0.1"),
    ...      "unidade_base": "L", "comprar": False, "custo_compra": None}
    >>> j, _ = consolidar([a, b])
    >>> j[0]["comprar"], str(j[0]["quantidade"]), str(j[0]["custo_compra"])
    (True, '0.3', '2.98')

    Unidades incompativeis nao somam — avisam:

    >>> j, avisos = consolidar([
    ...     {"ingrediente": "Ovo", "quantidade": Decimal("2"), "unidade_base": "un",
    ...      "comprar": False, "custo_compra": None},
    ...     {"ingrediente": "Ovo", "quantidade": Decimal("0.05"), "unidade_base": "kg",
    ...      "comprar": False, "custo_compra": None}])
    >>> len(j), len(avisos)
    (1, 1)
    >>> "Ovo" in avisos[0]
    True
    """
    juntos: dict[str, dict] = {}
    avisos: list[str] = []

    for item in itens:
        nome = item["ingrediente"]
        anterior = juntos.get(nome)
        if anterior is None:
            juntos[nome] = dict(item)
            continue

        if anterior["unidade_base"] != item["unidade_base"]:
            avisos.append(
                f"{nome}: a receita cita o mesmo ingrediente em "
                f"'{anterior['unidade_base']}' e em '{item['unidade_base']}'. "
                f"Usei so a primeira — confirme a quantidade total antes de seguir."
            )
            continue

        anterior["quantidade"] += item["quantidade"]
        anterior["comprar"] = anterior["comprar"] or item["comprar"]
        custo = item.get("custo_compra")
        if custo is not None:
            anterior["custo_compra"] = (anterior.get("custo_compra") or Decimal("0")) + custo

    return list(juntos.values()), avisos
