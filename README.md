# radar-normas

Feed público de normas ambientais recentes de Minas Gerais e da União, consumido
pelo **briefing diário** (agente Claude Code que roda na nuvem, com egress restrito
— só alcança `raw.githubusercontent.com`).

## O que é

Um arquivo `normas.json` reconstruído todo dia de manhã, com as normas publicadas
na janela recente, a partir de duas fontes (ambas gratuitas):

1. **ACAM** — endpoint `radar-recentes` (infralegal de FEAM/SEMAD/IEF/IGAM/COPAM via
   CTL/Pesquisa Legislativa + atos federais do DOU).
2. **ALMG** — API de dados abertos (`dadosabertos.almg.gov.br/api/v2/legislacao`):
   leis e decretos estaduais recentes. É aqui que entram os **decretos do Governador**
   (ex.: Decreto 49.260/2026), que a coleta por órgão da CTL não capta.

O briefing lê `https://raw.githubusercontent.com/cvc339/radar-normas/main/normas.json`,
aplica a própria janela e watchlist, e monta a seção "Normas publicadas".

## Como roda

- `.github/workflows/publish-normas.yml` — agendado (07:30 e 09:30 BRT) + manual.
- `scripts/build-normas.mjs` — monta o JSON. Sem dependências (Node 20+).
- `state.json` — último número de decreto/lei varrido no ALMG (o coletor caminha
  pelos números sequenciais a partir daí; sem paginação, cobertura completa).

## Segredo necessário

- `RADAR_READ_KEY` (Settings → Secrets → Actions): mesma string da variável
  `RADAR_READ_KEY` no Railway do ACAM. Chave só de leitura.

## Formato do normas.json

```json
{
  "gerado_em": "2026-07-24T10:35:00Z",
  "janela_dias": 5,
  "total": 3,
  "itens": [
    { "origem": "ALMG", "fonte": "ALMG", "orgao": "Executivo",
      "tipo": "DEC", "numero": "49260",
      "ementa": "Dispõe sobre o procedimento...", "data": "2026-07-17",
      "url": "https://www.almg.gov.br/legislacao-mineira/DEC/49260/2026/" }
  ]
}
```
