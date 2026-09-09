-- =============================================================================
--  Sabor da Maria — esquema do banco
-- =============================================================================
--  Dois grupos de tabelas, com donos diferentes.
--
--  Projecao da planilha — o ETL do runner reescreve a cada subida:
--
--    despensa    aba Despensa    o que ela tem hoje
--    precos      aba Precos      o que ela pagou
--    orcamento   R$ 80,00        o teto para complementos
--
--  Estado da conversa — so o agente escreve, via servidor MCP:
--
--    perfil               o que ela tem e sabe fazer   (elicitacao, secao 2.2)
--    pratos               o cardapio                   (secao 2.4)
--    pratos_ingredientes  o que cada prato consome     (ledger)
--
--  As duas abas da planilha casam 1:1 pelo nome do ingrediente, mas continuam
--  separadas: respondem perguntas diferentes, e o join e trivial quando
--  precisar. Nao ha FK entre elas nem do estado para a projecao — assim uma
--  recarga da planilha nunca esbarra num prato antigo.
--
--  Este script e IDEMPOTENTE: roda quantas vezes for, nunca apaga dado.
-- =============================================================================


-- -----------------------------------------------------------------------------
--  Busca sem acento
-- -----------------------------------------------------------------------------
--  A despensa tem "Feijao preto", "Acucar", "Acafrao" — com acento. O agente
--  vai procurar "feijao", "acucar". Sem normalizar, a busca falha, ele conclui
--  que o ingrediente nao existe e passa a sugerir comprar o que ja esta ali.
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE OR REPLACE FUNCTION unaccent_lower(txt TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
AS $$ SELECT lower(public.unaccent('public.unaccent', txt)) $$;

COMMENT ON FUNCTION unaccent_lower IS 'Minusculo e sem acento, para casar nome de ingrediente';


-- =============================================================================
--  1. despensa  —  aba Despensa
-- =============================================================================
--  A planilha diz "Alcaparras · 1 · balde 2kg". Isso significa 2 kg, mas o
--  numero esta preso no texto da unidade. As colunas *_base guardam a leitura
--  ja resolvida, para que ninguem precise interpretar string em tempo de
--  consulta — e para que a interpretacao seja a MESMA em todo lugar.
-- =============================================================================
CREATE TABLE IF NOT EXISTS despensa (
    ingrediente      TEXT           PRIMARY KEY,

    -- cru, como veio da planilha, preservado para auditoria
    quantidade       NUMERIC(12, 4) NOT NULL CHECK (quantidade >= 0),
    unidade          TEXT           NOT NULL,

    -- resolvidos pelo ETL a partir de `unidade`
    unidade_base     TEXT           CHECK (unidade_base IN ('kg', 'L', 'un')),
    fator            NUMERIC(14, 6) CHECK (fator > 0),

    -- Derivada, nunca digitada. NULA quando o texto da unidade nao permite
    -- extrair o fator — e nulo aqui nao e falha, e o sinal de PERGUNTE.
    quantidade_base  NUMERIC(14, 6)
        GENERATED ALWAYS AS (quantidade * fator) STORED,

    carregado_em     TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  despensa IS 'Aba Despensa: o que a Dona Maria tem hoje';
COMMENT ON COLUMN despensa.quantidade      IS 'Cru da planilha: Quantidade em estoque';
COMMENT ON COLUMN despensa.unidade         IS 'Cru da planilha: kg, L, un, "balde 2kg", "un 500ml"';
COMMENT ON COLUMN despensa.unidade_base    IS 'Unidade de medida real. un = item CONTADO, nao pesado';
COMMENT ON COLUMN despensa.fator           IS 'Quanto de unidade_base cabe em 1 unidade da planilha. "balde 2kg" -> 2';
COMMENT ON COLUMN despensa.quantidade_base IS 'Gerada: quantidade * fator. Estoque real na unidade_base';


-- =============================================================================
--  2. precos  —  aba Precos
-- =============================================================================
--  O enunciado define custo unitario como `preco total pago / quantidade
--  comprada`. Aplicado ao pe da letra na alcaparra isso da R$ 82,00 por
--  BALDE — aritmeticamente correto e operacionalmente inutil, porque receita
--  nenhuma pede um balde. O valor que serve e R$ 41,00 por kg.
--
--  As duas contas ficam gravadas, com nomes que nao deixam confundir:
--
--    custo_unitario          R$ por unidade_base   <- ESTE entra no CMV
--    custo_unitario_ingenuo  R$ por embalagem      <- so auditoria
--
--  Guardar o ingenuo tem proposito: a divergencia entre as duas colunas e a
--  medida exata da armadilha, e some se a gente so guardar a resposta certa.
-- =============================================================================
CREATE TABLE IF NOT EXISTS precos (
    ingrediente             TEXT           PRIMARY KEY,

    -- cru, como veio da planilha
    qtd_comprada            NUMERIC(12, 4) NOT NULL CHECK (qtd_comprada > 0),
    unidade                 TEXT           NOT NULL,
    preco_pago              NUMERIC(12, 2) NOT NULL CHECK (preco_pago >= 0),

    -- resolvidos pelo ETL a partir de `unidade`
    unidade_base            TEXT           CHECK (unidade_base IN ('kg', 'L', 'un')),
    fator                   NUMERIC(14, 6) CHECK (fator > 0),

    -- O numero certo. NULO quando o fator nao pode ser extraido.
    custo_unitario          NUMERIC(14, 6)
        GENERATED ALWAYS AS (preco_pago / (qtd_comprada * fator)) STORED,

    -- O numero ingenuo: a divisao literal do enunciado, sem normalizar.
    custo_unitario_ingenuo  NUMERIC(14, 6)
        GENERATED ALWAYS AS (preco_pago / qtd_comprada) STORED,

    carregado_em            TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  precos IS 'Aba Precos: quanto a Dona Maria pagou, com o custo unitario ja derivado';
COMMENT ON COLUMN precos.qtd_comprada  IS 'Cru da planilha: Quantidade comprada';
COMMENT ON COLUMN precos.unidade       IS 'Cru da planilha: kg, L, un, "balde 2kg", "un 500ml"';
COMMENT ON COLUMN precos.preco_pago    IS 'Cru da planilha: Preco total pago (R$)';
COMMENT ON COLUMN precos.unidade_base  IS 'Unidade de medida real. un = item CONTADO, nao pesado';
COMMENT ON COLUMN precos.fator         IS 'Quanto de unidade_base cabe em 1 unidade da planilha. "balde 2kg" -> 2';
COMMENT ON COLUMN precos.custo_unitario
    IS 'USE ESTE NO CMV. R$ por unidade_base: preco_pago / (qtd_comprada * fator)';
COMMENT ON COLUMN precos.custo_unitario_ingenuo
    IS 'NAO USE NO CMV. R$ por embalagem: preco_pago / qtd_comprada. Existe so para medir a divergencia';


-- =============================================================================
--  3. orcamento  —  R$ 80,00 para complementos
-- =============================================================================
--  Linha unica, garantida pelo CHECK. O ETL apenas SEMEIA o valor inicial e
--  nunca sobrescreve: depois da primeira carga quem manda aqui e o agente,
--  conforme a Dona Maria aprova compras complementares.
--
--  valor_inicial fica imutavel para que sempre se saiba quanto havia no
--  comeco — sem ele, "sobraram R$ 12" nao diz se ela gastou bem ou mal.
-- =============================================================================
CREATE TABLE IF NOT EXISTS orcamento (
    id             SMALLINT       PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    valor_inicial  NUMERIC(12, 2) NOT NULL CHECK (valor_inicial >= 0),
    atualizado_em  TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  orcamento IS 'Orcamento para complementos. Linha unica; o saldo e a view vw_orcamento';


-- =============================================================================
--  4. perfil  —  o que a Dona Maria tem e sabe fazer
-- =============================================================================
--  Quarta tabela, rompendo o "so tres" e por um motivo.
--
--  O Hermes tem memoria propria (MEMORY.md, USER.md), e ela nao serve aqui
--  por dois defeitos, nenhum contornavel:
--
--    limite    2.200 caracteres no total, algo entre 8 e 15 anotacoes curtas.
--              Utensilio, tecnica e restricao operacional nao cabem.
--
--    congelada o conteudo entra no system prompt no INICIO da sessao e nao
--              muda ate a proxima. O gate da proxima etapa consulta o perfil
--              na hora do aceite; lendo da memoria, ele ficaria cego ao que
--              ela respondeu tres mensagens atras.
--
--  Isto aqui e estado estruturado, consultavel e sempre atual. A memoria do
--  Hermes continua com o que ela e: preferencia durável, jeito de conversar.
--
--  `status` guarda o que o agente AINDA NAO SABE. Uma linha 'pendente' e uma
--  pergunta em aberto — e o item 2.2 do enunciado exige justamente que o
--  agente descubra o que ela nao falou espontaneamente.
-- =============================================================================
CREATE TABLE IF NOT EXISTS perfil (
    id             SERIAL      PRIMARY KEY,
    categoria      TEXT        NOT NULL
                   CHECK (categoria IN ('utensilio', 'tecnica', 'restricao', 'preferencia')),
    item           TEXT        NOT NULL,
    resposta       TEXT,
    status         TEXT        NOT NULL DEFAULT 'pendente'
                   CHECK (status IN ('pendente', 'confirmado')),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Uma linha por assunto: perguntar duas vezes sobre forno e ter duas
    -- respostas diferentes seria pior que nao ter nenhuma.
    UNIQUE (categoria, item)
);

CREATE INDEX IF NOT EXISTS idx_perfil_pendente
    ON perfil (categoria) WHERE status = 'pendente';

COMMENT ON TABLE  perfil IS 'Memoria da elicitacao — o que ja foi perguntado e respondido';
COMMENT ON COLUMN perfil.categoria IS 'utensilio, tecnica, restricao (operacional) ou preferencia (gosto)';
COMMENT ON COLUMN perfil.item      IS 'Ex.: forno, panela de pressao, massa fresca, espaco na geladeira';
COMMENT ON COLUMN perfil.resposta  IS 'O que ela respondeu, nas palavras dela: "tem", "nao tem", "so 2 bocas"';
COMMENT ON COLUMN perfil.status    IS 'pendente = pergunta em aberto, ainda sem resposta dela';


-- =============================================================================
--  5. pratos  —  o cardapio
-- =============================================================================
--  `requisitos` guarda o que a RECEITA exige da cozinha, gravado no momento em
--  que o prato e proposto. O gate le daqui, nao do argumento de quem chama.
--
--  A diferenca decide o desafio: se os requisitos viessem como parametro do
--  aceite, bastaria o agente mandar uma lista vazia para o gate aprovar
--  qualquer coisa. Ele passaria a validar a AFIRMACAO do agente em vez da
--  realidade — que e precisamente o que nao pode acontecer.
--
--      [{"categoria": "utensilio", "item": "forno"},
--       {"categoria": "tecnica",   "item": "bechamel"}]
-- =============================================================================
CREATE TABLE IF NOT EXISTS pratos (
    id          SERIAL         PRIMARY KEY,
    nome        TEXT           NOT NULL UNIQUE,
    fonte       TEXT,
    porcoes     INTEGER        NOT NULL DEFAULT 1 CHECK (porcoes > 0),
    status      TEXT           NOT NULL DEFAULT 'sugerido'
                CHECK (status IN ('sugerido', 'aceito', 'recusado')),
    cmv         NUMERIC(12, 2) CHECK (cmv >= 0),
    preco       NUMERIC(12, 2) CHECK (preco >= 0),
    requisitos  JSONB          NOT NULL DEFAULT '[]'::jsonb,
    criado_em   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

COMMENT ON TABLE  pratos IS 'Pratos sugeridos, aceitos ou recusados pela Dona Maria';
COMMENT ON COLUMN pratos.fonte      IS 'URL da receita encontrada na web';
COMMENT ON COLUMN pratos.status     IS 'So os aceitos consomem estoque e orcamento (ver as views)';
COMMENT ON COLUMN pratos.requisitos IS 'Utensilios/tecnicas que a receita exige — alimenta o gate';


-- =============================================================================
--  6. pratos_ingredientes  —  o que cada prato consome
-- =============================================================================
--  Este e o ledger. Estoque e orcamento NAO sao decrementados em lugar nenhum:
--  a disponibilidade e calculada a partir daqui pelas views abaixo.
--
--  O ganho e a reversibilidade. Se a Dona Maria desistir de um prato, o status
--  vira 'recusado' e o estoque e o dinheiro voltam sozinhos — sem estorno para
--  escrever, sem estorno para errar.
--
--  Nao ha FK para `despensa`: aquelas tres tabelas sao projecao da planilha e
--  o ETL as reescreve a cada subida. Uma dependencia daqui poderia bloquear
--  uma recarga por causa de um prato antigo.
-- =============================================================================
CREATE TABLE IF NOT EXISTS pratos_ingredientes (
    prato_id     INTEGER        NOT NULL REFERENCES pratos(id) ON DELETE CASCADE,
    ingrediente  TEXT           NOT NULL,
    quantidade   NUMERIC(14, 6) NOT NULL CHECK (quantidade > 0),
    comprar      BOOLEAN        NOT NULL DEFAULT FALSE,

    PRIMARY KEY (prato_id, ingrediente)
);

CREATE INDEX IF NOT EXISTS idx_pratos_ing_ingrediente
    ON pratos_ingredientes (ingrediente);

-- Ingrediente que ela NAO tem nao esta em `precos`, entao nao ha custo
-- unitario para multiplicar — e o orcamento ficava parado em R$ 80 mesmo com
-- compras pendentes. Esta coluna guarda quanto custa comprar ESTA quantidade,
-- em reais, pesquisado na hora.
ALTER TABLE pratos_ingredientes
    ADD COLUMN IF NOT EXISTS custo_compra NUMERIC(12, 2) CHECK (custo_compra >= 0);

-- A unidade precisa ser gravada, nao inferida. Para item da despensa da para
-- deduzir olhando `despensa`; para item COMPRADO nao ha de onde — e sem ela a
-- quantidade da receita entrava crua ("200", de 200 ml) e a view somava 200
-- com 0,2 no mesmo campo. Guardar a unidade e o que torna a soma valida.
ALTER TABLE pratos_ingredientes
    ADD COLUMN IF NOT EXISTS unidade_base TEXT CHECK (unidade_base IN ('kg', 'L', 'un'));

COMMENT ON COLUMN pratos_ingredientes.quantidade   IS 'Na unidade_base do ingrediente, ja convertida';
COMMENT ON COLUMN pratos_ingredientes.comprar      IS 'TRUE = nao tem na despensa, sai do orcamento';
COMMENT ON COLUMN pratos_ingredientes.custo_compra IS 'R$ para comprar a quantidade. So para item fora da despensa';


-- =============================================================================
--  Migracao: o saldo do orcamento virou conta
-- =============================================================================
--  `valor_disponivel` era coluna gravada. Com o ledger, o saldo e derivado dos
--  pratos aceitos — e manter as duas coisas criaria duas verdades sobre o
--  mesmo numero, que e o defeito que este projeto inteiro combate.
-- =============================================================================
ALTER TABLE orcamento DROP COLUMN IF EXISTS valor_disponivel;


-- =============================================================================
--  7. compras  —  o que ela comprou depois da planilha
-- =============================================================================
--  A planilha e uma foto do dia em que ela montou a despensa. Tudo que entra
--  depois — os complementos pagos com os R$ 80 — entra aqui.
--
--  Existe porque faltava um lugar para a compra ser um FATO. Antes, comprar
--  creme de leite era so uma linha em `pratos_ingredientes` dizendo quanto o
--  prato consome: a sobra sumia, e um segundo prato com o mesmo ingrediente
--  voltava a dizer "nao esta na despensa" — ela tinha a caixa na geladeira e
--  o sistema nao sabia.
--
--  Duas consequencias, e as duas importam:
--
--    estoque    `vw_estoque` soma despensa + compras. A caixa que sobrou fica
--               disponivel para o proximo prato, como deveria.
--
--    orcamento  `vw_orcamento` passa a debitar o que ela GASTOU, nao o que os
--               pratos consomem. Comprar duas caixas e usar uma tira duas do
--               orcamento — que e o que acontece no caixa dela.
-- =============================================================================
CREATE TABLE IF NOT EXISTS compras (
    id            SERIAL         PRIMARY KEY,
    ingrediente   TEXT           NOT NULL,
    quantidade    NUMERIC(14, 6) NOT NULL CHECK (quantidade > 0),
    unidade_base  TEXT           NOT NULL CHECK (unidade_base IN ('kg', 'L', 'un')),
    custo_total   NUMERIC(12, 2) NOT NULL CHECK (custo_total >= 0),
    prato_id      INTEGER        REFERENCES pratos(id) ON DELETE SET NULL,
    comprado_em   TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_compras_ingrediente ON compras (ingrediente);

COMMENT ON TABLE  compras IS 'Complementos comprados com o orcamento de R$ 80';
COMMENT ON COLUMN compras.quantidade  IS 'Quanto ela comprou, na unidade base — pode ser mais que a receita usa';
COMMENT ON COLUMN compras.custo_total IS 'R$ que sairam do bolso dela por esta compra';
COMMENT ON COLUMN compras.prato_id    IS 'Prato que motivou a compra. NULO quando ela compra por conta propria';


-- =============================================================================
--  8. cardapio  —  o que a Dona Maria colocou a venda
-- =============================================================================
--  Publicar e um FATO DATADO, nao uma coluna booleana em `pratos`. Tirar do ar
--  preenche `retirado_em` em vez de apagar a linha: os pedidos ja feitos
--  continuam apontando para a publicacao que os originou, com o preco que
--  vigorava. Com booleano, despublicar deixaria pedido orfao e o historico de
--  quanto ela cobrava sumiria.
--
--  `lotes` e quantas vezes ela vai cozinhar a receita. A receita rende
--  `pratos.porcoes`, entao o cardapio oferece lotes x porcoes — e compromete
--  lotes x ingrediente no `vw_estoque`. E o que impede publicar 60 marmitas
--  com feijao para 4.
-- =============================================================================
CREATE TABLE IF NOT EXISTS cardapio (
    id            SERIAL         PRIMARY KEY,
    prato_id      INTEGER        NOT NULL REFERENCES pratos(id) ON DELETE CASCADE,
    preco         NUMERIC(12, 2) NOT NULL CHECK (preco > 0),
    lotes         INTEGER        NOT NULL DEFAULT 1 CHECK (lotes > 0),
    publicado_em  TIMESTAMPTZ    NOT NULL DEFAULT now(),
    retirado_em   TIMESTAMPTZ
);

-- Uma publicacao ativa por prato. Nao e zelo: o `vw_estoque` faz LEFT JOIN
-- aqui, e duas linhas ativas para o mesmo prato duplicariam cada ingrediente
-- na conta do comprometido. O indice e o que torna aquele JOIN seguro.
CREATE UNIQUE INDEX IF NOT EXISTS idx_cardapio_ativo
    ON cardapio (prato_id) WHERE retirado_em IS NULL;

COMMENT ON TABLE  cardapio IS 'Pratos publicados para venda — o cliente so enxerga daqui';
COMMENT ON COLUMN cardapio.preco       IS 'Preco de venda por porcao, definido por ELA';
COMMENT ON COLUMN cardapio.lotes       IS 'Quantas vezes a receita sera feita; oferta = lotes x pratos.porcoes';
COMMENT ON COLUMN cardapio.retirado_em IS 'NULO = no ar. Preenchido = saiu, mas o historico fica';


-- =============================================================================
--  9. pedidos  —  o que o cliente comprou
-- =============================================================================
--  `preco_unitario` e COPIADO da publicacao, nao lido dela por FK. O preco e
--  do momento da compra: se ela reajustar amanha, o que ja foi vendido
--  continua valendo o que foi cobrado. Ler por JOIN reescreveria o passado a
--  cada mudanca de preco.
--
--  As tres colunas de dinheiro sao GENERATED. A taxa de 10% do enunciado
--  incide sobre a venda, e deixar essa multiplicacao para quem inserir a linha
--  e convidar a divergencia — foi exatamente esse tipo de conta derivada a
--  mao que gerou os piores erros deste projeto. Aqui o banco calcula, sempre.
-- =============================================================================
CREATE TABLE IF NOT EXISTS pedidos (
    id              SERIAL         PRIMARY KEY,
    cardapio_id     INTEGER        NOT NULL REFERENCES cardapio(id),
    cliente         TEXT           NOT NULL,
    porcoes         INTEGER        NOT NULL CHECK (porcoes > 0),
    preco_unitario  NUMERIC(12, 2) NOT NULL CHECK (preco_unitario > 0),
    criado_em       TIMESTAMPTZ    NOT NULL DEFAULT now(),

    valor_bruto   NUMERIC(12, 2) GENERATED ALWAYS AS
                  (porcoes * preco_unitario) STORED,
    taxa          NUMERIC(12, 2) GENERATED ALWAYS AS
                  (ROUND(porcoes * preco_unitario * 0.10, 2)) STORED,
    valor_liquido NUMERIC(12, 2) GENERATED ALWAYS AS
                  (porcoes * preco_unitario
                   - ROUND(porcoes * preco_unitario * 0.10, 2)) STORED
);

CREATE INDEX IF NOT EXISTS idx_pedidos_cardapio ON pedidos (cardapio_id);

COMMENT ON TABLE  pedidos IS 'Pedidos do cliente — a unica entrada de dinheiro';
COMMENT ON COLUMN pedidos.preco_unitario IS 'Congelado na compra: reajuste posterior nao reescreve o passado';
COMMENT ON COLUMN pedidos.taxa           IS 'Os 10% da plataforma. Calculado pelo banco, nunca informado';
COMMENT ON COLUMN pedidos.valor_liquido  IS 'O que de fato entra no caixa dela — USE ESTE';


-- =============================================================================
--  Visoes derivadas
--
--  Estoque comprometido e orcamento gasto sao CONTA, nao dado — por isso sao
--  view e nao coluna. Assim nunca dessincronizam do que esta em `pratos`.
-- =============================================================================
-- DROP antes de criar: `CREATE OR REPLACE VIEW` recusa mudanca de TIPO de
-- coluna, e a soma de despensa + compras muda `estoque_total` de
-- numeric(14,6) para numeric. Sem o drop o script aborta aqui e o resto do
-- arquivo — inclusive a outra view — nao chega a rodar.
DROP VIEW IF EXISTS vw_estoque;
CREATE VIEW vw_estoque AS
WITH origem AS (
    -- O que veio da planilha
    SELECT d.ingrediente, d.unidade_base,
           d.quantidade_base AS quantidade,
           d.quantidade_base * pr.custo_unitario AS valor,
           d.unidade AS unidade_planilha
      FROM despensa d
      JOIN precos   pr USING (ingrediente)
    UNION ALL
    -- E o que ela comprou depois
    SELECT c.ingrediente, c.unidade_base,
           c.quantidade,
           c.custo_total,
           'comprado' AS unidade_planilha
      FROM compras c
),
somado AS (
    SELECT ingrediente,
           min(unidade_base)      AS unidade_base,
           SUM(quantidade)        AS estoque_total,
           -- Media ponderada: comprar mais caro depois muda o custo do que
           -- resta. Usar so o preco da planilha subestimaria o CMV do prato
           -- seguinte; usar so o da ultima compra o superestimaria.
           SUM(valor) / NULLIF(SUM(quantidade), 0) AS custo_unitario,
           min(unidade_planilha)  AS unidade_planilha
      FROM origem GROUP BY ingrediente
),
compromisso AS (
    -- Conta TODO consumo de prato aceito, comprado ou nao. O filtro
    -- `NOT pi.comprar` fazia sentido quando a compra nao entrava no estoque;
    -- agora que entra, nao descontar o consumo faria ela comprar duas caixas,
    -- usar uma, e o sistema dizer que tem duas.
    --
    -- O `* lotes` e o que liga a publicacao ao estoque: publicar tres lotes e
    -- cozinhar a receita tres vezes, e compromete tres vezes o ingrediente.
    --
    -- COALESCE 1 e o que preserva o comportamento anterior — prato aceito e
    -- nao publicado continua comprometendo exatamente uma receita, como antes
    -- de existir cardapio. O LEFT JOIN nao multiplica linha porque
    -- `idx_cardapio_ativo` garante no maximo uma publicacao ativa por prato.
    SELECT pi.ingrediente,
           SUM(pi.quantidade * COALESCE(c.lotes, 1)) AS quantidade
      FROM pratos_ingredientes pi
      JOIN pratos        p ON p.id = pi.prato_id AND p.status = 'aceito'
      LEFT JOIN cardapio c ON c.prato_id = pi.prato_id AND c.retirado_em IS NULL
     GROUP BY pi.ingrediente
)
SELECT s.ingrediente,
       s.unidade_base,
       s.estoque_total,
       COALESCE(cp.quantidade, 0)                   AS comprometido,
       s.estoque_total - COALESCE(cp.quantidade, 0) AS disponivel,
       s.custo_unitario,
       s.unidade_planilha
  FROM somado s
  LEFT JOIN compromisso cp USING (ingrediente);

COMMENT ON VIEW vw_estoque IS 'Despensa + compras, menos o consumo dos ACEITOS x lotes publicados';


DROP VIEW IF EXISTS vw_orcamento;
CREATE VIEW vw_orcamento AS
-- Debita o que ela GASTOU, nao o que os pratos consomem. Comprar duas caixas
-- e usar uma tira duas do orcamento — que e o que acontece no caixa dela. A
-- versao anterior inferia o gasto do consumo dos pratos e a sobra sumia.
SELECT o.valor_inicial AS total,
       COALESCE((SELECT SUM(custo_total) FROM compras), 0)::NUMERIC(12, 2) AS gasto,
       (o.valor_inicial
        - COALESCE((SELECT SUM(custo_total) FROM compras), 0))::NUMERIC(12, 2) AS restante
  FROM orcamento o
 WHERE o.id = 1;

COMMENT ON VIEW vw_orcamento IS 'Os R$ 80 para complementos: total, gasto e restante';
COMMENT ON COLUMN orcamento.valor_inicial    IS 'R$ 80,00 do enunciado. Imutavel, e a referencia';


-- =============================================================================
--  vw_cardapio  —  o que esta a venda AGORA
-- =============================================================================
--  O `p.status = 'aceito'` nao e redundante. Se ela mudar de ideia e recusar um
--  prato ja publicado, ele sai do cardapio sozinho — mesmo padrao de ledger do
--  estoque: a disponibilidade e conta, nao dado, e nao ha estorno para esquecer.
--
--  `porcoes_disponiveis` e o teto que o cliente nao pode furar. Sai daqui, nao
--  do prompt do agente: e a diferenca entre um bot instruido a nao vender
--  demais e um que nao consegue.
-- =============================================================================
DROP VIEW IF EXISTS vw_cardapio;
CREATE VIEW vw_cardapio AS
SELECT c.id                AS cardapio_id,
       p.id                AS prato_id,
       p.nome,
       c.preco,
       p.cmv,
       p.porcoes           AS porcoes_por_lote,
       c.lotes,
       c.lotes * p.porcoes AS porcoes_publicadas,
       COALESCE(SUM(ped.porcoes), 0)::INTEGER AS porcoes_vendidas,
       (c.lotes * p.porcoes - COALESCE(SUM(ped.porcoes), 0))::INTEGER
                           AS porcoes_disponiveis,
       COALESCE(SUM(ped.valor_liquido), 0)::NUMERIC(12, 2) AS receita_liquida,
       c.publicado_em
  FROM cardapio c
  JOIN pratos   p   ON p.id = c.prato_id
  LEFT JOIN pedidos ped ON ped.cardapio_id = c.id
 WHERE c.retirado_em IS NULL
   AND p.status = 'aceito'
 GROUP BY c.id, p.id, p.nome, c.preco, p.cmv, p.porcoes, c.lotes, c.publicado_em;

COMMENT ON VIEW vw_cardapio IS 'Pratos no ar, com quantas porcoes ainda restam';


-- =============================================================================
--  vw_caixa  —  o dinheiro dela, com as vendas
-- =============================================================================
--  Fica AO LADO de `vw_orcamento`, nao no lugar dela. A vw_orcamento responde
--  a pergunta do enunciado ("dos R$ 80, quanto sobrou para complementos") e
--  continua respondendo exatamente isso. Esta responde outra: quanto ela tem
--  no caixa hoje, ja com o que vendeu.
--
--  Entra o LIQUIDO. Somar o bruto contaria como dela os 10% que vao para a
--  plataforma, e o saldo mentiria para cima justamente na direcao que faria
--  ela gastar o que nao tem.
-- =============================================================================
DROP VIEW IF EXISTS vw_caixa;
CREATE VIEW vw_caixa AS
SELECT o.valor_inicial AS orcamento_inicial,
       COALESCE((SELECT SUM(custo_total)   FROM compras), 0)::NUMERIC(12, 2) AS gasto,
       COALESCE((SELECT SUM(valor_bruto)   FROM pedidos), 0)::NUMERIC(12, 2) AS vendas_brutas,
       COALESCE((SELECT SUM(taxa)          FROM pedidos), 0)::NUMERIC(12, 2) AS taxa_plataforma,
       COALESCE((SELECT SUM(valor_liquido) FROM pedidos), 0)::NUMERIC(12, 2) AS receita_liquida,
       (o.valor_inicial
        - COALESCE((SELECT SUM(custo_total)   FROM compras), 0)
        + COALESCE((SELECT SUM(valor_liquido) FROM pedidos), 0))::NUMERIC(12, 2) AS saldo
  FROM orcamento o
 WHERE o.id = 1;

COMMENT ON VIEW vw_caixa IS 'Orcamento - compras + vendas liquidas. O que ela pode gastar hoje';
