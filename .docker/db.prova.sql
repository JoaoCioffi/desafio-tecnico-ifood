-- =============================================================================
--  Prova do esquema — roda contra um Postgres descartavel, nao contra o seu.
-- =============================================================================
--  Oito invariantes, sendo a primeira a mais importante: `vw_estoque` tem que
--  continuar devolvendo EXATAMENTE o que devolvia antes de existir cardapio.
--  A comparacao e por diferenca de conjuntos nos dois sentidos, nao por
--  inspecao — olhar duas tabelas parecidas e dizer "esta igual" e o jeito
--  classico de nao ver a linha que mudou.
--
--      docker run -d --name prova -e POSTGRES_PASSWORD=x -e POSTGRES_DB=prova postgres:alpine
--      docker cp .docker/db.sql       prova:/tmp/
--      docker cp .docker/db.prova.sql prova:/tmp/
--      docker exec prova psql -U postgres -d prova -q -f /tmp/db.sql
--      docker exec prova psql -U postgres -d prova -q -f /tmp/db.prova.sql
--      docker rm -f prova
--
--  Em Git Bash no Windows, prefixe os comandos com MSYS_NO_PATHCONV=1 — senao
--  o shell converte /tmp para um caminho do Windows antes do docker ver.
-- =============================================================================

\set ON_ERROR_STOP on

-- --------------------------------------------------------------------------
-- A view ANTIGA, sob outro nome, para comparar linha a linha.
-- --------------------------------------------------------------------------
CREATE VIEW vw_estoque_antigo AS
WITH origem AS (
    SELECT d.ingrediente, d.unidade_base, d.quantidade_base AS quantidade,
           d.quantidade_base * pr.custo_unitario AS valor,
           d.unidade AS unidade_planilha
      FROM despensa d JOIN precos pr USING (ingrediente)
    UNION ALL
    SELECT c.ingrediente, c.unidade_base, c.quantidade, c.custo_total,
           'comprado' AS unidade_planilha
      FROM compras c
),
somado AS (
    SELECT ingrediente, min(unidade_base) AS unidade_base,
           SUM(quantidade) AS estoque_total,
           SUM(valor) / NULLIF(SUM(quantidade), 0) AS custo_unitario,
           min(unidade_planilha) AS unidade_planilha
      FROM origem GROUP BY ingrediente
)
SELECT s.ingrediente, s.unidade_base, s.estoque_total,
       COALESCE(SUM(pi.quantidade) FILTER (WHERE p.status = 'aceito'), 0) AS comprometido,
       s.estoque_total
         - COALESCE(SUM(pi.quantidade) FILTER (WHERE p.status = 'aceito'), 0) AS disponivel,
       s.custo_unitario, s.unidade_planilha
  FROM somado s
  LEFT JOIN pratos_ingredientes pi ON pi.ingrediente = s.ingrediente
  LEFT JOIN pratos              p  ON p.id = pi.prato_id
 GROUP BY s.ingrediente, s.unidade_base, s.estoque_total,
          s.custo_unitario, s.unidade_planilha;

-- --------------------------------------------------------------------------
-- Dados: uma despensa pequena, um prato aceito, um recusado.
-- --------------------------------------------------------------------------
-- quantidade_base e custo_unitario sao GENERATED: entram o bruto e o fator.
INSERT INTO despensa (ingrediente, unidade, quantidade, unidade_base, fator)
VALUES ('Peito de frango', 'kg', 2, 'kg', 1),
       ('Arroz branco',    'kg', 5, 'kg', 1),
       ('Alcaparras', 'balde 2kg', 1, 'kg', 2);

INSERT INTO precos (ingrediente, unidade, qtd_comprada, preco_pago, unidade_base, fator)
VALUES ('Peito de frango', 'kg', 2, 28.00, 'kg', 1),
       ('Arroz branco',    'kg', 5, 24.90, 'kg', 1),
       ('Alcaparras', 'balde 2kg', 1, 82.00, 'kg', 2);

INSERT INTO orcamento (id, valor_inicial) VALUES (1, 80.00)
    ON CONFLICT (id) DO UPDATE SET valor_inicial = 80.00;

INSERT INTO pratos (id, nome, porcoes, status, cmv, preco)
VALUES (1, 'Frango com arroz', 4, 'aceito',  5.15, 17.00),
       (2, 'Prato recusado',   4, 'recusado', 9.00, 20.00);
SELECT setval('pratos_id_seq', 2);

INSERT INTO pratos_ingredientes (prato_id, ingrediente, quantidade, unidade_base)
VALUES (1, 'Peito de frango', 0.400, 'kg'),
       (1, 'Arroz branco',    0.320, 'kg'),
       (2, 'Peito de frango', 1.000, 'kg');

\echo ''
\echo '=== 1. sem nada publicado, vw_estoque tem que ser IDENTICA a antiga ==='
SELECT CASE WHEN NOT EXISTS (
           (SELECT * FROM vw_estoque EXCEPT SELECT * FROM vw_estoque_antigo)
           UNION ALL
           (SELECT * FROM vw_estoque_antigo EXCEPT SELECT * FROM vw_estoque))
       THEN 'ok    identicas' ELSE 'FALHOU  divergiram' END AS resultado;

SELECT ingrediente, estoque_total, comprometido, disponivel FROM vw_estoque ORDER BY 1;

\echo ''
\echo '=== 2. publicar 3 lotes compromete 3x o ingrediente ==='
INSERT INTO cardapio (prato_id, preco, lotes) VALUES (1, 17.00, 3);
SELECT ingrediente, comprometido, disponivel FROM vw_estoque
 WHERE ingrediente = 'Peito de frango';
SELECT CASE WHEN (SELECT comprometido FROM vw_estoque WHERE ingrediente='Peito de frango')
              = 1.200000
       THEN 'ok    0,400 x 3 lotes = 1,200' ELSE 'FALHOU' END AS resultado;

\echo ''
\echo '=== 3. o cardapio oferece lotes x porcoes ==='
SELECT nome, preco, lotes, porcoes_por_lote, porcoes_publicadas,
       porcoes_vendidas, porcoes_disponiveis FROM vw_cardapio;

\echo ''
\echo '=== 4. vender desconta das porcoes disponiveis e o dinheiro entra liquido ==='
INSERT INTO pedidos (cardapio_id, cliente, porcoes, preco_unitario)
VALUES ((SELECT cardapio_id FROM vw_cardapio), 'cliente-teste', 5, 17.00);
SELECT porcoes, preco_unitario, valor_bruto, taxa, valor_liquido FROM pedidos;
SELECT porcoes_publicadas, porcoes_vendidas, porcoes_disponiveis FROM vw_cardapio;

\echo ''
\echo '=== 5. o caixa soma a venda liquida ao orcamento ==='
SELECT * FROM vw_caixa;

\echo ''
\echo '=== 6. recusar o prato tira do cardapio sozinho (padrao ledger) ==='
UPDATE pratos SET status = 'recusado' WHERE id = 1;
SELECT count(*) AS linhas_no_cardapio FROM vw_cardapio;
UPDATE pratos SET status = 'aceito' WHERE id = 1;

\echo ''
\echo '=== 7. duas publicacoes ativas do mesmo prato: o indice barra ==='
DO $$
BEGIN
    INSERT INTO cardapio (prato_id, preco, lotes) VALUES (1, 20.00, 1);
    RAISE EXCEPTION 'FALHOU: aceitou duas publicacoes ativas';
EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'ok    barrou a segunda publicacao ativa';
END $$;

\echo ''
\echo '=== 8. despublicar preserva o pedido e libera o ingrediente ==='
UPDATE cardapio SET retirado_em = now() WHERE retirado_em IS NULL;
SELECT (SELECT count(*) FROM pedidos)                                AS pedidos_preservados,
       (SELECT count(*) FROM vw_cardapio)                            AS no_cardapio,
       (SELECT comprometido FROM vw_estoque WHERE ingrediente='Peito de frango')
                                                                     AS volta_a_1_lote;
