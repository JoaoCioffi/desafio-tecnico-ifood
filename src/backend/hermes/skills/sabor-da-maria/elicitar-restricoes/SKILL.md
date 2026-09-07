---
name: elicitar-restricoes
description: "Descobrir na conversa o que a Dona Maria tem e sabe fazer — utensílios, técnicas e limites operacionais — antes de ela aceitar um prato."
version: 1.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [elicitacao, cardapio, dona-maria]
    category: sabor-da-maria
    related_skills: [avaliar-viabilidade, pesquisar-receitas]
---

# Elicitar restrições

O erro que não pode acontecer:

> A Dona Maria aceita um prato, gasta o orçamento comprando ingrediente, e
> só na hora de cozinhar descobre que não tem panela de pressão.

Descobrir isso **antes** é o trabalho. O `prato_checar` te trava se faltar
algo, mas ele só sabe o que você registrou — se você não perguntou, ele não
tem como saber.

## Comece sabendo o que já sabe

Toda sessão abre com:

```
perfil_pendente()
```

Ele devolve o que os pratos exigem e ela ainda não respondeu. Se vier vazio,
não há o que perguntar agora — não invente pergunta para parecer diligente.

E antes de perguntar qualquer coisa, `perfil_ler()`. Repetir pergunta que ela
já respondeu passa a impressão de que você não escutou.

## Três categorias

| Categoria | O que investigar |
| --- | --- |
| `utensilio` | fogão (quantas bocas?), forno, panela de pressão, air fryer, liquidificador, batedeira, freezer |
| `tecnica` | massa fresca, molho que talha fácil, ponto de carne, fritura, confeitaria |
| `restricao` | espaço na geladeira, botijão de gás, quanto tempo por cozinhada, quantas marmitas por vez |

## Como perguntar

**Puxe pelo prato, não pela lista.** Ninguém responde bem a um questionário.

> ❌ *"Antes de começar, preciso saber: você tem forno? Panela de pressão?
> Air fryer? Liquidificador? Sabe fazer massa fresca?"*

> ✅ *"Essa feijoada fica bem melhor na panela de pressão — a senhora tem uma
> em casa?"*

A segunda tem contexto: ela entende **por que** você está perguntando, e a
resposta vem junto com informação que você não pediu ("tenho, mas é pequena,
só faz uns 2 quilos").

**Uma por vez.** Se faltam três coisas, pergunte a mais bloqueante primeiro.
As outras aparecem naturalmente.

## Registrando

Toda resposta vira `perfil_gravar`, na hora:

```
perfil_gravar("utensilio", "panela de pressao", "sim")
perfil_gravar("utensilio", "forno", "nao tem")
perfil_gravar("restricao", "fogao", "so 2 bocas")
```

Detalhe importante: **grave o que ela disse**, não a sua interpretação. Se
ela falou "tenho, mas é pequena", grave `"tem, pequena"` — esse detalhe pode
importar num prato futuro.

Respostas livres contam como afirmativas. Só `"nao"`, `"nao tem"` e afins
contam como negativa.

## O que fazer com um "não tem"

Não é o fim do prato — é uma bifurcação:

1. **Tem substituição?** Feijoada sem panela de pressão dá, só demora mais.
   Pergunte se ela topa o tempo a mais.
2. **Dá para adaptar a receita?** Bolo sem forno não. Mas frango assado vira
   frango de panela.
3. **Se não dá**, diga com clareza e ofereça alternativa:
   > *"Esse prato precisa de forno, que a senhora não tem. Quer que eu
   > procure outra receita que use os mesmos ingredientes?"*

Nunca deixe a conversa morrer num "não dá". Ela veio montar um cardápio.

## Restrições operacionais rendem mais do que parecem

Utensílio é fácil de perguntar. O que quase ninguém investiga:

- **Quantas marmitas por cozinhada?** Define se o prato escala.
- **Espaço na geladeira.** Prato que precisa descansar 12 h ocupa espaço que
  ela talvez não tenha.
- **Gás.** Prato de 4 horas de fogo é caro de um jeito que não aparece no CMV.
- **Tempo dela.** Ela vai cozinhar e entregar. Prato que toma a manhã inteira
  limita o resto do cardápio.

Essas respostas mudam quais receitas fazem sentido buscar — vale perguntar
cedo, antes de gastar tempo pesquisando prato que ela não consegue produzir
em escala.
