// build-normas.mjs
// Monta o arquivo normas.json que o briefing diario le (via raw.githubusercontent).
// Duas fontes, todas em infra gratuita e egress aberto (roda no GitHub Actions):
//   (1) endpoint radar-recentes do ACAM  -> infralegal MG (CTL) + DOU ja coletados
//   (2) API de dados abertos do ALMG      -> leis e decretos estaduais recentes
// Node 20+ (fetch global), sem dependencias.

import { readFileSync, writeFileSync } from "node:fs"

const ACAM_URL =
  process.env.ACAM_RADAR_URL ||
  "https://www.acam.com.br/api/admin/newsletter/radar-recentes?dias=4&fontes=MG,DOU"
const READ_KEY = process.env.RADAR_READ_KEY || ""

const JANELA_DIAS = 5 // so entram normas com data dentro dos ultimos N dias
const ALMG_TIPOS = ["DEC", "LEI"]
const ALMG_MISS_STOP = 20 // para apos N numeros seguidos inexistentes (tolera gaps na sequencia)
const ALMG_MAX_SCAN = 500 // teto de seguranca por tipo/execucao
const ALMG_BUFFER = 40 // re-varre N numeros abaixo do ultimo visto p/ cobrir toda a janela de dias

// Relevancia do briefing (definida pelo consumidor): meio fisico + biotico +
// socioeconomico no escopo ambiental amplo, + gestao ambiental. Atos meramente
// administrativos/organizacionais (nomeacao, composicao, regimento de conselho —
// inclusive de UC) NAO entram, mesmo citando uma UC.
const RELEVANTE =
  /ambient|licenciament|condicionante|\bEIA\b|\bRIMA\b|\bRAS\b|compensa[cç][aã]o|interven[cç][aã]o|\bASV\b|supress[aã]o|desmatament|reserva legal|\bAPP\b|[aá]rea de preserva[cç]|unidade de conserva|\bRPPN\b|\bAPA\b|parque (estadual|nacional|natural)|esta[cç][aã]o ecol[oó]gica|monumento natural|ref[uú]gio de vida|plano de manejo|zona de amortecimento|fauna|flora|vegeta[cç]|florest|bioma|biodiversidade|recurso[s]? h[ií]dric|outorga|barragem|efluente|res[ií]duo|polui[cç]|emiss[aã]o (atmosf|de gases|de poluentes)|geolog|espeleol|caverna|minera[cç]|lavra|\bANM\b|l[ií]tio|terras raras|patrim[oô]nio (cultural|arqueol[oó]|hist[oó]ric|natural|espeleol)|arqueol[oó]|regulariza[cç][aã]o (fundi[aá]ria|ambiental)|\bTCCFM\b|auto de infra[cç]|embargo ambiental|\bISO 14001\b|\bESG\b|gest[aã]o ambiental|saneament/i
const ADMINISTRATIVO =
  /regimento interno|\bnome[aeio]|exonera|dispensa[^.]{0,20}(membro|conselheir)|composi[cç][aã]o[^.]{0,20}(conselho|c[aâ]mara|comit[eê]|colegiado|grupo)|designa[^.]{0,20}(membro|representant|conselheir|servidor)|institui[^.]{0,25}(comit[eê]|c[aâ]mara t[eé]cnica|grupo de trabalho|conselho)|recomp[oõ]|recomposi[cç]|substitui[cç][aã]o de (membro|conselheir|servidor)|altera[^.]{0,25}composi[cç]/i
// Tema oficial do ALMG (campo indexacao) — sinal positivo autoritativo adicional.
const ALMG_TEMAS =
  /meio ambiente|recursos h[ií]dricos|minera[cç][aã]o|recursos minerais|florest|patrim[oô]nio (cultural|arqueol|natural)|unidade de conserva/i

// Interessa ao briefing se toca o escopo ambiental amplo/gestao e NAO e meramente
// administrativo. Para o ALMG, a indexacao (tema oficial) reforca o sinal positivo.
function interessa(ementa, indexacao = "") {
  const em = ementa || ""
  if (ADMINISTRATIVO.test(em)) return false
  return RELEVANTE.test(em) || (indexacao && ALMG_TEMAS.test(indexacao))
}

function parseYMD(s) {
  if (!/^\d{8}$/.test(String(s))) return null
  return new Date(+s.slice(0, 4), +s.slice(4, 6) - 1, +s.slice(6, 8))
}
function isoDate(d) {
  return d.toISOString().slice(0, 10)
}
function dentroJanela(dataStr) {
  const d = parseYMD(dataStr)
  if (!d) return false
  const corte = new Date()
  corte.setDate(corte.getDate() - JANELA_DIAS)
  return d >= corte
}

async function coletarAcam() {
  if (!READ_KEY) {
    console.error("RADAR_READ_KEY ausente — pulando ACAM")
    return []
  }
  try {
    const r = await fetch(ACAM_URL, { headers: { Authorization: `Bearer ${READ_KEY}` } })
    if (!r.ok) {
      console.error("ACAM HTTP", r.status)
      return []
    }
    const j = await r.json()
    return (j.itens || []).map((it) => ({
      origem: "ACAM",
      fonte: it.fonte,
      orgao: it.orgao || null,
      tipo: it.tipo || null,
      numero: it.numero || null,
      ementa: (it.resumo || it.titulo || "").replace(/\s+/g, " ").trim(),
      data: it.data_publicacao || null,
      url: it.url || null,
    }))
  } catch (e) {
    console.error("ACAM erro:", e.message)
    return []
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Retorna { status: "ok"|"vazio"|"error", item? }.
// Distingue "norma nao existe" (HTTP 200 + noOcorrencias 0) de "falha transitoria"
// (rede/HTTP/parse). Falha NAO pode ser confundida com fim da sequencia, senao o
// throttle da API faria a varredura parar cedo e perder normas.
async function fetchNorma(tipo, numero, ano) {
  const u = `https://dadosabertos.almg.gov.br/api/v2/legislacao/mineira/${tipo}/${numero}/${ano}?formato=json`
  for (let tent = 0; tent < 3; tent++) {
    try {
      const r = await fetch(u, { redirect: "follow" })
      if (r.status === 200) {
        const j = await r.json().catch(() => null)
        const res = j && j.resultado
        if (!res) return { status: "error" }
        if (res.noOcorrencias === 0 || !res.listaItem || !res.listaItem[0]) return { status: "vazio" }
        return { status: "ok", item: res.listaItem[0] }
      }
    } catch {
      /* rede — tenta de novo */
    }
    await sleep(500 * (tent + 1))
  }
  return { status: "error" }
}

async function coletarAlmg(state) {
  const ano = new Date().getFullYear()
  const out = []
  for (const tipo of ALMG_TIPOS) {
    let n = Math.max(1, (state[tipo] || 0) - ALMG_BUFFER)
    let misses = 0
    let scanned = 0
    let maxVisto = state[tipo] || 0
    let erros = 0
    while (misses < ALMG_MISS_STOP && scanned < ALMG_MAX_SCAN) {
      const res = await fetchNorma(tipo, n, ano)
      scanned++
      if (res.status === "ok") {
        const it = res.item
        misses = 0
        maxVisto = Math.max(maxVisto, +it.numero || n)
        const idx = it.indexacao || ""
        const em = it.ementa || ""
        if (interessa(em, idx) && dentroJanela(it.data)) {
          const d = parseYMD(it.data)
          out.push({
            origem: "ALMG",
            fonte: "ALMG",
            orgao: it.origem || null,
            tipo: it.tipo,
            numero: it.numero,
            ementa: em.replace(/\s+/g, " ").trim(),
            data: d ? isoDate(d) : null,
            url: `https://www.almg.gov.br/legislacao-mineira/${it.tipo}/${it.numero}/${it.ano}/`,
          })
        }
      } else if (res.status === "vazio") {
        misses++
      } else {
        // falha transitoria: NAO conta como fim de sequencia; pausa e segue.
        if (++erros > 40) break
        await sleep(1000)
      }
      n++
      await sleep(150)
    }
    state[tipo] = maxVisto
  }
  return out
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
    return { DEC: 0, LEI: 0 }
  }
})()

const acam = await coletarAcam()
const almg = await coletarAlmg(state)
let itens = dedup([...acam.filter((a) => interessa(a.ementa)), ...almg])
itens.sort((a, b) => String(b.data || "").localeCompare(String(a.data || "")))

writeFileSync(
  "normas.json",
  JSON.stringify(
    { gerado_em: new Date().toISOString(), janela_dias: JANELA_DIAS, total: itens.length, itens },
    null,
    2,
  ),
)
writeFileSync("state.json", JSON.stringify(state, null, 2))
console.log(`normas.json: ${itens.length} itens (ACAM ${acam.length}, ALMG ${almg.length})`)
