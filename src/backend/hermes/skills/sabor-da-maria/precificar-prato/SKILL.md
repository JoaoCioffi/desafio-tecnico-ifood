---
name: precificar-prato
description: "Calcular o CMV, montar os três cenários de preço e deixar a Dona Maria escolher."
version: 1.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [preco, cmv, margem]
    category: sabor-da-maria
    related_skills: [explicar-cmv, avaliar-viabilidade]
---

# Precificar um prato

Só depois de `prato_checar` devolver `apto: true`.

## A conta

```
cmv(prato_id)        → custo dos ingredientes, aberto por item
cenarios(prato_id)   → preço mínimo + 3 opções com a matemática aberta
```

Você **não calcula nada**. As duas ferramentas devolvem pronto.

## O que a Dona Maria precisa entender

Três números, nesta ordem:

1. **CMV** — quanto de comida vai em cada marmita
2. **Preço mínimo** — abaixo disso ela paga para trabalhar
3. **As três opções** — e o que cada uma significa

A taxa de 10% incide sobre a **venda**, não sobre o lucro. É a armadilha
principal: com CMV de R$ 6,02, vender a R$ 6,50 parece lucro e é prejuízo
(ela recebe R$ 5,85). O `preco_minimo` já resolve — cite o valor, não
recalcule.

## Apresentando

Mostre a conta aberta, em reais, sem jargão:

> *"Cada marmita leva R$ 6,02 de ingredientes. Como o iFood fica com 10% da
> venda, o mínimo para não sair no prejuízo é R$ 6,69.*
>
> *Aí a senhora tem três caminhos:*
>
> ***R$ 17,90** — preço de entrada, bom para começar e aparecer nas buscas.
> Sobram R$ 10,09 por marmita.*
>
> ***R$ 23,90** — o equilíbrio. Sobram R$ 15,49, e o preço ainda é
> competitivo para uma feijoada completa.*
>
> ***R$ 29,90** — só faz sentido com porção maior ou embalagem melhor.
> Sobram R$ 20,89, mas vende menos.*
>
> *Qual a senhora prefere?"*

## Depois que ela escolhe

```
prato_aceitar(prato_id, preco)
```

Se ela escolher abaixo do mínimo, a ferramenta recusa e devolve o motivo.
Explique **por que** aquele preço não fecha — não insista, mostre a conta.

## Duas coisas para dizer sem ela perguntar

**O lucro não é o final.** O CMV cobre só ingrediente. Gás, embalagem, sacola
e o tempo dela ainda saem daí. Avise:

> *"Desses R$ 15,49 ainda saem a embalagem e o gás. Quer que a gente estime
> isso também?"*

**Preço alto demais não é lucro.** Margem boa em prato que ninguém pede vale
zero. Se ela quiser cobrar muito acima do mercado, diga com franqueza — e
respeite se ela insistir. A decisão é dela.
