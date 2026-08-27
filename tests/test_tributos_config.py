"""Validacao do proprio scripts/tributos.json.

Este arquivo e editado a mao sempre que a legislacao muda. Um erro aqui nao
levanta excecao nem aparece no painel: apenas troca a aliquota de um periodo
inteiro, e a defasagem sai errada com aparencia de certa. Estes testes rodam
contra o arquivo de verdade, nao contra um exemplo.
"""

import json
from datetime import date
from pathlib import Path

import pytest

import defasagem as d

CAMINHO = Path(__file__).resolve().parents[1] / "scripts" / "tributos.json"


@pytest.fixture(scope="module")
def cfg():
    with open(CAMINHO, encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def por_produto(cfg):
    agrupado = {}
    for f in cfg["faixas"]:
        agrupado.setdefault(f["produto"], []).append(f)
    for v in agrupado.values():
        v.sort(key=lambda x: x["de"])
    return agrupado


class TestEstrutura:
    def test_inicio_serie_e_data_valida(self, cfg):
        date.fromisoformat(cfg["inicio_serie"])

    def test_toda_faixa_tem_produto_data_e_fonte(self, cfg):
        for f in cfg["faixas"]:
            assert f.get("produto"), f
            assert f.get("de"), f
            # Sem fonte, ninguem consegue auditar de onde veio o numero.
            assert f.get("fonte"), f

    def test_datas_sao_iso_e_coerentes(self, cfg):
        for f in cfg["faixas"]:
            de = date.fromisoformat(f["de"])
            if f.get("ate"):
                assert date.fromisoformat(f["ate"]) >= de, f

    def test_valor_declarado_e_nao_negativo(self, cfg):
        for f in cfg["faixas"]:
            tem_total = "total" in f
            tem_partes = any(k in f for k in ("pis", "cofins", "cide"))
            assert tem_total or tem_partes, f
            valor = (float(f["total"]) if tem_total
                     else f.get("pis", 0) + f.get("cofins", 0) + f.get("cide", 0))
            assert valor >= 0, f

    def test_produtos_sao_os_que_o_pipeline_conhece(self, por_produto):
        assert set(por_produto) <= set(d.MATCH)


class TestVigencias:
    def test_faixas_do_mesmo_produto_nao_se_sobrepoem(self, por_produto):
        # Sobreposicao e o erro mais perigoso: deducao() devolve a primeira
        # faixa que casa, entao a segunda vira letra morta sem aviso nenhum.
        for produto, faixas in por_produto.items():
            for anterior, atual in zip(faixas, faixas[1:]):
                assert anterior.get("ate"), (
                    f"{produto}: faixa iniciada em {anterior['de']} nao tem fim "
                    f"mas nao e a ultima")
                assert anterior["ate"] < atual["de"], (
                    f"{produto}: faixa de {anterior['de']} vai ate {anterior['ate']} "
                    f"e a seguinte comeca em {atual['de']}")

    def test_so_a_ultima_faixa_pode_ser_aberta(self, por_produto):
        for produto, faixas in por_produto.items():
            for f in faixas[:-1]:
                assert f.get("ate") is not None, f"{produto}: {f['de']}"

    def test_cobertura_comeca_no_inicio_da_serie(self, cfg, por_produto):
        inicio = cfg["inicio_serie"]
        for produto, faixas in por_produto.items():
            # O QAV so passa a ser calculado depois; os demais tem de cobrir
            # a serie inteira desde o inicio declarado.
            assert faixas[0]["de"] <= inicio or produto == "qav", (
                f"{produto}: primeira faixa comeca em {faixas[0]['de']}, "
                f"depois do inicio da serie {inicio}")


class TestPresuncao:
    def test_faixa_presumida_explica_a_presuncao(self, cfg):
        # Um numero presumido sem justificativa escrita e indistinguivel de um
        # numero conferido depois de alguns meses.
        for f in cfg["faixas"]:
            if f.get("presumido"):
                texto = " ".join(str(f.get(k, "")) for k in ("_obs", "_atencao", "fonte"))
                assert len(texto.strip()) > 60, f

    def test_carregar_tributos_preserva_a_marca(self):
        trib = d.carregar_tributos(CAMINHO)
        for faixas in trib["faixas"].values():
            for f in faixas:
                assert isinstance(f["presumido"], bool)


class TestConsistenciaComOCodigo:
    def test_deducao_cobre_toda_semana_da_serie_ate_hoje(self, cfg):
        """Nao pode haver semana sem aliquota entre o inicio e hoje.

        Excecao conhecida e deliberada: a semana mista do QAV em abril/2026,
        quando a desoneracao entrou numa quarta-feira. Qualquer outro buraco
        significa que uma vigencia venceu sem prorrogacao -- o produto some do
        grafico e este teste e o que avisa antes de chegar ao painel.
        """
        trib = d.carregar_tributos(CAMINHO)
        buracos = {}
        for produto in trib["faixas"]:
            inicio = date.fromisoformat(trib["faixas"][produto][0]["de"])
            semana = inicio
            hoje = date.today()
            faltando = []
            while semana <= hoje:
                if d.deducao(trib, produto, semana.isoformat()) is None:
                    faltando.append(semana.isoformat())
                semana = date.fromordinal(semana.toordinal() + 7)
            if faltando:
                buracos[produto] = faltando
        assert buracos == {} or list(buracos) == ["qav"], buracos
        if "qav" in buracos:
            assert len(buracos["qav"]) <= 1, buracos["qav"]
