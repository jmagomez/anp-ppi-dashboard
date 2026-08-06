#!/usr/bin/env python3
"""
Defasagem PPI x preco de realizacao.

Preco interno: ANP, "Precos Medios Ponderados Semanais" praticados por
produtores e importadores de derivados de petroleo e biodiesel (serie semanal
a partir de 2013), coluna Brasil.

Compatibilizacao de base tributaria
-----------------------------------
O PPI da ANP e publicado sem tributos. A planilha de produtores inclui Cide,
PIS/Pasep e Cofins (exclui ICMS). Para comparar as duas series deduzimos os
tributos federais ad rem definidos em scripts/tributos.json.

A premissa e conferida a cada execucao contra os valores de REALIZACAO
publicados pela propria ANP na Sintese Semanal de Precos (ver a secao
validacao_sintese em docs/data/_debug_ppidp.json). Semanas anteriores ao
inicio da serie de aliquotas ficam sem defasagem, em vez de receberem um
numero calculado sobre base desconhecida.

O QAV entra na serie: ao contrario do que este arquivo afirmava, seus tributos
federais TAMBEM sao ad rem -- o Decreto 5.059/2004, art. 2o, IV, fixa PIS/Pasep
em R$ 12,69/m3 e Cofins em R$ 58,51/m3 (R$ 0,0712/l somados), e o Decreto
5.060/2004 zera a Cide do querosene de aviacao. A diferenca em relacao aos
outros produtos e que a Sintese Semanal nao cobre QAV, entao nao ha valor de
realizacao publicado pela ANP para conferir a deducao; a checagem possivel e a
transicao de abril/2026, quando a aliquota foi a zero.

Saidas em docs/data/:
  defasagem.json     series pareadas PPI x realizacao + defasagem
  defasagem.csv      mesma coisa em formato longo
  _debug_ppidp.json  diagnostico da extracao e das validacoes
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

PPIDP_URL = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/ppidp/precos-medios-ponderados-semanais-2013.xls"
)
PPIDP_PAGE = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/precos-de-produtores-e-importadores-de-derivados-de-petroleo-e-biodiesel"
)
# A ANP renomeou esta pagina: o endereco antigo (.../sintese-semanal-de-precos)
# passou a responder 404. O link e so referencia no JSON e no dashboard, entao a
# quebra nao derruba nada -- por isso ficou tempo no ar apontando para lugar
# nenhum. Agora `checar_links` confere o endereco a cada execucao.
SINTESE_PAGE = (
    "https://www.gov.br/anp/pt-br/assuntos/precos-e-defesa-da-concorrencia/"
    "precos/sintese-semanal-do-comportamento-dos-precos-dos-combustiveis"
)
# Indice de todas as edicoes, usado como alternativa se a pagina acima mudar
# de novo. Mais estavel porque nao carrega o nome da publicacao na URL.
SINTESE_FALLBACK = "https://www.gov.br/anp/pt-br/centrais-de-conteudo/publicacoes/sinteses"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; anp-ppi-dashboard/1.0)",
    "Accept": "*/*",
}

BR_TZ = timezone(timedelta(hours=-3))
# Antecedencia com que a expiracao de uma faixa de aliquota passa a ser avisada.
AVISO_VIGENCIA_DIAS = 45
COL_PRODUTO, COL_INI, COL_FIM, COL_BRASIL = 0, 1, 2, 8

# chave do PPI -> (regex do produto na planilha de produtores, fator de unidade)
MATCH = {
    "gasolina": (r"^gasolina a comum", 1.0),
    "diesel": (r"^oleo diesel s-?10", 1.0),
    "qav": (r"^querosene de aviacao", 1.0),
    "glp": (r"^gas liquefeito de petroleo", 13.0),
}

# Valores de REALIZACAO publicados pela ANP na Sintese Semanal de Precos,
# edicao 13/2026. Usados para conferir base tributaria e ordem de grandeza.
# chave -> {segunda-feira da semana ISO: valor}
SINTESE_REF = {
    "gasolina": {"2026-02-23": 2.70, "2026-03-02": 2.85,
                 "2026-03-09": 2.68, "2026-03-16": 3.58},
    "diesel": {"2026-02-23": 3.59, "2026-03-02": 4.71,
               "2026-03-09": 5.36, "2026-03-16": 5.29},
    "glp": {"2026-02-23": 36.71, "2026-03-02": 39.03,
            "2026-03-09": 36.02, "2026-03-16": 35.91},
}
SINTESE_PPI_REF = {
    "gasolina": {"2026-03-16": 3.92},
    "diesel": {"2026-03-16": 6.01},
    "glp": {"2026-03-16": 48.02},
}


def semana_chave(iso: str) -> str:
    """Segunda-feira da semana ISO que contem a data.

    O PPI fecha a semana na sexta e a planilha de produtores usa outro
    fechamento; ancorar as duas na segunda-feira da semana ISO faz o
    pareamento correto.
    """
    d = date.fromisoformat(iso)
    return (d - timedelta(days=d.weekday())).isoformat()


def norm(text) -> str:
    if text is None:
        return ""
    s = str(text).replace("\xa0", " ").strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).lower()


def download(url: str) -> bytes:
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, timeout=180)
            r.raise_for_status()
            if len(r.content) < 5000:
                raise RuntimeError(f"arquivo truncado: {len(r.content)} bytes")
            return r.content
        except Exception as exc:  # noqa: BLE001
            last = exc
            print(f"  [ppidp] tentativa {attempt + 1} falhou: {exc}")
    raise RuntimeError(f"download falhou: {last}")


def as_number(v):
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return None if f != f else f
    s = str(v).strip()
    if not s or set(s) <= {"*", "-", "."} or s.lower() in {"n/d", "nd"}:
        return None
    s = s.replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# tributos
# --------------------------------------------------------------------------- #
def carregar_tributos() -> dict:
    path = Path(__file__).resolve().parent / "tributos.json"
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    faixas = {}
    for f in cfg["faixas"]:
        if "total" in f:
            total = round(float(f["total"]), 6)
        else:
            total = round(f.get("pis", 0) + f.get("cofins", 0) + f.get("cide", 0), 6)
        faixas.setdefault(f["produto"], []).append(
            {"de": f["de"], "ate": f.get("ate"), "total": total, "fonte": f.get("fonte")}
        )
    for v in faixas.values():
        v.sort(key=lambda x: x["de"])
    return {"inicio_serie": cfg["inicio_serie"], "faixas": faixas}


def checar_links() -> dict:
    """Confere os enderecos que o dashboard publica como fonte.

    O link da Sintese ficou apontando para uma pagina 404 sem que nada
    quebrasse, porque ele so e exibido. Uma checagem barata a cada execucao
    evita que a proxima mudanca de endereco passe despercebida do mesmo jeito.
    """
    res = {}
    for nome, url in (("sintese", SINTESE_PAGE),
                      ("sintese_fallback", SINTESE_FALLBACK),
                      ("ppidp", PPIDP_PAGE)):
        try:
            r = requests.head(url, headers=HEADERS, timeout=30, allow_redirects=True)
            if r.status_code >= 400:
                r = requests.get(url, headers=HEADERS, timeout=30)
            res[nome] = {"url": url, "status": r.status_code, "ok": r.status_code < 400}
        except Exception as exc:  # noqa: BLE001
            res[nome] = {"url": url, "status": None, "ok": False,
                         "erro": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if not res[nome]["ok"]:
            print(f"[ppidp] AVISO: link de {nome} nao responde ({res[nome].get('status')}): {url}")
    return res


def validade_faixa(trib: dict, produto: str):
    """Data final da ultima faixa do produto, ou None se ela for aberta.

    Uma faixa com prazo (uma desoneracao temporaria, por exemplo) e uma bomba
    relogio silenciosa: passada a data, deducao() devolve None e as semanas
    somem do painel sem erro nenhum.
    """
    faixas = trib["faixas"].get(produto, [])
    return faixas[-1].get("ate") if faixas else None


def avisos_cobertura(data: dict, trib: dict, hoje: date) -> list:
    """Alertas sobre aliquotas: as que ja venceram e as que estao para vencer."""
    avisos = []
    for key, p in data.items():
        cob = p.get("cobertura", {})
        rotulo = p["label"]

        # 1. A serie parou: ha dado da ANP mais novo que a ultima semana
        # publicada, e ele nao entra por falta de aliquota.
        n_rec = cob.get("sem_faixa_recente", 0)
        if n_rec:
            avisos.append({
                "produto": key, "label": rotulo, "nivel": "erro",
                "texto": (f"{rotulo}: {n_rec} semana(s) com dado da ANP nao entraram na "
                          f"serie por falta de aliquota em tributos.json "
                          f"({cob['recente_de']} a {cob['recente_ate']}). A defasagem "
                          f"parou em {p['weeks'][-1]['end']} e so volta a andar quando "
                          f"o arquivo for atualizado."),
            })

        # 2. A vigencia da ultima faixa. Avaliada mesmo quando ja ha buraco --
        # antes ela ficava suprimida por um buraco historico e o alarme que
        # importava nunca saia.
        ate = cob.get("vigencia_ate")
        if ate:
            faltam = (date.fromisoformat(ate) - hoje).days
            if faltam < 0 and not n_rec:
                avisos.append({
                    "produto": key, "label": rotulo, "nivel": "erro",
                    "texto": (f"{rotulo}: a aliquota vigente venceu em {ate} e nao ha "
                              f"prorrogacao em tributos.json. A serie ainda esta "
                              f"completa porque o dado da ANP nao passou dessa data, "
                              f"mas a proxima semana publicada ficara de fora."),
                })
            elif 0 <= faltam <= AVISO_VIGENCIA_DIAS:
                avisos.append({
                    "produto": key, "label": rotulo, "nivel": "aviso",
                    "texto": (f"{rotulo}: a aliquota vigente expira em {ate}, daqui a "
                              f"{faltam} dia(s). Confirme se houve prorrogacao antes "
                              f"que as semanas comecem a sair da serie."),
                })

        # 3. Buraco no meio da serie: informativo, nao alarme.
        interior = cob.get("sem_faixa", 0) - n_rec
        if interior > 0 and cob.get("de"):
            avisos.append({
                "produto": key, "label": rotulo, "nivel": "info",
                "texto": (f"{rotulo}: {interior} semana(s) no meio da serie ficam de fora "
                          f"por nao haver aliquota unica no periodo ({cob['de']} a "
                          f"{cob['ate']}) -- exclusao deliberada, nao falta de dado."),
            })
    return avisos


def deducao(trib: dict, produto: str, semana_iso: str):
    """Tributo federal ad rem vigente na semana, ou None se fora de cobertura."""
    for f in trib["faixas"].get(produto, []):
        if semana_iso >= f["de"] and (f["ate"] is None or semana_iso <= f["ate"]):
            return f["total"]
    return None


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
def parse_ppidp(blob: bytes) -> tuple[dict, dict]:
    import xlrd

    wb = xlrd.open_workbook(file_contents=blob)
    ws = wb.sheet_by_index(0)
    datemode = wb.datemode

    def to_iso(v):
        n = as_number(v)
        if n is None or n < 20000:
            return None
        try:
            y, m, d, *_ = xlrd.xldate_as_tuple(n, datemode)
            return date(y, m, d).isoformat()
        except Exception:  # noqa: BLE001
            return None

    produtos: dict[str, dict[str, float]] = {}
    rotulos: dict[str, str] = {}

    for r in range(9, ws.nrows):
        raw = ws.cell_value(r, COL_PRODUTO)
        label = str(raw).strip() if raw is not None else ""
        if not label:
            continue
        key = norm(label)
        fim = to_iso(ws.cell_value(r, COL_FIM))
        if not fim:
            continue
        val = as_number(ws.cell_value(r, COL_BRASIL))
        if val is None:
            continue
        produtos.setdefault(key, {})[semana_chave(fim)] = val
        rotulos.setdefault(key, label)

    return ({"series": produtos, "rotulos": rotulos},
            {"linhas": ws.nrows, "produtos_encontrados": sorted(rotulos.values())})


def escolher(produtos: dict, pattern: str) -> str | None:
    rx = re.compile(pattern)
    hits = [k for k in produtos if rx.search(k)]
    return sorted(hits, key=len)[0] if hits else None


def build(products: dict, raw: dict, trib: dict) -> tuple[dict, dict]:
    series, rotulos = raw["series"], raw["rotulos"]
    out, checks = {}, {}
    inicio = trib["inicio_serie"]

    for key, (pattern, fator) in MATCH.items():
        ppi = products.get(key)
        if not ppi:
            continue
        pkey = escolher(series, pattern)
        if not pkey:
            checks[key] = {"status": "produto nao encontrado na planilha"}
            continue

        real = series[pkey]
        weeks, sem_cobertura, sem_faixa = [], 0, []

        for w in ppi["weeks"]:
            chave = semana_chave(w["end"])
            bruto = real.get(chave)
            if bruto is None:
                continue
            vals = [v for v in w["v"] if v is not None]
            if not vals:
                continue
            ppi_med = sum(vals) / len(vals)

            ded = deducao(trib, key, chave) if chave >= inicio else None
            if ded is None:
                sem_cobertura += 1
                # Semana com PPI e com preco de produtor, dentro do periodo da
                # serie, mas sem aliquota declarada: e o caso que precisa gritar.
                # Sem isso o produto simplesmente encolhe no grafico e ninguem
                # percebe que a vigencia de uma faixa venceu.
                if chave >= inicio:
                    sem_faixa.append(chave)
                continue

            r = (bruto - ded) * fator
            weeks.append({
                "end": w["end"],
                "label": w["label"],
                "ppi": round(ppi_med, 6),
                "real": round(r, 6),
                "bruto": round(bruto * fator, 6),
                "gap": round(r - ppi_med, 6),
                "gap_pct": round((r / ppi_med - 1) * 100, 4) if ppi_med else None,
            })

        weeks.sort(key=lambda x: x["end"])
        if not weeks:
            checks[key] = {"status": "sem semanas cobertas", "produto": rotulos[pkey]}
            continue

        # Buraco no meio da serie (a semana mista do QAV em abril/2026, por
        # exemplo) e uma exclusao deliberada e historica: nao adianta alertar
        # toda semana. O que precisa de alarme e o buraco NO FIM -- quando as
        # semanas mais recentes nao podem ser publicadas e a serie para de andar.
        ultima_pub = semana_chave(weeks[-1]["end"])
        recentes = [c for c in sem_faixa if c > ultima_pub]
        interior = [c for c in sem_faixa if c <= ultima_pub]
        cobertura = {"sem_faixa": len(sem_faixa), "sem_faixa_recente": len(recentes)}
        if interior:
            cobertura["de"] = min(interior)
            cobertura["ate"] = max(interior)
        if recentes:
            cobertura["recente_de"] = min(recentes)
            cobertura["recente_ate"] = max(recentes)
        vence = validade_faixa(trib, key)
        if vence:
            cobertura["vigencia_ate"] = vence

        out[key] = {
            "key": key,
            "label": ppi["label"],
            "unit": ppi["unit"],
            "fonte_realizacao": rotulos[pkey],
            "fator": fator,
            "cobertura": cobertura,
            "weeks": weeks,
        }
        last = weeks[-1]
        checks[key] = {
            "status": "ok",
            "produto": rotulos[pkey],
            "semanas": len(weeks),
            "semanas_sem_cobertura": sem_cobertura,
            "semanas_sem_faixa": len(sem_faixa),
            "cobertura": cobertura,
            "ultima": last["end"],
            "ppi": last["ppi"],
            "realizacao": last["real"],
            "defasagem_pct": last["gap_pct"],
        }

    return out, checks


def validar(data: dict, raw: dict, trib: dict) -> dict:
    """Confere contra os valores publicados pela ANP e mede o tributo implicito."""
    res = {}
    for key, refs in SINTESE_REF.items():
        pkey = escolher(raw["series"], MATCH[key][0]) if key in MATCH else None
        if not pkey:
            res[key] = {"status": "produto ausente"}
            continue
        fator = MATCH[key][1]
        itens = []
        for semana, esperado in sorted(refs.items()):
            bruto = raw["series"][pkey].get(semana)
            if bruto is None:
                itens.append({"semana": semana, "status": "semana ausente"})
                continue
            ded = deducao(trib, key, semana)
            calc = (bruto - (ded or 0)) * fator
            itens.append({
                "semana": semana,
                "anp": esperado,
                "calculado": round(calc, 4),
                "erro_pct": round((calc / esperado - 1) * 100, 2) if esperado else None,
                "tributo_aplicado": ded,
                "tributo_implicito": round(bruto - esperado / fator, 4),
            })
        ppi_chk = []
        p = data.get(key)
        if p:
            for semana, esperado in SINTESE_PPI_REF.get(key, {}).items():
                w = next((x for x in p["weeks"] if semana_chave(x["end"]) == semana), None)
                if w:
                    ppi_chk.append({
                        "semana": semana, "anp": esperado, "calculado": w["ppi"],
                        "erro_pct": round((w["ppi"] / esperado - 1) * 100, 2),
                    })
        res[key] = {"realizacao": itens, "ppi": ppi_chk}
    return res


def write_outputs(data: dict, trib: dict, out_dir: Path) -> None:
    payload = {
        "generated_at": datetime.now(BR_TZ).isoformat(timespec="seconds"),
        "source_file": PPIDP_URL,
        "source_page": PPIDP_PAGE,
        "sintese_page": SINTESE_PAGE,
        "sintese_fallback": SINTESE_FALLBACK,
        "inicio_serie": trib["inicio_serie"],
        "metodologia": (
            "Defasagem = preco de realizacao menos PPI, ambos sem tributos. "
            "O preco de realizacao e o preco medio ponderado semanal de produtores "
            "e importadores publicado pela ANP (coluna Brasil), do qual sao "
            "deduzidos os tributos federais ad rem (PIS/Pasep, Cofins e Cide) "
            "listados em scripts/tributos.json. Valor negativo indica preco interno "
            "abaixo da paridade de importacao."
        ),
        "order": [k for k in MATCH if k in data],
        "avisos_cobertura": avisos_cobertura(data, trib, datetime.now(BR_TZ).date()),
        "products": data,
    }
    with open(out_dir / "defasagem.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))

    rows = [("produto", "unidade", "semana_fim", "ppi", "realizacao",
             "defasagem_abs", "defasagem_pct")]
    for p in data.values():
        for w in p["weeks"]:
            rows.append((p["label"], p["unit"], w["end"],
                         f"{w['ppi']:.6f}", f"{w['real']:.6f}", f"{w['gap']:.6f}",
                         "" if w["gap_pct"] is None else f"{w['gap_pct']:.4f}"))
    with open(out_dir / "defasagem.csv", "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def run(products: dict, out_dir: Path) -> None:
    """Executado ao final do build principal. Nunca levanta excecao."""
    debug = {"source_file": PPIDP_URL, "source_page": PPIDP_PAGE}
    try:
        trib = carregar_tributos()
        print(f"[ppidp] baixando {PPIDP_URL}")
        blob = download(PPIDP_URL)
        print(f"[ppidp] {len(blob):,} bytes")

        raw, diag = parse_ppidp(blob)
        debug.update(diag)

        data, checks = build(products, raw, trib)
        debug["inicio_serie"] = trib["inicio_serie"]
        debug["checagens"] = checks
        debug["validacao_sintese"] = validar(data, raw, trib)
        debug["links"] = checar_links()
        avisos = avisos_cobertura(data, trib, datetime.now(BR_TZ).date())
        debug["avisos_cobertura"] = avisos
        for a in avisos:
            print(f"[ppidp] {a['nivel'].upper()}: {a['texto']}")

        if data:
            write_outputs(data, trib, out_dir)
            for k, c in checks.items():
                if c.get("status") == "ok":
                    print(f"[ppidp] {k:<9} {c['semanas']:>4} semanas | ate {c['ultima']} "
                          f"| PPI {c['ppi']:.4f} vs realizacao {c['realizacao']:.4f} "
                          f"| defasagem {c['defasagem_pct']:+.2f}%")
                else:
                    print(f"[ppidp] {k:<9} {c['status']}")
        else:
            debug["error"] = "nenhum produto pareado"
            print("[ppidp] nenhum produto pareado")
    except Exception as exc:  # noqa: BLE001
        debug["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[ppidp] ERRO: {debug['error']}")

    with open(out_dir / "_debug_ppidp.json", "w", encoding="utf-8") as fh:
        json.dump(debug, fh, ensure_ascii=False, indent=1)
