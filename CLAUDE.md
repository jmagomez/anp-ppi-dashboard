# Instruções para quem mexe no código

O `README.md` explica **o produto**: o que é o PPI, por que a defasagem dá trabalho,
a metodologia e os limites dos números. Não repita nada disso aqui.

Este arquivo é a outra metade: **o fluxo do processo, os invariantes e as armadilhas**.
Leia antes de alterar qualquer coisa em `scripts/`.

---

## O fluxo, de ponta a ponta

Um único ponto de entrada. `build_data.py` é o orquestrador e também o parser do PPI.

```
GitHub Actions (atualizar-ppi.yml)
   │
   └─► scripts/build_data.py
         │
         │  1. baixa ppi.xlsx da ANP, parseia as 4 abas semanais
         │     grava: ppi.json, meta.json, ppi_long.csv
         │
         ├─► defasagem.run(products, out_dir)  ──► devolve lista de alertas
         │     baixa a planilha de produtores (.xls, via xlrd)
         │     deduz tributos de tributos.json, pareia por semana ISO
         │     confere contra pontos fixos da Síntese Semanal
         │     grava: defasagem.json, defasagem.csv, diagnostico.json
         │
         └─► qualidade.run(products, anterior, out_dir, alertas_da_defasagem)
               compara com a execução anterior, checa plausibilidade
               detecta degrau artificial na virada de alíquota
               grava: qualidade.json  ◄── a lista ÚNICA de alertas do projeto

GitHub Pages serve docs/ ──► docs/index.html lê os .json em runtime
scripts/email_semanal.py  ──► só no cron de segunda e no disparo manual
```

**Quem escreve o quê** — nenhum dado aparece em dois arquivos:

| arquivo | conteúdo | quem lê |
|---|---|---|
| `ppi.json` | série completa do PPI | dashboard |
| `defasagem.json` | série pareada + resumo da conferência | dashboard, e-mail |
| `qualidade.json` | **todos** os alertas + degraus | dashboard, e-mail |
| `diagnostico.json` | rastro da extração, validação ponto a ponto, links | humano investigando |
| `meta.json` | metadados enxutos | testes |
| `ppi_long.csv`, `defasagem.csv` | download do usuário | ninguém no código |

---

## Invariantes

Quebrar qualquer um destes produz número errado com cara de certo. São os que o
projeto já quebrou alguma vez.

1. **Nunca publicar sobre base tributária desconhecida.** Se não há faixa vigente em
   `tributos.json` para a semana, `deducao()` devolve `None` e a semana fica **fora**
   da série. Não invente tributo zero por omissão.

2. **PPI e realização quase nunca são da mesma semana.** O PPI sai poucos dias depois
   do fim da semana; a planilha de produtores, cerca de dez dias. Todo lugar que mostra
   os dois **carimba a semana de cada um**. Nunca apresente os dois sob um rótulo só.

3. **O pareamento é pela segunda-feira da semana ISO** (`comum.semana_chave`). As duas
   fontes fecham a semana em dias diferentes. Comparar as datas finais diretamente
   desloca as séries em uma semana, silenciosamente.

4. **Uma lista de alertas, um schema:** `{nivel, codigo, produto, label, texto}`,
   `nivel ∈ {erro, aviso, info}`, publicada em `qualidade.json`. Alertas nascidos em
   `defasagem.py` são **devolvidos**, não publicados por lá.

5. **Escrita é idempotente** (`comum.escrever_json` / `escrever_csv`). Arquivo cujo
   conteúdo não mudou não é tocado, e `generated_at` passa a significar *quando este
   dado foi gerado*. Sem isso o pipeline diário gerava um commit por dia sem dado novo.

6. **Afirmação de verificação é recalculada, nunca escrita à mão.** O erro da conferência
   vem de `defasagem.json → validacao`. Já houve um "erro máximo de 0,12%" fixo no HTML.

7. **Nível `info` não vai para o e-mail.** São fatos permanentes da série; repetidos toda
   semana, ensinam o leitor a ignorar a seção de avisos.

---

## Antes de commitar

```bash
pip install -r requirements.txt pytest ruff
pytest tests -q                       # 99 testes, todos offline
ruff check --select E9,F scripts tests
```

O CI roda exatamente isso. **A suíte não cobre o JavaScript do dashboard** — a única
verificação do `index.html` é abrir a página e olhar. Depois de mexer nele, confira:
os quatro produtos nas abas, os KPIs com a semana certa, a tabela, o painel de qualidade
e o console sem erro.

---

## Armadilhas (todas já custaram tempo)

- **O editor web do GitHub duplica arquivos ao colar via script.** Já triplicou o
  `index.html` e quebrou o `build_data.py`. Prefira a API (`create_or_update_file`).
  Para `.github/workflows/`, que a API do app não pode escrever, use o editor e
  **confira o resultado pela API depois** — ou apague e recrie o arquivo, que é o
  caminho que não duplica.
- **Reescrever um arquivo inteiro pela API perde caractere invisível.** O `index.html`
  usa **espaço inseparável** depois da seta (`▲`, `▼`) para o sinal não se separar do
  número na quebra de linha. Numa reescrita ele virou espaço comum: nenhum teste pega,
  o diff não mostra e o sintoma só apareceria em tela estreita. Vale para NBSP, hífen
  não separável e aspas tipográficas.
- **Confronte o blob depois de todo push.** `git hash-object <arquivo>` local contra o
  `sha` que a API devolve. Igual, a transcrição foi fiel; diferente, há divergência
  **mesmo quando o tamanho bate** — foi assim que o espaço inseparável apareceu, com
  dois bytes trocados que se anulavam na contagem.
- **`raw.githubusercontent.com` serve conteúdo velho por minutos.** Para conferir o que
  foi realmente commitado, use a API de contents ou fixe o SHA do commit.
- **Dois-pontos seguido de espaço dentro de escalar YAML sem aspas quebra o parse.**
  Derrubou o `ci.yml` em `print('JSON publicados: ok')`.
- **O sandbox não alcança `gov.br`.** O pipeline inteiro só roda no runner do GitHub.
  Para testar offline, carregue os `.json` já publicados em `docs/data/`.
- **`xlrd` lê o `.xls` da planilha de produtores; `openpyxl` lê o `.xlsx` do PPI.** São
  formatos diferentes e bibliotecas diferentes. Não troque.

---

## Quando um decreto mudar alíquota

1. Edite a faixa em `scripts/tributos.json`. Faixa nova fecha a anterior com `ate`;
   `de` começa na **primeira segunda-feira inteiramente sob o novo regime** — semana
   mista fica de fora, não entra pela metade.
2. Sem fonte oficial confirmada, marque `"presumido": true` e explique em `_obs`.
3. `pytest tests/test_tributos_config.py` acusa sobreposição, faixa aberta fora do fim
   e buraco de cobertura entre o início da série e hoje.
4. Na execução seguinte, confira `qualidade.json → degraus`: `razao_residuo` perto de
   zero é assinatura de alíquota errada; muito acima de 1 significa que o mercado se
   moveu demais e o teste **não conclui nada** — e ele diz isso, em vez de chutar.

---

## Em aberto

- **QAV desde 03/08/2026 usa alíquota presumida.** A desoneração dos Decretos 12.924 e
  12.991/2026 venceu em 31/07/2026 e não foi localizada prorrogação. O valor voltou ao
  estatutário do Decreto 5.059/2004 (R$ 0,0712/l). O teste de continuidade segue
  inconclusivo. Confirmar em fonte oficial e remover a marca `presumido`.
- **A conferência cobre 15 pontos de uma edição só** (13/2026) e não inclui QAV. É teste
  de regressão contra um instante conhecido, não validação da semana corrente.
- **Sem teste de fumaça do dashboard.** Um headless que carregue a página e confira
  console limpo mais os elementos principais fecharia o único buraco grande da suíte.
