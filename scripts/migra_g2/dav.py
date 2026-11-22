"""
dav - cliente WebDAV do PARCEIRO INFRA (Nextcloud) para a operacao G2.

Leitura: PROPFIND Depth 1 com os identificadores estaveis do Nextcloud (oc:fileid, getetag,
oc:checksums, oc:permissions).

Escrita: SO criacao condicional, decidida pelo servidor:
  - arquivo pequeno: PUT com `If-None-Match: *`  -> 412 se o caminho ja existir;
  - arquivo grande: chunking v2 e montagem final por MOVE com `Overwrite: F` -> 412 se o destino existir;
  - pasta: MKCOL -> 405 se ja existir (nunca recria nem renomeia).
Nao existe metodo para DELETE, MOVE de arquivo do usuario, PROPPATCH ou COPY. Toda escrita passa
antes por base.escrita_permitida (escopo das quatro arvores, Eventograma bloqueado).
O comportamento do 412 no PARCEIRO so vale depois do teste do motor (365_g2_testar_motor.py).
"""

from __future__ import annotations

import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from migra_parceiro.retomavel import Nextcloud, _checar, _q  # noqa: F401  (reuso do cliente que ja funcionou)

from .base import escrita_permitida

NS = {"d": "DAV:", "oc": "http://owncloud.org/ns", "nc": "http://nextcloud.org/ns"}
PROPS = b"""<?xml version="1.0"?>
<d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns" xmlns:nc="http://nextcloud.org/ns">
 <d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getetag/>
  <oc:fileid/><oc:size/><oc:checksums/><oc:permissions/><oc:owner-display-name/>
  <nc:upload_time/><nc:creation_time/></d:prop>
</d:propfind>"""


class Ocupado(Exception):
    """O servidor recusou a criacao porque o caminho ja existe (412/405). Nada foi sobrescrito."""


class EscritaProibida(Exception):
    pass


@dataclass
class Entrada:
    caminho: str            # relativo a raiz do usuario, sem barra inicial/final
    eh_pasta: bool
    tamanho: int | None
    modificado: str | None
    etag: str | None
    fileid: str | None
    checksums: str | None
    permissoes: str | None
    dono: str | None
    criado: str | None


class PARCEIRO(Nextcloud):
    def __init__(self, base_dav: str, uid: str, login: str, senha: str, timeout: float = 300):
        super().__init__(base_dav, uid, login, senha, timeout)
        self._prefixo = urllib.parse.urlparse(self.url_arquivo("")).path.rstrip("/") + "/"

    # metodos herdados do cliente da migracao anterior que escrevem SEM a trava: bloqueados (achado 4, 5a revisao)
    def garantir_pastas(self, *a, **k):
        raise EscritaProibida("garantir_pastas herdado nao passa pela trava: use criar_pasta")

    def montar(self, *a, **k):
        raise EscritaProibida("montar herdado nao usa a trava nem Overwrite: F garantido: use montar_se_ausente")

    # ------------------------------------------------------------ leitura
    def _req_ret(self, metodo, url, headers=None, dados=None, timeout=None, tentativas=5):
        """_req com retentativa para falhas transitorias (rede, 429, 5xx), respeitando Retry-After."""
        for k in range(tentativas):
            try:
                h = {"Authorization": self._auth, **(headers or {})}
                req = urllib.request.Request(url, data=dados, method=metodo, headers=h)
                with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                corpo = e.read()
                if e.code in (423, 429, 502, 503, 504) and k < tentativas - 1:
                    espera = int(e.headers.get("Retry-After", "0") or 0) or min(5 * 2 ** k, 120)
                    time.sleep(espera)
                    continue
                return e.code, corpo
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                if k == tentativas - 1:
                    raise
                time.sleep(min(5 * 2 ** k, 120))
        raise IOError("sem resposta")

    def _rel(self, href: str) -> str:
        p = urllib.parse.unquote(urllib.parse.urlparse(href).path)
        if not p.startswith(self._prefixo):
            raise ValueError(f"href fora da raiz do usuario: {p}")
        return p[len(self._prefixo):].strip("/")

    def listar(self, caminho: str, profundidade: str = "1") -> list[Entrada] | None:
        """PROPFIND. None se o caminho nao existir (404)."""
        st, r = self._req_ret("PROPFIND", self.url_arquivo(caminho),
                              {"Depth": profundidade, "Content-Type": "application/xml"}, PROPS)
        if st == 404:
            return None
        _checar(st, r, f"PROPFIND {caminho}", (207,))
        out = []
        for resp in ET.fromstring(r).findall("d:response", NS):
            props = {}
            for ps in resp.findall("d:propstat", NS):
                if "200" in (ps.findtext("d:status", "", NS) or ""):
                    for el in ps.find("d:prop", NS):
                        props[el.tag] = el
            rt = props.get("{DAV:}resourcetype")
            eh_pasta = rt is not None and rt.find("d:collection", NS) is not None
            tam = props.get("{DAV:}getcontentlength") if not eh_pasta else props.get("{http://owncloud.org/ns}size")
            ck = props.get("{http://owncloud.org/ns}checksums")
            cks = " ".join((c.text or "") for c in ck.iter() if c.text and c.text.strip()) if ck is not None else None

            def t(tag):
                el = props.get(tag)
                return el.text if el is not None and el.text else None
            out.append(Entrada(
                caminho=self._rel(resp.findtext("d:href", "", NS)), eh_pasta=eh_pasta,
                tamanho=int(tam.text) if tam is not None and tam.text else None,
                modificado=t("{DAV:}getlastmodified"), etag=(t("{DAV:}getetag") or "").strip('"') or None,
                fileid=t("{http://owncloud.org/ns}fileid"), checksums=cks or None,
                permissoes=t("{http://owncloud.org/ns}permissions"),
                dono=t("{http://owncloud.org/ns}owner-display-name"),
                # upload_time e registrado pelo SERVIDOR; creation_time vem do cliente (X-OC-CTime)
                criado=t("{http://nextcloud.org/ns}upload_time") or (
                    t("{http://nextcloud.org/ns}creation_time") if t("{http://nextcloud.org/ns}creation_time")
                    not in (None, "0") else None)))
        return out

    def estado(self, caminho: str) -> Entrada | None:
        r = self.listar(caminho, "0")
        return r[0] if r else None

    def ler_stream(self, caminho: str, bloco: int = 8 * 2**20):
        req = urllib.request.Request(self.url_arquivo(caminho), headers={"Authorization": self._auth})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            while True:
                b = r.read(bloco)
                if not b:
                    return
                yield b

    # ------------------------------------------------------------ escrita condicional
    def _exigir(self, caminho: str, eh_pasta: bool, cfg: dict) -> str:
        """Checa a trava e devolve o caminho em NFC: o que foi validado e exatamente o que e escrito."""
        caminho = unicodedata.normalize("NFC", caminho)
        ok, motivo = escrita_permitida(caminho, eh_pasta, cfg)
        if not ok:
            raise EscritaProibida(f"{caminho}: {motivo}")
        return caminho

    def criar_pasta(self, caminho: str, cfg: dict) -> str:
        """MKCOL de UMA pasta. 'CRIADA' ou 'JA_EXISTIA'. Nunca mexe na existente."""
        caminho = self._exigir(caminho, True, cfg)
        st, r = self._req_ret("MKCOL", self.url_arquivo(caminho))
        if st == 201:
            return "CRIADA"
        if st == 405:
            return "JA_EXISTIA"
        _checar(st, r, f"MKCOL {caminho}", ())
        return "?"

    def put_se_ausente(self, caminho: str, dados: bytes, cfg: dict, mtime: int | None = None) -> int:
        """PUT com If-None-Match: *. Levanta Ocupado (412) sem tocar no existente."""
        caminho = self._exigir(caminho, False, cfg)
        h = {"If-None-Match": "*", "Content-Type": "application/octet-stream"}
        if mtime:
            h["X-OC-Mtime"] = str(int(mtime))
        st, r = self._req("PUT", self.url_arquivo(caminho), h, dados)
        if st == 412:
            raise Ocupado(caminho)
        _checar(st, r, f"PUT {caminho}", (201,))       # 204 = substituiu algo: NAO aceito
        return st

    def montar_se_ausente(self, envio: str, caminho: str, total: int, cfg: dict, timeout: float) -> int:
        """MOVE final do chunking v2 com Overwrite: F (RFC 4918: 412 se o DESTINO existir).
        Sem If-None-Match aqui: no MOVE ele e avaliado sobre a ORIGEM (.file), que sempre existe, e o
        servidor recusa tudo (comprovado no teste de 28/10/2026, rodada 20261028-110216)."""
        caminho = self._exigir(caminho, False, cfg)
        st, r = self._req("MOVE", self.url_upload(envio, ".file"),
                          {"Destination": self.url_arquivo(caminho), "OC-Total-Length": str(total),
                           "Overwrite": "F"}, timeout=timeout)
        if st == 412:
            raise Ocupado(caminho)
        _checar(st, r, f"MOVE montagem {caminho}", (201,))
        return st
