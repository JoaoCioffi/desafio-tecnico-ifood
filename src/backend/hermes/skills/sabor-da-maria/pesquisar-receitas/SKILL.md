---
name: pesquisar-receitas
description: "Buscar receitas reais na web que aproveitem a despensa da Dona Maria, e traduzi-las para as unidades do banco."
version: 1.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [receitas, web-search, cardapio]
    category: sabor-da-maria
    related_skills: [elicitar-restricoes, avaliar-viabilidade]
---

# Pesquisar receitas

Receita vem da **internet, com fonte**. Nunca da sua memória — a Dona Maria
vai cozinhar isso de verdade, e quantidade inventada vira prato ruim ou
prejuízo.

## Olhe a despensa primeiro

```
despensa()
```

Ela tem 37 ingredientes. Procure receita que **aproveite o que já está lá**,
não que exija compra. Cada item novo sai do orçamento de R$ 80.

Repare no que ela tem em quantidade — arroz, feijão, frango, carne, batata,
ovos. É a base do cardápio. E repare no que está parado e caro: um balde de
2 kg de alcaparra é dinheiro sentado na prateleira. Prato que gaste isso vale
ouro.

## Busque

**A formulação da busca decide a qualidade do resultado.** Isto foi medido,
não é opinião:

```
❌ "receita feijoada caseira feijão preto bacon acém"
   → TikTok, lista de restaurantes em Goiás, catálogo de PDF no Scribd

✅ "feijoada receita ingredientes modo de preparo"
   → Receitas Nestlé, TudoGostoso, Sabores Ajinomoto
```

Empilhar ingredientes na busca **piora** o resultado: o buscador tenta casar
todos os termos e cai em página de supermercado e vídeo curto. O que funciona
é o padrão de quem procura receita de verdade:

```
<nome do prato> receita ingredientes modo de preparo
```

Quando quiser resultado previsível, restrinja o site:

```
receita de feijoada completa site:tudogostoso.com.br
```

Os ingredientes da despensa entram **na escolha do prato**, não na string de
busca. Você olha a despensa, decide "dá para fazer feijoada", e aí busca
feijoada.

Depois `web_extract` na página escolhida para pegar a lista de ingredientes.

**Descarte receita sem quantidade.** "Sal a gosto" tudo bem; "carne" sem
peso, não — sem número não dá para calcular CMV.

## Traduza para o banco

A receita vem em unidade caseira. O banco quer **kg, L ou un**.

| A receita diz | Você salva |
| --- | --- |
| 1 xícara de arroz | `0.180` kg |
| 2 colheres de sopa de óleo | `0.030` L |
| 3 dentes de alho | `0.009` kg |
| 500 g de carne | `0.500` kg |

Conversões que você não souber, **pergunte a ela**. Quem cozinha sabe quanto
pesa uma xícara de farinha; você não. Chutar aqui contamina o CMV inteiro.

Nome do ingrediente: use o nome **da receita**, não o da despensa. O
`prato_salvar` faz o casamento — "cebola roxa" acha "Cebola". O que não casar
volta em `nao_casados`, e aí você pergunta.

## Salve — uma vez só, com a lista completa

> ⚠️ **`prato_salvar` SUBSTITUI a lista de ingredientes a cada chamada.**
> Chamar duas vezes não soma: a segunda apaga a primeira. Se você salvar em
> pedaços, sobra só o último pedaço — e o CMV sai errado por baixo.

O procedimento:

1. **Extraia a receita inteira primeiro.** Monte a lista completa antes de
   tocar na ferramenta.
2. **Converta cada item** para kg/L, por porção.
3. **Confira o número de ingredientes.** Uma feijoada tem 8–12 itens; um
   arroz de forno, 6–10. Se sua lista tem 2, você não terminou de ler a
   página — volte.
4. **Aí sim** chame `prato_salvar`, uma vez.

```
prato_salvar(
  nome="Feijoada da Maria",
  fonte="https://...",                     # a URL de verdade
  ingredientes=[
    {"ingrediente": "Feijão preto",           "quantidade": 0.120},
    {"ingrediente": "Carne de panela (acém)", "quantidade": 0.100},
    {"ingrediente": "Bacon",                  "quantidade": 0.040},
    {"ingrediente": "Arroz branco tipo 1",    "quantidade": 0.100},
    {"ingrediente": "Couve",                  "quantidade": 0.040},
    {"ingrediente": "Farinha de mandioca",    "quantidade": 0.030},
    {"ingrediente": "Cebola",                 "quantidade": 0.030},
    {"ingrediente": "Alho",                   "quantidade": 0.005},
    # ... a lista TODA
  ],
  requisitos=[{"categoria": "utensilio", "item": "panela de pressao"}]
)
```

### Ordem de grandeza — confira antes de salvar

Quantidade por porção, para você perceber quando errou uma casa decimal:

| Item | Por marmita |
| --- | --- |
| grão (feijão, arroz, cru) | 0,08 – 0,15 kg |
| carne | 0,10 – 0,20 kg |
| embutido (bacon, calabresa) | 0,03 – 0,06 kg |
| legume/verdura | 0,03 – 0,08 kg |
| tempero (alho, sal, caldo) | 0,003 – 0,01 kg |
| óleo | 0,01 – 0,02 L |

`0,033 kg` de feijão numa feijoada é 33 gramas — não alimenta ninguém. Se um
número seu ficar fora dessas faixas, você provavelmente dividiu pelo número
errado de porções.

**Os `requisitos` são a parte que costuma ser esquecida.** Leia o modo de
preparo e extraia o que a cozinha precisa ter: forno, panela de pressão,
liquidificador, batedeira. Sem isso o gate não tem o que checar, e o prato
passa sem ninguém garantir que ela consegue fazer.

Quantidade sempre **por porção**, não da receita inteira. Se a receita rende
6 e pede 1,2 kg de feijão, salve `0.200`.

## Apresente e escute

Não pesquise dez receitas de uma vez. Traga **uma ou duas**, com a fonte, e
pergunte:

> *"Achei uma feijoada que usa o feijão preto, o acém e o bacon que a senhora
> tem. A senhora gosta de fazer feijoada? Vê algum problema?"*

A opinião dela vale mais que a margem. Se ela não gosta de fazer o prato, ele
sai — mesmo que a conta feche bem. Ela vai cozinhar isso toda semana.
