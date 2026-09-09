---
name: precificar-prato
description: "Use ao chegar no preco: CMV por porcao e cenarios de margem."
version: 2.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [preco, cmv, margem]
    category: sabor-da-maria
    related_skills: [explicar-cmv, avaliar-viabilidade]
---

# Precificar um prato

## A conta

```
calcular_cmv(ingredientes, porcoes)   → custo por porção, aberto por item
cenarios_preco(cmv_porcao)            → mínimo + 3 opções, matemática aberta
```

Você **não calcula nada**. Nem no terminal, nem em código, nem de cabeça.

## O erro que multiplica o preço por seis

`calcular_cmv` devolve dois números:

```
cmv          R$ 16,54   custo da RECEITA INTEIRA
cmv_porcao   R$  2,76   custo de UMA porção     ← este vai para o preço
```

Passar o `cmv` para `cenarios_preco` produz preços seis vezes maiores numa
receita que rende seis. Nada na resposta denuncia — os números ficam
coerentes entre si e absurdos na prática.

**Sempre `cmv_porcao`.** Quem se vende no delivery é a marmita, não a panela.

Se você não sabe o rendimento, pergunte antes. Sem ele, `porcoes` vira 1 e o
custo da panela inteira vira o preço de uma marmita.

## Três números, nesta ordem

1. **Quanto de comida vai em cada marmita** — em reais, não como sigla
2. **O preço mínimo** — abaixo disso ela paga para trabalhar
3. **As opções** — e o que cada uma significa

A taxa de 10% incide sobre a **venda**, não sobre o lucro. É a armadilha
principal: com CMV de R$ 6,02, vender a R$ 6,50 parece lucro e é prejuízo —
ela recebe R$ 5,85. O `preco_minimo` já resolve; cite o valor, não recalcule.

## Apresentando

Conta aberta, em reais, sem jargão:

> *"Cada marmita leva R$ 2,76 de ingredientes. Como o delivery fica com 10%
> da venda, o mínimo para não sair no prejuízo é R$ 3,07 — e nesse preço a
> senhora não ganha nada.*
>
> *A R$ 9,20 a comida fica em 30% do preço: entram R$ 8,28, saem R$ 2,76 de
> ingrediente, e sobram R$ 5,52 para a senhora."*

Depois de mostrar em reais, a leitura em porcentagem ajuda:

> *"De cada R$ 10 que o cliente paga: R$ 1 vai para a plataforma, R$ 3 são os
> ingredientes, e R$ 6 ficam com a senhora."*

## Diga o que a margem ainda vai pagar

Os cenários cobrem comida e taxa. Não cobrem embalagem, gás, energia nem o
trabalho dela. O retorno traz o campo `cobertura` com o texto pronto — **use**.

Margem de 60% que ela entende como 60% no bolso é pior que não ter dado
margem nenhuma.

## Ela escolhe

Recomende, com o motivo. Nunca decida.

> *"Eu iria de R$ 9,20 — é o meio-termo. Mas a senhora conhece a vizinhança;
> qual dessas faixas faz sentido para o seu público?"*

E quando ela escolher, `aceitar_prato(prato_id, preco)` com o valor **dela**.
Nunca com o que você recomendou.
