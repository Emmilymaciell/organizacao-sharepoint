import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro import espaco  # noqa: E402
from migra_parceiro.espaco import GIB, Guarda  # noqa: E402

Uso = namedtuple("Uso", "total used free")


def guarda(tmp_path, monkeypatch, livre_gib):
    monkeypatch.setattr(espaco.shutil, "disk_usage", lambda _: Uso(500 * GIB, 0, livre_gib * GIB))
    return Guarda(tmp_path, tmp_path / "tmp", reserva_minima=40 * GIB, teto_temporario=8 * GIB)


def test_streaming_puro_passa_com_folga(tmp_path, monkeypatch):
    assert guarda(tmp_path, monkeypatch, 78).pode_iniciar(0)[0]


def test_bloqueia_quando_invade_a_reserva(tmp_path, monkeypatch):
    ok, motivo = guarda(tmp_path, monkeypatch, 45).pode_iniciar(6 * GIB)
    assert not ok and motivo.startswith("reserva")


def test_bloqueia_pelo_teto_de_temporarios(tmp_path, monkeypatch):
    g = guarda(tmp_path, monkeypatch, 300)
    g.tmp.mkdir()
    (g.tmp / "parcial.bin").write_bytes(b"x" * 1024)
    ok, motivo = g.pode_iniciar(8 * GIB)
    assert not ok and "teto" in motivo


def test_conta_bytes_em_voo(tmp_path, monkeypatch):
    g = guarda(tmp_path, monkeypatch, 50)
    assert g.pode_iniciar(5 * GIB)[0]
    assert not g.pode_iniciar(5 * GIB, em_voo=6 * GIB)[0]
