"""Servidor MCP do Sabor da Maria.

Esta e a fronteira de determinismo. O LLM nao soma e nao converte unidade:
ele chama uma ferramenta e recebe o numero pronto.

Etapa 1 do plano — so leitura. As ferramentas de calculo, perfil e aceite
entram nas etapas seguintes.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from domain.precificacao import ItemCusto
from domain.precificacao import calcular_cmv as calcular
from domain.precificacao import montar_cenarios, preco_minimo, preco_por_alvo
from domain.unidades import UnidadeIncompativel, converter, normalizar
# Importado como MODULO, nao pelos nomes: `FatoPerfil` e `ItemReceita` tambem
# existem aqui como modelos pydantic das ferramentas, e a definicao local
# sombreava silenciosamente o import do dominio — o erro so aparecia em
# runtime, como "BaseModel.__init__() takes 1 positional argument".
from domain import viabilidade as vb

from . import repo

mcp = FastMCP(
    "sabor-da-maria",
    instructions=(
        "Ferramentas da consultora de cardapio da Dona Maria. "
        "Use-as para QUALQUER numero: nunca calcule custo de cabeca e nunca "
        "converta unidade por conta propria. O custo que estas ferramentas "
        "devolvem ja esta normalizado por unidade de medida real."
    ),
)


# --------------------------------------------------------------------------- #
# Modelos
# --------------------------------------------------------------------------- #
class Ingrediente(BaseModel):
    """Um item da despensa com o custo ja normalizado.

    `custo_unitario` e sempre por `unidade_base` — R$/kg, R$/L ou R$/un. Nunca
    por embalagem. A alcaparra sai a R$ 41,00/kg, e nao a R$ 82,00/balde.
    """

    nome: str
    # Os tres campos sao nomeados sem ambiguidade de proposito. O campo se
    # chamava `estoque` e significava DISPONIVEL: o agente leu o nome, tratou
    # como total, e subtraiu o comprometido de novo — reportou "600 ml, 400
    # disponiveis" com 800 e 600 no banco. Descricao nao corrige nome errado.
    total: float = Field(description="Tudo que existe: planilha + compras")
    comprometido: float = Field(description="Reservado por pratos ja aceitos")
    disponivel: float = Field(
        description="total - comprometido. E ESTE que cabe num prato novo"
    )
    unidade_base: Literal["kg", "L", "un"]
    custo_unitario: float = Field(description="R$ por unidade_base")
    medida: Literal["massa", "volume", "contagem"]
    unidade_planilha: str = Field(
        description="Texto cru da planilha, para conferencia: 'kg', 'balde 2kg', 'un 500ml'"
    )
    aviso: str | None = Field(
        default=None,
        description="Preenchido quando o item nao pode responder pergunta em gramas",
    )


_MEDIDA = {"kg": "massa", "L": "volume", "un": "contagem"}

_AVISO_CONTAGEM = (
    "Vendido por unidade, nao por peso. A planilha nao diz quanto pesa uma "
    "unidade, entao NAO ha como responder 'quanto custam 80 g disto'. Se a "
    "receita pedir em gramas, pergunte a Dona Maria quanto pesa a embalagem "
    "antes de calcular qualquer coisa."
)


def _converter(linha: dict) -> Ingrediente:
    base = linha["unidade_base"]
    return Ingrediente(
        nome=linha["ingrediente"],
        total=float(linha.get("estoque_total") or 0),
        comprometido=float(linha.get("comprometido") or 0),
        disponivel=float(linha["estoque"] or 0),
        unidade_base=base,
        custo_unitario=float(linha["custo_unitario"] or 0),
        medida=_MEDIDA[base],
        unidade_planilha=linha["unidade_planilha"],
        aviso=_AVISO_CONTAGEM if base == "un" else None,
    )


# --------------------------------------------------------------------------- #
# Ferramentas
# --------------------------------------------------------------------------- #
@mcp.tool
def consultar_despensa(ingrediente: str | None = None) -> list[Ingrediente]:
    """Consulta o que a Dona Maria tem e quanto custa cada item.

    Use SEMPRE que precisar de custo de ingrediente. O `custo_unitario` que
    volta ja esta na unidade de medida real — multiplique pela quantidade da
    receita e pronto, sem nenhuma conversao adicional.

    Sao TRES quantidades e elas nao se somam nem se subtraem entre si: use
    `disponivel` para saber o que cabe num prato novo, e `total` quando ela
    perguntar quanto tem. Subtrair o comprometido do disponivel conta o mesmo
    consumo duas vezes.

    Um item com `medida: "contagem"` e contado, nao pesado: uma receita que
    peca gramas dele nao tem resposta na planilha. O campo `aviso` explica.

    Args:
        ingrediente: filtro por parte do nome, sem precisar de acento
            ("feijao" acha "Feijao carioquinha"). Omita para trazer tudo.
    """
    return [_converter(linha) for linha in repo.despensa(ingrediente)]


class ItemReceita(BaseModel):
    """Um ingrediente como a receita pede — na unidade da receita, nao da despensa."""

    ingrediente: str = Field(description="Nome, sem precisar de acento nem grafia exata")
    quantidade: float
    unidade: Literal["g", "kg", "ml", "L", "un"] = Field(
        description="A unidade da RECEITA. A conversao para a unidade da despensa e feita aqui."
    )
    custo_compra: float | None = Field(
        default=None,
        description=(
            "SO para ingrediente que NAO esta na despensa: quantos reais custa "
            "comprar esta quantidade. Pesquise o preco antes de propor — sem "
            "ele o orcamento de R$ 80 nao desce e a Dona Maria acha que tem "
            "dinheiro que ja gastou."
        ),
    )


class LinhaCusto(BaseModel):
    ingrediente: str
    usa: str = Field(description="Quantidade e unidade como a receita pediu")
    custo_unitario: str = Field(description="R$ por unidade base da despensa")
    custo: float = Field(description="R$ deste ingrediente no prato")


class ResultadoCMV(BaseModel):
    cmv: float = Field(description="Custo da RECEITA INTEIRA, somando todos os ingredientes")
    porcoes: int
    cmv_porcao: float = Field(description="cmv / porcoes — E ESTE que entra no preco")
    linhas: list[LinhaCusto]
    preco_minimo: float = Field(
        description="Por PORCAO. Abaixo disto a Dona Maria paga para trabalhar"
    )
    completo: bool = Field(description="False = o CMV esta SUBESTIMADO, ha pendencia abaixo")
    nao_encontrados: list[str] = Field(default_factory=list)
    sem_conversao: list[str] = Field(
        default_factory=list,
        description="Receita pediu em peso um item vendido por unidade. PERGUNTE o peso da embalagem.",
    )


@mcp.tool
def calcular_cmv(ingredientes: list[ItemReceita], porcoes: int = 1) -> ResultadoCMV:
    """Calcula o CMV de um prato e o preco minimo para nao dar prejuizo.

    Use SEMPRE esta ferramenta para somar custo de prato. Nunca some de
    cabeca, nunca use o terminal e NUNCA divida por porcao por fora: aqui a
    conversao de unidade, o arredondamento, a divisao por porcao e a taxa da
    plataforma seguem uma regra so, e o resultado vem com a memoria de
    calculo item a item para voce mostrar a ela.

    Quem se vende no delivery e a PORCAO, nao a panela. Use `cmv_porcao` e
    `preco_minimo` para falar de preco; `cmv` e o custo da receita inteira e
    so serve para conferencia.

    Quando `completo` for False o total esta SUBESTIMADO — nao apresente o
    numero como se fosse final. `sem_conversao` lista o que virou pergunta.

    Args:
        ingredientes: o que a receita pede, na unidade da receita.
        porcoes: quantas porcoes a receita rende. Se nao souber, PERGUNTE —
            um CMV de receita inteira apresentado como preco de porcao
            multiplicaria o preco pelo rendimento.
    """
    linhas: list[LinhaCusto] = []
    itens: list[ItemCusto] = []
    nao_encontrados: list[str] = []
    sem_conversao: list[str] = []

    for pedido in ingredientes:
        achados = repo.despensa(pedido.ingrediente)
        if not achados:
            nao_encontrados.append(pedido.ingrediente)
            continue

        # O primeiro resultado e o mais curto: "sal" nao pode casar em
        # "caldo de carne (tempero)" so por ordem alfabetica.
        linha = min(achados, key=lambda a: len(a["ingrediente"]))
        base = linha["unidade_base"]
        try:
            quantidade = converter(Decimal(str(pedido.quantidade)), pedido.unidade, base)
        except UnidadeIncompativel:
            # Nao e erro de programa: e o gatilho da elicitacao. A receita
            # pede grama de um item que a despensa so sabe contar.
            sem_conversao.append(
                f"{linha['ingrediente']}: a receita pede em {pedido.unidade}, mas a "
                f"despensa so sabe '{base}' ({linha['unidade_planilha']}). "
                f"Pergunte a Dona Maria quanto pesa uma unidade."
            )
            continue

        custo_unitario = Decimal(str(linha["custo_unitario"]))
        itens.append(ItemCusto(linha["ingrediente"], quantidade, custo_unitario, base))
        linhas.append(LinhaCusto(
            ingrediente=linha["ingrediente"],
            usa=f"{pedido.quantidade:g} {pedido.unidade}",
            custo_unitario=f"R$ {custo_unitario:.2f}/{base}",
            custo=float(quantidade * custo_unitario),
        ))

    resultado = calcular(itens)
    # A divisao acontece aqui e nao no agente: e a mesma regra de
    # arredondamento do resto, e um `execute_code` dividindo por fora fica
    # fora da auditoria e pode usar outro rendimento que o gravado no prato.
    por_porcao = (resultado.total / max(1, porcoes)).quantize(Decimal("0.01"))
    return ResultadoCMV(
        cmv=float(resultado.total),
        porcoes=max(1, porcoes),
        cmv_porcao=float(por_porcao),
        linhas=linhas,
        preco_minimo=float(preco_minimo(por_porcao)),
        completo=not (nao_encontrados or sem_conversao),
        nao_encontrados=nao_encontrados,
        sem_conversao=sem_conversao,
    )


class CenarioPreco(BaseModel):
    rotulo: str
    preco: float = Field(description="O que o cliente paga")
    taxa: float = Field(description="10% que fica com a plataforma")
    recebe: float = Field(description="O que chega na Dona Maria: 0,90 x preco")
    lucro: float = Field(description="recebe - CMV")
    comida_pct: float = Field(description="Quanto do preco e ingrediente")
    margem_pct: float = Field(description="Quanto do preco sobra para ela")


@mcp.tool
def cenarios_preco(cmv: float, alvos_comida: list[float] | None = None) -> dict:
    """Monta cenarios de preco a partir do CMV, com a conta aberta.

    APRESENTE OS CENARIOS ASSIM QUE TIVER O CMV. Nao espere levantar custo de
    embalagem, gas ou mao de obra: eles nao entram nesta conta e nao sao
    pre-requisito dela. Mostre os precos e diga, em uma linha, que a margem
    cobre comida e taxa mas ainda vai pagar essas outras coisas — o campo
    `cobertura` traz o texto pronto. Travar a resposta por causa de um custo
    que a conta nem usa deixa a Dona Maria sem o numero que ela pediu.

    Apresente os cenarios e DEIXE A DONA MARIA ESCOLHER. Voce pode dizer qual
    acha melhor e por que — nunca escolher por ela.

    A leitura que ela entende sem formula: o preco e uma pizza de 100%. A
    plataforma leva 10%, a comida leva `comida_pct`, e `margem_pct` fica com
    ela. As tres somam 100.

    Args:
        cmv: o `cmv_porcao` de `calcular_cmv` — o custo de UMA porcao.
            Passar o CMV da receita inteira aqui produziria precos varias
            vezes maiores que o correto.
        alvos_comida: fracoes do preco que a comida deve representar.
            Padrao [0.35, 0.30, 0.25] — quanto menor, mais caro o prato.
    """
    valor = Decimal(str(cmv))
    alvos = [Decimal(str(a)) for a in (alvos_comida or [0.35, 0.30, 0.25])]

    precos = [(f"comida em {a * 100:.0f}% do preco", preco_por_alvo(valor, a)) for a in alvos]
    cenarios = montar_cenarios(valor, precos)

    return {
        "cmv": float(valor),
        "preco_minimo": float(preco_minimo(valor)),
        "taxa_plataforma": "10% sobre a venda",
        # Vai no retorno, e nao so no docstring, porque e o que o agente deve
        # REPETIR a ela. Premissa que fica implicita vira margem otimista: ela
        # olha 60% e acha que sao 60% no bolso.
        "cobertura": (
            "Estes numeros cobrem ingredientes e a taxa da plataforma. Nao "
            "incluem embalagem, gas, energia nem o pagamento pelo trabalho "
            "dela — a margem mostrada ainda vai pagar essas coisas."
        ),
        "cenarios": [
            CenarioPreco(
                rotulo=c.rotulo, preco=float(c.preco), taxa=float(c.taxa),
                recebe=float(c.recebe), lucro=float(c.lucro),
                comida_pct=float(c.cmv_pct), margem_pct=float(c.margem),
            ).model_dump()
            for c in cenarios
        ],
    }


# --------------------------------------------------------------------------- #
# Perfil da cozinha
# --------------------------------------------------------------------------- #
Categoria = Literal["utensilio", "tecnica", "restricao", "preferencia"]


class FatoPerfil(BaseModel):
    """Uma coisa que a Dona Maria contou sobre a cozinha dela."""

    categoria: Categoria = Field(
        description=(
            "utensilio: equipamento da cozinha — fogao, forno, panela de pressao, "
            "air fryer, liquidificador, batedeira. "
            "tecnica: habilidade que ela domina ou nao — massa fresca, bechamel, "
            "ponto de carne, temperagem de chocolate. "
            "restricao: limite operacional — gas, energia, espaco na geladeira, "
            "tempo por cozinhada, quantas marmitas por vez. "
            "preferencia: o que ela gosta ou nao gosta de COZINHAR."
        )
    )
    item: str = Field(
        description=(
            "O assunto em duas ou tres palavras, no singular e generico: 'forno', "
            "'panela de pressao', 'bechamel'. Nunca amarre ao prato da vez — "
            "'forno' serve para todo prato que precisa de forno; "
            "'forno do bolo de chocolate' nao serve para nenhum."
        )
    )
    resposta: str | None = Field(
        default=None,
        description=(
            "O que ela disse, nas palavras dela: 'tem', 'nao tem', 'so 2 bocas'. "
            "Deixe vazio para registrar uma pergunta que voce fez e ela ainda nao respondeu."
        ),
    )


@mcp.tool
def registrar_perfil(fatos: list[FatoPerfil]) -> dict:
    """Grava o que a Dona Maria contou sobre a COZINHA dela.

    CHAME ASSIM QUE ELA CONTAR, na mesma resposta. A conversa nao guarda
    nada: quando a sessao termina, some tudo o que ela disse e voce vai
    perguntar de novo o que ela ja respondeu.

    O QUE VAI AQUI: so o que decide se ela CONSEGUE PRODUZIR um prato —
    equipamento, habilidade e limite operacional. Nada mais.

    O QUE NAO VAI, e onde cada coisa mora:

        receita, ingredientes, rendimento  ->  propor_prato
        preco escolhido por ela            ->  aceitar_prato
        custo de ingrediente               ->  ja esta na despensa
        o que ela pediu na conversa        ->  lugar nenhum, e contexto

    Gravar receita ou preco aqui polui o perfil e ENFRAQUECE O GATE: ele casa
    o requisito do prato ("forno") contra os fatos gravados, e quanto mais
    entulho, mais dificil o casamento. Uma linha "receita de bolo de
    chocolate" nao responde nenhuma pergunta sobre a cozinha dela.

    Uma frase costuma trazer varios fatos — "tenho fogao de 4 bocas e forno,
    mas nao tenho panela de pressao" sao tres. Mande os tres juntos.

    Registre tambem a PERGUNTA que voce acabou de fazer, com `resposta` vazia:
    isso marca o que ainda falta descobrir, e e o que impede de perguntar duas
    vezes a mesma coisa.
    """
    gravados = repo.perfil_gravar([f.model_dump() for f in fatos])
    return {
        "gravados": len(gravados),
        "fatos": gravados,
    }


@mcp.tool
def consultar_perfil(categoria: Categoria | None = None) -> dict:
    """O que ja se sabe sobre a cozinha da Dona Maria, e o que ainda falta.

    CONSULTE ANTES de perguntar qualquer coisa sobre equipamento, tecnica ou
    limite de producao — ela pode ja ter respondido numa conversa anterior, e
    repetir a pergunta passa a impressao de que ninguem anotou.

    Consulte tambem antes de sugerir um prato: uma receita que pede forno nao
    serve para quem nao tem forno, e descobrir isso depois de ela comprar
    ingrediente e exatamente o que nao pode acontecer.

    `pendentes` lista o que foi perguntado e continua sem resposta.
    """
    linhas = repo.perfil_listar(categoria)
    confirmados = [l for l in linhas if l["status"] == "confirmado"]
    pendentes = [l for l in linhas if l["status"] == "pendente"]
    return {
        "sabido": [
            {"categoria": l["categoria"], "item": l["item"], "resposta": l["resposta"]}
            for l in confirmados
        ],
        "pendentes": [{"categoria": l["categoria"], "item": l["item"]} for l in pendentes],
        "vazio": not linhas,
    }


# --------------------------------------------------------------------------- #
# Pratos e o gate
# --------------------------------------------------------------------------- #
class Requisito(BaseModel):
    """O que a receita exige da cozinha ou da cozinheira."""

    categoria: Literal["utensilio", "tecnica", "restricao"]
    item: str = Field(description="Curto e no singular: 'forno', 'panela de pressao', 'bechamel'")


def _avaliar(prato: dict):
    """Roda o gate contra o estado do banco. Uma fonte so para tool e hook."""
    despensa_atual = {d["ingrediente"]: d for d in repo.estoque()}

    # Item a comprar COM preco pesquisado entra na despensa como disponivel
    # zero e custo conhecido. Assim o `avaliar` o trata como compra — calcula
    # quanto sai do orcamento — em vez de barrar com "nao esta na despensa".
    #
    # Sem preco ele NAO entra, e continua barrando: e a pendencia certa, e a
    # pergunta que o proprio dominio ja escreve e melhor que a nossa.
    #
    # A unidade aqui e nominal. Para item fora da planilha nao ha unidade base
    # de verdade; o que importa e que `quantidade x custo_unitario` reproduza
    # exatamente o custo informado, e com os dois lados em 'un' isso vale.
    compraveis = {}
    for item in prato["itens"]:
        if item["comprar"] and item.get("custo_compra") is not None:
            quantidade = Decimal(str(item["quantidade"]))
            compraveis[item["ingrediente"]] = vb.ItemDespensa(
                nome=item["ingrediente"],
                unidade_base=item.get("unidade_base") or "un",
                disponivel=Decimal("0"),
                custo_unitario=Decimal(str(item["custo_compra"])) / quantidade,
            )

    # A quantidade ja foi convertida para a unidade base la na proposta, entao
    # aqui a unidade da "receita" E a unidade base — nao ha o que reconverter,
    # e declarar a mesma dos dois lados faz a conversao virar identidade.
    receita = [
        vb.ItemReceita(
            i["ingrediente"],
            Decimal(str(i["quantidade"])),
            (i.get("unidade_base") or "un") if i["ingrediente"] in compraveis
            else (despensa_atual.get(i["ingrediente"], {}).get("unidade_base") or "un"),
        )
        for i in prato["itens"]
    ]

    return vb.avaliar(
        receita=receita,
        despensa=[
            vb.ItemDespensa(d["ingrediente"], d["unidade_base"],
                            Decimal(str(d["disponivel"] or 0)),
                            Decimal(str(d["custo_unitario"])) if d["custo_unitario"] else None)
            for d in despensa_atual.values()
        ] + list(compraveis.values()),
        requisitos=[vb.RequisitoPerfil(r["categoria"], r["item"]) for r in prato["requisitos"]],
        perfil=[vb.FatoPerfil(f["categoria"], f["item"], f["resposta"], f["status"])
                for f in repo.perfil_listar()],
        orcamento_restante=Decimal(str(repo.orcamento()["restante"])),
        # O rendimento vem do BANCO, nao de um palpite sobre o peso. Sem ele o
        # dominio comparava a receita inteira contra o peso de uma marmita e
        # barrava prato correto — perguntando justamente o numero que estava
        # gravado na linha ao lado.
        porcoes=int(prato.get("porcoes") or 1),
    )


@mcp.tool
def propor_prato(nome: str, ingredientes: list[ItemReceita],
                 requisitos: list[Requisito], porcoes: int = 1,
                 fonte: str | None = None) -> dict:
    """Registra um prato candidato, com o que ele exige da cozinha.

    CHAME ANTES de comentar a receita com a Dona Maria. Os `requisitos` ficam
    gravados aqui e o gate os le do banco no momento do aceite — nao do que
    voce mandar depois. Uma lista de requisitos incompleta agora vira um prato
    aprovado sem checagem la na frente.

    Liste tudo que a receita exige mesmo que voce ache que ela tem: forno,
    panela de pressao, liquidificador, batedeira, tecnicas como massa fresca
    ou bechamel, e restricoes como tempo longo de cozimento.

    Args:
        nome: nome do prato, unico. Repropor o mesmo nome atualiza.
        ingredientes: o que a receita pede, na unidade da receita.
        requisitos: o que a cozinha precisa ter. Vazio so se nao exigir nada.
        porcoes: quantas porcoes a receita rende.
        fonte: URL da receita, quando veio da web.
    """
    itens: list[dict] = []
    sem_conversao: list[str] = []

    for pedido in ingredientes:
        achados = repo.despensa(pedido.ingrediente)
        if not achados:
            # Nao esta na despensa: entra como compra, na unidade da receita.
            # Normaliza mesmo fora da despensa: 200 ml viram 0,2 L. Sem isso a
            # quantidade entrava crua e a view somava numeros de unidades
            # diferentes no mesmo campo.
            base, fator = normalizar(pedido.unidade)
            itens.append({
                "ingrediente": pedido.ingrediente,
                "quantidade": Decimal(str(pedido.quantidade)) * (fator or Decimal("1")),
                "unidade_base": base or "un",
                "comprar": True,
                "custo_compra": (Decimal(str(pedido.custo_compra))
                                 if pedido.custo_compra is not None else None),
            })
            continue
        linha = min(achados, key=lambda a: len(a["ingrediente"]))
        base = linha["unidade_base"]
        try:
            quantidade = converter(Decimal(str(pedido.quantidade)), pedido.unidade, base)
        except UnidadeIncompativel:
            sem_conversao.append(
                f"{linha['ingrediente']}: receita em {pedido.unidade}, despensa em "
                f"'{base}'. Pergunte quanto pesa uma unidade antes de propor."
            )
            continue
        itens.append({"ingrediente": linha["ingrediente"], "quantidade": quantidade,
                      "unidade_base": base, "comprar": False, "custo_compra": None})

    prato = repo.prato_propor(nome, fonte, porcoes,
                              [r.model_dump() for r in requisitos], itens)
    viab = _avaliar(repo.prato_carregar(prato["id"]))
    return {
        "prato_id": prato["id"],
        "nome": prato["nome"],
        "status": prato["status"],
        "apto": viab.apto,
        "pendencias": [{"tipo": p.tipo, "pergunta": p.pergunta} for p in viab.pendencias],
        "sem_conversao": sem_conversao,
        "comprar": [{"ingrediente": c.ingrediente, "quantidade": float(c.quantidade),
                     "unidade": c.unidade_base, "custo": float(c.custo)} for c in viab.compras],
        "custo_compras": float(viab.custo_compras),
    }


@mcp.tool
def checar_prato(prato_id: int) -> dict:
    """Diz se um prato ja pode ser aceito, e o que falta se nao puder.

    Use isto para saber o que perguntar. Cada pendencia vem com a pergunta
    pronta — nao invente a sua.

    O aceite roda a MESMA checagem e recusa enquanto houver pendencia, entao
    tentar aceitar sem passar por aqui nao adianta.
    """
    prato = repo.prato_carregar(prato_id)
    if prato is None:
        return {"erro": f"prato {prato_id} nao existe"}
    viab = _avaliar(prato)
    return {
        "prato_id": prato_id,
        "nome": prato["nome"],
        "status": prato["status"],
        "apto": viab.apto,
        "pendencias": [{"tipo": p.tipo, "chave": p.chave, "pergunta": p.pergunta,
                        "detalhe": p.detalhe} for p in viab.pendencias],
        "comprar": [{"ingrediente": c.ingrediente, "quantidade": float(c.quantidade),
                     "unidade": c.unidade_base, "custo": float(c.custo)} for c in viab.compras],
        "custo_compras": float(viab.custo_compras),
    }


@mcp.tool
def aceitar_prato(prato_id: int, preco: float | None = None) -> dict:
    """Fecha um prato no cardapio. So depois que a Dona Maria aprovar o preco.

    A partir daqui o prato consome estoque e orcamento de verdade — as duas
    coisas passam a descontar sozinhas, e outros pratos deixam de contar com
    o que este ja comprometeu.

    A chamada RECUSA enquanto houver pendencia de viabilidade. Isso nao e uma
    instrucao que voce possa relevar: e uma checagem contra o banco, e ela
    roda de novo aqui mesmo que voce ja tenha usado `checar_prato`.

    Args:
        prato_id: o id devolvido por `propor_prato`.
        preco: o preco que a DONA MARIA escolheu. Nunca invente um.
    """
    prato = repo.prato_carregar(prato_id)
    if prato is None:
        return {"aceito": False, "motivo": f"prato {prato_id} nao existe"}

    viab = _avaliar(prato)
    if not viab.apto:
        return {
            "aceito": False,
            "motivo": "ha pendencia de viabilidade — pergunte antes de fechar",
            "pendencias": [{"tipo": p.tipo, "pergunta": p.pergunta} for p in viab.pendencias],
        }

    # Aceitar o prato compromete a compra: o que ele precisa e nao esta na
    # despensa entra em `compras`, some do orcamento e passa a existir no
    # estoque. Sem isto a compra vivia so como consumo do prato — a sobra
    # sumia e um segundo prato com o mesmo ingrediente voltava a dizer que
    # ele "nao esta na despensa", com a caixa na geladeira dela.
    ja_comprado = {c["ingrediente"] for c in repo.compras_listar(prato_id)}
    compradas = []
    for item in prato["itens"]:
        if not item["comprar"] or item.get("custo_compra") is None:
            continue
        if item["ingrediente"] in ja_comprado:
            continue  # reaceitar o mesmo prato nao compra duas vezes
        compradas.append(repo.compra_registrar(
            item["ingrediente"], item["quantidade"],
            item.get("unidade_base") or "un", item["custo_compra"], prato_id,
        ))

    marcado = repo.prato_marcar(prato_id, "aceito", prato.get("cmv"), preco)
    saldo = repo.orcamento()
    return {
        "aceito": True,
        "prato": marcado["nome"],
        "preco": float(marcado["preco"]) if marcado["preco"] else None,
        "comprou": [{"ingrediente": c["ingrediente"], "quantidade": float(c["quantidade"]),
                     "custo": float(c["custo_total"])} for c in compradas],
        "orcamento_restante": float(saldo["restante"]),
    }


@mcp.tool
def registrar_compra(ingrediente: str, quantidade: float,
                     unidade: Literal["g", "kg", "ml", "L", "un"],
                     custo_total: float) -> dict:
    """Registra um complemento que a Dona Maria comprou de fato.

    Use quando ela comprar MAIS do que a receita pede — "vou levar duas
    caixas" — ou quando comprar algo por conta propria. O aceite de um prato
    ja registra sozinho o que aquele prato precisa; esta ferramenta e para o
    excedente.

    O que entra aqui passa a existir na despensa e sai do orcamento. A sobra
    fica disponivel para o proximo prato, em vez de virar compra repetida.

    O retorno traz `estoque_apos_a_compra` com o TOTAL acumulado. Use esse
    numero ao contar para ela — nao some de cabeca com o que lembra da
    conversa: pode haver compra anterior que voce nao viu.

    Args:
        ingrediente: nome, como ela chama.
        quantidade: quanto ela comprou NO TOTAL, nao o que a receita usa.
        unidade: a unidade da compra.
        custo_total: quantos reais ela pagou pelo total.
    """
    try:
        base, fator = normalizar(unidade)
        qtd = Decimal(str(quantidade)) * (fator if fator else Decimal("1"))
    except Exception:
        base, qtd = "un", Decimal(str(quantidade))

    compra = repo.compra_registrar(ingrediente, qtd, base or "un",
                                   Decimal(str(custo_total)), None)
    saldo = repo.orcamento()

    # Devolve o ACUMULADO, nao so o que acabou de entrar. Com apenas o delta,
    # o agente somava de cabeca com o que lembrava da conversa e errava a
    # contagem — disse "duas caixas, 400 ml" com tres caixas e 600 ml no
    # banco. Numero conferido pelo agente e numero que ele nao viu.
    atual = repo.despensa(ingrediente)
    linha = min(atual, key=lambda a: len(a["ingrediente"])) if atual else None

    return {
        "comprou_agora": {
            "ingrediente": compra["ingrediente"],
            "quantidade": float(compra["quantidade"]),
            "unidade": compra["unidade_base"],
            "custo": float(compra["custo_total"]),
        },
        "estoque_apos_a_compra": {
            "total": float(linha["estoque_total"]),
            "comprometido": float(linha["comprometido"]),
            "disponivel": float(linha["estoque"]),
            "unidade": linha["unidade_base"],
        } if linha else None,
        "orcamento_restante": float(saldo["restante"]),
    }


@mcp.tool
def consultar_cardapio(status: Literal["sugerido", "aceito", "recusado"] | None = None) -> dict:
    """O cardapio da Dona Maria: o que ja fechou, o que esta em aberto.

    CONSULTE ANTES de propor prato novo. O orcamento e o estoque sao do
    CARDAPIO, nao de cada prato: tres receitas com frango dividem os mesmos
    2 kg, e tres compras de R$ 40 nao cabem nos R$ 80.

    Use tambem quando ela pedir um resumo — e a unica forma de responder
    "o que ja temos fechado?" sem depender do que sobrou na conversa.

    Args:
        status: filtre por 'aceito' para ver so o cardapio fechado. Omita
            para ver tambem o que esta sugerido e o que ela recusou.
    """
    pratos = repo.pratos_listar(status)
    saldo = repo.orcamento()

    def resumir(p: dict) -> dict:
        porcoes = p["porcoes"] or 1
        custo = Decimal(str(p["custo_ingredientes"] or 0))
        return {
            "prato_id": p["id"],
            "nome": p["nome"],
            "status": p["status"],
            "porcoes": porcoes,
            "cmv_porcao": float((custo / porcoes).quantize(Decimal("0.01"))),
            "preco": float(p["preco"]) if p["preco"] else None,
            "lucro_porcao": (
                float((Decimal(str(p["preco"])) * Decimal("0.9")
                       - custo / porcoes).quantize(Decimal("0.01")))
                if p["preco"] else None
            ),
            "fonte": p["fonte"],
        }

    itens = [resumir(p) for p in pratos]
    aceitos = [i for i in itens if i["status"] == "aceito"]
    return {
        "pratos": itens,
        "total_aceitos": len(aceitos),
        "orcamento": {
            "total": float(saldo["total"]),
            "gasto": float(saldo["gasto"]),
            "restante": float(saldo["restante"]),
        },
    }


@mcp.tool
def recusar_prato(prato_id: int, motivo: str | None = None) -> dict:
    """Tira um prato do cardapio. O estoque e o orcamento voltam sozinhos.

    Use quando a Dona Maria desistir. Nao ha estorno a fazer: o consumo e
    calculado a partir dos pratos ACEITOS, entao mudar o status ja devolve
    tudo.
    """
    marcado = repo.prato_marcar(prato_id, "recusado")
    if marcado is None:
        return {"recusado": False, "motivo": f"prato {prato_id} nao existe"}
    # Desfaz so as compras que o ACEITE deste prato gerou. Compra avulsa, que
    # ela mandou registrar por conta propria, fica: recusar o prato depois nao
    # devolve o dinheiro nem tira a caixa da geladeira dela.
    desfeitas = repo.compras_do_prato_apagar(prato_id)
    return {"recusado": True, "prato": marcado["nome"],
            "compras_desfeitas": desfeitas,
            "orcamento_restante": float(repo.orcamento()["restante"])}


# --------------------------------------------------------------------------- #
#  Rota do gate — nao e ferramenta MCP
# --------------------------------------------------------------------------- #
#  O hook `pre_tool_call` do Hermes roda dentro do container DELE, como
#  subprocesso, e precisa saber se um prato pode ser aceito. Falar MCP dali
#  exigiria implementar o handshake JSON-RPC em stdlib pura.
#
#  Esta rota devolve a mesma decisao em JSON simples: um GET, uma resposta.
#  O hook fica em trinta linhas de urllib, sem dependencia nenhuma.

# --------------------------------------------------------------------------- #
#  Venda
#
#  Daqui para baixo existe um SEGUNDO agente falando com este mesmo servidor:
#  o bot do cliente. Os dois nunca trocam mensagem — coordenam pelo banco.
#
#  A separacao entre o que cada um pode fazer e feita por `tools.include` no
#  config de cada perfil, fora do alcance dos dois modelos. Mas nao e so nisso
#  que se confia: as ferramentas do cliente nao TEM como violar as regras, mesmo
#  que alguem as exponha por engano. Vender exige linha em `vw_cardapio`, e o
#  preco vem de la, nao do parametro.
# --------------------------------------------------------------------------- #
class ItemCardapio(BaseModel):
    cardapio_id: int
    prato: str
    preco: float
    porcoes_disponiveis: int
    porcoes_vendidas: int


def _item_cardapio(linha: dict) -> ItemCardapio:
    return ItemCardapio(
        cardapio_id=linha["cardapio_id"],
        prato=linha["nome"],
        preco=float(linha["preco"]),
        porcoes_disponiveis=int(linha["porcoes_disponiveis"]),
        porcoes_vendidas=int(linha["porcoes_vendidas"]),
    )


# ---------------------------- lado da Dona Maria --------------------------- #
@mcp.tool
def publicar_prato(prato_id: int, preco: float, lotes: int = 1) -> dict:
    """Poe um prato ACEITO a venda, para o cliente poder pedir.

    So depois que ela decidir o preco. E o mesmo principio do aceite: o preco e
    dela, voce nunca inventa um.

    `lotes` e quantas vezes ela vai COZINHAR a receita, nao quantas porcoes ela
    quer vender. Uma receita que rende 4 porcoes, publicada com 3 lotes, oferece
    12 porcoes — e compromete tres vezes o ingrediente na despensa.

    A chamada RECUSA se a despensa nao aguentar os lotes pedidos, e a recusa vem
    com `lotes_possiveis` e o ingrediente que limita. Nao tente contornar
    republicando: ofereca o numero que cabe, ou proponha comprar o que falta.

    Republicar o mesmo prato ATUALIZA preco e lotes. Nao cria uma segunda
    oferta, e os pedidos ja feitos continuam valendo o preco que tinham.

    Args:
        prato_id: o id do prato, que precisa estar aceito.
        preco: o preco por porcao que a DONA MARIA escolheu.
        lotes: quantas vezes a receita sera feita.
    """
    if lotes < 1:
        return {"publicado": False, "motivo": "lotes precisa ser pelo menos 1"}

    linha = repo.cardapio_publicar(prato_id, preco, lotes)
    if linha is None:
        # A recusa ja aconteceu no banco. Aqui so se descobre QUAL das duas foi,
        # para a resposta dizer algo acionavel em vez de "nao deu".
        prato = repo.prato_carregar(prato_id)
        if prato is None:
            return {"publicado": False, "motivo": f"prato {prato_id} nao existe"}
        if prato["status"] != "aceito":
            return {"publicado": False,
                    "motivo": f"o prato esta '{prato['status']}' — so prato aceito vai ao ar"}

        cabe = repo.lotes_possiveis(prato_id) or {}
        possiveis = cabe.get("lotes") or 0
        return {
            "publicado": False,
            "motivo": f"a despensa nao aguenta {lotes} lotes",
            "lotes_possiveis": possiveis,
            "ingrediente_limitante": cabe.get("limitante"),
            "porcoes_possiveis": possiveis * int(prato["porcoes"] or 1),
            "sugestao": "publique menos lotes, ou compre mais do ingrediente "
                        "que limita antes de publicar",
        }

    no_ar = next((c for c in repo.cardapio_listar()
                  if c["cardapio_id"] == linha["id"]), None)
    return {
        "publicado": True,
        "prato": no_ar["nome"] if no_ar else None,
        "preco": float(linha["preco"]),
        "lotes": linha["lotes"],
        "porcoes_a_venda": int(no_ar["porcoes_disponiveis"]) if no_ar else None,
    }


@mcp.tool
def despublicar_prato(prato_id: int) -> dict:
    """Tira um prato do cardapio. Os pedidos ja feitos continuam valendo.

    O ingrediente que a publicacao comprometia volta a ficar disponivel para
    outros pratos. O que ja foi vendido nao volta: aquilo ela recebeu.
    """
    linha = repo.cardapio_retirar(prato_id)
    if linha is None:
        return {"retirado": False, "motivo": f"o prato {prato_id} nao estava no ar"}
    return {"retirado": True, "prato_id": prato_id, "preco_que_vigorava": float(linha["preco"])}


@mcp.tool
def consultar_pedidos(cliente: str | None = None) -> dict:
    """O que foi vendido, e quanto disso e dela depois da taxa.

    Use quando ela perguntar como estao as vendas, ou antes de sugerir uma
    compra nova — o saldo aqui ja soma o que entrou.

    `liquido` e o que sobra depois dos 10% da plataforma. E esse o numero que
    entra no caixa; o bruto nunca foi dela.
    """
    pedidos = repo.pedidos_listar(cliente)
    c = repo.caixa()
    return {
        "pedidos": [
            {"id": p["id"], "cliente": p["cliente"], "prato": p["prato"],
             "porcoes": p["porcoes"], "preco_unitario": float(p["preco_unitario"]),
             "bruto": float(p["valor_bruto"]), "taxa": float(p["taxa"]),
             "liquido": float(p["valor_liquido"]),
             "quando": p["criado_em"].isoformat()}
            for p in pedidos
        ],
        "caixa": {
            "orcamento_inicial": float(c["orcamento_inicial"]),
            "gasto_em_ingredientes": float(c["gasto"]),
            "vendas_brutas": float(c["vendas_brutas"]),
            "taxa_plataforma": float(c["taxa_plataforma"]),
            "receita_liquida": float(c["receita_liquida"]),
            "saldo": float(c["saldo"]),
        },
    }


# ------------------------------ lado do cliente ---------------------------- #
@mcp.tool
def consultar_cardapio_publico() -> dict:
    """O que a Dona Maria tem a venda agora.

    Esta e a UNICA fonte de pratos. Nao ha como pedir algo que nao esteja
    listado aqui, e nao adianta o cliente descrever um prato que ele viu em
    outro lugar — se nao esta nesta lista, ela nao esta vendendo.

    `porcoes_disponiveis` ja desconta o que outros clientes levaram.
    """
    itens = [_item_cardapio(c) for c in repo.cardapio_listar()
             if c["porcoes_disponiveis"] > 0]
    return {"itens": [i.model_dump() for i in itens],
            "total": len(itens),
            "vazio": not itens}


@mcp.tool
def fazer_pedido(cardapio_id: int, cliente: str, porcoes: int = 1) -> dict:
    """Compra porcoes de um prato do cardapio.

    Repare que nao ha parametro de preco: quem define quanto custa e a Dona
    Maria, na publicacao. O valor do pedido sai do cardapio.

    A chamada RECUSA em dois casos, e a recusa e do banco, nao uma checagem que
    voce possa contornar reformulando o pedido:

      - o prato nao esta publicado (ou saiu do ar)
      - nao restam porcoes suficientes

    Quando recusar, diga o que esta disponivel de verdade em vez de tentar de
    novo com outro numero.

    Args:
        cardapio_id: o id que veio de `consultar_cardapio_publico`.
        cliente: quem esta pedindo.
        porcoes: quantas porcoes.
    """
    if porcoes < 1:
        return {"pedido_feito": False, "motivo": "precisa pedir pelo menos uma porcao"}

    pedido = repo.pedido_registrar(cardapio_id, cliente.strip(), porcoes)
    if pedido is None:
        # Descobre QUAL das duas recusas foi, so para explicar. A decisao ja
        # foi tomada pelo banco; isto aqui e redacao da resposta.
        no_ar = next((c for c in repo.cardapio_listar()
                      if c["cardapio_id"] == cardapio_id), None)
        if no_ar is None:
            return {"pedido_feito": False,
                    "motivo": "este prato nao esta a venda",
                    "cardapio": consultar_cardapio_publico()["itens"]}
        return {"pedido_feito": False,
                "motivo": f"restam {no_ar['porcoes_disponiveis']} porcoes, "
                          f"e voce pediu {porcoes}",
                "porcoes_disponiveis": int(no_ar["porcoes_disponiveis"])}

    return {
        "pedido_feito": True,
        "pedido_id": pedido["id"],
        "prato": (repo.pedido_carregar(pedido["id"]) or {}).get("prato"),
        "porcoes": pedido["porcoes"],
        "preco_unitario": float(pedido["preco_unitario"]),
        "total": float(pedido["valor_bruto"]),
    }


@mcp.tool
def consultar_pedido(pedido_id: int) -> dict:
    """Detalhe de um pedido ja feito."""
    p = repo.pedido_carregar(pedido_id)
    if p is None:
        return {"encontrado": False, "motivo": f"pedido {pedido_id} nao existe"}
    return {
        "encontrado": True,
        "pedido_id": p["id"],
        "cliente": p["cliente"],
        "prato": p["prato"],
        "porcoes": p["porcoes"],
        "preco_unitario": float(p["preco_unitario"]),
        "total": float(p["valor_bruto"]),
        "quando": p["criado_em"].isoformat(),
    }


# --------------------------------------------------------------------------- #
@mcp.custom_route("/saude", methods=["GET"])
async def rota_saude(request):
    """Healthcheck: HTTP de pe e banco respondendo. Nada alem disso.

    Deliberadamente NAO consulta tabela nenhuma. A versao anterior sondava
    /gate/0, que le `pratos` — e num cluster recem-criado a tabela ainda nao
    existia, entao o container nunca ficava healthy e o Hermes, que depende
    dele, nunca subia. Saude de servico e "estou no ar", nao "o negocio ja
    esta modelado".
    """
    from starlette.responses import JSONResponse

    try:
        with repo._pool.connection() as conexao:
            conexao.execute("SELECT 1")
        return JSONResponse({"status": "ok"})
    except Exception as erro:
        return JSONResponse({"status": "sem banco", "erro": str(erro)[:200]},
                            status_code=503)


@mcp.custom_route("/gate/{prato_id}", methods=["GET"])
async def rota_gate(request):
    from starlette.responses import JSONResponse

    try:
        prato = repo.prato_carregar(int(request.path_params["prato_id"]))
    except (TypeError, ValueError):
        prato = None
    if prato is None:
        return JSONResponse({"apto": False, "motivo": "prato inexistente"})

    viab = _avaliar(prato)
    return JSONResponse({
        "apto": viab.apto,
        "prato": prato["nome"],
        "pendencias": [p.pergunta for p in viab.pendencias],
    })


@mcp.tool
def consultar_orcamento() -> dict:
    """Quanto a Dona Maria ainda tem para comprar ingredientes que faltam.

    O orcamento e do CARDAPIO inteiro, nao de um prato. Tres pratos pedindo
    R$ 40 de complemento cada nao cabem.
    """
    dados = repo.orcamento()
    return {
        "inicial": float(dados["total"]),
        "gasto": float(dados["gasto"]),
        "disponivel": float(dados["restante"]),
    }
