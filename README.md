# radar-normas

Feed público de normas ambientais recentes de Minas Gerais e da União, consumido
pelo **briefing diário** (agente Claude Code que roda na nuvem, com egress restrito
— só alcança `raw.githubusercontent.com`).

## O que é

Um arquivo `normas.json` reconstruído todo dia de manhã, com as normas publicadas
na janela recente, a partir de duas fontes (ambas gratuitas):

1. **Diários Oficiais de MG**, lidos pela **data de publicação**, por
   `scripts/doe_mg_coleta.py`:
   - **Diário do Executivo** (Jornal Minas Gerais): decretos do Governador (seção
     "Governo do Estado") e o infralegal das seções Meio Ambiente (SEMAD, FEAM, IEF,
     IGAM, COPAM), Agricultura (SEAPA), Cultura (SECULT/IEPHA) e Infraestrutura
     (SEINFRA/DER-MG). Sem filtro de relevância: o filtro é a seção. O tipo
     "Decreto NE" (atos de pessoal) fica de fora.
   - **Diário do Legislativo** (ALMG): leis sancionadas, que não saem no Executivo.
2. **ACAM**, endpoint `radar-recentes`, só para os **atos federais do DOU**, com
   filtro de relevância por ementa.

Desde 22/09/2026 o radar **não** usa mais a coleta MG do CTL (via ACAM) nem a
varredura por número da API do ALMG. O CTL indexa pela data de assinatura e recebe a
norma dias depois de publicada, o que fez o briefing perder a Resolução Conjunta
SEMAD/SEAPA/FEAM/IEF 3.424/2026. O Diário publica no dia em que a norma passa a
existir.

O briefing lê `https://raw.githubusercontent.com/cvc339/radar-normas/main/normas.json`,
aplica a própria janela e watchlist, e monta a seção "Normas publicadas".

## Como roda

- `.github/workflows/publish-normas.yml` — agendado (07:30 e 09:30 BRT) + manual
  (com a janela de dias como parâmetro). O Diário sai de terça a sábado.
- `scripts/doe_mg_coleta.py` — coleta os últimos 10 dias dos dois Diários e grava
  `normas-doe.json` (não versionado). Precisa de `pymupdf`.
- `scripts/build-normas.mjs` — junta com o DOU e monta o JSON. Sem dependências (Node 20+).
- `state.json` — `vistos`: data da primeira aparição de cada norma. O `normas.json`
  só traz o que apareceu pela primeira vez nos últimos 3 dias.

## Segredo

- `RADAR_READ_KEY` (Settings → Secrets → Actions): mesma string da variável
  `RADAR_READ_KEY` no Railway do ACAM. Chave só de leitura, usada só para o DOU.

## Formato do normas.json

```json
{
  "gerado_em": "2026-09-23T10:35:00Z",
  "janela_dias": 30,
  "janela_visivel_dias": 3,
  "total": 2,
  "itens": [
    { "origem": "DOE-MG", "fonte": "DOE-Executivo", "orgao": "Secretaria de Estado de Meio Ambiente e Desenvolvimento Sustentável",
      "tipo": "Resolução Conjunta", "numero": "3.424", "ano": "2026",
      "rotulo": "Resolução Conjunta SEMAD/SEAPA/FEAM/IEF nº 3.424, de 31 de Agosto de 2026",
      "ementa": "Dispõe sobre...", "data": "2026-09-01", "data_assinatura": "2026-08-31",
      "pagina": 15, "url": "https://www.jornalminasgerais.mg.gov.br/?dataJornal=2026-09-01",
      "visto_em": "2026-09-01" }
  ]
}
```
