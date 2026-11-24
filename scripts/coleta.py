#!/usr/bin/env python3
"""Estado da coleta: o que acontece quando nao da para falar com a ANP.

O run #155 (21/11/2026) foi o primeiro de tres falhas seguidas, todas iguais:
`ConnectionResetError(104)` nas quatro tentativas, 42 segundos, e o job
inteiro vermelho. O arquivo da ANP estava no ar -- conferido na mao, mesma URL,
mesmo conteudo -- e o codigo nao havia mudado desde a ultima execucao bem
sucedida. Quem recusou foi a origem, e so para o runner.

Dai a separacao que este modulo existe para sustentar:

- **nao consegui coletar** nao e **coletei e o dado esta errado**. A segunda
  tem de parar tudo; a primeira, na maioria dos dias, so precisa ser dita. O
  pipeline roda TODO DIA para pegar a semana nova assim que ela sai; falhar um
  dia quando a origem esta fora nao e evento, e o custo normal de tentar todo
  dia. Falhar tres dias seguidos e;
- alarme que toca todo dia sem exigir acao ensina a ignorar o alarme. Esse
  e o estrago real de deixar o job vermelho por indisponibilidade de origem:
  quando quebrar o parser -- defeito nosso, que exige gente -- o e-mail vai
  parecer igual aos tres anteriores.

O que NAO muda: o painel nunca mostra numero velho como se fosse novo. Cada
numero ja carrega a semana a que pertence, e agora carrega tambem o aviso de
que a coleta parou de rodar.
"""
from __future__ import annotations

import json
from pathlib import Path

from comum import agora_br, escrever_json, fmt_data, hoje_br

NOME = "coleta.json"

# Abaixo disto o alerta e `aviso` e a execucao termina verde; daqui para cima
# vira `erro` e o job fica vermelho. Tres dias e o ponto em que "a origem
# oscilou" deixa de explicar e alguem precisa olhar.
FALHAS_PARA_ERRO = 3


def _ler(out_dir: Path) -> dict | None:
    caminho = out_dir / NOME
    if not caminho.exists():
        return None
    try:
        with open(caminho, encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None


def registrar_falha(out_dir: Path, fonte: str, erro: str) -> dict:
    """Soma mais uma falha consecutiva e grava o estado. Devolve o estado.

    `desde` e a data da PRIMEIRA falha da sequencia: e o que responde "ha
    quanto tempo o painel esta sem coleta", que e a pergunta do leitor. Nao
    guardamos "ultimo sucesso" porque o pipeline nao tem esse carimbo -- as
    escritas sao idempotentes de proposito (invariante 5) e `generated_at` so
    anda quando o CONTEUDO muda, nao quando a coleta roda.
    """
    antes = _ler(out_dir) or {}
    estado = {
        "ultima_tentativa": agora_br().isoformat(timespec="seconds"),
        "desde": antes.get("desde") or hoje_br().isoformat(),
        "falhas_consecutivas": int(antes.get("falhas_consecutivas", 0)) + 1,
        "fonte": fonte,
        "erro": erro[:300],
    }
    escrever_json(out_dir / NOME, estado, ignorar=("ultima_tentativa",))
    return estado


def registrar_sucesso(out_dir: Path) -> bool:
    """Apaga o estado de falha. Devolve True se havia algo para apagar.

    Apagar, e nao gravar "ok": em regime normal o arquivo NAO EXISTE, e por
    isso a coleta diaria nao produz um commit por dia. Ausencia e a saude.
    """
    caminho = out_dir / NOME
    if not caminho.exists():
        return False
    caminho.unlink()
    print(f"[coleta] origem respondeu de novo; {NOME} removido")
    return True


def nivel(estado: dict) -> str:
    return "erro" if estado["falhas_consecutivas"] >= FALHAS_PARA_ERRO else "aviso"


def alerta(estado: dict, ultima_semana: str | None = None) -> dict:
    """Alerta no schema unico do projeto (invariante 4)."""
    n = estado["falhas_consecutivas"]
    desde = fmt_data(estado["desde"])
    dias = "1 dia" if n == 1 else f"{n} dias"
    texto = (
        f"Nao foi possivel baixar o arquivo da ANP em {dias} seguido(s), "
        f"desde {desde}. O painel segue mostrando a ultima coleta bem sucedida"
    )
    if ultima_semana:
        texto += f" (semana ate {fmt_data(ultima_semana)})"
    texto += (
        f". Ultimo erro: {estado['erro']}. "
        "Enquanto isto durar, nenhum numero desta pagina e novo."
    )
    if nivel(estado) == "erro":
        texto += (
            " Tres dias ou mais deixa de ser oscilacao da origem: confira se a "
            "URL mudou de lugar e se a ANP nao passou a recusar o runner."
        )
    return {
        "nivel": nivel(estado), "codigo": "fonte_indisponivel",
        "produto": None, "label": None, "texto": texto,
    }


def injetar_em_qualidade(out_dir: Path, novo: dict) -> bool:
    """Põe o alerta no qualidade.json que já está publicado, sem recalcular.

    Quando a coleta falha não há `products`, e portanto não há como rodar
    `qualidade.run`. Em vez de publicar uma lista vazia -- que apagaria os
    alertas verdadeiros da última coleta boa e faria o painel parecer saudável
    justamente no dia em que não é --, o arquivo existente é preservado e só
    este alerta entra, substituindo a versão anterior dele se houver.
    """
    caminho = out_dir / "qualidade.json"
    if not caminho.exists():
        payload = {"resumo": {}, "alertas": []}
    else:
        try:
            with open(caminho, encoding="utf-8") as fh:
                payload = json.load(fh)
        except (json.JSONDecodeError, OSError):
            payload = {"resumo": {}, "alertas": []}

    itens = [a for a in payload.get("alertas", [])
             if a.get("codigo") != novo["codigo"]]
    itens.append(novo)
    ordem = {"erro": 0, "aviso": 1, "info": 2}
    itens.sort(key=lambda x: (ordem.get(x["nivel"], 9), x.get("produto") or ""))

    payload["alertas"] = itens
    payload["resumo"] = {n: sum(1 for i in itens if i["nivel"] == n)
                         for n in ("erro", "aviso", "info")}
    payload["coleta_falhou_em"] = agora_br().isoformat(timespec="seconds")
    return escrever_json(caminho, payload, ignorar=("generated_at", "coleta_falhou_em"))
