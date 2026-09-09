"""Acesso ao Postgres.

Unica camada que fala SQL. O `domain/` continua puro; o `server.py` orquestra.

A conexao vem por pool porque o transporte HTTP do MCP atende chamadas
concorrentes: o agente dispara ferramentas em paralelo, e abrir conexao por
chamada gastaria mais tempo em handshake do que em consulta.
"""

from __future__ import annotations

import json
import os
import unicodedata
from decimal import Decimal

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _dsn() -> str:
    """Montada das mesmas variaveis que o compose entrega a todo mundo.

    Dentro da rede do compose o banco atende por `postgres`, nunca por
    `localhost` — e o compose ja sobrescreve DB_HOST para isso.
    """
    return (
        f"host={os.environ.get('DB_HOST', 'postgres')} "
        f"port={os.environ.get('DB_PORT', '5432')} "
        f"dbname={os.environ.get('DB_NAME', 'postgres')} "
        f"user={os.environ.get('DB_USER', 'admin')} "
        f"password={os.environ.get('DB_PASSWORD', '')}"
    )


_pool = ConnectionPool(_dsn(), min_size=1, max_size=4, open=False,
                       kwargs={"row_factory": dict_row})


def abrir() -> None:
    _pool.open()
    _pool.wait(timeout=20)


def fechar() -> None:
    _pool.close()


def sem_acento(texto: str) -> str:
    """Espelha o `unaccent_lower` do banco, para o lado Python comparar igual."""
    decomposto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn").lower()


# --------------------------------------------------------------------------- #
#  A juncao das duas abas acontece AQUI, nao no schema: as tabelas ficaram
#  isoladas de proposito, cada uma projetando uma aba da planilha. Quem precisa
#  das duas juntas e quem responde pergunta de custo — e essa e a unica.
#
#  `custo_unitario_ingenuo` NAO e selecionada. A coluna existe no banco para
#  medir a divergencia e auditar; devolve-la aqui abriria caminho para o
#  agente citar R$ 82,00/balde de alcaparra num prato de R$ 6,00.
# --------------------------------------------------------------------------- #
#
#  A leitura sai de `vw_estoque`, e nao da tabela `despensa`: a view ja
#  desconta o que os pratos ACEITOS comprometeram. Lendo o estoque bruto, o
#  agente proporia um segundo prato contando com o frango que o primeiro ja
#  reservou — os mesmos 2 kg vendidos duas vezes, no papel.
_DESPENSA = """
    SELECT v.ingrediente,
           v.disponivel                             AS estoque,
           v.estoque_total,
           v.comprometido,
           v.unidade_base,
           v.unidade_planilha,
           v.custo_unitario
      FROM vw_estoque v
     -- O ::text nao e decoracao: sem ele o Postgres nao consegue inferir o
     -- tipo de um parametro solto num IS NULL e recusa a query inteira com
     -- "could not determine data type of parameter $1".
     WHERE %(termo)s::text IS NULL
        OR unaccent_lower(v.ingrediente) LIKE '%%' || unaccent_lower(%(termo)s::text) || '%%'
     ORDER BY v.ingrediente
"""


def despensa(termo: str | None = None) -> list[dict]:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(_DESPENSA, {"termo": termo}).fetchall()


# --------------------------------------------------------------------------- #
# Perfil
# --------------------------------------------------------------------------- #
_GRAVAR_PERFIL = """
    INSERT INTO perfil (categoria, item, resposta, status)
    -- O ::text de novo: parametro solto num IS NULL nao tem tipo inferivel, e
    -- o Postgres recusa a query inteira. Mesma pegadinha do filtro da despensa.
    VALUES (%(categoria)s, %(item)s, %(resposta)s::text,
            CASE WHEN %(resposta)s::text IS NULL THEN 'pendente' ELSE 'confirmado' END)
    ON CONFLICT (categoria, item) DO UPDATE SET
        resposta      = EXCLUDED.resposta,
        status        = EXCLUDED.status,
        atualizado_em = now()
    RETURNING categoria, item, resposta, status
"""


def perfil_gravar(fatos: list[dict]) -> list[dict]:
    """Grava ou atualiza. Tudo numa transacao: ou entram todos, ou nenhum.

    Uma frase dela costuma trazer varios fatos de uma vez — "tenho fogao de 4
    bocas e forno, mas nao tenho panela de pressao" sao tres. Gravar um a um
    deixaria o perfil pela metade se a segunda chamada falhasse.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return [cur.execute(_GRAVAR_PERFIL, fato).fetchone() for fato in fatos]


def perfil_listar(categoria: str | None = None) -> list[dict]:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT categoria, item, resposta, status, atualizado_em
              FROM perfil
             WHERE %(categoria)s::text IS NULL OR categoria = %(categoria)s::text
             ORDER BY categoria, item
            """,
            {"categoria": categoria},
        ).fetchall()


# --------------------------------------------------------------------------- #
# Pratos
# --------------------------------------------------------------------------- #
def prato_propor(nome: str, fonte: str | None, porcoes: int,
                 requisitos: list[dict], itens: list[dict]) -> dict:
    """Grava um prato como 'sugerido' e o que ele consome. Transacional.

    Os requisitos entram AQUI, na proposta — e nao no aceite. E o que impede o
    gate de validar a afirmacao de quem chama em vez da realidade.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        prato = cur.execute(
            """
            INSERT INTO pratos (nome, fonte, porcoes, requisitos, status)
            VALUES (%s, %s, %s, %s::jsonb, 'sugerido')
            ON CONFLICT (nome) DO UPDATE SET
                fonte      = EXCLUDED.fonte,
                porcoes    = EXCLUDED.porcoes,
                requisitos = EXCLUDED.requisitos,
                -- Repropor um prato recusado o devolve para a mesa; um ja
                -- aceito continua aceito, senao o ledger perderia o consumo.
                status     = CASE WHEN pratos.status = 'aceito' THEN 'aceito' ELSE 'sugerido' END
            RETURNING id, nome, status
            """,
            (nome, fonte, porcoes, json.dumps(requisitos)),
        ).fetchone()

        cur.execute("DELETE FROM pratos_ingredientes WHERE prato_id = %s", (prato["id"],))
        if itens:
            cur.executemany(
                """
                INSERT INTO pratos_ingredientes
                       (prato_id, ingrediente, quantidade, unidade_base,
                        comprar, custo_compra)
                VALUES (%(prato_id)s, %(ingrediente)s, %(quantidade)s, %(unidade_base)s,
                        %(comprar)s, %(custo_compra)s)
                """,
                [dict(i, prato_id=prato["id"]) for i in itens],
            )
        return prato


def prato_carregar(prato_id: int) -> dict | None:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        prato = cur.execute(
            "SELECT id, nome, fonte, porcoes, status, cmv, preco, requisitos "
            "  FROM pratos WHERE id = %s", (prato_id,)
        ).fetchone()
        if prato is None:
            return None
        prato["itens"] = cur.execute(
            "SELECT ingrediente, quantidade, unidade_base, comprar, custo_compra "
            "  FROM pratos_ingredientes WHERE prato_id = %s ORDER BY ingrediente",
            (prato_id,),
        ).fetchall()
        return prato


def prato_marcar(prato_id: int, status: str, cmv=None, preco=None) -> dict | None:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            UPDATE pratos
               SET status = %s,
                   cmv    = COALESCE(%s, cmv),
                   preco  = COALESCE(%s, preco)
             WHERE id = %s
            RETURNING id, nome, status, cmv, preco
            """,
            (status, cmv, preco, prato_id),
        ).fetchone()


def pratos_listar(status: str | None = None) -> list[dict]:
    """O cardapio. Sem filtro traz tudo, inclusive recusados."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT p.id, p.nome, p.fonte, p.porcoes, p.status, p.cmv, p.preco,
                   p.requisitos,
                   -- O custo sai do ledger, nao da coluna `cmv` do prato: a
                   -- coluna guarda o que foi calculado quando o prato entrou,
                   -- e isto aqui e o custo com os precos de hoje.
                   (SELECT COALESCE(SUM(pi.quantidade * pr.custo_unitario), 0)
                      FROM pratos_ingredientes pi
                      JOIN precos pr USING (ingrediente)
                     WHERE pi.prato_id = p.id) AS custo_ingredientes
              FROM pratos p
             WHERE %(status)s::text IS NULL OR p.status = %(status)s::text
             ORDER BY p.status, p.nome
            """,
            {"status": status},
        ).fetchall()


def compra_registrar(ingrediente: str, quantidade, unidade_base: str,
                     custo_total, prato_id: int | None = None) -> dict:
    """Grava uma compra. Ela entra no estoque e sai do orcamento."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            INSERT INTO compras (ingrediente, quantidade, unidade_base, custo_total, prato_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, ingrediente, quantidade, unidade_base, custo_total
            """,
            (ingrediente, quantidade, unidade_base, custo_total, prato_id),
        ).fetchone()


def compras_listar(prato_id: int | None = None) -> list[dict]:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT c.id, c.ingrediente, c.quantidade, c.unidade_base,
                   c.custo_total, c.prato_id, c.comprado_em, p.nome AS prato
              FROM compras c
              LEFT JOIN pratos p ON p.id = c.prato_id
             WHERE %(prato_id)s::int IS NULL OR c.prato_id = %(prato_id)s::int
             ORDER BY c.comprado_em DESC
            """,
            {"prato_id": prato_id},
        ).fetchall()


def compras_do_prato_apagar(prato_id: int) -> int:
    """Desfaz as compras que o aceite deste prato gerou.

    Compra avulsa (prato_id nulo) nunca e apagada: aquilo ela comprou de fato,
    e recusar um prato depois nao devolve o dinheiro nem tira a caixa da
    geladeira.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        cur.execute("DELETE FROM compras WHERE prato_id = %s", (prato_id,))
        return cur.rowcount


def estoque() -> list[dict]:
    """A despensa ja com o que os pratos aceitos consomem descontado."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            "SELECT ingrediente, unidade_base, disponivel, custo_unitario FROM vw_estoque"
        ).fetchall()


def orcamento() -> dict:
    """total / gasto / restante — tudo derivado dos pratos aceitos."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute("SELECT total, gasto, restante FROM vw_orcamento").fetchone() or {
            "total": Decimal(0), "gasto": Decimal(0), "restante": Decimal(0)
        }


# --------------------------------------------------------------------------- #
#  Cardapio publicado e pedidos
#
#  A partir daqui existe um SEGUNDO agente lendo este mesmo banco — o bot do
#  cliente. Os dois nunca conversam entre si: coordenam pelas tabelas. Por
#  isso as regras de venda vivem em SQL e nao em prompt; um prompt vale para o
#  agente que o carrega, uma constraint vale para todo mundo.
# --------------------------------------------------------------------------- #
# Quanto ESTE prato ja compromete hoje, por ingrediente. `vw_estoque.comprometido`
# inclui a publicacao atual dele, entao republicar com o mesmo numero se recusaria
# sozinho se a gente nao devolvesse isso para a conta. COALESCE 1 porque prato
# aceito e nao publicado ja segura uma receita.
_LOTES_ATUAIS = """
    COALESCE((SELECT c.lotes FROM cardapio c
               WHERE c.prato_id = pi.prato_id AND c.retirado_em IS NULL), 1)
"""


def lotes_possiveis(prato_id: int) -> dict | None:
    """Quantos lotes a despensa aguenta hoje, e qual ingrediente limita.

    Existe para a recusa ser util. "Nao da" manda o agente adivinhar o proximo
    numero; "da para 2, o leite integral limita" e uma frase que ela entende e
    sobre a qual decide.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            f"""
            SELECT MIN(FLOOR((e.disponivel + pi.quantidade * {_LOTES_ATUAIS})
                             / pi.quantidade))::int AS lotes,
                   (ARRAY_AGG(pi.ingrediente ORDER BY
                        (e.disponivel + pi.quantidade * {_LOTES_ATUAIS})
                        / pi.quantidade))[1] AS limitante
              FROM pratos_ingredientes pi
              JOIN vw_estoque e ON e.ingrediente = pi.ingrediente
             WHERE pi.prato_id = %(prato_id)s
            """,
            {"prato_id": prato_id},
        ).fetchone()


def cardapio_publicar(prato_id: int, preco, lotes: int = 1) -> dict | None:
    """Poe um prato ACEITO a venda. Devolve None se o prato nao pode ir ao ar.

    Duas condicoes, e as duas moram no proprio INSERT: o prato tem que estar
    aceito, e a despensa tem que aguentar os lotes pedidos.

    A segunda estava so na skill — "confira a despensa antes de publicar numero
    alto". Instrucao em prompt vale para o agente que a carrega e cede quando
    alguem insiste; foi por isso que o gate de viabilidade virou hook em vez de
    paragrafo no SOUL. Publicar 3 lotes de um prato que so tem despensa para 2
    deixaria o estoque NEGATIVO, e ela descobriria na hora de cozinhar — que e
    exatamente o que o enunciado manda impedir.

    Checar antes e inserir depois nao serve: entre a leitura e a escrita o
    numero pode mudar. Uma sentenca so nao tem essa janela.

    Republicar troca preco e lotes em vez de criar linha nova — o indice
    parcial `idx_cardapio_ativo` so admite uma publicacao ativa por prato.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            f"""
            INSERT INTO cardapio (prato_id, preco, lotes)
            SELECT p.id, %(preco)s, %(lotes)s
              FROM pratos p
             WHERE p.id = %(prato_id)s
               AND p.status = 'aceito'
               AND NOT EXISTS (
                   SELECT 1
                     FROM pratos_ingredientes pi
                     JOIN vw_estoque e ON e.ingrediente = pi.ingrediente
                    WHERE pi.prato_id = p.id
                      AND e.disponivel + pi.quantidade * {_LOTES_ATUAIS}
                          < pi.quantidade * %(lotes)s
               )
            ON CONFLICT (prato_id) WHERE retirado_em IS NULL
            DO UPDATE SET preco = EXCLUDED.preco,
                          lotes = EXCLUDED.lotes,
                          publicado_em = now()
            RETURNING id, prato_id, preco, lotes, publicado_em
            """,
            {"prato_id": prato_id, "preco": preco, "lotes": lotes},
        ).fetchone()


def cardapio_retirar(prato_id: int) -> dict | None:
    """Tira do ar. Nao apaga: preenche `retirado_em` e o historico fica."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            UPDATE cardapio SET retirado_em = now()
             WHERE prato_id = %s AND retirado_em IS NULL
            RETURNING id, prato_id, preco, lotes
            """,
            (prato_id,),
        ).fetchone()


def cardapio_listar() -> list[dict]:
    """O que esta no ar agora, com quantas porcoes ainda restam."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT cardapio_id, prato_id, nome, preco, cmv,
                   porcoes_por_lote, lotes, porcoes_publicadas,
                   porcoes_vendidas, porcoes_disponiveis, receita_liquida
              FROM vw_cardapio
             ORDER BY nome
            """
        ).fetchall()


def pedido_registrar(cardapio_id: int, cliente: str, porcoes: int) -> dict | None:
    """Vende, ou devolve None. As duas recusas possiveis sao a MESMA sentenca.

    Nao existe caminho para vender o que nao esta publicado nem para vender
    mais porcoes do que restam: `vw_cardapio` so tem prato no ar e aceito, e o
    `porcoes_disponiveis >= %(porcoes)s` esta dentro do INSERT. Se o SELECT nao
    devolver linha, nao ha o que inserir — a recusa e a ausencia de dados, nao
    uma decisao de codigo que alguem possa esquecer de escrever.

    O PRECO vem da view, nunca do chamador. O cliente diz o que quer e quanto;
    quanto custa e dela. Aceitar preco por parametro seria deixar o agente do
    cliente negociar sozinho — e este projeto inteiro existe para manter esse
    tipo de numero fora do alcance do modelo.

    O advisory lock serializa os pedidos DESTA linha do cardapio. Sem ele, dois
    pedidos simultaneos leem `porcoes_disponiveis` no mesmo instantaneo, ambos
    veem folga e ambos entram: em READ COMMITTED o Postgres nao trava um
    agregado, entao a ultima porcao pode ser vendida duas vezes. O lock e por
    transacao e cai sozinho no commit.
    """
    with _pool.connection() as conexao, conexao.cursor() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (cardapio_id,))
        return cur.execute(
            """
            INSERT INTO pedidos (cardapio_id, cliente, porcoes, preco_unitario)
            SELECT c.cardapio_id, %(cliente)s, %(porcoes)s, c.preco
              FROM vw_cardapio c
             WHERE c.cardapio_id = %(cardapio_id)s
               AND c.porcoes_disponiveis >= %(porcoes)s
            RETURNING id, cardapio_id, cliente, porcoes, preco_unitario,
                      valor_bruto, taxa, valor_liquido, criado_em
            """,
            {"cardapio_id": cardapio_id, "cliente": cliente, "porcoes": porcoes},
        ).fetchone()


def pedido_carregar(pedido_id: int) -> dict | None:
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT ped.id, ped.cliente, ped.porcoes, ped.preco_unitario,
                   ped.valor_bruto, ped.taxa, ped.valor_liquido, ped.criado_em,
                   p.nome AS prato
              FROM pedidos  ped
              JOIN cardapio c ON c.id = ped.cardapio_id
              JOIN pratos   p ON p.id = c.prato_id
             WHERE ped.id = %s
            """,
            (pedido_id,),
        ).fetchone()


def pedidos_listar(cliente: str | None = None) -> list[dict]:
    """Todos os pedidos, ou os de um cliente. Do mais recente para o mais antigo."""
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT ped.id, ped.cliente, ped.porcoes, ped.preco_unitario,
                   ped.valor_bruto, ped.taxa, ped.valor_liquido, ped.criado_em,
                   p.nome AS prato
              FROM pedidos  ped
              JOIN cardapio c ON c.id = ped.cardapio_id
              JOIN pratos   p ON p.id = c.prato_id
             WHERE %(cliente)s::text IS NULL
                OR unaccent_lower(ped.cliente) = unaccent_lower(%(cliente)s::text)
             ORDER BY ped.criado_em DESC
            """,
            {"cliente": cliente},
        ).fetchall()


def caixa() -> dict:
    """Orcamento - compras + vendas liquidas. O que ela pode gastar hoje."""
    zerado = {"orcamento_inicial": Decimal(0), "gasto": Decimal(0),
              "vendas_brutas": Decimal(0), "taxa_plataforma": Decimal(0),
              "receita_liquida": Decimal(0), "saldo": Decimal(0)}
    with _pool.connection() as conexao, conexao.cursor() as cur:
        return cur.execute(
            """
            SELECT orcamento_inicial, gasto, vendas_brutas,
                   taxa_plataforma, receita_liquida, saldo
              FROM vw_caixa
            """
        ).fetchone() or zerado
