# Dashboard PPI — ANP

Dashboard interativo dos **Preços de Paridade de Importação (PPI)** publicados pela
Agência Nacional do Petróleo, Gás Natural e Biocombustíveis (ANP), com coleta e
atualização automáticas.

**Dashboard:** https://jmagomez.github.io/anp-ppi-dashboard/

---

## O que o projeto faz

1. Baixa o arquivo oficial [`ppi.xlsx`](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/arq-ppi/ppi.xlsx) da ANP.
2. Normaliza as **4 abas semanais**:

   | Aba na planilha | Produto | Unidade | Terminais |
   |---|---|---|---|
   | `Gasolina R$ semanal` | Gasolina A Comum | R$/litro | 16 |
   | `Diesel R$ semanal` | Diesel A S10 | R$/litro | 16 |
   | `QAV R$ semanal` | QAV | R$/litro | 16 |
   | `GLP R$ kg semanal` | GLP | R$/13kg | 2 (Suape, Santos) |

   Série disponível desde **novembro/2018**, com frequência semanal.
3. Gera `docs/data/ppi.json` (consumido pelo dashboard) + CSVs por produto e consolidado.
4. Publica o resultado no GitHub Pages.

## Estrutura

```
scripts/build_data.py                  coleta + normalizacao + geracao dos artefatos
.github/workflows/atualizar-ppi.yml    automacao semanal (segunda, 20h BRT)
docs/index.html                        dashboard (pagina unica, Chart.js via CDN)
docs/data/ppi.json                     dataset completo
docs/data/meta.json                    metadados da ultima execucao
docs/data/ppi_<produto>.csv            serie longa por produto
docs/data/ppi_long.csv                 serie longa consolidada dos 4 produtos
```

## O que o dashboard mostra

- **KPIs** — PPI médio da semana de referência, variação semanal, variação em 12 meses,
  amplitude entre terminais e terminais extremos.
- **Série histórica** — uma linha por terminal + média nacional simples, com filtros de
  período (3M, 6M, 1A, 3A, 5A, tudo) e seleção de terminais.
- **Defasagem por terminal** — diferença percentual de cada terminal em relação à média
  da semana de referência.
- **Variação semanal** — variação % do PPI médio contra a semana anterior.
- **Comparativo entre produtos** — os 4 produtos indexados a 100 no início do período.
- **Tabela detalhada** — semanas × terminais, ordenável, filtrável e exportável em CSV.

## Automação

O workflow `Atualizar PPI (ANP)` roda:

- **toda segunda-feira às 20:00 (America/Sao_Paulo)** — cron `0 23 * * 1` em UTC;
- sob demanda, pelo botão **Run workflow** na aba Actions;
- a cada push que altere `scripts/` ou `requirements.txt`.

Se os dados mudarem, o próprio workflow faz commit em `docs/data/` e o GitHub Pages
republica a página. Se nada mudar, nenhum commit é criado.

> Os horários agendados do GitHub Actions são executados em regime de melhor esforço e
> podem atrasar alguns minutos em janelas de alta demanda.

## Rodar localmente

```bash
pip install -r requirements.txt
python scripts/build_data.py
python -m http.server 8000 --directory docs   # abra http://localhost:8000
```

## Notas metodológicas

- O PPI é a estimativa da ANP do custo de importação do produto colocado no terminal
  (cotação internacional + frete + seguro + custos portuários + tributos). Não é preço
  de venda praticado.
- A **média** exibida é a média aritmética simples dos terminais com dado publicado na
  semana; não há ponderação por volume movimentado.
- Células em branco na planilha da ANP viram `null` e são ignoradas nos cálculos —
  vários terminais só passaram a ter publicação em anos posteriores a 2018.
- A "defasagem" do dashboard compara terminais **entre si**. A comparação clássica entre
  PPI e preço de realização da Petrobras exige uma fonte adicional de preços internos,
  que não está contida neste arquivo da ANP.

## Fonte

ANP — [Preços de Paridade de Importação](https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/precos/precos-de-paridade-de-importacao).
Dados públicos; este repositório apenas coleta, organiza e visualiza.
