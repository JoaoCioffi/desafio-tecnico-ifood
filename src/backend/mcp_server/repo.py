"""Acesso ao Postgres.

Unica camada que fala SQL. O `domain/` continua puro; o `server.py` orquestra.

Toda escrita que muda o cardapio passa por transacao: ou grava tudo, ou nada.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from decimal import Decimal
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from domain.unidades import normalizar

DSN = os.environ.get(
    "SABOR_DSN",
    "host=localhost port=5432 dbname=sabor_da_maria user=admin password=admin",
)

_pool = ConnectionPool(DSN, min_size=1, max_size=4, open=False, kwargs={"row_factory": dict_row})


def abrir() -> None:
    _pool.open()
    _pool.wait(timeout=15)


def fechar() -> None:
    _pool.close()


# --------------------------------------------------------------------------- #
# Eventos
#
# A tabela `evento` existia desde o primeiro schema e ninguem escrevia nela.
# Sem isso a unica prova do que aconteceu numa rodada era a PROSA do agente —
# e a prosa dele ja disse "Ingredientes Salvos" com o banco vazio.
#
# Com eventos, o que aconteceu fica no banco: da para testar o fluxo contra
# fato em vez de contra narrativa, e o cockpit tem de onde ler estado.
# --------------------------------------------------------------------------- #
_AVISADO = False


def registrar(ferramenta: str, fase: str, mensagem: str, dados: Any = None) -> None:
    """Grava um evento. NUNCA levanta.

    Observabilidade que derruba a ferramenta observada e pior que nao ter
    observabilidade: o agente perderia uma chamada boa por causa do log.
    """
    global _AVISADO
    try:
        with _pool.connection() as conn:
            conn.execute(
                "INSERT INTO evento (ferramenta, fase, mensagem, dados)"
                " VALUES (%s, %s, %s, %s::jsonb)",
                (ferramenta, fase, mensagem[:500], json.dumps(dados, default=str)),
            )
    except Exception as erro:
        # Engolir e certo: log nao pode derrubar a ferramenta que observa. Mas
        # engolir em SILENCIO faz observador quebrado parecer sistema quieto —
        # uma fase fora do CHECK da tabela sumiu com todas as recusas, e so deu
        # para notar contando `inicio` contra `fim`. Avisa uma vez, no stderr.
        if not _AVISADO:
            _AVISADO = True
            print(f"[evento] gravacao falhando: {type(erro).__name__}: {erro}",
                  file=sys.stderr, flush=True)


def eventos(limite: int = 50) -> list[dict]:
    """Ultimos eventos, do mais novo para o mais velho."""
    return _ler(
        "SELECT id, momento, ferramenta, fase, mensagem, dados"
        "  FROM evento ORDER BY id DESC LIMIT %s",
        (limite,),
    )


def _ler(sql: str, params: tuple = ()) -> list[dict]:
    with _pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def _um(sql: str, params: tuple = ()) -> dict | None:
    linhas = _ler(sql, params)
    return linhas[0] if linhas else None


# --------------------------------------------------------------------------- #
# Ingredientes
# --------------------------------------------------------------------------- #
_ORDENS = {
    "nome": "nome ASC",
    "custo": "custo_unitario DESC NULLS LAST",
    "disponivel": "disponivel DESC",
}


def despensa(
    ingrediente: str | None = None,
    ordenar_por: str = "nome",
    limite: int | None = None,
) -> list[dict]:
    """Despensa com o comprometido pelos pratos aceitos ja descontado.

    Filtro e ordenacao acontecem no Postgres, nao no modelo. "Qual o
    ingrediente mais caro" e um ORDER BY, nao uma varredura de 37 linhas
    dentro do contexto do LLM — devolver a despensa inteira para responder
    sobre um item custa token e tempo em toda rodada.
    """
    onde, params = "", []
    if ingrediente:
        onde = "WHERE unaccent_lower(nome) LIKE unaccent_lower(%s)"
        params.append(f"%{ingrediente}%")

    ordem = _ORDENS.get(ordenar_por, _ORDENS["nome"])
    lim = ""
    if limite and limite > 0:
        lim = "LIMIT %s"
        params.append(int(limite))

    return _ler(
        f"""
        SELECT nome, unidade_base, unidade_planilha,
               estoque_total, comprometido, disponivel, custo_unitario
          FROM vw_estoque
          {onde}
         ORDER BY {ordem}
          {lim}
        """,
        tuple(params),
    )


def _chave(texto: str) -> str:
    """Normaliza para comparar nome de ingrediente: sem acento, minusculo."""
    sem_acento = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return " ".join(sem_acento.lower().split())


def casar_ingrediente(texto: str) -> str | None:
    """Casa o nome que a receita usa com o SKU da despensa.

    Tres passadas, da mais segura para a mais tolerante:

      1. igualdade normalizada (sem acento, minusculo)
      2. um nome contido no outro — 'cebola roxa' casa com 'Cebola'
      3. mesmas DUAS primeiras palavras

    A terceira existe porque a segunda nao bastava. A despensa tem "Arroz
    branco tipo 1" e a receita pediu "Arroz branco cozido": nenhum contem o
    outro, o casamento falhou e o prato ganhou um ingrediente novo, sem preco,
    ao lado de 5 kg de arroz parados no estoque.

    Duas palavras e o limiar de proposito. Uma so casaria "Carne seca" com
    "Carne de panela", que sao coisas diferentes e com precos diferentes —
    e errar para o lado do casamento e pior: entra silencioso no CMV.

    Devolve None quando nao ha correspondencia. O chamador transforma isso em
    pergunta, nao em chute.
    """
    alvo = _chave(texto)
    nomes = [linha["nome"] for linha in _ler("SELECT nome FROM ingredientes")]

    for nome in nomes:
        if _chave(nome) == alvo:
            return nome

    if candidatos := [n for n in nomes if _chave(n) in alvo or alvo in _chave(n)]:
        return min(candidatos, key=len)

    inicio = _chave(texto).split()[:2]
    if len(inicio) == 2:
        pelo_comeco = [n for n in nomes if _chave(n).split()[:2] == inicio]
        if pelo_comeco:
            return min(pelo_comeco, key=len)
    return None


# --------------------------------------------------------------------------- #
# Perfil
# --------------------------------------------------------------------------- #
def perfil() -> list[dict]:
    return _ler(
        "SELECT categoria, item, resposta, status, atualizado_em"
        "  FROM perfil ORDER BY categoria, item"
    )


def perfil_gravar(categoria: str, item: str, resposta: str) -> dict:
    with _pool.connection() as conn:
        linha = conn.execute(
            """
            INSERT INTO perfil (categoria, item, resposta, status)
            VALUES (%s, %s, %s, 'confirmado')
            ON CONFLICT (categoria, item) DO UPDATE
               SET resposta = EXCLUDED.resposta,
                   status = 'confirmado',
                   atualizado_em = now()
            RETURNING categoria, item, resposta, status
            """,
            (categoria, item, resposta),
        ).fetchone()
    return linha


def ingrediente_preco(
    ingrediente: str, preco_pago: Decimal, quantidade: Decimal, unidade: str
) -> dict:
    """Grava o que a Dona Maria paga por um ingrediente, e deriva o custo real.

    Esta e a resposta a pergunta que o gate faz quando falta ingrediente. Sem
    ela o fluxo nao fechava: o agente perguntava o preco e nao tinha onde por.

    Passa pela normalizacao de unidade de proposito — e o erro central da
    planilha original. "R$ 82,00 o balde de 2 kg" nao e R$ 82,00 o quilo:

    >>> from decimal import Decimal as D
    >>> base, fator = normalizar("balde 2kg")
    >>> D("82.00") / (D("1") * fator)
    Decimal('41.00')

    Gravar o preco cru sem o fator dobraria o CMV da alcaparra — e o preco de
    venda junto.
    """
    nome = str(ingrediente).strip()
    if not nome:
        raise ValueError("Falta o nome do ingrediente.")

    # Mesma regra de casamento do prato_salvar. Sem isto o preco caia numa
    # linha NOVA: a receita pedia "Farofa", ela respondeu sobre "farofa
    # pronta", e o banco ficou com as duas — o prato continuou sem custo e o
    # gate continuou pedindo o preco que ela ja tinha dado.
    #
    # Nome que entra no sistema passa pelo mesmo funil, seja por qual porta
    # for. Duas portas com regras diferentes criam duas verdades.
    if (sku := casar_ingrediente(nome)) is not None:
        nome = sku
    valor, qtd = _numero(preco_pago), _numero(quantidade)
    if valor is None or valor <= 0:
        raise ValueError(f"preco_pago precisa ser um numero maior que zero. Recebi: {preco_pago!r}")
    if qtd is None or qtd <= 0:
        raise ValueError(f"quantidade precisa ser um numero maior que zero. Recebi: {quantidade!r}")

    base, fator = normalizar(str(unidade))
    if base is None or fator is None:
        raise ValueError(
            f"Nao sei converter a unidade {unidade!r}.\n"
            "Use kg, g, L, ml, un — ou a embalagem com o peso dentro, "
            'como "pacote 500g" ou "balde 2kg".\n'
            "Se ela falou em unidade caseira, pergunte quanto pesa a embalagem."
        )

    with _pool.connection() as conn, conn.transaction():
        linha = conn.execute(
            """
            INSERT INTO ingredientes
                   (nome, unidade, estoque, qtd_comprada, preco_pago, unidade_base, fator)
            VALUES (%s, %s, 0, %s, %s, %s, %s)
            ON CONFLICT (nome) DO UPDATE
               SET unidade      = EXCLUDED.unidade,
                   qtd_comprada = EXCLUDED.qtd_comprada,
                   preco_pago   = EXCLUDED.preco_pago,
                   unidade_base = EXCLUDED.unidade_base,
                   fator        = EXCLUDED.fator
            RETURNING nome, unidade, estoque, qtd_comprada, preco_pago,
                      unidade_base, fator, custo_unitario
            """,
            (nome, str(unidade).strip(), qtd, valor, base, fator),
        ).fetchone()
    return linha


def perfil_pendente() -> list[dict]:
    """O que os pratos exigem e o perfil ainda nao confirma."""
    return _ler(
        """
        SELECT DISTINCT
               req->>'categoria' AS categoria,
               req->>'item'      AS item,
               p.nome            AS prato
          FROM pratos p
          CROSS JOIN LATERAL jsonb_array_elements(p.requisitos) AS req
          LEFT JOIN perfil f
                 -- unaccent_lower dos dois lados: o requisito vem do JSONB do
                 -- prato e a resposta vem da conversa, e os dois nunca
                 -- coincidem no acento. Comparar literal fazia a pergunta
                 -- voltar mesmo depois de respondida.
                 ON unaccent_lower(f.categoria) = unaccent_lower(req->>'categoria')
                AND unaccent_lower(f.item)      = unaccent_lower(req->>'item')
                AND f.status    = 'confirmado'
         WHERE p.status <> 'recusado'
           AND f.item IS NULL
         ORDER BY categoria, item
        """
    )


# --------------------------------------------------------------------------- #
# Pratos
# --------------------------------------------------------------------------- #
# Um LLM nao acerta o nome da chave toda vez, e o erro tem forma conhecida:
# ele copia a DESCRICAO do campo para o NOME dele. Pedir "nome e quantidade
# em kg por porcao" ja devolveu, literalmente:
#
#     {"nome": "Feijao preto", "quantidade em kg por porcao": 0.120}
#
# Comparar chave por igualdade reprova isso. A leitura tem tres camadas, da
# mais exata para a mais tolerante, e so reclama quando as tres falham — com
# uma mensagem que diz COMO corrigir, porque KeyError cru so trava o fluxo.
_ALIAS_NOME = ("ingrediente", "nome", "name", "item", "ingredient")
_ALIAS_QTD = ("quantidade", "qtd", "quantity", "amount", "qty", "peso", "volume")
_ALIAS_CATEG = ("categoria", "category", "tipo", "type")
_CATEGORIAS = ("utensilio", "tecnica", "restricao")


def _chave(txt: Any) -> str:
    """Normaliza um nome de chave para comparacao.

    >>> _chave("Quantidade_em_KG por porção")
    'quantidade em kg por porcao'
    """
    plano = unicodedata.normalize("NFKD", str(txt)).encode("ascii", "ignore").decode()
    return " ".join(plano.replace("_", " ").replace("-", " ").lower().split())


def _numero(valor: Any) -> Decimal | None:
    """Decimal ou None. Aceita virgula decimal — o modelo as vezes manda '0,12'.

    >>> _numero("0,120"), _numero("abc"), _numero(None)
    (Decimal('0.120'), None, None)
    """
    if isinstance(valor, bool):
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except Exception:
        return None


def _achar(dado: dict, aliases: tuple[str, ...]) -> tuple[str | None, Any]:
    """Procura um valor pelas grafias plausiveis. Devolve (chave achada, valor).

    Duas passadas: igualdade primeiro, para que a chave certa sempre ganhe de
    uma parecida; prefixo depois, para as chaves descritivas.

    >>> _achar({"nome": "Feijao"}, _ALIAS_NOME)
    ('nome', 'Feijao')
    >>> _achar({"quantidade em kg por porcao": 0.12}, _ALIAS_QTD)
    ('quantidade em kg por porcao', 0.12)
    >>> _achar({"cor": "preto"}, _ALIAS_QTD)
    (None, None)
    """
    normalizado = {_chave(k): v for k, v in dado.items()}
    for alias in aliases:
        if (valor := normalizado.get(alias)) not in (None, ""):
            return alias, valor
    for alias in aliases:
        for chave, valor in normalizado.items():
            if chave.startswith(alias) and valor not in (None, ""):
                return chave, valor
    return None, None


def _campo(dado: dict, aliases: tuple[str, ...]) -> Any:
    return _achar(dado, aliases)[1]


def _sem_rotulo(item: Any, categoria: str) -> str:
    """Tira o rotulo da categoria colado no comeco do valor.

    Mesmo vicio de copiar o prompt: pedir "requisito: utensilio panela de
    pressao" produziu `{"item": "utensilio panela de pressao"}`, e a pergunta
    saiu "A senhora tem utensilio panela de pressao?".

    >>> _sem_rotulo("utensilio panela de pressao", "utensilio")
    'panela de pressao'
    >>> _sem_rotulo("Tecnica: refogar", "tecnica")
    'refogar'
    >>> _sem_rotulo("panela de pressao", "utensilio")
    'panela de pressao'
    >>> _sem_rotulo("utensilio", "utensilio")
    'utensilio'
    """
    limpo = " ".join(str(item).split())
    plano = _chave(limpo)
    if len(plano) != len(limpo):  # a normalizacao mexeu no tamanho: offset nao vale
        return limpo
    for rotulo in (categoria, *_CATEGORIAS):
        casou = re.match(rf"{rotulo}s?\b[\s:,.-]*", plano)
        if casou and casou.end() < len(limpo):
            return limpo[casou.end() :].strip()
    return limpo


def normalizar_itens(ingredientes: list[dict]) -> list[dict]:
    """Aceita as grafias comuns de chave e devolve o formato canonico.

    Erro aqui e devolvido como ValueError com instrucao — o agente consegue
    corrigir e tentar de novo. Um KeyError cru so trava o fluxo.
    """
    limpos, ruins = [], []
    for i, bruto in enumerate(ingredientes or []):
        if not isinstance(bruto, dict):
            ruins.append(f"item {i}: esperava objeto, veio {type(bruto).__name__}")
            continue
        chave_nome, nome = _achar(bruto, _ALIAS_NOME)
        if nome is None:
            ruins.append(f"item {i}: sem nome do ingrediente (chaves: {list(bruto)})")
            continue

        qtd = _campo(bruto, _ALIAS_QTD)
        if qtd is None:
            # Ultima camada: se sobrou UMA chave e ela guarda um numero, ela
            # so pode ser a quantidade — seja qual for o nome que recebeu.
            sobras = [
                v
                for k, v in bruto.items()
                if _chave(k) != chave_nome and _numero(v) is not None
            ]
            qtd = sobras[0] if len(sobras) == 1 else None
        if qtd is None:
            ruins.append(f"item {i} ({nome}): sem quantidade (chaves: {list(bruto)})")
            continue

        valor = _numero(qtd)
        if valor is None:
            ruins.append(f"item {i} ({nome}): quantidade {qtd!r} nao e numero")
            continue
        if valor <= 0:
            ruins.append(f"item {i} ({nome}): quantidade precisa ser maior que zero")
            continue
        limpos.append({"ingrediente": str(nome).strip(), "quantidade": valor})

    if ruins:
        raise ValueError(
            "Nao consegui ler a lista de ingredientes. Formato esperado:\n"
            '  [{"ingrediente": "Feijao preto", "quantidade": 0.120}]\n'
            "quantidade em kg (ou L) por porcao, numero decimal.\n"
            "Problemas encontrados:\n  - " + "\n  - ".join(ruins)
        )
    if not limpos:
        raise ValueError("A lista de ingredientes veio vazia — a receita precisa de itens.")
    return limpos


def normalizar_requisitos(requisitos: list[dict]) -> list[dict]:
    """Mesma tolerancia dos ingredientes, mais a limpeza do rotulo grudado.

    >>> normalizar_requisitos([{"item": "utensilio panela de pressao",
    ...                         "categoria": "utensilio"}])
    [{'categoria': 'utensilio', 'item': 'panela de pressao'}]
    >>> normalizar_requisitos([{"tipo": "TECNICA", "nome": "refogar"}])
    [{'categoria': 'tecnica', 'item': 'refogar'}]
    """
    limpos = []
    for bruto in requisitos or []:
        if not isinstance(bruto, dict):
            continue
        item = _campo(bruto, _ALIAS_NOME)
        if item is None:
            continue
        categoria = _chave(_campo(bruto, _ALIAS_CATEG) or "utensilio")
        if categoria not in _CATEGORIAS:
            categoria = "utensilio"
        if texto := _sem_rotulo(item, categoria):
            limpos.append({"categoria": categoria, "item": texto})
    return limpos


# Nao entram no CMV e nao viram pergunta: sao de torneira, e perguntar
# "quanto custa a agua?" para quem vai vender marmita destroi a confianca no
# agente mais rapido que qualquer erro de conta. Toda receita lista agua.
SEM_CUSTO = ("agua", "gelo", "agua filtrada", "agua fervente")

_URL = re.compile(r"^https?://[^\s]+\.[^\s]+", re.IGNORECASE)


def exigir_fonte(fonte: Any) -> str:
    """A receita precisa vir de uma pagina real, e a URL fica gravada.

    O enunciado pede receita pesquisada na internet (secao 2.1). Sem a URL nao
    ha como conferir se a receita existe — e ja aconteceu de o agente pesquisar
    de verdade, com web_search e web_extract, e gravar os tres pratos com
    `fonte` vazia. A procedencia se perdeu no caminho entre ler e gravar.

    >>> exigir_fonte("https://www.tudogostoso.com.br/receita/2998-feijoada.html")
    'https://www.tudogostoso.com.br/receita/2998-feijoada.html'
    """
    texto = str(fonte or "").strip()
    if not _URL.match(texto):
        raise ValueError(
            "Falta a fonte da receita. `fonte` precisa ser a URL da pagina que\n"
            "voce leu, ex: https://www.tudogostoso.com.br/receita/2998-feijoada.html\n"
            f"Recebi: {texto!r}\n"
            "Se a receita veio de um worker, peca a URL a ele — sem procedencia\n"
            "nao da para conferir se a receita existe."
        )
    return texto


def prato_salvar(
    nome: str,
    fonte: str | None,
    ingredientes: list[dict],
    requisitos: list[dict],
) -> dict:
    """Grava um prato candidato e seus ingredientes. Idempotente pelo nome."""
    fonte = exigir_fonte(fonte)
    ingredientes = normalizar_itens(ingredientes)
    gratis = [i["ingrediente"] for i in ingredientes if _chave(i["ingrediente"]) in SEM_CUSTO]
    ingredientes = [i for i in ingredientes if _chave(i["ingrediente"]) not in SEM_CUSTO]
    if not ingredientes:
        raise ValueError("Sobrou nenhum ingrediente com custo — confira a receita.")
    requisitos = normalizar_requisitos(requisitos)
    with _pool.connection() as conn, conn.transaction():
        prato = conn.execute(
            """
            INSERT INTO pratos (nome, fonte, requisitos)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (nome) DO UPDATE
               SET fonte = EXCLUDED.fonte,
                   requisitos = EXCLUDED.requisitos
            RETURNING id, nome, status
            """,
            (nome, fonte, json.dumps(requisitos)),
        ).fetchone()

        conn.execute("DELETE FROM pratos_ingredientes WHERE prato_id = %s", (prato["id"],))
        novos: list[str] = []
        for ing in ingredientes:
            nome = ing["ingrediente"]
            sku = casar_ingrediente(nome)

            if sku is None:
                # A receita pede algo que a despensa nao tem — carne seca, paio,
                # linguica. Descartar em silencio esconderia o custo do prato:
                # o CMV sairia baixo e o gate aprovaria um prato impossivel.
                #
                # Um ingrediente faltante E um item de despensa: estoque zero e
                # custo desconhecido. Registrado assim, todo o resto ja funciona
                # sem codigo novo — calcular_cmv devolve em `sem_custo`,
                # viabilidade gera "quanto custa?", e prato_aceitar recusa ate
                # a Dona Maria informar o preco.
                conn.execute(
                    """
                    INSERT INTO ingredientes (nome, unidade, estoque, unidade_base, fator)
                    VALUES (%s, 'kg', 0, 'kg', 1)
                    ON CONFLICT (nome) DO NOTHING
                    """,
                    (nome,),
                )
                sku = nome
                novos.append(nome)

            conn.execute(
                """
                INSERT INTO pratos_ingredientes (prato_id, ingrediente, quantidade, comprar)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (prato_id, ingrediente) DO UPDATE
                   SET quantidade = EXCLUDED.quantidade,
                       comprar    = EXCLUDED.comprar
                """,
                (prato["id"], sku, ing["quantidade"], sku in novos),
            )

    prato["fora_da_despensa"] = novos
    prato["total_ingredientes"] = len(ingredientes)
    # Reportado, nao escondido: quem le a resposta precisa saber por que a
    # agua da receita nao aparece na conta.
    if gratis:
        prato["sem_custo"] = gratis
    return prato


def prato(prato_id: int) -> dict | None:
    return _um(
        "SELECT id, nome, fonte, status, cmv, preco, requisitos FROM pratos WHERE id = %s",
        (prato_id,),
    )


def prato_ingredientes(prato_id: int) -> list[dict]:
    return _ler(
        """
        SELECT pi.ingrediente, pi.quantidade, pi.comprar,
               v.unidade_base, v.disponivel, v.custo_unitario
          FROM pratos_ingredientes pi
          JOIN vw_estoque v ON v.nome = pi.ingrediente
         WHERE pi.prato_id = %s
         ORDER BY pi.ingrediente
        """,
        (prato_id,),
    )


def orcamento() -> dict:
    return _um("SELECT orcamento_total, gasto, restante FROM vw_orcamento") or {
        "orcamento_total": Decimal("80.00"),
        "gasto": Decimal("0"),
        "restante": Decimal("80.00"),
    }


def prato_aceitar(prato_id: int, preco: Decimal, cmv: Decimal, compras: list[dict]) -> dict:
    """Fecha o prato. Chamado APENAS depois de o gate aprovar.

    Transacao: marca aceito, grava CMV e preco, e sinaliza as compras
    complementares. Se qualquer passo falhar, nada e gravado.
    """
    with _pool.connection() as conn, conn.transaction():
        for compra in compras:
            conn.execute(
                "UPDATE pratos_ingredientes SET comprar = TRUE"
                " WHERE prato_id = %s AND ingrediente = %s",
                (prato_id, compra["ingrediente"]),
            )
        linha = conn.execute(
            """
            UPDATE pratos
               SET status = 'aceito', preco = %s, cmv = %s
             WHERE id = %s
            RETURNING id, nome, status, cmv, preco
            """,
            (preco, cmv, prato_id),
        ).fetchone()
    return linha


def cardapio(apenas_aceitos: bool = False) -> list[dict]:
    """Os pratos e o estado de cada um.

    Devolvia SO os aceitos, e isso escondia o trabalho em andamento: o agente
    salvou um prato, chamou `cardapio`, recebeu lista vazia e respondeu a Dona
    Maria que nao havia prato nenhum — com a receita dela gravada no banco.

    Um prato `sugerido` nao esta no cardapio, mas existe. Quem pergunta "quais
    pratos eu tenho" quer saber dos dois.
    """
    onde = "WHERE status = 'aceito'" if apenas_aceitos else ""
    return _ler(
        f"""
        SELECT p.id, p.nome, p.status, p.fonte, p.cmv, p.preco,
               count(pi.*) AS ingredientes
          FROM pratos p
          LEFT JOIN pratos_ingredientes pi ON pi.prato_id = p.id
          {onde}
         GROUP BY p.id, p.nome, p.status, p.fonte, p.cmv, p.preco
         ORDER BY CASE p.status WHEN 'aceito' THEN 0 WHEN 'sugerido' THEN 1 ELSE 2 END,
                  p.nome
        """
    )


def json_seguro(valor: Any) -> Any:
    """Decimal -> float na fronteira do JSON. Só aqui, nunca no meio da conta."""
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, dict):
        return {k: json_seguro(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [json_seguro(v) for v in valor]
    return valor
