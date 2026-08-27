"""Testes da logica de defasagem.

O que se testa aqui e o que produz numero errado em silencio: pareamento de
semana entre duas fontes com calendarios diferentes, escolha da faixa de
aliquota nas bordas de vigencia e leitura de numero em formato brasileiro.
"""

import json

import pytest

import defasagem as d


# --------------------------------------------------------------------------- #
# pareamento de semanas
# --------------------------------------------------------------------------- #
class TestSemanaChave:
    """O PPI fecha na sexta e a planilha de produtores usa outro fechamento.

    Ancorar as duas na segunda-feira da semana ISO e o que faz o pareamento
    funcionar. Se isso quebrar, as series se cruzam deslocadas de uma semana e
    a defasagem sai errada sem nenhum sintoma visivel.
    """

    def test_sexta_e_segunda_da_mesma_semana_batem(self):
        # PPI: semana de 13/07 a 17/07/2026 (segunda a sexta)
        assert d.semana_chave("2026-07-17") == "2026-07-13"

    def test_sabado_cai_na_mesma_semana_iso(self):
        # Planilha de produtores fechando no sabado 18/07
        assert d.semana_chave("2026-07-18") == "2026-07-13"

    def test_domingo_cai_na_mesma_semana_iso(self):
        # Fechamento no domingo 19/07 ainda e a semana ISO iniciada em 13/07
        assert d.semana_chave("2026-07-19") == "2026-07-13"

    def test_segunda_e_ela_mesma(self):
        assert d.semana_chave("2026-07-13") == "2026-07-13"

    def test_virada_de_ano(self):
        # 01/01/2026 e quinta; a semana ISO comeca em 29/12/2025
        assert d.semana_chave("2026-01-01") == "2025-12-29"

    def test_fontes_com_fechamentos_diferentes_convergem(self):
        sexta, sabado, domingo = "2026-03-20", "2026-03-21", "2026-03-22"
        assert d.semana_chave(sexta) == d.semana_chave(sabado) == d.semana_chave(domingo)


# --------------------------------------------------------------------------- #
# faixas de aliquota
# --------------------------------------------------------------------------- #
@pytest.fixture
def trib(tmp_path):
    cfg = {
        "inicio_serie": "2023-09-04",
        "faixas": [
            {"produto": "gasolina", "de": "2023-09-04", "ate": None,
             "pis": 0.1411, "cofins": 0.6514, "cide": 0.10},
            {"produto": "diesel", "de": "2023-09-04", "ate": "2026-03-01",
             "total": 0.3550},
            {"produto": "diesel", "de": "2026-03-02", "ate": "2026-12-31",
             "total": 0.0},
            {"produto": "qav", "de": "2026-04-13", "ate": "2026-07-31",
             "total": 0.0, "presumido": True},
        ],
    }
    p = tmp_path / "tributos.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return d.carregar_tributos(p)


class TestDeducao:
    """As bordas de vigencia sao inclusivas nos dois extremos.

    Um erro de um dia aqui troca a aliquota de uma semana inteira e produz um
    degrau artificial na serie.
    """

    def test_soma_pis_cofins_cide(self, trib):
        assert d.deducao(trib, "gasolina", "2024-01-01") == pytest.approx(0.8925)

    def test_campo_total_tem_precedencia(self, trib):
        assert d.deducao(trib, "diesel", "2025-06-02") == pytest.approx(0.3550)

    def test_primeiro_dia_da_faixa_esta_dentro(self, trib):
        assert d.deducao(trib, "diesel", "2026-03-02") == 0.0

    def test_ultimo_dia_da_faixa_esta_dentro(self, trib):
        assert d.deducao(trib, "diesel", "2026-03-01") == pytest.approx(0.3550)

    def test_faixa_aberta_nao_expira(self, trib):
        assert d.deducao(trib, "gasolina", "2099-01-01") == pytest.approx(0.8925)

    def test_depois_do_fim_sem_prorrogacao_devolve_none(self, trib):
        # E o caso do QAV em agosto/2026: melhor nao publicar do que publicar
        # sobre base tributaria desconhecida.
        assert d.deducao(trib, "qav", "2026-08-03") is None

    def test_antes_do_inicio_devolve_none(self, trib):
        assert d.deducao(trib, "qav", "2026-04-06") is None

    def test_produto_desconhecido_devolve_none(self, trib):
        assert d.deducao(trib, "etanol", "2024-01-01") is None

    def test_presumido_e_preservado(self, trib):
        qav = trib["faixas"]["qav"][0]
        assert qav["presumido"] is True
        assert trib["faixas"]["gasolina"][0]["presumido"] is False


# --------------------------------------------------------------------------- #
# leitura de numero
# --------------------------------------------------------------------------- #
class TestAsNumber:
    @pytest.mark.parametrize("entrada,esperado", [
        (3.6034, 3.6034),
        (5, 5.0),
        ("3,6034", 3.6034),
        ("1.234,56", 1234.56),
        ("R$ 3,60", 3.60),
        ("  2,50  ", 2.50),
    ])
    def test_converte(self, entrada, esperado):
        assert d.as_number(entrada) == pytest.approx(esperado)

    @pytest.mark.parametrize("entrada", [None, "", "***", "-", "n/d", "ND", "abc", True])
    def test_devolve_none(self, entrada):
        assert d.as_number(entrada) is None

    def test_zero_e_valor_valido_nao_none(self):
        # Distincao que importa: 0 e um preco (improvavel, mas dado);
        # None e ausencia de dado. Confundir os dois estraga a media.
        assert d.as_number(0) == 0.0
        assert d.as_number("0,00") == 0.0


# --------------------------------------------------------------------------- #
# resumo da conferencia
# --------------------------------------------------------------------------- #
class TestResumoValidacao:
    def test_agrega_erro_maximo_e_cobertura(self):
        val = {
            "gasolina": {
                "realizacao": [
                    {"semana": "2026-03-02", "erro_pct": 0.12},
                    {"semana": "2026-03-09", "erro_pct": -0.07},
                ],
                "ppi": [{"semana": "2026-03-16", "erro_pct": 0.01}],
            },
            "diesel": {
                "realizacao": [{"semana": "2026-03-16", "erro_pct": 0.06}],
                "ppi": [],
            },
        }
        r = d.resumo_validacao(val)
        assert r["n_pontos"] == 4
        assert r["erro_max_pct"] == 0.12
        assert r["produtos"] == ["diesel", "gasolina"]
        assert r["semana_de"] == "2026-03-02"
        assert r["semana_ate"] == "2026-03-16"

    def test_declara_produtos_sem_conferencia(self):
        # O QAV nao aparece na Sintese Semanal. Isso precisa ser dito, nao
        # omitido: era o que o texto fixo "erro maximo de 0,12%" escondia.
        val = {"gasolina": {"realizacao": [{"semana": "2026-03-02", "erro_pct": 0.1}],
                            "ppi": []}}
        r = d.resumo_validacao(val)
        assert "qav" in r["sem_conferencia"]
        assert "gasolina" not in r["sem_conferencia"]

    def test_semana_ausente_vira_ponto_faltante(self):
        val = {"gasolina": {"realizacao": [{"semana": "2026-03-02", "status": "semana ausente"}],
                            "ppi": []}}
        r = d.resumo_validacao(val)
        assert r["n_pontos"] == 0
        assert r["erro_max_pct"] is None
        assert "gasolina/2026-03-02" in r["pontos_ausentes"]

    def test_validacao_vazia_nao_quebra(self):
        r = d.resumo_validacao({})
        assert r["erro_max_pct"] is None
        assert r["n_pontos"] == 0
