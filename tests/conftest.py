"""Deixa os modulos de scripts/ importaveis pelos testes.

Nao ha pacote instalavel: o pipeline roda como scripts soltos e o import
entre eles funciona porque o Python poe o diretorio do script no sys.path.
Os testes precisam reproduzir isso.
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))
