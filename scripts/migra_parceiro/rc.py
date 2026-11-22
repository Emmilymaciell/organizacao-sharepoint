"""
rc - processo `rclone rcd` e chamadas a sua API local (127.0.0.1).

Um unico rclone fica de pe durante o lote; cada arquivo e uma chamada
`operations/copyfile` (origem -> caminho FINAL no destino), o que evita abrir
um processo por arquivo e permite paralelismo controlado pelo motor.

Em producao o rcd sobe por scripts/rcparceiro.ps1: e o wrapper que monta os
remotes (credenciais do .env e senha DPAPI) e aplica as travas. Este modulo
nunca ve credencial do SharePoint nem do PARCEIRO. A API local exige usuario e
senha aleatorios gerados aqui e passados ao filho so por variavel de ambiente.

`--rc-serve` permite ler bytes de qualquer remote por HTTP local - e assim que
a conferencia le o arquivo PERSISTIDO no PARCEIRO, sem cache de disco.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
WRAPPER = RAIZ / "scripts" / "rcparceiro.ps1"

# A API do rcd sabe apagar, mover e sincronizar. Este cliente so sabe ler e copiar.
METODOS_PERMITIDOS = frozenset({"rc/noop", "core/quit", "core/stats", "core/memstats",
                                "operations/stat", "operations/copyfile"})


class ErroRc(Exception):
    def __init__(self, status: int, mensagem: str, caminho: str = ""):
        self.status, self.mensagem, self.caminho = status, mensagem, caminho
        super().__init__(f"rc {caminho} HTTP {status}: {mensagem}")


def porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Rcd:
    """Sobe um `rclone rcd`. `comando_base` permite testes com o executavel direto."""

    def __init__(self, flags: list[str] | None = None, comando_base: list[str] | None = None,
                 log: Path | None = None):
        self.porta = porta_livre()
        self.usuario = "migra"
        self.senha = secrets.token_urlsafe(24)
        self.flags = flags or []
        self.comando_base = comando_base or ["powershell.exe", "-NoProfile", "-NonInteractive",
                                             "-ExecutionPolicy", "Bypass", "-File", str(WRAPPER)]
        self.log = log
        self.proc: subprocess.Popen | None = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.porta}/"

    def __enter__(self) -> "Rcd":
        env = dict(os.environ, RCLONE_RC_USER=self.usuario, RCLONE_RC_PASS=self.senha)
        args = self.comando_base + ["rcd", "--rc-addr", f"127.0.0.1:{self.porta}", "--rc-serve"] + self.flags
        if self.log:
            args += ["--log-file", str(self.log)]
        self.proc = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        limite = time.time() + 60
        while time.time() < limite:
            if self.proc.poll() is not None:
                erro = self.proc.stderr.read().decode("utf-8", "replace")[-800:]
                raise ErroRc(0, f"rcd encerrou ao subir: {erro}", "rcd")
            try:
                self.chamar("rc/noop", {})
                return self
            except (urllib.error.URLError, ConnectionError, ErroRc):
                time.sleep(0.5)
        self.__exit__(None, None, None)
        raise ErroRc(0, "rcd nao respondeu em 60 s", "rcd")

    def __exit__(self, *_):
        if not self.proc:
            return
        try:
            self.chamar("core/quit", {})
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    def _auth(self) -> str:
        return "Basic " + base64.b64encode(f"{self.usuario}:{self.senha}".encode()).decode()

    def chamar(self, caminho: str, corpo: dict, timeout: float = 24 * 3600) -> dict:
        if caminho not in METODOS_PERMITIDOS:
            raise ErroRc(0, f"BLOQUEADO: metodo {caminho} nao e permitido nesta migracao", caminho)
        req =urllib.request.Request(self.base + caminho, data=json.dumps(corpo).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": self._auth()})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            bruto = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(bruto).get("error", bruto)
            except json.JSONDecodeError:
                msg = bruto
            raise ErroRc(e.code, str(msg)[:500], caminho) from e

    def abrir_leitura(self, fs: str, remoto: str, timeout: float = 600):
        """Stream HTTP dos bytes de fs/remoto (via --rc-serve). Use com `with`."""
        alvo = "[" + fs + "]/" + remoto
        url = self.base + urllib.parse.quote(alvo, safe="/[]:")
        req = urllib.request.Request(url, headers={"Authorization": self._auth()})
        return urllib.request.urlopen(req, timeout=timeout)

    # -- atalhos -----------------------------------------------------------
    def stat(self, fs: str, remoto: str, hash_: bool = False) -> dict | None:
        r = self.chamar("operations/stat", {"fs": fs, "remote": remoto,
                                            "opt": {"showHash": hash_, "noModTime": False}})
        return r.get("item")

    def copiar(self, src_fs: str, src: str, dst_fs: str, dst: str, config: dict | None = None) -> None:
        corpo = {"srcFs": src_fs, "srcRemote": src, "dstFs": dst_fs, "dstRemote": dst}
        if config:
            corpo["_config"] = config
        self.chamar("operations/copyfile", corpo)
