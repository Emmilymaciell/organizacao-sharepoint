"""Upload retomavel contra um Nextcloud FALSO (chunking v2) em 127.0.0.1 - sem rede, sem PARCEIRO."""
import hashlib
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from migra_parceiro.retomavel import Conflito, Estrutural, FonteLocal, Nextcloud, enviar, plano_blocos  # noqa: E402

MiB = 2**20


class Servidor:
    def __init__(self):
        self.uploads, self.arquivos, self.puts, self.falhar_no_put = {}, {}, 0, None
        self.pastas, self.status_put = {"/remote.php/dav/files/usuario"}, None

    def iniciar(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _p(self):
                return urllib.parse.unquote(urllib.parse.urlparse(self.path).path)

            def _r(self, st, corpo=b""):
                self.send_response(st)
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)

            def do_MKCOL(self):
                p = self._p().rstrip("/")
                if "/files/" in p:
                    if p in srv.pastas or p.rsplit("/", 1)[0] not in srv.pastas:
                        return self._r(405 if p in srv.pastas else 409)
                    srv.pastas.add(p)
                    return self._r(201)
                if p in srv.uploads:
                    return self._r(405)
                srv.uploads[p] = {}
                self._r(201)

            def do_PUT(self):
                p = self._p()
                corpo = self.rfile.read(int(self.headers["Content-Length"]))
                srv.puts += 1
                if srv.status_put:
                    return self._r(srv.status_put)
                if srv.falhar_no_put and srv.puts == srv.falhar_no_put:
                    return self._r(503)
                pasta, n = p.rsplit("/", 1)
                srv.uploads[pasta][int(n)] = corpo
                self._r(201)

            def do_PROPFIND(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                p = self._p().rstrip("/")
                if self.headers.get("Depth") == "1" and p in srv.uploads:
                    itens = "".join(f"<d:response><d:href>{p}/{n}</d:href><d:propstat><d:prop><d:getcontentlength>"
                                    f"{len(b)}</d:getcontentlength></d:prop></d:propstat></d:response>"
                                    for n, b in srv.uploads[p].items())
                elif p in srv.pastas:
                    itens = f"<d:response><d:href>{p}/</d:href></d:response>"
                elif p in srv.arquivos:
                    itens = (f"<d:response><d:href>{p}</d:href><d:propstat><d:prop><d:getcontentlength>"
                             f"{len(srv.arquivos[p])}</d:getcontentlength></d:prop></d:propstat></d:response>")
                else:
                    return self._r(404)
                self._r(207, f'<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">{itens}</d:multistatus>'.encode())

            def do_MOVE(self):
                pasta = self._p().rsplit("/", 1)[0]
                dest = urllib.parse.unquote(urllib.parse.urlparse(self.headers["Destination"]).path)
                if dest in srv.arquivos and self.headers.get("Overwrite") == "F":
                    return self._r(412)
                if dest.rsplit("/", 1)[0] not in srv.pastas:
                    return self._r(409)
                blocos = srv.uploads.pop(pasta)
                srv.arquivos[dest] = b"".join(blocos[n] for n in sorted(blocos))
                self._r(201)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/remote.php/dav"


@pytest.fixture
def ambiente(tmp_path):
    dados = os.urandom(23 * MiB + 12345)
    arq = tmp_path / "video.mp4"
    arq.write_bytes(dados)
    s = Servidor()
    base = s.iniciar()
    yield s, Nextcloud(base, "usuario", "usuario", "x"), FonteLocal(arq), dados
    s.httpd.shutdown()


DEST = "PARCEIRO-INFRA/02. Programa/04. Atendimento aos Órgãos de Controle/01. MP-Estado/Filmagem/P01 vídeo.mp4"
CHAVE = "/remote.php/dav/files/usuario/" + DEST


def test_plano_respeita_limites():
    assert len(plano_blocos(87 * 2**30, 64 * MiB)) == 1392
    with pytest.raises(ValueError):
        plano_blocos(100 * MiB, 1 * MiB)
    with pytest.raises(ValueError):
        plano_blocos(87 * 2**30, 5 * MiB)      # passaria de 10.000 blocos


def test_envio_completo_monta_arquivo_identico(ambiente):
    s, nc, fonte, dados = ambiente
    r = enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, paralelo=3, dormir=lambda x: None)
    assert r["situacao"] == "MONTADO" and r["blocos"] == 5
    assert hashlib.sha256(s.arquivos[CHAVE]).digest() == hashlib.sha256(dados).digest()


def test_queda_no_meio_retoma_so_os_blocos_que_faltam(ambiente):
    s, nc, fonte, dados = ambiente
    s.falhar_no_put = 3
    with pytest.raises(Exception):
        enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, paralelo=1, tentativas=1, dormir=lambda x: None)
    no_servidor = len(s.uploads["/remote.php/dav/uploads/usuario/prog-teste"])
    assert 2 <= no_servidor < 5                     # parte ficou guardada no servidor; o resto nao comecou
    puts_antes = s.puts
    s.falhar_no_put = None
    r = enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, paralelo=2, dormir=lambda x: None)
    assert r["situacao"] == "MONTADO"
    assert s.puts - puts_antes == 5 - no_servidor   # so os blocos que faltavam foram reenviados
    assert s.arquivos[CHAVE] == dados


def test_falha_transitoria_de_bloco_e_retentada(ambiente):
    s, nc, fonte, dados = ambiente
    s.falhar_no_put = 2
    r = enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, paralelo=1, dormir=lambda x: None)
    assert r["situacao"] == "MONTADO" and s.arquivos[CHAVE] == dados


def test_nunca_sobrescreve_destino_existente(ambiente):
    s, nc, fonte, dados = ambiente
    s.arquivos[CHAVE] = b"arquivo de outra pessoa"
    with pytest.raises(Conflito):
        enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, dormir=lambda x: None)
    assert s.arquivos[CHAVE] == b"arquivo de outra pessoa"
    assert s.puts == 0


def test_cria_as_pastas_que_faltam_sem_mexer_nas_existentes(ambiente):
    s, nc, fonte, dados = ambiente
    s.pastas.add("/remote.php/dav/files/usuario/PARCEIRO-INFRA")
    r = enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, dormir=lambda x: None)
    assert r["situacao"] == "MONTADO"
    assert CHAVE.rsplit("/", 1)[0] in s.pastas


def test_recusa_4xx_para_sem_insistir(ambiente):
    s, nc, fonte, dados = ambiente
    s.status_put = 401
    with pytest.raises(Estrutural):
        enviar(fonte, nc, DEST, "prog-teste", 5 * MiB, paralelo=1, dormir=lambda x: None)
    assert s.puts == 1                               # nao repetiu apos 401
