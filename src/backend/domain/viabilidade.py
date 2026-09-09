"""O gate: um prato so entra no cardapio se a Dona Maria conseguir produzi-lo.

    "O agente nao pode deixar ela comprar ingredientes e descobrir depois
     que nao consegue cozinhar."          -- enunciado, secao 2.2

Isso nao e uma instrucao de prompt: e uma funcao que devolve `apto=False` e
uma lista de perguntas. Quem chama (o MCP) recusa a gravacao enquanto houver
pendencia — o modelo nao tem como pular.

Cinco coisas podem travar um prato:

    utensilio/tecnica  ela nao tem, ou ainda nao foi perguntado
    unidade            a receita pede grama e a despensa so sabe 'un'
    estoque            falta ingrediente e nao da para comprar
    porcao             a porcao nao tem peso de porcao — nem prato de verdade
                       (unidade errada), nem receita inteira (sem dividir)
    orcamento          as compras nao cabem nos R$ 80

Toda pendencia carrega a PERGUNTA pronta. O agente nao precisa inventar.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Sequence

from .unidades import UnidadeIncompativel, converter, normalizar

__all__ = [
    "PORCAO_MAXIMA",
    "achatar",
    "PORCAO_MINIMA",
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
    tipo: str  # utensilio | tecnica | restricao | unidade | estoque | porcao | orcamento
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
def achatar(texto: str) -> str:
    """Chave de comparacao: sem acento, sem caixa, sem espaco sobrando.

    O requisito do prato e a resposta dela sao digitados por partes diferentes
    do sistema, e nunca coincidem no acento. O gate ficou pedindo "panela de
    pressao" com o perfil ja gravado como "panela de pressão" — ela respondia,
    o dado entrava, e a pergunta voltava na rodada seguinte. Para sempre.

    >>> achatar("Panela de Pressão")
    'panela de pressao'
    >>> achatar("panela de pressao") == achatar("Panela de Pressão")
    True
    """
    plano = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return " ".join(plano.lower().split())


def _checar_perfil(
    requisitos: Iterable[RequisitoPerfil],
    perfil: Iterable[FatoPerfil],
) -> list[Pendencia]:
    conhecido = {(achatar(f.categoria), achatar(f.item)): f for f in perfil}
    pendencias: list[Pendencia] = []

    for req in requisitos:
        if not req.obrigatorio:
            continue
        fato = conhecido.get((achatar(req.categoria), achatar(req.item)))

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


# Uma porcao de prato principal pesa entre 300 g e 800 g. Os DOIS lados
# importam, e cada um pega um erro diferente:
#
#   abaixo de 100 g   unidade lida errada. A extracao ja devolveu 5 g de
#                     carne seca para uma feijoada, por ler "1 xicara" como
#                     0,005 kg
#   acima de 1,2 kg   a receita foi salva INTEIRA, sem dividir pelo rendimento.
#                     Aconteceu: 1,570 kg num prato, com CMV de R$ 34,18 —
#                     o custo da panela toda apresentado como custo de marmita
#
# Os dois distorcem o CMV, e o CMV vira preco de venda. Por isso e pendencia
# de gate, nao aviso que o agente pode ignorar.
#
# O TETO hoje pega bem menos do que pegava, e de proposito: "receita salva
# inteira" deixou de ser um palpite sobre o peso e virou uma divisao. O
# `avaliar` recebe o rendimento e compara o peso de UMA porcao; a panela de
# 1,570 kg que virou CMV de marmita nao passa mais por aqui, ela e dividida
# antes. O que sobra para o teto e o caso em que o proprio rendimento esta
# errado.
#
# O PISO desceu de 300 g para 150 g. 300 g era o peso de uma marmita de
# verdade — e era essa a suposicao errada: nem todo prato e marmita. Arroz-doce
# a 170 g por porcao e sobremesa, nao erro; frango ao molho branco a 180 g e o
# principal sem o acompanhamento. Os dois eram barrados por estarem certos.
#
# 150 g e onde os casos reais deste projeto se separam: pega o prato de 106 g
# com quinze ingredientes de 5 g cada (CMV a um quarto do real) e os 5 g de
# carne seca de "1 xicara" lida como 0,005 kg, sem barrar sobremesa. E menos
# guarda do que 300 g dava, e vale dizer: um prato entre 106 g e 150 g com
# unidade lida errada passaria. O limite anterior custava recusar prato certo,
# que e o erro pior — ele para a conversa e nao tem como o agente contornar.
#
# Estes numeros sao a UNICA fonte de verdade da faixa: quem mais precisar
# importa daqui em vez de repetir, porque duas copias divergem sozinhas.
_PORCAO_MINIMA = PORCAO_MINIMA = Decimal("0.150")
_PORCAO_MAXIMA = PORCAO_MAXIMA = Decimal("1.200")


def _peso_porcao(receita: Iterable[ItemReceita],
                 porcoes: int = 1) -> Decimal | None:
    """Quanto pesa UMA porcao, somando so o que da para medir em kg ou L.

    A receita que chega aqui e a INTEIRA — e o que `propor_prato` documenta e
    o que `pratos_ingredientes` guarda. Dividir pelo rendimento e o que torna
    o numero comparavel com o peso de uma marmita.

    Sem essa divisao a funcao mentia no proprio nome: devolvia o peso da
    panela chamando de porcao, e o gate barrava receita CORRETA de 8 porcoes
    perguntando qual era o rendimento — que ja estava gravado ao lado.

    None quando a receita e toda em 'un': nao ha peso para comparar.

    >>> _peso_porcao([ItemReceita("Feijao", Decimal("120"), "g"),
    ...               ItemReceita("Ovo", Decimal("2"), "un")])
    Decimal('0.120')
    >>> _peso_porcao([ItemReceita("Frango", Decimal("1.44"), "kg")], porcoes=8)
    Decimal('0.18')
    >>> _peso_porcao([ItemReceita("Ovo", Decimal("2"), "un")]) is None
    True
    """
    total, mediu = Decimal("0"), False
    for item in receita:
        base, fator = normalizar(item.unidade)
        if base in ("kg", "L") and fator:
            total += item.quantidade * fator
            mediu = True
    return total / max(1, porcoes) if mediu else None


def avaliar(
    receita: Sequence[ItemReceita],
    despensa: Sequence[ItemDespensa],
    requisitos: Sequence[RequisitoPerfil] = (),
    perfil: Sequence[FatoPerfil] = (),
    orcamento_restante: Decimal = Decimal("0"),
    porcoes: int = 1,
) -> Viabilidade:
    """Responde: da para fazer este prato hoje?

    Um prato viavel, tudo na despensa:

    >>> despensa = [ItemDespensa("Feijao preto", "kg", Decimal("1"), Decimal("9.60"))]
    >>> receita = [ItemReceita("Feijao preto", Decimal("450"), "g")]
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
    >>> r = avaliar([ItemReceita("Bacon", Decimal("0.5"), "kg")], pouco,
    ...             orcamento_restante=Decimal("2.00"))
    >>> r.apto, r.custo_compras
    (False, Decimal('11.950'))
    >>> r.pendencias[0].tipo
    'orcamento'

    A receita inteira pesa 45 g — leitura errada de unidade, nao marmita:

    >>> r = avaliar([ItemReceita("Feijao preto", Decimal("45"), "g")], despensa)
    >>> r.apto, r.pendencias[0].tipo
    (False, 'porcao')
    >>> "pouco para" in r.pendencias[0].pergunta
    True

    E o oposto: a receita salva sem dividir pelo rendimento. Sem `porcoes`,
    1,5 kg e o que uma pessoa recebe no prato:

    >>> r = avaliar([ItemReceita("Feijao preto", Decimal("1.5"), "kg")], despensa)
    >>> r.pendencias[0].tipo
    'porcao'
    >>> "receita inteira" in r.pendencias[0].pergunta
    True

    COM o rendimento, a mesma receita passa — 1,5 kg para 8 da 187 g por
    porcao, que e marmita. Este caso e regressao: o gate barrava a receita
    correta perguntando o rendimento que ja estava gravado ao lado.

    >>> farta = [ItemDespensa("Feijao preto", "kg", Decimal("3"), Decimal("9.60"))]
    >>> avaliar([ItemReceita("Feijao preto", Decimal("1.5"), "kg")],
    ...         farta, porcoes=8).apto
    True

    E uma sobremesa nao precisa pesar como marmita:

    >>> doce = [ItemDespensa("Leite integral", "L", Decimal("2"), Decimal("5.00"))]
    >>> avaliar([ItemReceita("Leite integral", Decimal("1.36"), "L")],
    ...         doce, porcoes=8).apto
    True
    """
    pendencias = _checar_perfil(requisitos, perfil)
    p_ing, compras, tem = _checar_ingredientes(receita, despensa)
    pendencias += p_ing

    peso = _peso_porcao(receita, porcoes)
    if peso is not None and not (_PORCAO_MINIMA <= peso <= _PORCAO_MAXIMA):
        if peso < _PORCAO_MINIMA:
            pergunta = (
                f"Somando tudo, uma porcao daria {peso * 1000:.0f} g — e pouco para "
                "uma marmita. Quanto a senhora serve por porcao?"
            )
        else:
            # Quantas porcoes o peso sugere, para a pergunta trazer um numero
            # em vez de devolver o problema em branco.
            rende = int(peso * porcoes / Decimal("0.5")) or 2
            pergunta = (
                f"Somando tudo da {peso:.2f} kg — isso parece a receita inteira, nao "
                f"uma marmita (daria umas {rende} porcoes). Para quantas porcoes essa "
                "receita rende? Preciso dividir antes de calcular o custo de UMA."
            )
        pendencias.append(
            Pendencia(
                tipo="porcao",
                chave="peso",
                pergunta=pergunta,
                detalhe=f"{peso} kg somados, esperado entre "
                f"{_PORCAO_MINIMA} e {_PORCAO_MAXIMA} kg",
            )
        )

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
