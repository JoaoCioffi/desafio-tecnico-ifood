"""Telemetria do proprio servidor: quem chamou o que, com que forma e quanto demorou.

Anel em memoria, nao tabela. Telemetria nao e coordenacao: as tabelas deste
projeto existem para dois agentes se entenderem, e a clareza delas e argumento
da entrega. Uma escrita por chamada de ferramenta sujaria isso para guardar o
que so interessa enquanto alguem esta olhando. O preco e conhecido: reiniciar o
MCP zera o historico.

O QUE E GRAVADO E A FORMA, NAO O CONTEUDO

Um payload vira `{5}`, uma lista vira `[7]`, uma string longa e cortada. E de
proposito: as saidas variam demais para caber num molde narrativo, e um painel
que tenta contar a historia ("propondo receita de frango...") vira leitura
diagonal. A forma cabe sempre no mesmo lugar, entao o olho compara linha com
linha em vez de ler cada uma.

Tambem e o que mantem o painel seguro de olhar em cima de qualquer conversa:
nada do que a Dona Maria escreve e ecoado, so a estrutura do que trafegou.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
#  Quem chamou
#
#  Inferido pelo NOME da ferramenta, nao pela identidade no transporte. Hoje e
#  exato porque os dois conjuntos sao disjuntos — 15 ferramentas dela, 3 dele,
#  zero em comum — e a lista vive no config de cada perfil, fora do alcance dos
#  modelos.
#
#  Se um dia uma ferramenta for compartilhada, esta coluna passa a mentir. O
#  caminho melhor e o session id do FastMCP, rotulado por primeira aparicao;
#  nao foi feito agora para nao inventar complexidade antes de existir o caso.
# --------------------------------------------------------------------------- #
FERRAMENTAS_DO_CLIENTE = frozenset({
    "consultar_cardapio_publico",
    "fazer_pedido",
    "consultar_pedido",
})

# Resposta de ferramenta que carrega uma recusa do dominio. Nao e erro — o
# servidor respondeu certo — mas e o momento mais interessante do painel, entao
# ganha marca propria. As chaves sao as que as nossas ferramentas devolvem.
_CHAVES_DE_RECUSA = ("publicado", "aceito", "pedido_feito", "retirado", "encontrado")

_LIMITE_TEXTO = 36


def forma(valor: Any) -> str:
    """Como o valor APARECE. Nunca o que ele diz.

    >>> forma({"a": 1, "b": 2})
    '{2}'
    >>> forma([1, 2, 3])
    '[3]'
    >>> forma(True), forma(None), forma(4)
    ('true', 'null', '4')
    >>> forma("Frango ao molho branco")
    '"Frango ao molho branco"'
    >>> forma("x" * 50)
    '"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx…"'
    """
    if isinstance(valor, bool):          # antes de int: bool E int em Python
        return "true" if valor else "false"
    if valor is None:
        return "null"
    if isinstance(valor, dict):
        return "{%d}" % len(valor)
    if isinstance(valor, (list, tuple)):
        return "[%d]" % len(valor)
    if isinstance(valor, str):
        corte = valor[:_LIMITE_TEXTO]
        return f'"{corte}…"' if len(valor) > _LIMITE_TEXTO else f'"{valor}"'
    return str(valor)


def campos(valor: Any) -> list[tuple[str, str]]:
    """Um nivel de profundidade, para o bloco ULTIMA. Nada alem disso.

    Descer mais transformaria o painel num visualizador de JSON, e o que se quer
    aqui e a silhueta da chamada.

    >>> campos({"nome": "Feijoada", "itens": [1, 2]})
    [('nome', '"Feijoada"'), ('itens', '[2]')]
    >>> campos([1, 2, 3])
    [('', '[3]')]
    """
    if isinstance(valor, dict):
        return [(str(k), forma(v)) for k, v in valor.items()]
    return [("", forma(valor))]


@dataclass(frozen=True)
class Evento:
    seq: int
    # Dois relogios, porque servem a coisas diferentes. `ts` e de parede e so
    # existe para imprimir a hora. `mono` e monotonico e e o unico valido para
    # "ha quanto tempo": o relogio de parede da VM do Docker Desktop volta atras
    # quando o NTP corrige depois de uma suspensao do host, e a conta com ele
    # devolvia uptime NEGATIVO — coisa que parece bug do painel, nao do relogio.
    # `ts` e DERIVADO, nao lido na hora: sai da ancora do anel mais o delta
    # monotonico. Carimbar `time.time()` por evento parece obvio e produz
    # historico fora de ordem — o relogio da VM do Docker Desktop anda para
    # tras quando o NTP corrige, e o painel mostrava o gate ANTES das chamadas
    # que o dispararam, com `seq` maior. Ancorado, a linha do tempo so avanca.
    ts: float
    mono: float
    quem: str
    tool: str
    ms: float
    estado: str
    entrada: str
    saida: str
    bytes: int
    detalhe: str = ""
    campos_entrada: list = field(default_factory=list)
    campos_saida: list = field(default_factory=list)


class Anel:
    """Historico curto das chamadas, com o agregado por ferramenta.

    `deque` com maxlen: o descarte do mais antigo e do proprio tipo, entao nao
    ha poda para esquecer de escrever nem crescimento sem teto num servidor que
    fica dias de pe.
    """

    def __init__(self, tamanho: int = 300) -> None:
        self._eventos: deque[Evento] = deque(maxlen=tamanho)
        self._seq = 0
        self.subiu_em = time.monotonic()
        # Os dois relogios lidos no MESMO instante. Toda hora exibida depois
        # sai desta dupla, entao o painel e coerente consigo mesmo mesmo que o
        # relogio de parede pule no meio da execucao.
        self._ancora_parede = time.time()
        # Latencias por ferramenta, para p50/max e a curva. Tambem com teto:
        # a mediana dos ultimos 60 diz mais sobre agora do que a de sempre.
        self._latencias: dict[str, deque[float]] = {}
        self._erros: dict[str, int] = {}
        self._total = 0

    def anotar(self, **campos_do_evento) -> Evento:
        self._seq += 1
        self._total += 1
        agora = time.monotonic()
        evento = Evento(seq=self._seq, mono=agora,
                        ts=self._ancora_parede + (agora - self.subiu_em),
                        **campos_do_evento)
        self._eventos.append(evento)

        lat = self._latencias.setdefault(evento.tool, deque(maxlen=60))
        lat.append(evento.ms)
        if evento.estado == "erro":
            self._erros[evento.tool] = self._erros.get(evento.tool, 0) + 1
        return evento

    def desde(self, seq: int) -> list[dict]:
        return [asdict(e) for e in self._eventos if e.seq > seq]

    def agregado(self) -> list[dict]:
        """Uma linha por ferramenta, da mais chamada para a menos."""
        linhas = []
        for tool, lat in self._latencias.items():
            ordenado = sorted(lat)
            meio = ordenado[len(ordenado) // 2] if ordenado else 0.0
            linhas.append({
                "tool": tool,
                "n": len(lat),
                "p50": meio,
                "max": max(lat) if lat else 0.0,
                "erros": self._erros.get(tool, 0),
                "latencias": list(lat),
            })
        return sorted(linhas, key=lambda x: -x["n"])

    def resumo(self) -> dict:
        erros = sum(self._erros.values())
        ultimo = self._eventos[-1] if self._eventos else None
        gate = [e for e in self._eventos if e.quem == "gate"]
        agora = time.monotonic()
        return {
            "uptime": agora - self.subiu_em,
            "chamadas": self._total,
            "erros": erros,
            "ultima_ha": (agora - ultimo.mono) if ultimo else None,
            "seq": self._seq,
            "gate_total": len(gate),
            "gate_bloqueios": sum(1 for e in gate if e.estado == "BLOQUEIA"),
        }


ANEL = Anel()


def _tamanho(valor: Any) -> int:
    """Bytes que a resposta ocuparia serializada. Aproximado de proposito: o
    numero serve para comparar chamadas entre si, nao para cobrar banda."""
    try:
        return len(json.dumps(valor, default=str, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _resultado_bruto(resultado: Any) -> Any:
    """O dicionario que a ferramenta devolveu, de dentro do ToolResult.

    O FastMCP embrulha a saida e o formato do embrulho ja mudou entre versoes.
    Como isto e telemetria, tentar e desistir e melhor que exigir: um painel
    que derruba a ferramenta que ele observa nao vale o que custa.
    """
    for atributo in ("structured_content", "data"):
        conteudo = getattr(resultado, atributo, None)
        if conteudo is not None:
            return conteudo
    return resultado


def registrar_gate(prato_id: int, apto: bool, pendencias: int, ms: float) -> None:
    """A decisao do hook, que entra pelo /gate e nao por ferramenta.

    E a linha mais importante do painel: e onde se ve a fronteira deterministica
    disparando, ao vivo, sem depender do que o modelo diz que fez.
    """
    ANEL.anotar(
        quem="gate", tool=f"GET /gate/{prato_id}", ms=ms,
        estado="ok" if apto else "BLOQUEIA",
        entrada="{1}", saida="{3}", bytes=0,
        detalhe="" if apto else f"pendencias {pendencias}",
        campos_entrada=[("prato_id", str(prato_id))],
        campos_saida=[("apto", forma(apto)), ("pendencias", f"[{pendencias}]")],
    )


def registrar_chamada(nome: str, entrada: dict | None, resultado, erro,
                      ms: float) -> None:
    """Anota uma chamada de ferramenta. Quem mede o tempo e quem chama.

    Esta funcao e stdlib pura, e o middleware que a invoca mora no `server.py`.
    A separacao nao e estetica: o `Middleware` do FastMCP so existe dentro da
    imagem do MCP, e este modulo precisa importar no host — e o que faz os
    doctests de `forma` e `campos` rodarem no `pytest` junto com o resto, em
    0,2s e sem Docker.
    """
    entrada = entrada or {}
    bruto = _resultado_bruto(resultado) if erro is None else None
    estado, detalhe = _classificar(bruto, erro)
    ANEL.anotar(
        quem="cliente" if nome in FERRAMENTAS_DO_CLIENTE else "maria",
        tool=nome, ms=ms, estado=estado,
        entrada=forma(entrada) if entrada else "0",
        saida=forma(bruto) if erro is None else "—",
        bytes=_tamanho(bruto) if erro is None else 0,
        detalhe=detalhe,
        campos_entrada=campos(entrada),
        campos_saida=campos(bruto) if erro is None else [],
    )


def _classificar(bruto: Any, erro: Exception | None) -> tuple[str, str]:
    """ok, RECUSA ou erro — e sao coisas diferentes.

    RECUSA e o dominio funcionando: a ferramenta respondeu, e a resposta foi
    "nao". Misturar com erro esconderia o momento mais interessante do painel
    atras da mesma marca de uma excecao.
    """
    if erro is not None:
        return "erro", f"{type(erro).__name__}: {erro}"[:60]
    if isinstance(bruto, dict):
        for chave in _CHAVES_DE_RECUSA:
            if bruto.get(chave) is False:
                return "RECUSA", str(bruto.get("motivo", ""))[:60]
    return "ok", ""
