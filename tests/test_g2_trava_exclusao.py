"""PD-18 (04/11/2026): 380, 381 e 382 so apagam com fase 'execucao'."""
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.trava import FaseNaoPermite, exigir_fase_execucao  # noqa: E402


@pytest.mark.parametrize("fase", ["diagnostico", "teste", "piloto", None])
def test_fase_diferente_de_execucao_aborta(fase):
    with pytest.raises(FaseNaoPermite):
        exigir_fase_execucao({"fase": fase}, "x")


def test_execucao_libera():
    exigir_fase_execucao({"fase": "execucao"}, "x")


@pytest.mark.parametrize("script", ["380_g2_word_com_pdf.py", "381_g2_pastas_vazias.py", "382_g2_excluir_copias.py"])
def test_scripts_chamam_a_trava_antes_de_conectar(script):
    s = (RAIZ / "scripts" / script).read_text(encoding="utf-8")
    i_trava = s.index("exigir_fase_execucao(c,")
    assert s.index("if a.executar:", s.index("c = cfg()")) < i_trava < s.index("con = conectar()")
