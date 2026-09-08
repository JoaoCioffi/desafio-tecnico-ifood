---
name: orquestrar-atendimento
description: "Quando delegar e quando fazer você mesmo. Use ao pesquisar receita nova — a web vai para um worker, o banco é seu."
version: 2.0.0
author: Sabor da Maria
license: MIT
metadata:
  hermes:
    tags: [orquestracao, delegacao, subagentes]
    category: sabor-da-maria
    related_skills: [pesquisar-receitas, avaliar-viabilidade, elicitar-restricoes]
---

# Orquestrar o atendimento

```
você          conversa, decide, e chama as ferramentas do MCP DIRETO
  └ worker    um por vez, só para pesquisar receita na web
```

## A regra em uma linha

> **Delegue o que enche o contexto. Faça você mesmo o que precisa ser exato.**

Ler uma página de receita enche o contexto: são milhares de tokens de HTML
para extrair dez ingredientes. Isso vai para um worker.

Consultar a despensa, gravar um prato, rodar o gate — nada disso enche
contexto, e **o valor exato importa**. Isso é seu.

## Por que não delegar o banco

O subagente devolve **um resumo em prosa**, não os dados. Se você pedir a
despensa a um worker, `custo_unitario: 23.90` volta como *"o bacon está uns
vinte e poucos reais"* — e os números exatos morrem junto com o contexto dele.

Foi medido: um worker gastou **dez minutos e duas chamadas** para ler 37
linhas que você lê em uma. Delegação custa uma sessão inteira; a tool custa
uma chamada.

## Um worker por vez

Não dispare dois em paralelo. O modelo é local e a GPU é uma só: três
sessões concorrentes dividem o mesmo hardware, e cada uma anda a um terço.

Medido, o mesmo pedido:

| | tempo | resultado |
| --- | --- | --- |
| 2 workers em paralelo | 1300 s+ | banco vazio, workers em loop |
| etapas em série | 694 s | banco populado, zero preço inventado |

Delegar **não deixa mais rápido**. Delegar deixa o contexto do worker limpo,
e é por isso que o trabalho sai feito em vez de narrado.

---

## O único worker: pesquisar receita

```json
{"tasks": [{
  "goal": "Pesquise na internet UMA receita real de <PRATO> e devolva os ingredientes.",
  "context": "Use web_search e leia a pagina com web_extract. Devolva a URL real que voce leu — nao invente. Quantidades de UMA porcao de marmita: o total tem que somar entre 0.3 e 0.8 kg. Referencia: feijao ~0.120 kg, carne ~0.080 kg, tempero ~0.005 kg. Converta xicara e colher para kg antes de responder. NAO consulte banco de dados e NAO grave nada — so pesquise e devolva.",
  "output_schema": {
    "type": "object",
    "properties": {
      "prato": {"type": "string"},
      "fonte": {"type": "string"},
      "ingredientes": {"type": "array", "items": {
        "type": "object",
        "properties": {"ingrediente": {"type": "string"}, "quantidade": {"type": "number"}},
        "required": ["ingrediente", "quantidade"]}},
      "requisitos": {"type": "array", "items": {
        "type": "object",
        "properties": {"categoria": {"type": "string"}, "item": {"type": "string"}},
        "required": ["categoria", "item"]}}
    },
    "required": ["prato", "fonte", "ingredientes"]
  }
}]}
```

O `output_schema` é o que faz voltar **dado**, não prosa. O Hermes avisa o
worker do contrato, valida a resposta e dá uma chance de correção.

### Confira antes de usar

O resumo do worker é **auto-relato**, não fato verificado. Antes de gravar:

- as quantidades somam entre 0,3 e 0,8 kg? Se vier 5 g de carne, a unidade
  foi lida errada — devolva para o worker, não conserte na mão
- `fonte` é uma URL de verdade?

## Depois do worker, o trabalho é seu

Em sequência, você mesmo, sem delegar nada:

1. `prato_salvar` **uma vez**, com a lista completa e a **URL** que o worker
   trouxe — sem `fonte` a ferramenta recusa, e a gravação SUBSTITUI os
   ingredientes do prato, duas chamadas não somam
2. `prato_checar` no id que voltou
3. as perguntas que vierem, você faz **a ela** — uma por vez, gravando cada
   resposta com `perfil_gravar`
4. só então `cenarios` — `cmv` e `cenarios` **recusam** prato que não passou
   no gate, porque precificar o que ela talvez não consiga cozinhar é o erro
   que este projeto existe para evitar

Não precisa decorar essa ordem: toda resposta de prato traz `proximo_passo`
dizendo qual é a próxima ação.

O worker não conversa com a Dona Maria: subagente não consegue fazer
pergunta. Quem fala com ela é você, sempre.

## Confirme que gravou

Depois do `prato_salvar`, o `total_ingredientes` da resposta bate com a
receita que você leu? Só então diga a ela que está salvo.

Dizer que gravou sem ter gravado é a pior falha possível — ela vai às compras
confiando em algo que não existe.

## Quando não delegar nada

- ela perguntou uma coisa só (*"quanto custa o bacon?"*) → `despensa`
- ela está respondendo o gate → `perfil_gravar` e `prato_checar` de novo
- já existe prato salvo e ela quer o preço → `cmv` e `cenarios`

Delegar custa uma sessão inteira. Para uma chamada, custa mais do que faz.
