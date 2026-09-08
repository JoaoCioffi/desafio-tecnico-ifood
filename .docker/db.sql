-- =============================================================================
--  Sabor da Maria — esquema do banco
-- =============================================================================
--  Quatro tabelas. Cada uma responde uma pergunta do desafio:
--
--    ingredientes         o que ela tem e quanto pagou   (as duas abas da planilha)
--    perfil               o que ela tem e sabe fazer     (elicitacao, secao 2.2)
--    pratos               o cardapio                     (secao 2.4)
--    pratos_ingredientes  o que cada prato consome       (CMV e contencao de estoque)
--
--  As duas abas da planilha (Despensa e Precos) tem a mesma chave (Ingrediente)
--  e casam 1:1 — por isso viram uma tabela so.
--
--  Este script e IDEMPOTENTE: pode rodar quantas vezes for, nunca apaga
--  nem sobrescreve dado existente.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Busca sem acento
-- -----------------------------------------------------------------------------
--  A despensa tem "Feijão preto", "Açúcar", "Açafrão". O agente vai procurar
--  "feijao", "acucar". Sem normalizar, a busca falha e ele conclui que o
--  ingrediente nao existe — e passa a perguntar coisa que ja esta na despensa.
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION unaccent_lower(txt TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$ SELECT lower(public.unaccent('public.unaccent', txt)) $$;

COMMENT ON FUNCTION unaccent_lower IS 'Minusculo e sem acento, para casar nome de ingrediente';


-- -----------------------------------------------------------------------------
-- 1. ingredientes  —  a planilha, com a unidade normalizada
-- -----------------------------------------------------------------------------
--  A planilha traz a unidade como texto livre: "kg" em uns itens, "balde 2kg"
--  em outros. Dividir preco_pago por qtd_comprada direto daria R$ por EMBALAGEM,
--  nao por kg — o balde de alcaparras sairia a R$ 82,00 em vez de R$ 41,00/kg.
--
--  O ETL le esse texto e extrai duas coisas:
--     unidade_base  a unidade de medida real   (kg, L, un)
--     fator         quanto de unidade_base cabe em 1 unidade da planilha
--
--  O banco entao calcula o custo na unidade base. Onde o texto nao permite
--  extrair o fator, as duas colunas ficam NULAS — e custo_unitario tambem.
--  Nulo aqui nao e falha: e o sinal de que o agente precisa PERGUNTAR.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingredientes (
    nome            TEXT           PRIMARY KEY,

    -- como veio da planilha, preservado para auditoria
    unidade         TEXT           NOT NULL,
    estoque         NUMERIC(12, 4) NOT NULL DEFAULT 0 CHECK (estoque >= 0),
    qtd_comprada    NUMERIC(12, 4) CHECK (qtd_comprada > 0),
    preco_pago      NUMERIC(12, 2) CHECK (preco_pago >= 0),

    -- extraidos de `unidade` pelo ETL
    unidade_base    TEXT           CHECK (unidade_base IN ('kg', 'L', 'un')),
    fator           NUMERIC(12, 6) CHECK (fator > 0),

    -- Derivada, nunca digitada: o banco garante que custo_unitario e sempre
    -- coerente. NULO quando o fator nao pode ser extraido da planilha.
    custo_unitario  NUMERIC(14, 6)
        GENERATED ALWAYS AS (
            CASE
                WHEN qtd_comprada > 0 AND fator > 0
                THEN preco_pago / (qtd_comprada * fator)
            END
        ) STORED,

    criado_em       TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  ingredientes IS 'Despensa da Dona Maria — juncao das abas Despensa e Precos';
COMMENT ON COLUMN ingredientes.unidade        IS 'Texto cru da planilha: kg, L, un, "balde 2kg", "un 500ml"';
COMMENT ON COLUMN ingredientes.estoque        IS 'Aba Despensa: Quantidade em estoque';
COMMENT ON COLUMN ingredientes.qtd_comprada   IS 'Aba Precos: Quantidade comprada';
COMMENT ON COLUMN ingredientes.preco_pago     IS 'Aba Precos: Preco total pago (R$)';
COMMENT ON COLUMN ingredientes.unidade_base   IS 'Unidade de medida real extraida de `unidade`';
COMMENT ON COLUMN ingredientes.fator          IS 'Quanto de unidade_base cabe em 1 unidade da planilha. "balde 2kg" -> 2';
COMMENT ON COLUMN ingredientes.custo_unitario IS 'Gerada: preco_pago / (qtd_comprada * fator). R$ por unidade_base';


-- -----------------------------------------------------------------------------
-- 2. perfil  —  o que a Dona Maria tem e sabe fazer
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS perfil (
    id             SERIAL      PRIMARY KEY,
    categoria      TEXT        NOT NULL
                   CHECK (categoria IN ('utensilio', 'tecnica', 'restricao')),
    item           TEXT        NOT NULL,
    resposta       TEXT,
    status         TEXT        NOT NULL DEFAULT 'pendente'
                   CHECK (status IN ('pendente', 'confirmado')),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (categoria, item)
);

COMMENT ON TABLE  perfil IS 'Memoria da elicitacao — o que ja foi perguntado e respondido';
COMMENT ON COLUMN perfil.item     IS 'Ex.: forno, panela de pressao, massa fresca, espaco na geladeira';
COMMENT ON COLUMN perfil.resposta IS 'Ex.: tem, nao tem, "so 2 bocas"';
COMMENT ON COLUMN perfil.status   IS 'pendente = ainda nao perguntado / sem resposta';


-- -----------------------------------------------------------------------------
-- 3. pratos  —  o cardapio
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pratos (
    id          SERIAL         PRIMARY KEY,
    nome        TEXT           NOT NULL UNIQUE,
    fonte       TEXT,
    status      TEXT           NOT NULL DEFAULT 'sugerido'
                CHECK (status IN ('sugerido', 'aceito', 'recusado')),
    cmv         NUMERIC(12, 2) CHECK (cmv >= 0),
    preco       NUMERIC(12, 2) CHECK (preco >= 0),

    -- O que a receita exige da cozinha: utensilio, tecnica, restricao.
    -- Fica no prato porque o gate precisa reavaliar na hora do aceite —
    -- se dependesse do agente reenviar a lista, bastaria ele esquecer
    -- para a checagem ser burlada.
    --   [{"categoria": "utensilio", "item": "panela de pressao"}]
    requisitos  JSONB          NOT NULL DEFAULT '[]'::jsonb,

    criado_em   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  pratos IS 'Pratos sugeridos, aceitos ou recusados pela Dona Maria';
COMMENT ON COLUMN pratos.fonte      IS 'URL da receita encontrada na web';
COMMENT ON COLUMN pratos.cmv        IS 'Custo de Mercadoria Vendida do prato, em R$';
COMMENT ON COLUMN pratos.preco      IS 'Preco de venda escolhido pela Dona Maria, em R$';
COMMENT ON COLUMN pratos.requisitos IS 'Utensilios/tecnicas que a receita exige — alimenta o gate';


-- -----------------------------------------------------------------------------
-- 4. pratos_ingredientes  —  o que cada prato consome
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pratos_ingredientes (
    prato_id     INTEGER        NOT NULL REFERENCES pratos(id)         ON DELETE CASCADE,
    ingrediente  TEXT           NOT NULL REFERENCES ingredientes(nome) ON UPDATE CASCADE,
    quantidade   NUMERIC(12, 4) NOT NULL CHECK (quantidade > 0),
    comprar      BOOLEAN        NOT NULL DEFAULT FALSE,

    PRIMARY KEY (prato_id, ingrediente)
);

CREATE INDEX IF NOT EXISTS idx_pratos_ing_ingrediente
    ON pratos_ingredientes (ingrediente);

COMMENT ON TABLE  pratos_ingredientes IS 'Ligacao prato <-> ingrediente, com a quantidade usada';
COMMENT ON COLUMN pratos_ingredientes.quantidade IS 'Na unidade_base do ingrediente';
COMMENT ON COLUMN pratos_ingredientes.comprar    IS 'TRUE = nao tem na despensa, sai do orcamento de R$ 80';


-- -----------------------------------------------------------------------------
-- 5. evento  —  trilha do que o agente fez
-- -----------------------------------------------------------------------------
--  Nao e estado do negocio: e observabilidade. Cada ferramenta do MCP registra
--  o que esta fazendo enquanto faz, e isso serve a tres publicos:
--
--    o cockpit    le por SSE e mostra o progresso ao vivo, em vez de
--                 deixar a Dona Maria olhando tela parada
--    a auditoria  responde "por que este prato foi recusado?" depois
--    o avaliador  consegue inspecionar o fluxo sem assistir ao video
--
--  Tabela append-only. Nada aqui e lido para decidir regra de negocio.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evento (
    id          BIGSERIAL   PRIMARY KEY,
    momento     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ferramenta  TEXT        NOT NULL,
    fase        TEXT        NOT NULL
                CHECK (fase IN ('inicio', 'progresso', 'fim', 'recusa', 'cancelado', 'erro')),
    mensagem    TEXT        NOT NULL,
    dados       JSONB
);

CREATE INDEX IF NOT EXISTS idx_evento_momento ON evento (momento DESC);

COMMENT ON TABLE  evento IS 'Trilha append-only do que o agente fez — alimenta o cockpit e a auditoria';
COMMENT ON COLUMN evento.ferramenta IS 'Qual tool do MCP gerou o evento';
COMMENT ON COLUMN evento.mensagem   IS 'Texto curto para humano: "lendo 37 ingredientes"';
COMMENT ON COLUMN evento.dados      IS 'Payload estruturado opcional, para o cockpit desenhar';


-- =============================================================================
--  Visoes derivadas
--  Estoque comprometido e orcamento gasto sao CONTA, nao dado — por isso sao
--  view e nao tabela. Assim nunca dessincronizam do que esta em pratos.
-- =============================================================================

CREATE OR REPLACE VIEW vw_estoque AS
SELECT
    i.nome,
    i.unidade_base,
    i.estoque                                          AS estoque_total,
    COALESCE(SUM(pi.quantidade) FILTER (
        WHERE p.status = 'aceito' AND NOT pi.comprar
    ), 0)                                              AS comprometido,
    i.estoque - COALESCE(SUM(pi.quantidade) FILTER (
        WHERE p.status = 'aceito' AND NOT pi.comprar
    ), 0)                                              AS disponivel,
    i.custo_unitario,
    i.unidade                                          AS unidade_planilha
FROM ingredientes i
LEFT JOIN pratos_ingredientes pi ON pi.ingrediente = i.nome
LEFT JOIN pratos              p  ON p.id = pi.prato_id
GROUP BY i.nome, i.unidade_base, i.estoque, i.custo_unitario, i.unidade;

COMMENT ON VIEW vw_estoque IS 'Despensa com o que ja esta comprometido pelos pratos aceitos';


CREATE OR REPLACE VIEW vw_orcamento AS
SELECT
    80.00::NUMERIC(12, 2)                                              AS orcamento_total,
    COALESCE(SUM(pi.quantidade * i.custo_unitario), 0)::NUMERIC(12, 2) AS gasto,
    (80.00 - COALESCE(SUM(pi.quantidade * i.custo_unitario), 0))::NUMERIC(12, 2) AS restante
FROM pratos_ingredientes pi
JOIN pratos       p ON p.id = pi.prato_id
JOIN ingredientes i ON i.nome = pi.ingrediente
WHERE p.status = 'aceito'
  AND pi.comprar;

COMMENT ON VIEW vw_orcamento IS 'Orcamento de R$ 80 para complementos: total, gasto e restante';


-- Ingredientes cujo custo nao pode ser derivado da planilha.
-- Alimenta a elicitacao: o agente pergunta em vez de chutar.
CREATE OR REPLACE VIEW vw_unidade_pendente AS
SELECT nome, unidade, qtd_comprada, preco_pago
  FROM ingredientes
 WHERE fator IS NULL;

COMMENT ON VIEW vw_unidade_pendente IS 'Itens sem fator de conversao — precisam de pergunta a Dona Maria';
