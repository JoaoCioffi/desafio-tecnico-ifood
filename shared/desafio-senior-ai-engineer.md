# Desafio Técnico — Senior AI Engineer

> Vaga: Senior AI Engineer
> Prazo: **7 dias**
> Entrega: link do repositório (+ demo em vídeo, opcional) para [email removido] e [email removido]

---

## 1. Contexto

A **Dona Maria** é uma cozinheira de mão cheia abrindo o seu primeiro delivery, o *Sabor da Maria*. Ela montou uma despensa comprando os ingredientes, sabe exatamente quanto pagou por cada um e quanto ainda pode investir em complementos. O que ela **não** sabe é:

1. quais receitas consegue produzir com o que tem na despensa;
2. se possui os utensílios/equipamentos e as habilidades para cada prato;
3. quanto cobrar para o delivery ser lucrativo depois da taxa da plataforma.

Seu papel é construir um **agente de IA** que a acompanhe da despensa ao cardápio de lançamento.

## 2. O desafio

Você deve **instalar e customizar o [Hermes Agent](https://github.com/nousresearch/hermes-agent)** (Nous Research) para criar um agente conversacional que atue como consultora de cardápio e precificação da Dona Maria. A escolha de modelo, context files, tools/MCP, estrutura de memória e skills fica **a seu critério** — esperamos ver suas decisões de arquitetura justificadas no README.

O agente deve conduzir um fluxo interativo (não precisa ser linear — ele adapta a conversa):

### 2.1 Pesquisa de receitas viáveis
A partir dos ingredientes da despensa e do orçamento, o agente deve **pesquisar receitas reais na internet** (web search) que aproveitem o que ela já tem. À medida que encontrar candidatas, deve apresentá-las e pedir o feedback dela: gosta de cozinhar aquilo? Vê algum impedimento?

### 2.2 Elicitação de restrições (o coração do desafio)
Antes de a Dona Maria aceitar um prato, o agente precisa garantir que ela **consegue de fato produzi-lo**. Deve descobrir, na conversa:

- **Utensílios e equipamentos** (fogão de quantas bocas? forno? panela de pressão? air fryer? liquidificador?).
- **Técnicas/habilidades** que ela domina (massa fresca? molho béchamel? pontos de carne?).
- **Restrições operacionais** (energia, gás, espaço na geladeira, tempo por cozinhada).

O agente **não pode deixar ela comprar ingredientes e descobrir depois que não consegue cozinhar**. Se ela não falou algo espontaneamente, o agente pergunta.

### 2.3 Coleta e preenchimento de ingredientes
Para cada receita viável em princípio, o agente compara os ingredientes necessários com a despensa e identifica:

- o que ela **já tem** (e em que quantidade);
- o que **falta comprar**, o custo disso e se cabe no orçamento restante.

### 2.4 Aceitação → CMV e preço de venda
Quando a Dona Maria aceitar um prato (gostou, tem utensílios + habilidades + ingredientes garantidos), o agente deve **calcular e explicar**:

- **CMV (Custo de Mercadoria Vendida) estimado do prato**: somatório de `quantidade usada × custo unitário` de cada ingrediente, onde o **custo unitário** é derivado do cruzamento das duas abas da planilha (`preço total pago ÷ quantidade comprada`). Inclua o custo de eventuais compras complementares.
- **Preço final de venda no delivery**, considerando taxa de **10% sobre a venda**: se o preço for `P`, a Dona Maria recebe `0,90·P`.
  - Preço mínimo para não perder dinheiro: `P ≥ CMV / 0,90`.
  - Lucro da Dona Maria: `0,90·P − CMV`.
- O agente deve **propor 2–3 cenários de preço** (margens diferentes), mostrar a matemática de forma didática e **deixar a Dona Maria decidir** qual adotar.

## 3. Dados de entrada

A Dona Maria entregará o arquivo **`despensa_dona_maria.xlsx`**, com **duas abas**:

| Aba | Colunas | Uso |
|---|---|---|
| `Despensa` | Ingrediente · Quantidade em estoque · Unidade | O que ela tem hoje |
| `Precos` | Ingrediente · Quantidade comprada · Unidade · Preço total pago (R$) | Quanto ela pagou; o agente deriva o custo unitário |

**Orçamento restante para complementos:** R$ 80,00.


##  4. Entregáveis

1. **Repositório** com o Hermes Agent configurado + todas as customizações (obrigatório).
2. **Demo** em vídeo de 5–10 min

## 5. Submissão e prazo

- Enviar link do repositório (+ demo, se houver) para [email removido] e [email removido].
- **Prazo: 7 dias** corridos a partir do recebimento deste desafio.

## 6. Anexo

- `despensa_dona_maria.xlsx` — despensa da Dona Maria (abas `Despensa` e `Precos`).
