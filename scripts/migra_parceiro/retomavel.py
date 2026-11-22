"""
retomavel - upload RETOMAVEL de arquivo grande para o Nextcloud (chunking v2), lendo a origem por
intervalos (HTTP Range) do SharePoint. Feito para o video de 87,76 GiB, que o rclone (WebDAV) nao
consegue retomar: uma queda la recomeca do zero.

Protocolo (Nextcloud >= 20, "chunked upload v2"):
  MKCOL  /remote.php/dav/uploads/<uid>/<id>            (Destination: .../files/<uid>/<destino>)
  PUT    /remote.php/dav/uploads/<uid>/<id>/<n>        n = 1..10000; blocos de 5 MiB a 5 GiB
  MOVE   /remote.php/dav/uploads/<uid>/<id>/.file      (Destination, OC-Total-Length, Overwrite: F)

Retomada: o id do envio e fixo por arquivo; ao reiniciar, o PROPFIND da pasta de upload diz quais
blocos o SERVIDOR ja tem com o tamanho certo, e so os que faltam sao enviados. Nada vai para o SSD:
cada bloco fica so na memoria (bloco x paralelo).

Seguranca: se o destino ja existir, para (CONFLITO) - nunca sobrescreve; o MOVE leva Overwrite: F.
A prova de integridade NAO e o fim do upload: e a conferencia do motor (337), que rele o arquivo
inteiro do PARCEIRO e compara o QuickXorHash com o da Microsoft.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

MIN_BLOCO = 5 * 2**20
MAX_BLOCOS = 10000


class Conflito(Exception):
    pass


class Estrutural(Exception):
    """Recusa do servidor (4xx: credencial, permissao, protocolo). Nao adianta repetir: para e preserva os blocos."""


def _checar(st: int, r: bytes, oque: str, aceitos: tuple):
    if st in aceitos:
        return
    if 400 <= st < 500 and st not in (408, 423, 429):
        raise Estrutural(f"{oque}: {st} {r[:200]!r}")
    raise IOError(f"{oque}: {st} {r[:200]!r}")


def _q(caminho: str) -> str:
    return "/".join(urllib.parse.quote(s, safe="") for s in caminho.split("/"))


class Nextcloud:
    def __init__(self, base_dav: str, uid: str, login: str, senha: str, timeout: float = 600):
        self.base = base_dav.rstrip("/")
        self.uid, self.timeout = uid, timeout
        self._auth = "Basic " + base64.b64encode(f"{login}:{senha}".encode()).decode()

    def url_arquivo(self, rel: str) -> str:
        return f"{self.base}/files/{_q(self.uid)}/{_q(rel)}"

    def url_upload(self, envio: str, nome: str = "") -> str:
        u = f"{self.base}/uploads/{_q(self.uid)}/{_q(envio)}"
        return f"{u}/{nome}" if nome else u

    def _req(self, metodo, url, headers=None, dados=None, timeout=None):
        h = {"Authorization": self._auth, **(headers or {})}
        req = urllib.request.Request(url, data=dados, method=metodo, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def tamanho(self, url: str) -> int | None:
        corpo = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>'
        st, r = self._req("PROPFIND", url, {"Depth": "0", "Content-Type": "application/xml"}, corpo)
        if st == 404:
            return None
        _checar(st, r, "PROPFIND", (207,))
        el = ET.fromstring(r).find(".//{DAV:}getcontentlength")
        return int(el.text) if el is not None and el.text else 0

    def blocos(self, envio: str) -> dict[int, int]:
        corpo = b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getcontentlength/></d:prop></d:propfind>'
        st, r = self._req("PROPFIND", self.url_upload(envio), {"Depth": "1", "Content-Type": "application/xml"}, corpo)
        if st == 404:
            return {}
        _checar(st, r, "PROPFIND blocos", (207,))
        out = {}
        for resp in ET.fromstring(r).findall("{DAV:}response"):
            nome = urllib.parse.unquote(resp.findtext("{DAV:}href", "").rstrip("/").rsplit("/", 1)[-1])
            tam = resp.find(".//{DAV:}getcontentlength")
            if nome.isdigit() and tam is not None and tam.text:
                out[int(nome)] = int(tam.text)
        return out

    def garantir_pastas(self, pasta_rel: str, log=print):
        """Cria (MKCOL) as pastas que faltam no caminho do destino. So cria; nunca apaga nem renomeia."""
        partes = pasta_rel.strip("/").split("/")
        for i in range(1, len(partes) + 1):
            sub = "/".join(partes[:i])
            if self.tamanho(self.url_arquivo(sub)) is None:
                st, r = self._req("MKCOL", self.url_arquivo(sub))
                _checar(st, r, f"MKCOL pasta {sub}", (201, 405))
                log(f"pasta criada no destino: {sub}")

    def criar_envio(self, envio: str, destino: str):
        st, r = self._req("MKCOL", self.url_upload(envio), {"Destination": destino})
        _checar(st, r, "MKCOL envio", (201, 405))          # 405 = ja existe (retomada)

    def enviar_bloco(self, envio: str, n: int, dados: bytes, destino: str, total: int):
        st, r = self._req("PUT", self.url_upload(envio, str(n)),
                          {"Destination": destino, "OC-Total-Length": str(total),
                           "Content-Type": "application/octet-stream"}, dados)
        _checar(st, r, f"PUT bloco {n}", (201, 204))

    def montar(self, envio: str, destino: str, total: int, timeout: float):
        return self._req("MOVE", self.url_upload(envio, ".file"),
                         {"Destination": destino, "OC-Total-Length": str(total), "Overwrite": "F"},
                         timeout=timeout)


class FonteLocal:
    """Origem em arquivo local (testes)."""
    def __init__(self, caminho: Path):
        self.caminho, self.total = Path(caminho), Path(caminho).stat().st_size

    def ler(self, inicio: int, n: int) -> bytes:
        with open(self.caminho, "rb") as f:
            f.seek(inicio)
            return f.read(n)


class FonteGraph:
    """Origem no SharePoint, lida por intervalos. A URL de download (pre-autenticada, sem token) e
    renovada sozinha quando expira."""
    def __init__(self, g, drive_id: str, item_id: str, total: int):
        self.g, self.drive_id, self.item_id, self.total = g, drive_id, item_id, total
        self._url, self._trava = None, threading.Lock()

    def _download_url(self, renovar=False) -> str:
        with self._trava:
            if renovar or not self._url:
                it = self.g.get(f"/drives/{self.drive_id}/items/{self.item_id}?$select=id,size")
                if it.get("size") != self.total:
                    raise IOError(f"origem mudou de tamanho: {it.get('size')} != {self.total}")
                # /content responde 302 para uma URL pre-autenticada; o token NAO segue para ela
                from graph_util import GRAPH
                from migra_parceiro.leitura import _SemRedirecionar
                req = urllib.request.Request(f"{GRAPH}/drives/{self.drive_id}/items/{self.item_id}/content",
                                             headers={"Authorization": f"Bearer {self.g.token()}"})
                try:
                    urllib.request.build_opener(_SemRedirecionar).open(req, timeout=120).close()
                    raise IOError("o Graph nao redirecionou /content para a URL de download")
                except urllib.error.HTTPError as e:
                    if e.code not in (301, 302, 303, 307, 308):
                        raise
                    self._url = e.headers["Location"]
            return self._url

    def ler(self, inicio: int, n: int) -> bytes:
        for tent in range(2):
            req = urllib.request.Request(self._download_url(renovar=tent > 0),
                                         headers={"Range": f"bytes={inicio}-{inicio + n - 1}"})
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    dados = r.read()
                if len(dados) != n:
                    raise IOError(f"leitura curta da origem: {len(dados)} de {n}")
                return dados
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404, 410) and tent == 0:
                    continue          # URL expirou: renova e tenta de novo
                raise
        raise IOError("falha ao ler a origem")


def plano_blocos(total: int, bloco: int) -> list[tuple[int, int, int]]:
    """[(n, inicio, tamanho)] com n a partir de 1."""
    if bloco < MIN_BLOCO:
        raise ValueError("bloco minimo do Nextcloud e 5 MiB")
    out, n, ini = [], 1, 0
    while ini < total:
        t = min(bloco, total - ini)
        out.append((n, ini, t))
        n, ini = n + 1, ini + t
    if len(out) > MAX_BLOCOS:
        raise ValueError(f"{len(out)} blocos excede o limite de {MAX_BLOCOS}; aumente o bloco")
    return out


def enviar(fonte, nc: Nextcloud, destino_rel: str, envio: str, bloco: int, paralelo: int = 3,
           tentativas: int = 6, checkpoint: Path | None = None, parar=lambda: False, log=print,
           espera_montagem_s: float = 4 * 3600, dormir=time.sleep) -> dict:
    total = fonte.total
    destino = nc.url_arquivo(destino_rel)
    if nc.tamanho(destino) is not None:
        raise Conflito(f"o destino ja existe no PARCEIRO: {destino_rel} (nao sobrescreve)")
    plano = plano_blocos(total, bloco)
    nc.garantir_pastas(destino_rel.rsplit("/", 1)[0], log=log)
    nc.criar_envio(envio, destino)
    ja = nc.blocos(envio)
    faltam = [(n, i, t) for n, i, t in plano if ja.get(n) != t]
    log(f"{len(plano)} blocos de {bloco // 2**20} MiB; no servidor: {len(plano) - len(faltam)}; faltam {len(faltam)}")
    t0, enviados, trava = time.time(), [0], threading.Lock()
    feitos = [len(plano) - len(faltam)]
    abortar = threading.Event()             # falha definitiva: nenhum bloco novo comeca

    def um(n, ini, tam):
        for k in range(tentativas):
            if parar() or abortar.is_set():
                return False
            try:
                nc.enviar_bloco(envio, n, fonte.ler(ini, tam), destino, total)
                with trava:
                    enviados[0] += tam
                    feitos[0] += 1
                return True
            except Estrutural:
                abortar.set()
                raise                       # 401/403/4xx: repetir nao resolve
            except Exception as e:
                if k == tentativas - 1:
                    abortar.set()
                    raise
                log(f"bloco {n}: {e} - nova tentativa em {min(10 * 2 ** k, 300)} s")
                dormir(min(10 * 2 ** k, 300))

    with ThreadPoolExecutor(max_workers=paralelo) as pool:
        futs = {pool.submit(um, n, i, t): n for n, i, t in faltam}
        for k, f in enumerate(as_completed(futs), 1):
            try:
                if not f.result():
                    continue                # interrompido antes de comecar este bloco
            except Exception:
                for outro in futs:          # falha definitiva: nao comeca mais blocos (os ja no
                    outro.cancel()          # servidor ficam guardados para a retomada)
                raise
            if checkpoint and (k % 10 == 0 or k == len(faltam)):
                dec = time.time() - t0
                vel = enviados[0] / 2**20 / dec if dec else 0
                resta = (len(plano) - feitos[0]) * bloco / 2**20 / vel if vel else None
                checkpoint.write_text(json.dumps({
                    "envio": envio, "destino": destino_rel, "total": total, "blocos": len(plano),
                    "blocos_no_servidor": feitos[0], "enviados_nesta_execucao": k,
                    "GiB_enviados": round(min(feitos[0] * bloco, total) / 2**30, 2), "MiB_s": round(vel, 2),
                    "fim_envio_estimado": time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() + resta))
                    if resta is not None else None,
                    "atualizado": time.strftime("%Y-%m-%dT%H:%M:%S")}, ensure_ascii=False), encoding="utf-8")
                log(f"progresso: {feitos[0]}/{len(plano)} blocos, {vel:.1f} MiB/s")
    if parar():
        return {"situacao": "INTERROMPIDO", "blocos": len(plano)}
    ja = nc.blocos(envio)
    ruins = [n for n, _, t in plano if ja.get(n) != t]
    if ruins:
        raise IOError(f"{len(ruins)} blocos ausentes ou com tamanho errado no servidor: {ruins[:5]}")
    log("todos os blocos no servidor; pedindo a montagem final (pode demorar)")
    t1 = time.time()
    try:
        st, r = nc.montar(envio, destino, total, timeout=espera_montagem_s)
        if st == 412:
            raise Conflito("o destino passou a existir antes da montagem (Overwrite: F)")
        if 400 <= st < 500 and st not in (408, 423, 429):
            raise Estrutural(f"MOVE recusado: {st} {r[:200]!r} (blocos preservados no servidor)")
        if st not in (201, 204):
            log(f"MOVE respondeu {st}: {r[:200]!r} - conferindo o destino")
    except (TimeoutError, urllib.error.URLError, OSError) as e:
        if isinstance(e, (Conflito, Estrutural)):
            raise
        log(f"MOVE sem resposta ({e}); aguardando o servidor terminar a montagem")
    fim = time.time() + espera_montagem_s
    while time.time() < fim:
        tam = nc.tamanho(destino)
        if tam == total:
            return {"situacao": "MONTADO", "blocos": len(plano), "segundos_montagem": round(time.time() - t1),
                    "MiB_s": round(enviados[0] / 2**20 / max(t1 - t0, 1e-3), 2)}
        dormir(30)
    raise IOError("montagem nao confirmada no prazo; o envio continua no servidor e pode ser retomado")
