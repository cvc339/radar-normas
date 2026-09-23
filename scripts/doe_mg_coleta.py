"""Coleta de normas nos Diários Oficiais de Minas Gerais, pela DATA DE PUBLICAÇÃO.

Duas fontes, porque o Estado publica em dois diários:
  - Diário do EXECUTIVO (Jornal Minas Gerais, jornalminasgerais.mg.gov.br): decretos do Governador
    e o infralegal de todos os órgãos (SEMAD, FEAM, IEF, IGAM, COPAM, SEAPA, IEPHA...).
    API não documentada, JWT anônimo, PDF assinado (CMS) com índice de seções por órgão.
  - Diário do LEGISLATIVO (ALMG, diariolegislativo.almg.gov.br/AAAA/LAAAAMMDD.pdf): é onde saem as
    LEIS sancionadas. Verificado em 22/09/2026 pela própria ficha da ALMG: a Lei 26.054/2026 saiu no
    Diário do Legislativo de 02/09/2026, p. 1, e não no Executivo.

Por que esta fonte e não o CTL: o CTL indexa pela data de assinatura e só recebe a norma dias
depois de publicada (defasagem medida de 5 a 7 dias), o que fez o briefing perder a Resolução
Conjunta SEMAD/SEAPA/FEAM/IEF 3.424/2026. Aqui a data é a de publicação, o dia em que a norma
passa a existir.

Sem filtro de relevância por ementa. O filtro é a SEÇÃO do Diário do Executivo (órgão), que é
autoritativa. Decisão do titular em 22/09/2026: prefere achar e descartar a perder por filtro.

Uso:
    python doe_mg_coleta.py --de 2026-08-25 --ate 2026-09-22 --saida normas-doe.json
    python doe_mg_coleta.py --dias 10 --saida normas-doe.json      (janela até hoje)
Opções:
    --secoes   trechos de nome de seção, separados por vírgula (padrão em SECOES_PADRAO), ou "todas"
    --excluir-tipos   tipos que ficam fora, separados por vírgula. Padrão "Decreto NE" (atos de
               pessoal do Governador), decisão do titular em 22/09/2026. Passar "" para não excluir.
    --cache    pasta dos PDFs baixados (não rebaixa o que já tem)
    --sem-legislativo   não busca o Diário do Legislativo

Cópia em produção: projetos/radar-normas/scripts/doe_mg_coleta.py (GitHub Actions, diário).
Alterar aqui e copiar para lá, ou o contrário, mas manter as duas iguais.
"""
import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import unicodedata
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Só IPv4. O runner do GitHub Actions não tem rota IPv6 e um host com AAAA dá "Network is
# unreachable" (Errno 101) antes de tentar o A. Sem efeito onde há IPv6 de verdade.
import socket
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [ai for ai in _getaddrinfo(*a, **k) if ai[0] == socket.AF_INET] or _getaddrinfo(*a, **k)

API ="https://www.jornalminasgerais.mg.gov.br/api/v1/"
SITE_EXEC = "https://www.jornalminasgerais.mg.gov.br/?dataJornal="
URL_LEG = "https://diariolegislativo.almg.gov.br/{ano}/L{ymd}.pdf"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

SECOES_PADRAO = [
    "governo do estado",   # decretos do Governador ("Leis e Decretos")
    "meio ambiente",       # SEMAD e, dentro dela, FEAM, IEF, IGAM, COPAM
    "agricultura",         # SEAPA
    "cultura",             # SECULT / IEPHA
    "infraestrutura",      # SEINFRA / DER-MG
]
EXCLUIR_TIPOS_PADRAO = ["decreto ne"]   # atos de pessoal; 71 de 157 itens no teste de 25/08-22/09

# número como o Diário tipografa: "49. 283", "2. 188", "3.424", "08/2026", "01337"
NUMERO = r"\d{1,3}(?:\.\s?\d{3})*(?:/\d{4})?|\d{1,6}(?:/\d{4})?"
TIPOS = (
    r"LEI COMPLEMENTAR|LEI DELEGADA|LEI|DECRETO LEGISLATIVO|DECRETO NE|DECRETO|"
    r"RESOLU[ÇC][ÃA]O CONJUNTA|RESOLU[ÇC][ÃA]O|DELIBERA[ÇC][ÃA]O NORMATIVA|DELIBERA[ÇC][ÃA]O|"
    r"PORTARIA CONJUNTA|PORTARIA|INSTRU[ÇC][ÃA]O NORMATIVA"
)
# cabeçalho no INÍCIO da linha, admitindo aspa de abertura (republicações vêm entre aspas)
CABECALHO = re.compile(
    rf"^[ \t]*[“\"']?(?P<tipo>{TIPOS})\s+"
    rf"(?P<orgaos>(?:[A-ZÇÃÕÉÊÍÓÚ]{{2,}}(?:/[A-ZÇÃÕÉÊÍÓÚ-]{{2,}})*\s+)?)"
    rf"(?:N[º°o.]?\s*)?(?P<numero>{NUMERO})\s*,?\s*"
    rf"(?:DE\s+)?(?P<data>\d{{1,2}}[ºo°]?\s+DE\s+[A-ZÇ]+\s*DE\s*\d{{4}}|\d{{2}}/\d{{2}}/\d{{4}})?"
    rf"[ \t.”\"']*$",   # cabeçalho ocupa a linha inteira; citação no meio de frase não casa
    re.M,
)
PREAMBULO = re.compile(
    r"\b(O GOVERNADOR|A GOVERNADORA|O SECRET[ÁA]RIO|A SECRET[ÁA]RIA|O PRESIDENTE|A PRESIDENTE|"
    r"O DIRETOR|A DIRETORA|O CONSELHO|A DIRETORIA|O SUPERINTENDENTE|A SUPERINTENDENTE|"
    r"O SUBSECRET|A SUBSECRET|no uso d[ae]s? atribui|O povo do Estado)"
)
MES = {"JANEIRO": 1, "FEVEREIRO": 2, "MARÇO": 3, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7,
       "AGOSTO": 8, "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12}


def sem_acento(s):
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode().lower()


def http_json(path, params=None, method="GET", body=None, token=None, timeout=120):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    h = {"User-Agent": UA, "Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class _RedirecionamentoVisivel(urllib.request.HTTPRedirectHandler):
    """Não segue redirecionamento às cegas: registra o alvo. O runner do GitHub recebeu 302 do
    Diário do Legislativo e o alvo era inalcançável (23/09/2026); daqui o mesmo URL dá 200."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirecionado para {newurl}", headers, fp)


_ABRIDOR = urllib.request.build_opener(_RedirecionamentoVisivel)


def http_bytes(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _ABRIDOR.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            print(f"  {url}: {e.code} {e.msg}", file=sys.stderr)
        return e.code, b""


# ----------------------------------------------------------------- Executivo
def autenticar():
    return http_json("Autenticacao/Autenticar", method="POST", body={})["dados"]


def edicoes_exec(token, de, ate):
    out, m = [], dt.date(de.year, de.month, 1)
    while m <= ate:
        j = http_json("Jornal/ListarEdicoesParaCalendario",
                      {"MesPublicacao": m.month, "AnoPublicacao": m.year}, token=token)
        for e in (j.get("dados") or []):
            d = dt.date.fromisoformat(e["dataPublicacao"][:10])
            if de <= d <= ate:
                out.append({"id": e["idCadernoPrincipal"], "data": d.isoformat(), "extra": bool(e.get("edicaoExtra"))})
        m = (m.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return sorted(out, key=lambda x: (x["data"], x["extra"]))


def baixar_exec(token, ed, cache):
    fn = os.path.join(cache, f"doe-{ed['data']}{'-extra' if ed['extra'] else ''}-{ed['id']}.pdf")
    fnj = fn[:-4] + ".json"
    if os.path.exists(fn) and os.path.exists(fnj):
        return fn, json.load(open(fnj, encoding="utf-8"))
    j = http_json(f"Jornal/ObterEdicaoPorId/{ed['id']}", token=token, timeout=300)
    dados = j.get("dados") or {}
    raw = base64.b64decode((dados.get("arquivoCadernoPrincipal") or {}).get("arquivo") or "")
    i = raw.find(b"%PDF")
    open(fn, "wb").write(raw[i:] if i >= 0 else raw)
    meta = {"dataPublicacao": dados.get("dataPublicacao"),
            "cadernos": [{"id": c.get("id"), "descricao": c.get("descricao"), "secoes": c.get("secoes", [])}
                         for c in dados.get("cadernos", [])],
            "totalPaginas": (dados.get("arquivoCadernoPrincipal") or {}).get("totalPaginas")}
    json.dump(meta, open(fnj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return fn, meta


def indice_secoes(meta):
    ex = next((c for c in meta["cadernos"] if "Executivo" in (c.get("descricao") or "")), None)
    if not ex:
        return []
    return sorted(ex["secoes"], key=lambda s: s["paginaInicial"])


def texto_pagina(page):
    """Texto da página na ORDEM DE LEITURA. O Diário do Executivo é diagramado em quatro colunas
    e cada ato ocupa meia página (duas colunas). A extração crua do PDF segue a ordem do fluxo
    interno, que em 19/09/2026 (p. 7) trazia as resoluções da SEJUSP antes dos cabeçalhos de seção
    da SEINFRA e da própria SEJUSP. A ordem de leitura é: metade esquerda de cima para baixo,
    depois a metade direita. Dentro de cada metade, um bloco de meia largura (cabeçalho de seção,
    ato) é separador de faixa, e a faixa entre dois separadores se lê coluna a coluna."""
    W = page.rect.width
    meio = W / 2
    TOL = 15
    bl = [tuple(b[:5]) for b in page.get_text("blocks") if len(b) < 7 or b[6] == 0]
    bl = [b for b in bl if b[4].strip()]
    if not bl:
        return ""
    margem = min(b[0] for b in bl)
    calha = {0: meio - (meio - margem) / 2, 1: meio + (meio - margem) / 2}   # entre as colunas de cada metade

    def cruza(b, x):
        return b[0] < x - TOL and b[2] > x + TOL

    def fecha(faixa, g):
        # dentro da faixa, coluna da esquerda inteira e depois a da direita; coluna pelo CENTRO do
        # bloco, porque linha centrada de cabeçalho começa uns pontos antes ou depois da vizinha
        faixa.sort(key=lambda b: (0 if (b[0] + b[2]) / 2 < g else 1, b[1]))
        return [b[4] for b in faixa]

    partes = []
    regiao = []
    # 1) bloco de largura total (cruza o meio da página) separa regiões verticais
    for b in sorted(bl, key=lambda b: (b[1], b[0])) + [None]:
        if b is not None and not cruza(b, meio):
            regiao.append(b)
            continue
        # 2) dentro da região, metade esquerda inteira e depois a direita
        for h in (0, 1):
            g = calha[h]
            faixa = []
            # 3) dentro da metade, bloco que cruza a calha (cabeçalho de seção, ato de meia
            #    página) separa faixas; 4) a faixa se lê coluna a coluna
            for c in sorted([z for z in regiao if ((z[0] + z[2]) / 2 < meio) == (h == 0)],
                            key=lambda z: (z[1], z[0])):
                if cruza(c, g):
                    partes.extend(fecha(faixa, g))
                    faixa = []
                    partes.append(c[4])
                else:
                    faixa.append(c)
            partes.extend(fecha(faixa, g))
        regiao = []
        if b is not None:
            partes.append(b[4])
    return "\n".join(p.rstrip("\n") for p in partes) + "\n"


def paginas_ordenadas(doc):
    return [texto_pagina(doc[p]) for p in range(doc.page_count)]


def ancora(nome, n=6, inicio_de_linha=False):
    """Regex tolerante ao cabeçalho da seção como impresso na página (quebras de linha, acentos,
    hífen que vira quebra)."""
    palavras = [re.escape(w).replace(r"\-", r"[-\s]*") for w in sem_acento(nome).split()[:n]]
    pref = r"(?:^|\n)[ \t]*" if inicio_de_linha else ""
    return re.compile(pref + r"\s+".join(palavras), re.I)


TITULAR_DA_SECAO = re.compile(r"\n[^\n:]{3,45}:\s*\S")   # "Secretário: Nome", "Diretor-Geral: Nome"


def posicao_do_cabecalho(nome, texto):
    """O nome da secretaria aparece muitas vezes no corpo dos atos ("lotado na Secretaria de Estado
    de Justiça..."). O cabeçalho impresso começa a linha e vem seguido, em poucos caracteres, da
    linha do titular do órgão ("Secretário: Nome"). Prefere esse; depois, a primeira ocorrência em
    início de linha; por fim, a primeira ocorrência em qualquer lugar."""
    primeira_linha = None
    for m in ancora(nome, inicio_de_linha=True).finditer(texto):
        if primeira_linha is None:
            primeira_linha = m.start()
        if TITULAR_DA_SECAO.search(texto[m.end():m.end() + 110]):
            return m.start()
    if primeira_linha is not None:
        return primeira_linha
    m = ancora(nome).search(texto) or ancora(nome, n=3, inicio_de_linha=True).search(texto)
    return m.start() if m else None


def mapa_de_ancoras(paginas, secs):
    """Posição real de cada seção: (página, deslocamento no texto ordenado). O índice do Diário só
    dá a página inicial, e várias seções começam na MESMA página (em 19/09/2026, SEPLAG e SEMAD na
    p. 11). Ordenar só por página atribui à seção errada as páginas seguintes. Aqui a ordem vem
    da posição do cabeçalho impresso; seção sem cabeçalho localizável fica no início da página."""
    pos = []
    for s in secs:
        p = s["paginaInicial"] - 1
        off = 0
        if 0 <= p < len(paginas):
            o = posicao_do_cabecalho(s["descricao"], sem_acento(paginas[p]))
            if o is not None:
                off = o
        pos.append((p, off, s))
    pos.sort(key=lambda x: (x[0], x[1]))
    return pos


def texto_da_secao(paginas, pos, k):
    """Texto da seção k do mapa: do seu cabeçalho até o cabeçalho da seção seguinte no mapa, que
    pode estar na mesma página ou páginas adiante."""
    p_ini, off_ini, s = pos[k]
    if k + 1 < len(pos):
        p_fim, off_fim, _ = pos[k + 1]
    else:
        p_fim, off_fim = len(paginas) - 1, None
    partes = []
    for p in range(p_ini, min(p_fim, len(paginas) - 1) + 1):
        t = paginas[p]
        ini = off_ini if p == p_ini else 0
        fim = off_fim if (p == p_fim and off_fim is not None) else len(t)
        if fim <= ini:
            continue
        partes.append((p + 1, t[ini:fim]))
    return partes


def ementa_apos(texto, pos):
    trecho = texto[pos:pos + 900]
    m = PREAMBULO.search(trecho)
    if m and m.start() > 15:
        trecho = trecho[:m.start()]
    trecho = re.sub(r"\s+", " ", trecho).strip(" .,;:-–”\"")
    trecho = re.sub(r"(\d)\.\s+(\d{3})", r"\1.\2", trecho)   # "nº 1. 234" -> "nº 1.234"
    # fim de frase: ponto seguido de maiúscula, aspa ou fim; não o ponto de "art.", "nº", "inc."
    mm = re.match(r"(.{25,}?(?<!\bart)(?<!\bn)(?<!\binc)(?<!\bal)\.)(?=\s+[A-ZÀ-Ú“\"(]|\s*$)", trecho)
    return (mm.group(1) if mm else trecho[:300]).strip()


def numero_limpo(n):
    return re.sub(r"\.\s+", ".", n.strip(" .,"))


def data_assinatura(s):
    if not s:
        return None
    m = re.search(r"(\d{1,2})[ºo°]?\s+DE\s+([A-ZÇ]+)\s*DE\s*(\d{4})", s)
    if m and m.group(2) in MES:
        try:
            return dt.date(int(m.group(3)), MES[m.group(2)], int(m.group(1))).isoformat()
        except ValueError:
            return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", s)
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def item_de(m, texto, pagina, secao, data_pub, url, fonte, extra=False):
    tipo = re.sub(r"\s+", " ", m.group("tipo")).title().replace("De ", "de ").replace("Ne", "NE")
    org = (m.group("orgaos") or "").strip()
    num = numero_limpo(m.group("numero"))
    ass = data_assinatura(m.group("data"))
    rot = f"{tipo} {org + ' ' if org else ''}nº {num}"
    if m.group("data"):
        dt_ = re.sub(r"\s+", " ", m.group("data"))
        dt_ = re.sub(r"(?i)([a-zç])de(\s*\d{4})", r"\1 de\2", dt_)   # "AGOSTODE 2026" (PDF)
        rot += ", de " + dt_.title().replace(" De ", " de ")
    em = ementa_apos(texto, m.end())
    if len(em) < 20:
        return None
    return {"origem": "DOE-MG", "fonte": fonte, "orgao": secao, "tipo": tipo, "numero": num,
            "rotulo": rot, "ementa": em, "data": data_pub, "data_assinatura": ass,
            "pagina": pagina, "edicao_extra": extra, "url": url}


def varrer_exec(fn, meta, filtros, ed):
    import fitz
    doc = fitz.open(fn)
    paginas = paginas_ordenadas(doc)
    pos = mapa_de_ancoras(paginas, indice_secoes(meta))
    itens = []
    for k, (_, _, s) in enumerate(pos):
        if filtros != "todas" and not any(f in sem_acento(s["descricao"]) for f in filtros):
            continue
        for pagina, t in texto_da_secao(paginas, pos, k):
            flat = re.sub(r"[ \t]+", " ", t)
            for m in CABECALHO.finditer(flat):
                it = item_de(m, flat, pagina, s["descricao"], ed["data"], SITE_EXEC + ed["data"],
                             "DOE-Executivo", ed["extra"])
                if it:
                    itens.append(it)
    return itens


# ---------------------------------------------------------------- Legislativo
def varrer_leg(de, ate, cache):
    import fitz
    itens, dias = [], []
    d = de
    falhas = 0
    while d <= ate:
        ymd = d.strftime("%Y%m%d")
        fn = os.path.join(cache, f"leg-{d.isoformat()}.pdf")
        if not os.path.exists(fn):
            try:
                st, b = http_bytes(URL_LEG.format(ano=d.year, ymd=ymd))
            except Exception as e:   # rede/redirecionamento: um dia perdido não derruba a coleta
                falhas += 1
                if falhas <= 2:
                    print(f"  Legislativo {d}: falha de rede ({e})", file=sys.stderr)
                d += dt.timedelta(days=1)
                continue
            if st == 200 and b[:4] == b"%PDF":
                open(fn, "wb").write(b)
        if os.path.exists(fn):
            try:
                doc = fitz.open(fn)
            except Exception:
                d += dt.timedelta(days=1)
                continue
            n = 0
            for p in range(doc.page_count):
                flat = re.sub(r"[ \t]+", " ", texto_pagina(doc[p]))
                for m in CABECALHO.finditer(flat):
                    if not m.group("tipo").startswith(("LEI", "DECRETO LEGISLATIVO", "RESOLU")):
                        continue
                    it = item_de(m, flat, p + 1, "Assembleia Legislativa (Diário do Legislativo)",
                                 d.isoformat(), URL_LEG.format(ano=d.year, ymd=ymd), "DOE-Legislativo")
                    if it:
                        itens.append(it)
                        n += 1
            dias.append((d.isoformat(), doc.page_count, n))
        d += dt.timedelta(days=1)
    if falhas:
        print(f"  Legislativo: {falhas} dia(s) sem acesso", file=sys.stderr)
    return itens, dias


def dedup(itens):
    seen, out = set(), []
    for it in itens:
        k = (it["fonte"], it["data"], it["tipo"].lower(), it["numero"], it["ementa"][:50].lower())
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--de")
    ap.add_argument("--ate")
    ap.add_argument("--dias", type=int, help="janela de N dias até hoje (alternativa a --de/--ate)")
    ap.add_argument("--saida", default="normas-doe.json")
    ap.add_argument("--secoes", default=",".join(SECOES_PADRAO))
    ap.add_argument("--excluir-tipos", default=",".join(EXCLUIR_TIPOS_PADRAO))
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache_doe"))
    ap.add_argument("--sem-legislativo", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.cache, exist_ok=True)
    filtros = "todas" if a.secoes.strip().lower() == "todas" else [sem_acento(x.strip()) for x in a.secoes.split(",") if x.strip()]
    excluir = {sem_acento(x.strip()) for x in a.excluir_tipos.split(",") if x.strip()}
    if a.dias:
        ate = dt.date.today()
        de = ate - dt.timedelta(days=a.dias)
    elif a.de and a.ate:
        de, ate = dt.date.fromisoformat(a.de), dt.date.fromisoformat(a.ate)
    else:
        ap.error("informe --dias ou --de e --ate")

    todos = []
    try:
        tok = autenticar()
        eds = edicoes_exec(tok, de, ate)
        print(f"Executivo: {len(eds)} edições em {de}..{ate}")
    except Exception as e:
        print(f"Executivo: sem acesso à API do Jornal Minas Gerais ({e})", file=sys.stderr)
        eds = []
    for ed in eds:
        try:
            fn, meta = baixar_exec(tok, ed, a.cache)
            itens = varrer_exec(fn, meta, filtros, ed)
        except Exception as e:   # uma edição com problema não derruba as demais
            print(f"  {ed['data']}: falha ({e})", file=sys.stderr)
            continue
        todos.extend(itens)
        print(f"  {ed['data']}{' (extra)' if ed['extra'] else ''}: {meta.get('totalPaginas')} p., {len(itens)} normas")
    if not a.sem_legislativo:
        leg, dias = varrer_leg(de, ate, a.cache)
        print(f"Legislativo: {len(dias)} PDFs, {len(leg)} leis/decretos legislativos")
        for d_, pg, n in dias:
            if n:
                print(f"  {d_}: {pg} p., {n}")
        todos.extend(leg)
    todos = dedup(todos)
    antes = len(todos)
    todos = [t for t in todos if sem_acento(t["tipo"]) not in excluir]
    if antes != len(todos):
        print(f"excluídos por tipo ({a.excluir_tipos}): {antes - len(todos)}")
    todos.sort(key=lambda x: (x["data"], x["fonte"], x["pagina"]))
    json.dump({"gerado_em": dt.datetime.now().isoformat(timespec="seconds"),
               "fonte": "Diário do Executivo (Jornal Minas Gerais) + Diário do Legislativo (ALMG)",
               "periodo": [de.isoformat(), ate.isoformat()], "secoes": a.secoes,
               "excluir_tipos": a.excluir_tipos, "total": len(todos), "itens": todos},
              open(a.saida, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"gravado {a.saida}: {len(todos)} itens")


if __name__ == "__main__":
    main()
