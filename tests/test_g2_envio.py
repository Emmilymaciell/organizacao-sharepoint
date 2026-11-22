"""Caminho de escrita da G2 (envio.enviar, envio.conferir, retomada) com um Nextcloud FALSO em memoria.
Sem rede. O comportamento do servidor real (412 no PUT/MOVE, 423 no MKCOL) esta provado no 365."""

import copy
import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import cfg  # noqa: E402
from migra_g2.dav import EscritaProibida, Ocupado  # noqa: E402
from migra_g2.envio import conferir, enviar, retomada_e_nossa  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402

R103 = "PARCEIRO-INFRA/05. RD-103/04. Engenharia"


class NCFalso:
    """Imita so o que o envio usa: PUT condicional, chunking v2 e MOVE com Overwrite: F, leitura."""

    def __init__(self):
        self.arquivos: dict[str, bytes] = {}
        self.envios: dict[str, dict[int, bytes]] = {}
        self.chamadas: list[str] = []

    def url_arquivo(self, rel):
        return "dav:" + rel

    def put_se_ausente(self, caminho, dados, c, mtime=None):
        from migra_g2.base import escrita_permitida
        ok, motivo = escrita_permitida(caminho, False, c)
        if not ok:
            raise EscritaProibida(motivo)
        self.chamadas.append("PUT_INM")
        if caminho in self.arquivos:
            raise Ocupado(caminho)
        self.arquivos[caminho] = dados
        return 201

    def criar_envio(self, envio, destino):
        self.envios[envio] = {}

    def enviar_bloco(self, envio, n, dados, destino, total):
        self.envios[envio][n] = dados

    def montar_se_ausente(self, envio, caminho, total, c, timeout):
        self.chamadas.append("MOVE_OVERWRITE_F")
        if caminho in self.arquivos:
            raise Ocupado(caminho)
        dados = b"".join(self.envios[envio][k] for k in sorted(self.envios[envio]))
        assert len(dados) == total
        self.arquivos[caminho] = dados
        return 201

    def ler_stream(self, caminho, bloco=1 << 20):
        d = self.arquivos[caminho]
        for i in range(0, len(d), bloco):
            yield d[i:i + bloco]

    def estado(self, caminho):
        class E:
            fileid, etag = "fid-" + caminho[-6:], "etag"
        return E() if caminho in self.arquivos else None


class Fonte:
    def __init__(self, dados):
        self.dados, self.total = dados, len(dados)

    def ler(self, ini, n):
        return self.dados[ini:ini + n]


def c_exec(limite_mib=1, bloco_mib=5):
    c = copy.deepcopy(cfg())
    c["fase"] = "execucao"
    c["destino"]["rodovias_liberadas"] = ["RD-103"]
    c["transferencia"]["limite_put_simples_mib"] = limite_mib
    c["transferencia"]["bloco_mib"] = bloco_mib
    return c


def qx(d):
    return QuickXorHash().update(d).base64()


def test_put_simples_e_conferencia():
    nc, d = NCFalso(), os.urandom(300_000)
    p = f"{R103}/05. Diário de Obras/2026-01 - Janeiro/a.pdf"
    r = enviar(nc, Fonte(d), p, c_exec())
    assert r["metodo"] == "PUT_INM" and r["sha256_origem"] == hashlib.sha256(d).hexdigest()
    v = conferir(nc, p, len(d), r["sha256_origem"], qx(d))
    assert v["validado"] and v["qx_igual_microsoft"]


def test_chunking_usa_move_com_overwrite_f():
    nc, d = NCFalso(), os.urandom(12 * 2**20 + 7)
    p = f"{R103}/01. Projetos/02. Executivo/04. OAE/x.zip"
    r = enviar(nc, Fonte(d), p, c_exec())
    assert r["metodo"] == "CHUNK_V2" and nc.chamadas == ["MOVE_OVERWRITE_F"]
    assert nc.arquivos[p] == d


def test_destino_ocupado_nao_sobrescreve():
    nc = NCFalso()
    p = f"{R103}/05. Diário de Obras/2026-01 - Janeiro/a.pdf"
    nc.arquivos[p] = b"do estagiario"
    with pytest.raises(Ocupado):
        enviar(nc, Fonte(os.urandom(1000)), p, c_exec())
    with pytest.raises(Ocupado):
        enviar(nc, Fonte(os.urandom(12 * 2**20)), p, c_exec())
    assert nc.arquivos[p] == b"do estagiario"


def test_trava_de_escopo_no_envio():
    nc = NCFalso()
    with pytest.raises(EscritaProibida):
        enviar(nc, Fonte(b"x"), "PARCEIRO-INFRA/03. RD-101/04. Engenharia/05. Diário de Obras/a.pdf", c_exec())
    assert nc.arquivos == {}


def test_conferencia_detecta_divergencia():
    nc, d = NCFalso(), os.urandom(5000)
    p = f"{R103}/05. Diário de Obras/2026-01 - Janeiro/b.pdf"
    r = enviar(nc, Fonte(d), p, c_exec())
    nc.arquivos[p] = b"X" + d[1:]                      # conteudo alterado depois do envio
    assert not conferir(nc, p, len(d), r["sha256_origem"], qx(d))["validado"]


def test_retomada_so_atribui_com_prova():
    brt = timezone(timedelta(hours=-3))
    marcado = datetime(2026, 10, 28, 11, 33, 50, tzinfo=brt)
    iso = marcado.isoformat(timespec="seconds")
    depois = str(int(marcado.timestamp()) + 7)        # upload registrado apos marcar o envio
    antes = str(int(marcado.timestamp()) - 3600)      # arquivo que ja estava la uma hora antes
    assert retomada_e_nossa("COPIADO", "abc", None, iso)
    assert retomada_e_nossa("COPIANDO", None, depois, iso)
    assert not retomada_e_nossa("COPIANDO", None, antes, iso)
    assert not retomada_e_nossa("COPIANDO", None, "0", iso)
    assert not retomada_e_nossa("COPIANDO", None, None, iso)
    assert not retomada_e_nossa("PLANEJADO", None, depois, iso)
    assert not retomada_e_nossa("NA_FILA", None, depois, iso)     # atualizado_em de NA_FILA marca a falha
    assert not retomada_e_nossa("ERRO", None, depois, iso)
    assert not retomada_e_nossa("COPIADO", None, depois, iso)     # COPIADO sem SHA-256 gravado nao prova



def test_retomada_com_limite_superior():
    brt = timezone(timedelta(hours=-3))
    marcado = datetime(2026, 10, 28, 11, 33, 50, tzinfo=brt)
    iso, t = marcado.isoformat(timespec="seconds"), int(marcado.timestamp())
    assert retomada_e_nossa("COPIANDO", None, str(t + 7), iso, limite_epoch=t + 60)
    assert not retomada_e_nossa("COPIANDO", None, str(t + 120), iso, limite_epoch=t + 60)   # subiu apos a retomada


def test_digital_do_codigo_de_escrita():
    from migra_g2.base import digital_codigo_escrita
    d = digital_codigo_escrita()
    assert {"scripts/migra_g2/base.py", "scripts/migra_g2/dav.py", "scripts/migra_g2/envio.py",
            "scripts/migra_g2/governador.py",       # 29/10/2026: decide se um envio comeca
            "scripts/367_g2_executar.py", "scripts/migra_parceiro/retomavel.py", "scripts/migra_parceiro/motor.py",
            "scripts/migra_parceiro/quickxor.py"} == set(d)
    assert all(len(v) == 64 for v in d.values())
    assert d == digital_codigo_escrita()                 # deterministica
