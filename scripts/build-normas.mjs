// build-normas.mjs
// Monta o arquivo normas.json que o briefing diario le (via raw.githubusercontent).
// Duas fontes, todas em infra gratuita e egress aberto (roda no GitHub Actions):
//   (1) Diarios Oficiais de MG, lidos pelo passo Python scripts/doe_mg_coleta.py e
//       entregues em normas-doe.json: Diario do Executivo (decretos do Governador e o
//       infralegal de SEMAD/FEAM/IEF/IGAM/COPAM, SEAPA, SECULT/IEPHA, SEINFRA/DER) e
//       Diario do Legislativo (leis sancionadas). A chave e a DATA DE PUBLICACAO.
//   (2) endpoint radar-recentes do ACAM -> atos federais do DOU.
// Decisao do titular em 22/09/2026: a varredura do ALMG por numero e a coleta MG do
// CTL (via ACAM) SAEM do radar. O CTL indexa pela data de assinatura e recebe a norma
// dias depois de publicada, o que fez o briefing perder a RC SEMAD/SEAPA/FEAM/IEF
// 3.424/2026. O Diario publica no dia em que a norma passa a existir.
// Node 20+ (fetch global), sem dependencias.

import { readFileSync, writeFileSync, existsSync } from "node:fs"

const ACAM_URL =
  process.env.ACAM_RADAR_URL ||
  "https://www.acam.com.br/api/admin/newsletter/radar-recentes?dias=30&fontes=DOU"
const READ_KEY = process.env.RADAR_READ_KEY || ""
const DOE_JSON = process.env.DOE_JSON || "normas-doe.json"

const JANELA_DIAS = 30 // teto de idade da norma; o corte que vale e o de PRIMEIRA APARICAO
const JANELA_VISIVEL = 3 // reporta o que passou a ser visto nos ultimos N dias
const VISTOS_TTL = 120 // dias que uma chave fica no state.vistos

// Relevancia do briefing, aplicada SO ao DOU, por volume. MG entra inteiro: o filtro
// e a secao do Diario (orgao), que e autoritativa, e o tipo "Decreto NE" (atos de
// pessoal) ja sai no coletor Python. Decisao do titular em 22/09/2026: preferir
// achar norma que ele descarte a perder norma por filtro.
const RELEVANTE =
  /ambient|licenciament|condicionante|\bEIA\b|\bRIMA\b|\bRAS\b|compensa[cç][aã]o|interven[cç][aã]o|\bASV\b|supress[aã]o|desmatament|reserva legal|\bAPP\b|[aá]rea de preserva[cç]|unidade de conserva|\bRPPN\b|\bAPA\b|parque (estadual|nacional|natural)|esta[cç][aã]o ecol[oó]gica|monumento natural|ref[uú]gio de vida|plano de manejo|zona de amortecimento|fauna|flora|vegeta[cç]|florest|bioma|biodiversidade|recurso[s]? h[ií]dric|outorga|barragem|efluente|res[ií]duo|polui[cç]|emiss[aã]o (atmosf|de gases|de poluentes)|geolog|espeleol|caverna|minera[cç]|lavra|\bANM\b|l[ií]tio|terras raras|patrim[oô]nio (cultural|arqueol[oó]|hist[oó]ric|natural|espeleol)|arqueol[oó]|regulariza[cç][aã]o (fundi[aá]ria|ambiental)|\bTCCFM\b|auto de infra[cç]|embargo ambiental|\bISO 14001\b|\bESG\b|gest[aã]o ambiental|saneament/i

function interessaDou(ementa) {
  return RELEVANTE.test(ementa || "")
}

function isoDate(d) {
  return d.toISOString().slice(0, 10)
}
function dentroJanela(iso) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(iso))) return false
  const corte = new Date()
  corte.setDate(corte.getDate() - JANELA_DIAS)
  return new Date(iso) >= corte
}

// Rotulo pronto para o briefing: tipo, numero e ano juntos, do jeito que o titular
// filtra a leitura (pedido de 22/09/2026). Orgao entra quando a fonte o informa.
function rotulo(tipo, numero, ano, orgao) {
  const t = (tipo || "Norma").trim()
  const n = numero ? `nº ${String(numero).trim()}` : ""
  const a = ano ? `/${ano}` : ""
  const o = orgao ? ` (${String(orgao).trim()})` : ""
  return `${t} ${n}${a}${o}`.replace(/\s+/g, " ").trim()
}

async function coletarAcamDou() {
  if (!READ_KEY) {
    console.error("RADAR_READ_KEY ausente — pulando ACAM/DOU")
    return []
  }
  try {
    const r = await fetch(ACAM_URL, { headers: { Authorization: `Bearer ${READ_KEY}` } })
    if (!r.ok) {
      console.error("ACAM HTTP", r.status)
      return []
    }
    const j = await r.json()
    return (j.itens || [])
      .filter((it) => String(it.fonte || "").toUpperCase() === "DOU")
      .map((it) => {
        const ano = it.ano || (it.data_publicacao ? String(it.data_publicacao).slice(0, 4) : null)
        return {
          origem: "ACAM",
          fonte: "DOU",
          orgao: it.orgao || null,
          tipo: it.tipo || null,
          numero: it.numero || null,
          ano,
          rotulo: rotulo(it.tipo, it.numero, ano, it.orgao),
          ementa: (it.resumo || it.titulo || "").replace(/\s+/g, " ").trim(),
          data: it.data_publicacao || null,
          url: it.url || null,
        }
      })
      .filter((it) => interessaDou(it.ementa))
  } catch (e) {
    console.error("ACAM erro:", e.message)
    return []
  }
}

// Le o que o coletor Python gravou. O rotulo ja vem pronto, com orgao e data de
// assinatura ("Resolucao Conjunta SEMAD/SEAPA/FEAM/IEF nº 3.424, de 31 de Agosto de
// 2026"); "data" e a de PUBLICACAO no Diario.
function coletarDoe() {
  if (!existsSync(DOE_JSON)) {
    console.error(`${DOE_JSON} ausente — pulando Diario Oficial de MG`)
    return []
  }
  try {
    const j = JSON.parse(readFileSync(DOE_JSON, "utf8"))
    return (j.itens || [])
      .filter((it) => dentroJanela(it.data))
      .map((it) => ({
        origem: "DOE-MG",
        fonte: it.fonte, // "DOE-Executivo" | "DOE-Legislativo"
        orgao: it.orgao || null, // secao do Diario
        tipo: it.tipo || null,
        numero: it.numero || null,
        ano: (it.data_assinatura || it.data || "").slice(0, 4) || null,
        rotulo: it.rotulo || rotulo(it.tipo, it.numero, (it.data || "").slice(0, 4), it.orgao),
        ementa: (it.ementa || "").replace(/\s+/g, " ").trim(),
        data: it.data || null,
        data_assinatura: it.data_assinatura || null,
        pagina: it.pagina || null,
        url: it.url || null,
      }))
  } catch (e) {
    console.error("DOE erro:", e.message)
    return []
  }
}

function dedup(itens) {
  const seen = new Set()
  const out = []
  for (const it of itens) {
    const k = `${it.fonte}|${it.tipo}|${it.numero}|${it.data}`.toLowerCase()
    if (seen.has(k)) continue
    seen.add(k)
    out.push(it)
  }
  return out
}

const state = (() => {
  try {
    return JSON.parse(readFileSync("state.json", "utf8"))
  } catch {
    return {}
  }
})()
delete state.DEC // restos da varredura do ALMG, desativada em 22/09/2026
delete state.LEI

const doe = coletarDoe()
const dou = await coletarAcamDou()

let itens = dedup([...doe, ...dou])

// Corte por PRIMEIRA APARICAO. Um radar reporta o que ficou visivel, nao o que foi
// datado. Com o Diario a data ja e a de publicacao, mas o corte continua valendo para
// o DOU e para o dia em que o coletor falha e recupera no seguinte.
const hojeISO = isoDate(new Date())
state.vistos = state.vistos || {}
const corteVis = new Date()
corteVis.setDate(corteVis.getDate() - JANELA_VISIVEL)
const corteVisISO = isoDate(corteVis)
const novos = []
for (const it of itens) {
  const k = `${it.fonte}|${it.tipo}|${it.numero}|${it.data}`.toLowerCase()
  if (!state.vistos[k]) state.vistos[k] = hojeISO
  it.visto_em = state.vistos[k]
  if (it.visto_em >= corteVisISO) novos.push(it) // datas ISO comparam como texto
}
itens = novos

// poda o historico de vistos
const corteTtl = new Date()
corteTtl.setDate(corteTtl.getDate() - VISTOS_TTL)
for (const [k, v] of Object.entries(state.vistos)) {
  if (new Date(v) < corteTtl) delete state.vistos[k]
}

itens.sort((a, b) => String(b.data || "").localeCompare(String(a.data || "")))

writeFileSync(
  "normas.json",
  JSON.stringify(
    {
      gerado_em: new Date().toISOString(),
      janela_dias: JANELA_DIAS,
      janela_visivel_dias: JANELA_VISIVEL,
      fontes: ["DOE-Executivo", "DOE-Legislativo", "DOU"],
      total: itens.length,
      itens,
    },
    null,
    2,
  ),
)
writeFileSync("state.json", JSON.stringify(state, null, 2))
console.log(`normas.json: ${itens.length} itens (DOE-MG ${doe.length}, DOU ${dou.length})`)
