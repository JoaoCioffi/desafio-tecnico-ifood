---
name: avaliar-viabilidade
description: "Rodar o gate antes de aceitar um prato e transformar cada pendência em pergunta para a Dona Maria."
version: 1.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [gate, viabilidade, elicitacao]
    category: sabor-da-maria
    related_skills: [elicitar-restricoes, precificar-prato]
---

# Avaliar viabilidade

`prato_checar(prato_id)` responde uma pergunta só: **a Dona Maria consegue
fazer este prato hoje?**

```
prato_checar(3)
→ { "apto": false,
    "pendencias": [{"tipo": "utensilio", "item": "panela de pressao",
                    "pergunta": "A senhora tem panela de pressao?"}],
    "perguntas": ["A senhora tem panela de pressao?"],
    "compras": [], "custo_compras": 0.0, "orcamento_restante": 80.0 }
```

## As perguntas já vêm prontas

Cada pendência traz o campo `pergunta`. **Use.** Não reescreva para soar mais
natural — elas foram escritas para serem ditas.

Se vierem três, faça **uma por vez**. Registre cada resposta com
`perfil_gravar` e chame `prato_checar` de novo. As pendências vão sumindo.

### Não responda no lugar dela

O erro que já aconteceu aqui: o `prato_checar` devolveu oito perguntas sobre
preço de ingrediente, e o agente **respondeu todas sozinho** com estimativas
de mercado — montou uma tabela bonita com `~R$ 12-15/kg` e encerrou dizendo
que era só ir às compras.

O prato ficou `apto: false` no banco, com oito pendências abertas, enquanto a
resposta dizia que estava resolvido.

> Pendência é **pergunta para a Dona Maria**, não lacuna para você preencher.

Se `prato_checar` devolveu `apto: false`, sua resposta termina com uma
pergunta — nunca com uma conclusão.

## O que cada tipo significa

| `tipo` | O que fazer |
| --- | --- |
| `utensilio` `tecnica` | pergunte; se ela não tem, veja se dá para adaptar ou troque de receita |
| `unidade` | a receita pede grama e a despensa só sabe "un" — pergunte quanto pesa a embalagem |
| `estoque` | falta ingrediente; pergunte o preço onde ela compra |
| `porcao` | a receita inteira pesa menos que um prato; as quantidades foram lidas na unidade errada — pergunte quanto ela serve e **regrave o prato**, não é pergunta de perfil |
| `orcamento` | as compras estouram os R$ 80; reduza a porção ou troque ingrediente |

## Nunca contorne

Se `apto` é `false`, `prato_aceitar` vai recusar. Isso é o desenho.

Não tente aceitar mesmo assim, não reformule os requisitos para o prato
passar, não assuma que ela tem. A checagem roda no servidor justamente para
não depender de você lembrar.

## Compras complementares

Quando o prato precisa de algo que não está na despensa, `compras` lista o
quê e quanto custa, e `custo_compras` soma. Compare com `orcamento_restante`
— e diga a ela em reais:

> *"Para essa feijoada faltam 200 g de linguiça, que dá R$ 4,40. Sobram
> R$ 75,60 do orçamento."*

O orçamento é **do cardápio inteiro**, não de um prato. Cada aceite consome
uma parte, e o que sobra fica menor para os próximos.

## Quando tudo passa

`apto: true` significa que ela tem os utensílios, sabe a técnica, os
ingredientes estão garantidos e as compras cabem. Só então vá para o preço —
ver a skill `precificar-prato`.
