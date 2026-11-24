"""Origem indisponivel: o que o pipeline faz quando a ANP nao responde.

Regressao dos runs #155, #156 e #157 (21 a 23/11/2026). Tres execucoes
seguidas morreram com `ConnectionResetError(104)` nas quatro tentativas, em 42
segundos, e o job inteiro ficou vermelho nos tres dias. O arquivo estava no ar
-- mesma URL, conferida na mao -- e o codigo nao mudara desde a ultima
execucao boa: o commit anterior so tocava `docs/data/qualidade.json`.

Dois defeitos nossos ficaram visiveis nisso:

1. as quatro tentativas eram SEGUIDAS, sem dormir. Contra um edge que limita
   taxa, quatro requisicoes em 42s confirmam o bloqueio em vez de contorna-lo;
2. nao conseguir coletar derrubava o job com a mesma severidade de um parser
   quebrado. Alarme diario que nao exige acao ensina a ignorar o alarme --
   e o proximo, que exigir, vai parecer igual.
"""
import json

import pytest
import requests

import coleta
import comum


# --------------------------------------------------------------------------- #
# baixar: indisponivel nao e o mesmo que resposta ruim
# --------------------------------------------------------------------------- #
class Resposta:
    def __init__(self, status=200, content=b"x" * 20000):
        self.status_code = status
        self.content = content


class Sessao:
    """Sessao falsa; `roteiro` e a lista do que cada GET devolve ou levanta."""

    def __init__(self, roteiro):
        self.roteiro = list(roteiro)
        self.chamadas = 0
        self.headers = {}

    def get(self, url, **kw):
        self.chamadas += 1
        item = self.roteiro[min(self.chamadas - 1, len(self.roteiro) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def sem_relogio():
    return {"dormir": lambda s: None, "aleatorio": lambda: 0.5}


def com_sessao(monkeypatch, roteiro):
    s = Sessao(roteiro)
    monkeypatch.setattr(comum.requests, "Session", lambda: s)
    return s


RESET = requests.ConnectionError(
    "('Connection aborted.', ConnectionResetError(104, 'Connection reset by peer'))")


def test_reset_de_conexao_vira_fonte_indisponivel(monkeypatch, sem_relogio):
    """O erro exato dos runs #155-#157."""
    com_sessao(monkeypatch, [RESET])
    with pytest.raises(comum.FonteIndisponivel):
        comum.baixar("http://x", **sem_relogio)


def test_404_nao_e_indisponibilidade(monkeypatch, sem_relogio):
    """Arquivo que mudou de lugar precisa de gente, nao de paciencia: se isso
    degradasse igual a uma queda, o painel ficaria meses avisando 'origem fora
    do ar' enquanto a URL certa estava a um clique."""
    s = com_sessao(monkeypatch, [Resposta(status=404)])
    with pytest.raises(RuntimeError) as e:
        comum.baixar("http://x", **sem_relogio)
    assert not isinstance(e.value, comum.FonteIndisponivel)
    assert s.chamadas == 1          # nem tenta de novo


def test_403_tambem_para_na_hora(monkeypatch, sem_relogio):
    """Bloqueio declarado e diferente de conexao derrubada: da para ler."""
    com_sessao(monkeypatch, [Resposta(status=403)])
    with pytest.raises(RuntimeError) as e:
        comum.baixar("http://x", **sem_relogio)
    assert not isinstance(e.value, comum.FonteIndisponivel)


def test_500_e_indisponibilidade_e_insiste(monkeypatch, sem_relogio):
    s = com_sessao(monkeypatch, [Resposta(status=503)])
    with pytest.raises(comum.FonteIndisponivel):
        comum.baixar("http://x", tentativas=3, **sem_relogio)
    assert s.chamadas == 3


def test_arquivo_truncado_derruba(monkeypatch, sem_relogio):
    """Resposta chegou e nao presta: publicar isso seria pior que falhar."""
    com_sessao(monkeypatch, [Resposta(content=b"nada")])
    with pytest.raises(RuntimeError) as e:
        comum.baixar("http://x", minimo=5000, **sem_relogio)
    assert not isinstance(e.value, comum.FonteIndisponivel)
    assert "truncado" in str(e.value)


def test_recupera_na_segunda_tentativa(monkeypatch, sem_relogio):
    com_sessao(monkeypatch, [RESET, Resposta()])
    assert len(comum.baixar("http://x", **sem_relogio)) == 20000


def test_headers_parecem_navegador(monkeypatch, sem_relogio):
    """O UA anterior se identificava como robo, que e exatamente o que um WAF
    descarta. O Referer e a pagina publica de onde o arquivo e linkado."""
    s = com_sessao(monkeypatch, [Resposta()])
    comum.baixar("http://x", **sem_relogio)
    assert "Chrome" in s.headers["User-Agent"]
    assert "anp-ppi-dashboard" not in s.headers["User-Agent"]
    assert s.headers["Referer"].startswith("https://www.gov.br/anp/")
    assert s.headers["Accept-Language"].startswith("pt-BR")


# --------------------------------------------------------------------------- #
# backoff
# --------------------------------------------------------------------------- #
def test_primeira_tentativa_nao_espera():
    assert comum.espera_da_tentativa(1) == 0.0


def test_backoff_cresce_e_tem_teto():
    """As quatro tentativas antigas cabiam em 42 segundos, sem dormir."""
    e = [comum.espera_da_tentativa(n, aleatorio=lambda: 0.5) for n in range(2, 9)]
    assert e[0] == pytest.approx(comum.ESPERA_BASE)
    assert e == sorted(e)
    assert max(e) <= comum.ESPERA_MAX + 1e-9
    assert sum(e) > 60          # a politica nova espera minutos, nao segundos


def test_jitter_fica_na_faixa():
    base = comum.ESPERA_BASE * 2
    for extremo in (0.0, 1.0):
        v = comum.espera_da_tentativa(3, aleatorio=(lambda e: lambda: e)(extremo))
        assert abs(v - base) == pytest.approx(base * comum.JITTER)


# --------------------------------------------------------------------------- #
# estado da coleta
# --------------------------------------------------------------------------- #
def test_primeira_falha_marca_a_data(tmp_path):
    e = coleta.registrar_falha(tmp_path, "http://x", "reset")
    assert e["falhas_consecutivas"] == 1
    assert e["desde"] == comum.hoje_br().isoformat()
    assert (tmp_path / coleta.NOME).exists()


def test_falhas_seguidas_somam_e_preservam_o_desde(tmp_path):
    """`desde` responde 'ha quanto tempo o painel esta sem coleta', que e a
    pergunta do leitor; recomecar a contagem a cada dia apagaria isso."""
    coleta.registrar_falha(tmp_path, "http://x", "reset")
    estado = json.loads((tmp_path / coleta.NOME).read_text(encoding="utf-8"))
    estado["desde"] = "2026-11-21"
    (tmp_path / coleta.NOME).write_text(json.dumps(estado), encoding="utf-8")

    e = coleta.registrar_falha(tmp_path, "http://x", "reset")
    assert e["falhas_consecutivas"] == 2
    assert e["desde"] == "2026-11-21"


def test_sucesso_apaga_o_estado(tmp_path):
    """Ausencia do arquivo e a saude: assim a coleta diaria nao gera um commit
    por dia so para dizer que esta tudo bem (invariante 5)."""
    coleta.registrar_falha(tmp_path, "http://x", "reset")
    assert coleta.registrar_sucesso(tmp_path) is True
    assert not (tmp_path / coleta.NOME).exists()
    assert coleta.registrar_sucesso(tmp_path) is False


@pytest.mark.parametrize("n,esperado", [(1, "aviso"), (2, "aviso"),
                                        (3, "erro"), (9, "erro")])
def test_nivel_escala_com_a_insistencia(n, esperado):
    """Um dia fora e o custo normal de tentar todo dia; tres dias e evento."""
    assert coleta.nivel({"falhas_consecutivas": n}) == esperado


def test_alerta_diz_a_semana_que_ficou_na_tela(tmp_path):
    e = coleta.registrar_falha(tmp_path, "http://x", "reset by peer")
    a = coleta.alerta(e, ultima_semana="2026-11-13")
    assert a["codigo"] == "fonte_indisponivel"
    assert a["nivel"] == "aviso"
    assert "13/11/2026" in a["texto"]
    assert "nenhum numero desta pagina e novo" in a["texto"]


def test_alerta_de_erro_sugere_o_que_conferir(tmp_path):
    for _ in range(3):
        e = coleta.registrar_falha(tmp_path, "http://x", "reset")
    a = coleta.alerta(e)
    assert a["nivel"] == "erro"
    assert "URL mudou de lugar" in a["texto"]


# --------------------------------------------------------------------------- #
# injecao no qualidade.json
# --------------------------------------------------------------------------- #
def qualidade_com(alertas, caminho):
    caminho.write_text(json.dumps({
        "generated_at": "2026-11-20T15:53:52-03:00",
        "resumo": {"erro": 0, "aviso": len(alertas), "info": 0},
        "alertas": alertas,
        "degraus": [{"produto": "qav"}],
    }, ensure_ascii=False), encoding="utf-8")


def test_injecao_preserva_os_alertas_verdadeiros(tmp_path):
    """O erro que isto evita: publicar uma lista vazia porque nao houve
    coleta, apagando os avisos reais da ultima coleta boa. O painel ficaria
    saudavel justamente no dia em que nao esta."""
    q = tmp_path / "qualidade.json"
    qualidade_com([
        {"nivel": "aviso", "codigo": "aliquota_presumida", "produto": "qav",
         "label": "QAV", "texto": "..."},
    ], q)

    estado = coleta.registrar_falha(tmp_path, "http://x", "reset")
    coleta.injetar_em_qualidade(tmp_path, coleta.alerta(estado))

    d = json.loads(q.read_text(encoding="utf-8"))
    codigos = [a["codigo"] for a in d["alertas"]]
    assert "aliquota_presumida" in codigos
    assert "fonte_indisponivel" in codigos
    assert d["resumo"]["aviso"] == 2
    assert d["degraus"] == [{"produto": "qav"}]      # o resto fica intacto


def test_injecao_substitui_a_versao_anterior_do_proprio_alerta(tmp_path):
    """Senao o painel acumularia um alerta por dia de falha."""
    q = tmp_path / "qualidade.json"
    qualidade_com([], q)
    for _ in range(3):
        estado = coleta.registrar_falha(tmp_path, "http://x", "reset")
        coleta.injetar_em_qualidade(tmp_path, coleta.alerta(estado))
    d = json.loads(q.read_text(encoding="utf-8"))
    assert [a["codigo"] for a in d["alertas"]].count("fonte_indisponivel") == 1
    assert d["resumo"]["erro"] == 1


def test_erro_vai_para_o_topo_da_lista(tmp_path):
    q = tmp_path / "qualidade.json"
    qualidade_com([{"nivel": "info", "codigo": "sem_semana_nova", "produto": None,
                    "label": None, "texto": "..."}], q)
    for _ in range(3):
        estado = coleta.registrar_falha(tmp_path, "http://x", "reset")
    coleta.injetar_em_qualidade(tmp_path, coleta.alerta(estado))
    d = json.loads(q.read_text(encoding="utf-8"))
    assert d["alertas"][0]["codigo"] == "fonte_indisponivel"


def test_sem_qualidade_previo_nao_quebra(tmp_path):
    estado = coleta.registrar_falha(tmp_path, "http://x", "reset")
    coleta.injetar_em_qualidade(tmp_path, coleta.alerta(estado))
    d = json.loads((tmp_path / "qualidade.json").read_text(encoding="utf-8"))
    assert d["alertas"][0]["codigo"] == "fonte_indisponivel"
