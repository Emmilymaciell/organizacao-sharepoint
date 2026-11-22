import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro import quickxor  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash, de_bytes  # noqa: E402

RCLONE = Path(os.environ.get("LOCALAPPDATA", "")) / (
    "Microsoft/WinGet/Packages/Rclone.Rclone_Microsoft.Winget.Source_8wekyb3d8bbwe/"
    "rclone-v1.75.1-windows-amd64/rclone.exe")


def puro(dados: bytes) -> str:
    np_salvo = quickxor._np
    quickxor._np = None
    try:
        return de_bytes(dados)
    finally:
        quickxor._np = np_salvo


def test_vetorizado_igual_ao_puro_em_blocos_irregulares():
    rnd = random.Random(7)
    dados = bytes(rnd.getrandbits(8) for _ in range(250_003))
    h = QuickXorHash()
    pos = 0
    for tam in (1, 159, 160, 161, 7_777, 100_000, 50_000):
        h.update(dados[pos:pos + tam])
        pos += tam
    h.update(dados[pos:])
    assert h.base64() == puro(dados)


def test_vazio():
    assert de_bytes(b"") == "AAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.mark.skipif(not RCLONE.exists(), reason="rclone do projeto ausente")
def test_igual_ao_rclone(tmp_path):
    dados = os.urandom(3_000_001)
    arq = tmp_path / "a.bin"
    arq.write_bytes(dados)
    saida = subprocess.run([str(RCLONE), "hashsum", "quickxor", "--base64", str(arq), "--config", "NUL"],
                           capture_output=True, text=True, check=True).stdout
    # rclone --base64 usa o alfabeto URL-safe (-_); o Graph usa o padrao (+/). Mesmo valor.
    assert saida.split()[0].replace("-", "+").replace("_", "/") == de_bytes(dados)
