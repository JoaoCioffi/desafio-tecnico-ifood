---
name: avaliar-viabilidade
description: "Use quando o gate bloquear: virar cada pendencia em pergunta."
version: 2.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [gate, viabilidade, elicitacao]
    category: sabor-da-maria
    related_skills: [elicitar-restricoes, precificar-prato]
---

# Avaliar viabilidade

`checar_prato(prato_id)` responde uma pergunta só: **a Dona Maria consegue
fazer este prato hoje?**

```
checar_prato(3)
→ { "apto": false,
    "pendencias": [{"tipo": "utensilio", "chave": "panela de pressao",
                    "pergunta": "A senhora tem panela de pressao?"}],
    "comprar": [], "custo_compras": 0.0 }
```

O `propor_prato` já devolve o mesmo `apto` e as mesmas pendências. Use o
`checar_prato` quando quiser reconferir depois que ela respondeu algo, sem
reproposar o prato.

## As perguntas já vêm prontas

Cada pendência traz o campo `pergunta`. **Use.** Não reescreva para soar mais
natural — foram escritas para serem ditas a ela.

Se vierem três, agrupe numa mensagem só. Registre as respostas com
`registrar_perfil` e chame `checar_prato` de novo: as pendências vão sumindo.

## Cinco coisas podem travar um prato

| tipo | o que significa | o que fazer |
|---|---|---|
| `utensilio` | ela não tem, ou nunca foi perguntado | pergunte |
| `tecnica` | idem, para habilidade | pergunte |
| `unidade` | a receita pede grama de um item vendido por unidade | pergunte o peso da embalagem |
| `estoque` | falta ingrediente e não dá para comprar | ofereça reduzir a porção ou trocar o prato |
| `orcamento` | as compras não cabem nos R$ 80 | mostre quanto falta e deixe ela decidir |

As três primeiras se resolvem perguntando. As duas últimas são aritmética — e
aí a conversa muda de "o que a senhora tem?" para "o que a senhora prefere?".

## Não responda no lugar dela

O erro que já aconteceu: o gate devolveu a pergunta sobre air fryer, e a
tentação foi assumir que quem chama um prato de *"batata na air fryer"*
obviamente tem uma. Talvez tenha. Mas quem grava a resposta é ela.

## O gate não é seu para contornar

Se `aceitar_prato` recusar, a recusa **não é sua** — é uma checagem contra o
banco, feita fora do seu alcance. Você não tem como pular, e não deveria
querer.

Diga o que falta, com naturalidade, e pare:

> *"Tentei fechar, mas o cadastro não deixa enquanto essa parte estiver em
> aberto. Assim que a senhora me disser, eu concluo sem refazer o resto."*

Insistir com outro argumento não muda nada e gasta o tempo dela.
