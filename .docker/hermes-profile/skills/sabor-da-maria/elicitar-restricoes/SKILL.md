---
name: elicitar-restricoes
description: "Use antes de fechar prato: descobrir utensilios, tecnicas e limites."
version: 2.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [elicitacao, cardapio, dona-maria]
    category: sabor-da-maria
    related_skills: [avaliar-viabilidade, orquestrar-atendimento]
---

# Elicitar restrições

O erro que não pode acontecer:

> A Dona Maria aceita um prato, gasta o orçamento comprando ingrediente, e só
> na hora de cozinhar descobre que não tem panela de pressão.

Descobrir isso **antes** é o trabalho. O `aceitar_prato` te trava se faltar
algo — mas ele só sabe o que você registrou. Se você não perguntou, ele não
tem como saber.

## Comece sabendo o que já sabe

```
consultar_perfil()
```

Devolve `sabido` e `pendentes`. Se `vazio` for true, ela é nova aqui e você
vai descobrir tudo do zero. Se houver `pendentes`, são perguntas que **você
já fez** e ela não respondeu — retome dali em vez de começar de novo.

Chame isto **antes** de perguntar qualquer coisa sobre cozinha. Repetir
pergunta que ela já respondeu passa a impressão de que ninguém escutou.

## As três categorias

| categoria | o que investigar |
|---|---|
| `utensilio` | fogão (quantas bocas?), forno, panela de pressão, air fryer, liquidificador, batedeira, freezer |
| `tecnica` | massa fresca, béchamel, ponto de carne, fritura, confeitaria, temperagem de chocolate |
| `restricao` | espaço na geladeira, botijão de gás, tempo por cozinhada, quantas marmitas por vez |

Há uma quarta, `preferencia`, para o que ela gosta ou não gosta de **cozinhar**.
Ela não trava prato nenhum — serve para você não insistir num prato que ela
já disse que não quer fazer.

## Não pergunte no vácuo

Pergunta genérica sobre equipamento vira formulário, e formulário cansa. As
perguntas boas nascem de um prato concreto:

> *"Essa lasanha vai ao forno por 40 minutos. A senhora tem forno?"*

O `propor_prato` já devolve as pendências **com a pergunta pronta**. Use o
texto que vem — foi escrito para ser dito. Não reescreva para soar mais
natural.

## Uma por vez, mas junte o que der

Três pendências viram três perguntas — porém uma mensagem só:

> *"Duas coisas antes de fechar: a senhora tem forno, e sabe fazer béchamel?"*

Melhor que duas rodadas. O que não vale é despejar oito de uma vez.

## Grave na mesma resposta

Cada coisa que ela contar vai para `registrar_perfil` **antes** de você
continuar. Uma frase costuma trazer vários fatos — *"tenho fogão de 4 bocas e
forno, mas não tenho panela de pressão"* são três. Mande os três juntos.

Registre também a pergunta que você acabou de fazer, com `resposta` vazia. É
assim que você sabe o que ainda falta na próxima conversa.

## Não responda no lugar dela

Se ela não disse, ela não disse. *"Provavelmente tem liquidificador"* não é
resposta — é chute com cara de fato, e o gate vai aceitar como se ela tivesse
confirmado.

Item vago também não vale: grave `forno`, não `forno do bolo de chocolate`. O
gate casa o requisito do prato contra o nome gravado, e nome amarrado ao prato
da vez não casa com nada.
