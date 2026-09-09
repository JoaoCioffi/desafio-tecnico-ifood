---
name: publicar-cardapio
description: "Use depois que ela aceitar um prato e quiser colocar a venda. Publica, acompanha pedidos e devolve o dinheiro ao orcamento."
version: 1.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [cardapio, venda, orcamento]
    category: sabor-da-maria
    related_skills: [precificar-prato, avaliar-viabilidade]
---

# Publicar no cardápio

Aceitar um prato é ela decidir que vai fazer. **Publicar é colocar à venda.**
São duas coisas, e o prato só chega ao cliente depois da segunda.

Só use isto depois do `aceitar_prato`. Prato sugerido ou recusado não vai ao ar
— a ferramenta recusa, e recusa com razão.

## Pergunte quantas ela consegue fazer

Este é o número que importa, e é dela:

> *"Quantas vezes a senhora consegue fazer essa receita hoje?"*

Não pergunte quantas marmitas ela quer vender. Ela cozinha em **lotes** — a
receita rende o que rende, e três lotes são a receita feita três vezes.

```
propor_prato disse: rende 4 porções
ela respondeu: "faço umas três panelas"
→ publicar_prato(prato_id, preco, lotes=3)  →  12 porções à venda
```

Se ela responder em marmitas ("quero vender umas 10"), faça a conta e confirme:
*"então são 3 panelas, que dão 12 — publico as 12?"*. Ela decide, você converte.

## Cada lote consome ingrediente de verdade

Publicar 3 lotes compromete **três vezes** o ingrediente na despensa. Não é
contabilidade: é o feijão que ela vai usar.

Confira antes de publicar um número alto. Se `consultar_despensa` mostra frango
para duas panelas e ela pedir cinco, diga isso — ela precisa comprar mais ou
publicar menos. Deixar o estoque negativo faz ela descobrir na hora de cozinhar,
que é exatamente o erro que este projeto existe para evitar.

## O preço é o mesmo que ela aprovou

Use o preço que saiu do `cenarios_preco` e que **ela** escolheu. Se ela quiser
outro na hora de publicar, tudo bem — mas recalcule a margem e diga o que muda
antes de gravar.

Republicar o mesmo prato troca preço e lotes. Não cria uma segunda oferta, e os
pedidos já feitos continuam valendo o preço que tinham.

## Acompanhe as vendas

```
consultar_pedidos()
```

Traz o que foi vendido e o caixa dela. Dois números diferentes:

| | |
|---|---|
| **bruto** | o que o cliente pagou |
| **líquido** | o que sobrou depois dos 10% da plataforma — **este é o dela** |

Ao contar para ela, use o líquido. O bruto nunca foi dela, e tratar os dois
como a mesma coisa é como ela ia se enganar sozinha.

## O dinheiro que entra financia a próxima compra

O `saldo` do caixa é **orçamento inicial − compras + vendas líquidas**. Ele
cresce quando ela vende.

É isso que muda a conversa depois da primeira venda. Antes, todo ingrediente
novo disputava os R$ 80. Depois, ela tem de onde tirar:

> *"Entraram R$ 76,50 das doze marmitas. Dá para comprar o creme de leite
> daquele prato que a senhora tinha gostado."*

Ofereça isso quando fizer sentido. É o motivo de ela estar fazendo tudo isso.

## Tirar do ar

```
despublicar_prato(prato_id)
```

Use quando acabar o ingrediente, quando ela quiser mudar o cardápio do dia, ou
quando ela simplesmente não quiser mais fazer aquele prato.

Os pedidos já feitos continuam existindo e o dinheiro continua no caixa — ela
recebeu, e recebeu de verdade. O que volta é o ingrediente comprometido, que
fica livre para outro prato.
