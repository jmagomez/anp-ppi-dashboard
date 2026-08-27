"""Testes das checagens de sanidade.

O detector de degrau tem um teste para cada resultado possivel porque a
primeira versao dele foi para producao com um falso positivo: acusava
qualquer salto maior que metade da mudanca de aliquota, sem teto, e chamou
de erro uma alta real de preco 39 vezes maior que a mudanca.
"""

from datetime import date

import pytest

import qualidade as q


def semana(fim, valores):
    return {"end": fim, "label": fim, "v": list(valores)}


def produto(label="Gasolina A", weeks=None, locations=None):
    # `weeks or [...]` faria uma lista vazia cair no default e o teste de serie
    # vazia passaria por acidente. O sentinela None e o unico jeito de dizer
    # "nao informei" sem confundir com "informei vazio".
    if weeks is None:
        weeks = [semana("2026-08-14", [3.0, 3.1]),
                 semana("2026-08-21", [3.05, 3.15])]
    if locations is None:
        locations = ["Santos", "Paulinia"]
    return {"label": label, "weeks": weeks, "locations": locations}


HOJE = date(2026, 8, 27)


# --------------------------------------------------------------------------- #
# integridade contra a execucao anterior
# --------------------------------------------------------------------------- #
class TestIntegridade:
    def test_serie_que_encolhe_e_erro(self):
        # A ANP republicar a planilha incompleta e plausivel; sobrescrever dado
        # bom por dado truncado sem avisar, nao.
        ach = q.Achados()
        atual = {"gasolina": produto()}
        anterior = {"products": {"gasolina": produto(weeks=[
            semana("2026-08-07", [2.9, 3.0]),
            semana("2026-08-14", [3.0, 3.1]),
            semana("2026-08-21", [3.05, 3.15]),
        ])}}
        q.checar_ppi(atual, anterior, ach, HOJE)
        assert "serie_encolheu" in ach.codigos("erro")

    def test_serie_que_retrocede_e_erro(self):
        ach = q.Achados()
        atual = {"gasolina": produto(weeks=[semana("2026-08-07", [2.9, 3.0])])}
        anterior = {"products": {"gasolina": produto(weeks=[
            semana("2026-08-07", [2.9, 3.0]),
            semana("2026-08-14", [3.0, 3.1]),
        ])}}
        q.checar_ppi(atual, anterior, ach, HOJE)
        assert "serie_retrocedeu" in ach.codigos("erro")

    def test_terminal_que_some_e_aviso(self):
        ach = q.Achados()
        atual = {"gasolina": produto(locations=["Santos"],
                                     weeks=[semana("2026-08-21", [3.0])])}
        anterior = {"products": {"gasolina": produto(
            locations=["Santos", "Paulinia"],
            weeks=[semana("2026-08-21", [3.0, 3.1])])}}
        q.checar_ppi(atual, anterior, ach, HOJE)
        assert "terminal_sumiu" in ach.codigos("aviso")

    def test_sem_semana_nova_e_um_unico_info_agregado(self):
        # Um achado por produto viraria quatro linhas identicas toda semana.
        ach = q.Achados()
        prods = {k: produto(label=k) for k in ("gasolina", "diesel", "qav", "glp")}
        anterior = {"products": {k: produto(label=k) for k in prods}}
        q.checar_ppi(prods, anterior, ach, HOJE)
        assert ach.codigos("info").count("sem_semana_nova") == 1

    def test_sem_execucao_anterior_nao_quebra(self):
        ach = q.Achados()
        q.checar_ppi({"gasolina": produto()}, None, ach, HOJE)
        assert "serie_encolheu" not in ach.codigos()


# --------------------------------------------------------------------------- #
# integridade interna
# --------------------------------------------------------------------------- #
class TestIntegridadeInterna:
    def test_semana_duplicada_e_erro(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-08-21", [3.0, 3.1]),
                           semana("2026-08-21", [3.0, 3.1])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "semana_duplicada" in ach.codigos("erro")

    def test_valor_nao_positivo_e_erro(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-08-21", [0.0, 3.1])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "valor_nao_positivo" in ach.codigos("erro")

    def test_serie_vazia_e_erro(self):
        ach = q.Achados()
        q.checar_ppi({"gasolina": produto(weeks=[])}, None, ach, HOJE)
        assert "sem_semanas" in ach.codigos("erro")

    def test_buraco_antigo_nao_alarma(self):
        # Buracos de 2022 nao dizem nada sobre a execucao de hoje.
        ach = q.Achados()
        p = produto(weeks=[semana("2022-04-14", [3.0, 3.1]),
                           semana("2022-04-29", [3.0, 3.1])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "buraco_calendario" not in ach.codigos()

    def test_buraco_recente_alarma(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-07-24", [3.0, 3.1]),
                           semana("2026-08-14", [3.0, 3.1])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "buraco_calendario" in ach.codigos("aviso")


# --------------------------------------------------------------------------- #
# plausibilidade da ultima semana
# --------------------------------------------------------------------------- #
class TestPlausibilidade:
    def test_variacao_de_publicacao_e_erro(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-08-14", [3.0, 3.0]),
                           semana("2026-08-21", [9.0, 9.0])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "variacao_impossivel" in ach.codigos("erro")

    def test_variacao_grande_mas_possivel_e_aviso(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-08-14", [3.0, 3.0]),
                           semana("2026-08-21", [3.75, 3.75])])  # +25%
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "variacao_extrema" in ach.codigos("aviso")
        assert "variacao_impossivel" not in ach.codigos("erro")

    def test_variacao_normal_nao_gera_nada(self):
        ach = q.Achados()
        p = produto(weeks=[semana("2026-08-14", [3.00, 3.00]),
                           semana("2026-08-21", [3.09, 3.09])])  # +3%
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert not [c for c in ach.codigos() if c.startswith("variacao")]

    def test_cobertura_baixa_e_aviso(self):
        ach = q.Achados()
        p = produto(locations=["A", "B", "C", "D", "E"],
                    weeks=[semana("2026-08-21", [3.0, 3.0, None, None, None])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "cobertura_baixa" in ach.codigos("aviso")

    def test_terminal_muito_fora_da_media_e_aviso(self):
        ach = q.Achados()
        p = produto(locations=["A", "B"], weeks=[semana("2026-08-21", [3.0, 5.0])])
        q.checar_ppi({"gasolina": p}, None, ach, HOJE)
        assert "terminal_fora_da_faixa" in ach.codigos("aviso")


# --------------------------------------------------------------------------- #
# detector de degrau
# --------------------------------------------------------------------------- #
TAXA = 0.0712  # QAV: PIS + Cofins ad rem, R$/l


def cfg_qav(presumido=False):
    return {"faixas": [
        {"produto": "qav", "de": "2023-09-04", "ate": "2026-04-05", "total": TAXA},
        {"produto": "qav", "de": "2026-04-13", "ate": None, "total": 0.0,
         **({"presumido": True} if presumido else {})},
    ]}


def defas_qav(gaps_antes, gaps_depois):
    semanas = ([{"end": f"2026-03-{d:02d}", "gap": g}
                for d, g in zip(range(1, 32, 5), gaps_antes)]
               + [{"end": f"2026-05-{d:02d}", "gap": g}
                  for d, g in zip(range(1, 32, 5), gaps_depois)])
    return {"products": {"qav": {"label": "QAV", "weeks": semanas}}}


class TestDetectorDeDegrau:
    def test_assinatura_de_aliquota_errada_e_erro(self):
        # Se deduzimos a taxa nova quando a antiga ainda valia, a realizacao
        # reconstruida fica alta demais e a defasagem salta exatamente -d_taxa.
        antes = [-1.00] * 6
        depois = [-1.00 + TAXA] * 6
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, defas_qav(antes, depois), cfg_qav())
        assert "degrau_aliquota" in ach.codigos("erro")
        assert degraus[0]["razao_residuo"] == pytest.approx(0.0, abs=0.01)

    def test_serie_continua_nao_acusa(self):
        antes = [-1.00] * 6
        depois = [-1.00] * 6
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, defas_qav(antes, depois), cfg_qav())
        assert "degrau_aliquota" not in ach.codigos("erro")
        assert degraus[0]["razao_residuo"] == pytest.approx(1.0, abs=0.01)

    def test_mercado_que_se_move_muito_nao_acusa(self):
        # REGRESSAO. Este e o caso que foi para producao como erro: na virada de
        # abril/2026 o QAV subiu 2,79 R$, 39 vezes a mudanca de aliquota de
        # 0,07 R$. A regra antiga so perguntava se o salto era grande.
        antes = [-1.00] * 6
        depois = [1.787] * 6  # salto de +2,787
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, defas_qav(antes, depois), cfg_qav())
        assert "degrau_aliquota" not in ach.codigos("erro")
        assert degraus[0]["razao_residuo"] > q.RESIDUO_INCONCLUSIVO

    def test_presumida_com_mercado_calmo_sustenta_a_presuncao(self):
        ach = q.Achados()
        q.checar_degraus(None, ach, defas_qav([-1.0] * 6, [-1.0] * 6), cfg_qav(True))
        avisos = [i for i in ach.itens if i["codigo"] == "aliquota_presumida"]
        assert avisos and "sustenta a presuncao" in avisos[0]["texto"]

    def test_presumida_com_mercado_agitado_se_declara_inconclusiva(self):
        # Honestidade importa mais que veredito: o teste nao pode fingir que
        # confirmou algo que nao consegue distinguir.
        ach = q.Achados()
        q.checar_degraus(None, ach, defas_qav([-1.0] * 6, [1.787] * 6), cfg_qav(True))
        avisos = [i for i in ach.itens if i["codigo"] == "aliquota_presumida"]
        assert avisos and "inconclusivo" in avisos[0]["texto"]

    def test_poucas_semanas_depois_do_corte_nao_conclui(self):
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, defas_qav([-1.0] * 6, [-1.0]), cfg_qav(True))
        assert degraus == []
        assert "aliquota_presumida" in ach.codigos("aviso")

    def test_faixa_sem_mudanca_de_taxa_e_ignorada(self):
        cfg = {"faixas": [
            {"produto": "qav", "de": "2023-09-04", "ate": "2026-04-05", "total": 0.0},
            {"produto": "qav", "de": "2026-04-13", "ate": None, "total": 0.0},
        ]}
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, defas_qav([-1.0] * 6, [-1.0] * 6), cfg)
        assert degraus == []
        assert ach.itens == []

    def test_produto_sem_defasagem_e_ignorado(self):
        ach = q.Achados()
        degraus = q.checar_degraus(None, ach, {"products": {}}, cfg_qav())
        assert degraus == []
