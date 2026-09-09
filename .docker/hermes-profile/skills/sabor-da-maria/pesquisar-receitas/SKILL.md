---
name: pesquisar-receitas
description: "Use ao procurar prato novo. Busca na web aproveitando a despensa."
version: 2.0.0
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

E não peça a receita a ela antes de procurar. Ela veio aqui justamente porque
não sabe o que fazer com o que comprou.

## Olhe a despensa primeiro

```
consultar_despensa()
```

São 37 ingredientes. Procure receita que **aproveite o que já está lá**, não
que exija compra: cada item novo sai do orçamento de R$ 80.

Repare no que ela tem em quantidade — arroz, feijão, frango, carne, batata,
ovos. É a base do cardápio.

E repare no que está **parado e caro**. Um balde de 2 kg de alcaparra são
R$ 82 sentados na prateleira; azeite extra virgem, chantilly e amêndoa
fatiada estão na mesma situação. Prato que gaste isso vale ouro — transforma
dinheiro empatado em cardápio.

Use o campo `estoque`, que já é o **disponível**: o que outros pratos aceitos
comprometeram não aparece ali.

## Busque

A formulação decide o resultado. Busque por **prato + ingrediente principal**,
não por lista de ingredientes:

```
web_search("receita frango com batata forno rendimento porções")
```

Uma busca por *"receita com frango, batata, cebola, alho, óleo, sal"* devolve
agregadores genéricos. Uma por *"frango assado com batata"* devolve receitas
de verdade.

Peça sempre **rendimento**. Sem quantas porções a receita rende, o custo por
marmita não existe — e é o custo por marmita que vira preço.

## Traduza para o banco

A receita da web fala em "1 xícara", "a gosto", "2 dentes". O `propor_prato`
precisa de número e unidade:

| na receita | o que registrar |
|---|---|
| 1 xícara de arroz | 180 g |
| 2 dentes de alho | 6 g |
| 1 colher de sopa de azeite | 15 ml |
| sal a gosto | 5 g, e **diga** que assumiu |
| 1 cebola média | 110 g |

Converter é seu trabalho, não dela. O que você não converte sozinho é o que
muda o custo de forma perceptível — aí pergunte.

## Ingrediente que ela não tem: pesquise o preço

Receita que pede algo fora da despensa custa dinheiro do orçamento de R$ 80.
E o `propor_prato` **não deixa passar sem preço** — vira pendência.

Então pesquise antes:

```
web_search("preço creme de leite 200ml supermercado")
```

E informe no `custo_compra` do ingrediente: quantos reais para comprar
**aquela quantidade**, não o preço por quilo.

Sem isso o orçamento fica parado em R$ 80 enquanto ela gasta de verdade — e
ela monta o cardápio achando que tem dinheiro que já foi.

Se não achar preço confiável, aí sim pergunte a ela: *"quanto costuma custar
o creme de leite onde a senhora compra?"* Ela conhece o mercado dela melhor
que a internet.

### Comprou um pacote, não gastou um pacote

Quando ela compra um pacote de tempero, o prato consome uma **fração** dele.
São coisas diferentes, e confundir as duas quebra o estoque:

```
comprou   1 pacote de pimenta-do-reino    R$ 3,49   → registrar_compra
consome   1/20 do pacote por prato        R$ 0,17   → propor_prato
```

Registrar o pacote inteiro como consumo faz dois pratos comprometerem dois
pacotes — e a despensa fica negativa com um pacote na prateleira. É o mesmo
erro do balde de alcaparra, do outro lado da conta.

Se ela comprar mais de uma unidade, use `registrar_compra` para a quantidade
**total** comprada. A sobra fica disponível para o próximo prato, em vez de
virar compra repetida.

## Prefira o que ela já tem

Antes de propor um prato com compra, veja se dá para trocar o ingrediente por
algo da despensa. Ela tem leite, manteiga e farinha — um molho branco caseiro
sai de graça e o creme de leite sai do orçamento.

Ofereça as duas versões e deixe ela escolher.

## Antes de apresentar, proponha

Chame `propor_prato` **antes** de comentar a receita com ela. O retorno já diz
se é viável, o que falta perguntar e o que precisa comprar. Apresentar
primeiro e descobrir depois que ela não tem o equipamento é o erro que este
projeto inteiro existe para evitar.

## Apresente com fonte

Diga de onde veio. Ela pode querer ver a receita, e um link também é como ela
confere que você não inventou.

Se a página bloquear a leitura, a skill `blocked-page-recovery` tem o caminho.
