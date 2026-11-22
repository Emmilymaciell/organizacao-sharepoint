"""SO WINDOWS (o 367 importa msvcrt e usa ctypes.windll; o projeto roda em Windows, D-11).

Executor G2 (367: Executor.item e garantir_pasta) e cliente WebDAV REAL (migra_g2/dav.PARCEIRO) contra um
servidor WebDAV FALSO em memoria que imita o Nextcloud nos pontos que importam:
  PUT com If-None-Match: * -> 412 se existir; MKCOL -> 405 se existir; MOVE da montagem com Overwrite: F -> 412
  se o destino existir e 412 SEMPRE que vier If-None-Match (comportamento real comprovado no 365);
  PROPFIND Depth 0/1 com oc:fileid, getetag e nc:upload_time. Sem rede, sem Graph real."""

import copy
import importlib.util
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2 import base  # noqa: E402
from migra_g2.dav import PARCEIRO, EscritaProibida, Ocupado  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402

_spec = importlib.util.spec_from_file_location("executor367", RAIZ / "scripts" / "367_g2_executar.py")
EX = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(EX)

BASE = "https://fake/remote.php/dav"
R180 = "PARCEIRO-INFRA/05. RD-103/04. Engenharia"


class Servidor:
    def __init__(self):
        self.corrida = set()    # caminhos em que um "terceiro" cria o arquivo no instante do nosso PUT
        self.ct = 0             # nc:creation_time devolvido (vem do cliente no Nextcloud real)
        self.no = {}            # caminho -> {"dados": bytes|None (pasta), "fileid": str, "etag": str, "up": int}
        self.envios = {}
        self.seq = 1000
        self.log = []
        for p in ("PARCEIRO-INFRA", "PARCEIRO-INFRA/05. RD-103", R180, "PARCEIRO-INFRA/03. RD-101",
                  "PARCEIRO-INFRA/03. RD-101/04. Engenharia"):
            self.criar(p, None)

    def criar(self, p, dados, up=None):
        self.seq += 1
        self.no[p] = {"dados": dados, "fileid": str(self.seq), "etag": f"e{self.seq}", "up": up or int(time.time())}
        return self.no[p]

    def pai_existe(self, p):
        return p.rsplit("/", 1)[0] in self.no

    def req(self, metodo, url, headers=None, dados=None):
        h = headers or {}
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        self.log.append((metodo, path, dict(h)))
        if "/uploads/u/" in path:
            env, _, parte = path.split("/uploads/u/", 1)[1].partition("/")
            if metodo == "MKCOL":
                self.envios.setdefault(env, {})
                return 201, b""
            if metodo == "PUT":
                self.envios[env][int(parte)] = dados
                return 201, b""
            if metodo == "MOVE":
                if "If-None-Match" in h:                       # Nextcloud avalia sobre a origem .file
                    return 412, b"precondition"
                dest = urllib.parse.unquote(urllib.parse.urlparse(h["Destination"]).path).split("/files/u/", 1)[1]
                if dest in self.no and h.get("Overwrite") == "F":
                    return 412, b"exists"
                self.criar(dest, b"".join(self.envios[env][k] for k in sorted(self.envios[env])))
                return 201, b""
        rel = path.split("/files/u/", 1)[1].strip("/")
        if metodo == "PUT":
            if rel in self.corrida:
                self.corrida.discard(rel)
                self.criar(rel, b"do terceiro, no mesmo instante")
            if rel in self.no:
                if h.get("If-None-Match") == "*":
                    return 412, b"exists"
                self.no[rel]["dados"] = dados                   # sobrescrita (o cliente nunca pede isso)
                return 204, b""
            if not self.pai_existe(rel):
                return 409, b"no parent"
            self.criar(rel, dados)
            return 201, b""
        if metodo == "MKCOL":
            if rel in self.no:
                return 405, b"exists"
            if not self.pai_existe(rel):
                return 409, b"no parent"
            self.criar(rel, None)
            return 201, b""
        if metodo == "PROPFIND":
            if rel not in self.no:
                return 404, b""
            alvos = [rel] + ([k for k in self.no if k.rsplit("/", 1)[0] == rel] if h.get("Depth") == "1" else [])
            return 207, self.xml(alvos)
        raise AssertionError(f"metodo nao esperado: {metodo}")

    def xml(self, alvos):
        partes = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" '
                  'xmlns:nc="http://nextcloud.org/ns">']
        for a in alvos:
            n = self.no[a]
            href = "/remote.php/dav/files/u/" + "/".join(urllib.parse.quote(s) for s in a.split("/"))
            tipo = "<d:collection/>" if n["dados"] is None else ""
            tam = "" if n["dados"] is None else f"<d:getcontentlength>{len(n['dados'])}</d:getcontentlength>"
            partes.append(f"<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype>{tipo}"
                          f"</d:resourcetype>{tam}<d:getetag>\"{n['etag']}\"</d:getetag><oc:fileid>{n['fileid']}"
                          f"</oc:fileid><nc:creation_time>{self.ct}</nc:creation_time><nc:upload_time>{n['up']}"
                          "</nc:upload_time></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
        partes.append("</d:multistatus>")
        return "".join(partes).encode()


class PARCEIROFalso(PARCEIRO):
    """O cliente REAL (dav.PARCEIRO); so o transporte HTTP vai para o servidor em memoria."""

    def __init__(self, srv):
        super().__init__(BASE, "u", "u", "x")
        self.srv = srv

    def _req(self, metodo, url, headers=None, dados=None, timeout=None):
        return self.srv.req(metodo, url, headers, dados)

    def _req_ret(self, metodo, url, headers=None, dados=None, timeout=None, tentativas=5):
        return self.srv.req(metodo, url, headers, dados)

    def ler_stream(self, caminho, bloco=1 << 20):
        d = self.srv.no[caminho]["dados"]
        for i in range(0, len(d), bloco):
            yield d[i:i + bloco]


class GraphFalso:
    def __init__(self):
        self.itens = {}

    def get(self, caminho):
        item_id = caminho.split("/items/")[1].split("?")[0]
        return self.itens[item_id]


class FonteFalsa:
    dados = {}

    def __init__(self, g, d, i, total):
        self.total, self.b = total, FonteFalsa.dados[i]

    def ler(self, ini, n):
        return self.b[ini:ini + n]


def qx(b):
    return QuickXorHash().update(b).base64()


@pytest.fixture
def amb(monkeypatch, tmp_path):
    c = copy.deepcopy(base.cfg())
    c["fase"] = "execucao"
    c["destino"]["rodovias_liberadas"] = ["RD-103"]
    c["transferencia"]["limite_put_simples_mib"] = 1
    c["transferencia"]["bloco_mib"] = 5
    con = base.conectar(tmp_path / "t.db")
    srv = Servidor()
    ex = EX.Executor.__new__(EX.Executor)
    ex.a, ex.c, ex.log = None, c, lambda m: None
    ex.con, ex.dbt, ex.nc, ex.g = con, threading.Lock(), PARCEIROFalso(srv), GraphFalso()
    ex.tmp, ex.pastas, ex.trava_pastas = tmp_path, {R180: srv.no[R180]["fileid"]}, threading.RLock()
    ex.parar, ex.cont = threading.Event(), {"VALIDADO": 0, "bytes": 0}
    ex.inicio_epoch = time.time()
    monkeypatch.setattr(EX, "FonteGraph", FonteFalsa)
    return ex, srv, con


def item(con, ex, nome, dados, destino, estado="PLANEJADO", atualizado="2026-10-28T10:00:00-03:00"):
    iid = f"I-{nome}"
    con.execute("INSERT INTO origem_item (drive_id, item_id, fonte, caminho, nome, eh_pasta, tamanho, etag,"
                " quick_xor_hash, inventariado_em) VALUES ('D', ?, 'Programa', ?, ?, 0, ?, 'et1', ?, 'x')",
                (iid, nome, nome, len(dados), qx(dados)))
    con.execute("INSERT INTO plano (drive_id, item_id, rodovia, categoria, classificacao, destino_rel, estado,"
                " etag_origem, atualizado_em) VALUES ('D', ?, 'RD-103', 'X', 'AUSENTE_E_ELEGIVEL', ?, ?, 'et1', ?)",
                (iid, destino, estado, atualizado))
    con.commit()
    ex.g.itens[iid] = {"size": len(dados), "eTag": "et1", "file": {"hashes": {"quickXorHash": qx(dados)}}}
    FonteFalsa.dados[iid] = dados
    return con.execute("SELECT * FROM plano WHERE item_id=?", (iid,)).fetchone()


def estado(con, nome):
    return con.execute("SELECT estado, classificacao, fileid_destino FROM plano WHERE item_id=?", (f"I-{nome}",)).fetchone()


D = f"{R180}/05. Diário de Obras/2026-01 - Janeiro"


def test_envio_normal_cria_pastas_e_valida(amb):
    ex, srv, con = amb
    r = item(con, ex, "a.pdf", b"conteudo-a" * 1000, f"{D}/a.pdf")
    assert ex.item(r) == "CRIADO"
    st = estado(con, "a.pdf")
    assert st[0] == "VALIDADO" and st[2] == srv.no[f"{D}/a.pdf"]["fileid"]
    puts = [h for m, p, h in srv.log if m == "PUT" and "/files/" in p]
    assert puts and all(h.get("If-None-Match") == "*" for h in puts)


def test_chunking_real_sem_if_none_match_no_move(amb):
    ex, srv, con = amb
    r = item(con, ex, "g.zip", b"z" * (6 * 2**20 + 3), f"{D}/g.zip")
    assert ex.item(r) == "CRIADO" and estado(con, "g.zip")[0] == "VALIDADO"
    mv = [h for m, p, h in srv.log if m == "MOVE"]
    assert mv and mv[0]["Overwrite"] == "F" and "If-None-Match" not in mv[0]


def test_terceiro_com_conteudo_diferente_nao_e_tocado(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    srv.criar(f"{D}/b.pdf", b"do estagiario")
    r = item(con, ex, "b.pdf", b"nosso", f"{D}/b.pdf")
    assert ex.item(r) == "JA_OCUPADO"
    assert srv.no[f"{D}/b.pdf"]["dados"] == b"do estagiario"
    assert estado(con, "b.pdf")[:2] == ("PULADO", "CONFLITO_CONCORRENTE")


def test_terceiro_identico_nao_vira_nosso(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    srv.criar(f"{D}/c.pdf", b"igual")
    r = item(con, ex, "c.pdf", b"igual", f"{D}/c.pdf")
    assert ex.item(r) == "JA_OCUPADO"
    st = estado(con, "c.pdf")
    assert st[:2] == ("PULADO", "JA_EXISTIA_CORRETO") and st[2] is None


def test_retomada_com_prova_de_upload_e_nossa(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    marcado = "2026-10-28T11:33:50-03:00"
    srv.criar(f"{D}/d.pdf", b"meu", up=int(datetime.fromisoformat(marcado).timestamp()) + 7)
    r = item(con, ex, "d.pdf", b"meu", f"{D}/d.pdf", estado="COPIANDO", atualizado=marcado)
    assert ex.item(r) == "CONFERIDO" and estado(con, "d.pdf")[0] == "VALIDADO"


def test_retomada_sem_prova_nao_atribui(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    marcado = "2026-10-28T11:33:50-03:00"
    srv.criar(f"{D}/e.pdf", b"meu", up=int(datetime.fromisoformat(marcado).timestamp()) - 3600)
    r = item(con, ex, "e.pdf", b"meu", f"{D}/e.pdf", estado="COPIANDO", atualizado=marcado)
    assert ex.item(r) == "JA_OCUPADO"
    assert estado(con, "e.pdf")[:2] == ("PULADO", "JA_EXISTIA_CORRETO")


def test_origem_alterada_nao_copia(amb):
    ex, srv, con = amb
    r = item(con, ex, "f.pdf", b"v1", f"{D}/f.pdf")
    ex.g.itens["I-f.pdf"]["eTag"] = "et2"
    assert ex.item(r) == "ORIGEM_ALTERADA"
    assert f"{D}/f.pdf" not in srv.no and estado(con, "f.pdf")[1] == "ALTERADO_DURANTE_EXECUCAO"


def test_pasta_movida_por_terceiro_nao_e_recriada(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    no = srv.no.pop(D)                                   # terceiro moveu/renomeou a pasta
    srv.no[f"{R180}/05. Diário de Obras/OUTRO NOME"] = no
    r = item(con, ex, "h.pdf", b"x", f"{D}/h.pdf")
    assert ex.item(r) == "PASTA_INVALIDA"
    assert D not in srv.no and f"{D}/h.pdf" not in srv.no


def test_escopo_bloqueado_no_cliente_real(amb):
    ex, srv, con = amb
    with pytest.raises(EscritaProibida):
        ex.nc.put_se_ausente("PARCEIRO-INFRA/03. RD-101/04. Engenharia/x.pdf", b"x", ex.c)
    with pytest.raises(EscritaProibida):
        ex.nc.put_se_ausente(f"{R180}/05. Diário de Obras/../../../03. RD-101/x.pdf", b"x", ex.c)
    with pytest.raises(EscritaProibida):
        ex.nc.criar_pasta(f"{R180}/09. Eventograma", ex.c)
    assert not any(m in ("PUT", "MKCOL") for m, p, h in srv.log)


def test_put_real_recusa_ocupado(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    srv.criar(f"{D}/k.pdf", b"antigo")
    with pytest.raises(Ocupado):
        ex.nc.put_se_ausente(f"{D}/k.pdf", b"novo", ex.c)
    assert srv.no[f"{D}/k.pdf"]["dados"] == b"antigo"


def test_na_fila_nunca_e_atribuido(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    falha = "2026-10-28T11:40:00-03:00"
    srv.criar(f"{D}/m.pdf", b"meu?", up=int(datetime.fromisoformat(falha).timestamp()) + 60)
    r = item(con, ex, "m.pdf", b"meu?", f"{D}/m.pdf", estado="NA_FILA", atualizado=falha)
    assert ex.item(r) == "JA_OCUPADO"
    st = estado(con, "m.pdf")
    assert st[:2] == ("PULADO", "JA_EXISTIA_CORRETO") and st[2] is None


def test_corrida_no_put_vira_conflito_sem_sobrescrever(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    srv.corrida.add(f"{D}/n.pdf")                      # ausente na checagem, ocupado no PUT
    r = item(con, ex, "n.pdf", b"nosso", f"{D}/n.pdf")
    assert ex.item(r) == "JA_OCUPADO"
    assert srv.no[f"{D}/n.pdf"]["dados"] == b"do terceiro, no mesmo instante"
    assert estado(con, "n.pdf")[:2] == ("PULADO", "CONFLITO_CONCORRENTE")


def test_prova_usa_upload_time_do_servidor(amb):
    ex, srv, con = amb
    ex.garantir_pasta(D)
    marcado = "2026-10-28T11:33:50-03:00"
    srv.ct = int(datetime.fromisoformat(marcado).timestamp()) + 30   # creation_time do cliente "recente"
    srv.criar(f"{D}/p.pdf", b"meu", up=int(datetime.fromisoformat(marcado).timestamp()) - 3600)  # servidor: antigo
    r = item(con, ex, "p.pdf", b"meu", f"{D}/p.pdf", estado="COPIANDO", atualizado=marcado)
    assert ex.item(r) == "JA_OCUPADO"                  # vale o upload_time do servidor, nao o creation_time



def test_selecionar_piloto_e_por_rodovia(amb):
    ex, srv, con = amb
    for k in range(3):
        item(con, ex, f"rd103_{k}.pdf", b"x" * (k + 1), f"{D}/rd103_{k}.pdf")
    con.execute("UPDATE plano SET lote='PILOTO', estado='VALIDADO' WHERE item_id='I-rd103_0.pdf'")   # piloto antigo
    for k in range(2):
        item(con, ex, f"rd101_{k}.pdf", b"y" * (k + 1), f"PARCEIRO-INFRA/03. RD-101/04. Engenharia/x/rd101_{k}.pdf")
    con.execute("UPDATE plano SET rodovia='RD-101' WHERE item_id LIKE 'I-rd101_%'")
    con.commit()
    assert EX.selecionar_piloto(con, "RD-103") == 0          # RD-103 ja tem piloto
    assert EX.selecionar_piloto(con, "RD-101") == 2          # a outra rodovia nao e afetada pelo piloto da RD-103
    assert EX.selecionar_piloto(con, "RD-101") == 0          # idempotente
    assert con.execute("SELECT COUNT(*) FROM plano WHERE lote='PILOTO' AND rodovia='RD-101'").fetchone()[0] == 2


def test_herdados_sem_trava_estao_bloqueados(amb):
    ex, srv, con = amb
    with pytest.raises(EscritaProibida):
        ex.nc.garantir_pastas(f"{R180}/qualquer")
    with pytest.raises(EscritaProibida):
        ex.nc.montar("env", "dest", 1, 1)
    assert not any(m in ("PUT", "MKCOL", "MOVE") for m, p, h in srv.log)


def test_monitor_pausa_se_arquivo_nosso_some(amb, monkeypatch):
    ex, srv, con = amb
    r = item(con, ex, "q.pdf", b"conteudo-q", f"{D}/q.pdf")
    assert ex.item(r) == "CRIADO"
    fid = estado(con, "q.pdf")[2]
    con.execute("INSERT INTO parceiro_foto (id, tipo, inicio, fim) VALUES (50, 'monitor', '2999-01-01T00:00:00-03:00', 'x')")
    con.commit()                                       # foto posterior ao envio, SEM o arquivo nosso

    class R:
        stdout = "mudancas: REMOVIDO"
    monkeypatch.setattr(EX.subprocess, "run", lambda *a, **k: R())
    assert ex.monitorar() is False
    assert (ex.tmp / "PAUSAR").exists()
    con.execute("INSERT INTO parceiro_item (foto_id, caminho, eh_pasta, fileid) VALUES (50, ?, 0, ?)", (f"{D}/q.pdf", fid))
    con.commit()
    (ex.tmp / "PAUSAR").unlink()
    assert ex.monitorar() is True and not (ex.tmp / "PAUSAR").exists()


def test_main_recusa_fase_diagnostico():
    import subprocess
    cfgtxt = (RAIZ / "config" / "migracao_parceiro_g2.yaml").read_text(encoding="utf-8")
    if not re.search(r"(?m)^fase: diagnostico", cfgtxt):
        pytest.skip("config fora da fase diagnostico")
    r = subprocess.run([sys.executable, "-W", "ignore", str(RAIZ / "scripts" / "367_g2_executar.py"), "--rodovia",
                        "RD-103"], capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
                       timeout=60, cwd=RAIZ)
    assert r.returncode == 1 and "BLOQUEADO" in r.stdout


def test_main_exige_teste_do_motor_do_codigo_atual(monkeypatch, tmp_path):
    import json
    c = copy.deepcopy(base.cfg())
    c["fase"] = "piloto"
    c["destino"]["rodovias_liberadas"] = ["RD-103"]
    monkeypatch.setattr(EX, "cfg", lambda: c)
    monkeypatch.setattr(EX, "saida", lambda c_=None: tmp_path)
    monkeypatch.setattr(EX, "pasta", lambda k, c_=None: tmp_path)
    monkeypatch.setattr(sys, "argv", ["367", "--rodovia", "RD-103", "--lote", "PILOTO"])
    (tmp_path / "TESTE_MOTOR_1.json").write_text(json.dumps({"todos_passaram": True}), encoding="utf-8")
    assert EX.main() == 1                                # sem impressao digital: bloqueia
    (tmp_path / "TESTE_MOTOR_2.json").write_text(json.dumps(
        {"todos_passaram": True, "codigo_escrita_sha256": {"x": "y"}}), encoding="utf-8")
    assert EX.main() == 1                                # digital de outro codigo: bloqueia
    log = (tmp_path / "LOG_OPERACIONAL_G2.log").read_text(encoding="utf-8")
    assert log.count("OUTRO codigo de escrita") == 2



def _preparar_rodar(ex, srv, con, monkeypatch, n=6):
    import argparse
    ex.a = argparse.Namespace(rodovia="RD-103", lote=None, limite=None, workers=3)
    con.execute("INSERT INTO parceiro_foto (id, tipo, inicio, fim) VALUES (1, 'baseline', 'x', 'x')")
    con.execute("INSERT INTO parceiro_item (foto_id, caminho, eh_pasta, fileid) VALUES (1, ?, 1, ?)",
                (R180, srv.no[R180]["fileid"]))
    for k in range(n):
        item(con, ex, f"r{k}.pdf", bytes([k]) * 4000, f"{D}/r{k}.pdf")
    monkeypatch.setattr(EX, "MONITOR_S", 0)                  # monitor sempre vencido
    monkeypatch.setattr(EX, "TICK_S", 0.01)
    ex.gov = GovFixo(3)
    lento = FonteFalsa.ler
    monkeypatch.setattr(FonteFalsa, "ler", lambda self, i, n_: (time.sleep(0.05), lento(self, i, n_))[1])


def test_rodar_monitora_no_meio_da_fila_com_varios_workers(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch)
    vistos = []

    def monitor_falso():
        vistos.append(con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0])
        return True
    ex.monitorar = monitor_falso
    assert ex.rodar() == 0
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0] == 6
    assert any(0 < v < 6 for v in vistos), vistos          # rodou DURANTE a fila, nao so no fim


def test_rodar_para_se_o_monitor_falha(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch)
    def monitor_que_acusa():                           # a 1a foto tirada NO MEIO da fila acusa perda
        v = con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0]
        return not (0 < v < 6)
    ex.monitorar = monitor_que_acusa
    assert ex.rodar() == 3                               # nunca "concluido"
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0] < 6


def test_monitor_trata_falha_da_foto_como_alerta(amb, monkeypatch):
    ex, srv, con = amb

    class R:
        stdout, returncode = "Traceback ...", 1
    monkeypatch.setattr(EX.subprocess, "run", lambda *a, **k: R())
    assert ex.monitorar() is False and (ex.tmp / "PAUSAR").exists()


# ---------------------------------------------------------------- governador de recursos (29/10/2026)
class GovFixo:
    """Governador falso: devolve a sequencia de (estado, workers) dada; repete o ultimo."""

    def __init__(self, *seq):
        self.seq = [s if isinstance(s, tuple) else ("EXECUTANDO", s) for s in seq]
        self.p = {"workers_max": 3}
        self.chamadas = 0

    def avaliar(self, manual=False):
        from migra_g2.governador import Decisao
        e, w = self.seq[min(self.chamadas, len(self.seq) - 1)]
        self.chamadas += 1
        return Decisao(e, w, "teste", {"ram_livre_mb": 1})


def test_rodar_em_pausa_por_recursos_nao_abre_envio_e_sai_retomavel(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch)
    ex.gov = GovFixo(("PAUSADO_POR_PRIORIDADE_EXTERNA", 0))
    ex.c = {**ex.c, "recursos": {"sair_apos_pausa_s": 0.05}}
    monkeypatch.setattr(EX.time, "sleep", lambda s: None)
    assert ex.rodar() == 3                                    # retomavel, nunca "concluido"
    assert not any(m == "PUT" for m, p, h in srv.log)         # nenhum envio nem pasta
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado='PLANEJADO'").fetchone()[0] == 6
    assert con.execute("SELECT COUNT(*) FROM recurso WHERE estado='PAUSADO_POR_PRIORIDADE_EXTERNA'").fetchone()[0] >= 1


def test_rodar_respeita_workers_do_governador_e_pausa_no_meio(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch, n=8)
    simultaneos, ativos, t = [], [0], threading.Lock()
    original = EX.Executor.item

    def item_contado(self, row):
        with t:
            ativos[0] += 1
            simultaneos.append(ativos[0])
        try:
            return original(self, row)
        finally:
            with t:
                ativos[0] -= 1
    monkeypatch.setattr(EX.Executor, "item", item_contado)
    ex.monitorar = lambda: True
    # 1a avaliacao (aguardar_recursos) libera; depois REDUZINDO com 1 worker por um tempo; depois pausa longa
    ex.gov = GovFixo(("EXECUTANDO", 1), *[("REDUZINDO_CARGA", 1)] * 12, ("PAUSA_POR_RECURSOS", 0))
    ex.c = {**ex.c, "recursos": {"sair_apos_pausa_s": 0.2}}
    r = ex.rodar()
    assert max(simultaneos) == 1                              # nunca mais que o governador permitiu
    validados = con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0]
    assert r == 3 and 0 < validados < 8                       # parou de abrir envios ao pausar; nada perdido
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado IN ('COPIANDO','ERRO')").fetchone()[0] == 0
    # retomada: governador libera de novo e a fila termina sem reenviar o que ja estava VALIDADO
    puts_antes = sum(1 for m, p, h in srv.log if m == "PUT" and "/files/" in p)
    ex.gov = GovFixo(("EXECUTANDO", 2))
    assert ex.rodar() == 0
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0] == 8
    puts_depois = sum(1 for m, p, h in srv.log if m == "PUT" and "/files/" in p)
    assert puts_depois - puts_antes == 8 - validados          # idempotente: so os que faltavam


def test_execucao_roda_piloto_antes_e_so_segue_se_ok(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch, n=7)
    ex.monitorar = lambda: True
    ex.c = {**ex.c, "fase": "execucao"}
    ordem = []
    original = EX.Executor.item

    def item_registrado(self, row):
        ordem.append((row["lote"], row["destino_rel"]))
        return original(self, row)
    monkeypatch.setattr(EX.Executor, "item", item_registrado)
    assert ex.rodar() == 0
    pil = [i for i, (lote, _) in enumerate(ordem) if lote == "PILOTO"]
    assert pil and max(pil) < min(i for i, (lote, _) in enumerate(ordem) if lote != "PILOTO")   # piloto primeiro
    assert con.execute("SELECT COUNT(*) FROM plano WHERE estado='VALIDADO'").fetchone()[0] == 7


def test_execucao_nao_segue_se_o_piloto_falhou(amb, monkeypatch):
    ex, srv, con = amb
    _preparar_rodar(ex, srv, con, monkeypatch, n=7)
    ex.monitorar = lambda: True
    ex.c = {**ex.c, "fase": "execucao"}
    EX.selecionar_piloto(con, "RD-103")
    con.execute("UPDATE plano SET estado='DIVERGENTE' WHERE rowid=(SELECT MIN(rowid) FROM plano WHERE lote='PILOTO')")
    con.commit()
    assert ex.rodar() == 1
    assert con.execute("SELECT COUNT(*) FROM plano WHERE lote IS NULL AND estado='VALIDADO'").fetchone()[0] == 0
