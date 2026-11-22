"""
envio - copia UM arquivo da origem para o PARCEIRO sem nunca sobrescrever, e confere.

  enviar(...)   streaming da origem (RAM, nada no SSD) para o destino:
                  <= limite_put_simples_mib : um PUT com If-None-Match: *
                  >  limite                 : chunking v2 (blocos) + MOVE com Overwrite: F (no MOVE o
                                              If-None-Match seria avaliado sobre a origem .file: ver D-11)
                SHA-256 e QuickXor da ORIGEM sao calculados sobre os mesmos bytes enviados.
                Se o servidor responder 412 (caminho passou a existir), levanta dav.Ocupado: nada foi
                substituido, e a montagem incompleta fica so na area de upload do proprio usuario.
  conferir(...) rele o arquivo inteiro DO PARCEIRO e compara QuickXor (vs. o da Microsoft, quando houver)
                e SHA-256 (vs. o calculado na leitura da origem). VALIDADO so com os dois iguais.
"""

from __future__ import annotations

import hashlib
import time
import uuid

from migra_parceiro.motor import _mesmo_qxh
from migra_parceiro.quickxor import QuickXorHash

from .dav import PARCEIRO, Ocupado  # noqa: F401


class OrigemMudou(Exception):
    pass


def retomada_e_nossa(estado: str, sha256_gravado: str | None, upload_epoch: str | None, inicio_envio_iso: str,
                     folga_s: int = 5, limite_epoch: float | None = None) -> bool:
    """Um destino ja ocupado na retomada so e atribuido ao motor com prova:
      - COPIADO com SHA-256 gravado: o PUT/MOVE do motor respondeu 201 antes da queda;
      - COPIANDO: so se o servidor registrou o upload (nc:upload_time) DEPOIS de o motor marcar o inicio do envio
        (plano.atualizado_em) e ANTES do inicio da execucao que retoma (limite_epoch). Resta uma janela (queda ->
        retomada) em que um terceiro com conteudo identico seria atribuido: registrado no HANDOFF. NA_FILA/ERRO (ex.: timeout do MOVE depois de montar) nunca e atribuido: o item fica
        JA_EXISTIA_CORRETO, conservador, para decisao humana.
    Sem prova, o arquivo e tratado como de terceiro (achado 3 do QA do checkpoint de 28/10/2026)."""
    from datetime import datetime
    if estado == "COPIADO" and sha256_gravado:
        return True
    # So COPIANDO: e o unico estado em que plano.atualizado_em marca o INICIO do envio. Em NA_FILA/ERRO ele marca a
    # falha (depois do upload), e a prova se inverteria (achado 1 da 3a revisao de QA): sem prova, nao e nosso.
    if estado != "COPIANDO" or not upload_epoch or not str(upload_epoch).isdigit():
        return False
    inicio = datetime.fromisoformat(inicio_envio_iso).timestamp()
    if limite_epoch is not None and int(upload_epoch) > limite_epoch:
        return False                                   # subiu depois que esta execucao comecou: nao foi a anterior
    return int(upload_epoch) >= inicio - folga_s


def _ler_tudo(fonte, total: int, bloco: int, hs) -> bytes:
    partes, ini = [], 0
    while ini < total:
        n = min(bloco, total - ini)
        b = fonte.ler(ini, n)
        for h in hs:
            h.update(b)
        partes.append(b)
        ini += n
    return b"".join(partes)


def enviar(nc: PARCEIRO, fonte, destino: str, cfg: dict, mtime: int | None = None) -> dict:
    """fonte: objeto com .total e .ler(inicio, n) (migra_parceiro.retomavel.FonteGraph/FonteLocal)."""
    t = cfg["transferencia"]
    total = fonte.total
    bloco = int(t["bloco_mib"]) * 2**20
    sha, qx = hashlib.sha256(), QuickXorHash()
    t0 = time.time()
    if total <= int(t["limite_put_simples_mib"]) * 2**20:
        dados = _ler_tudo(fonte, total, bloco, (sha, qx))
        if len(dados) != total:
            raise OrigemMudou(f"origem leu {len(dados)} B de {total}")
        nc.put_se_ausente(destino, dados, cfg, mtime)
        metodo = "PUT_INM"
    else:
        envio = f"g2-{uuid.uuid4().hex}"
        alvo = nc.url_arquivo(destino)
        nc.criar_envio(envio, alvo)
        n, ini = 1, 0
        while ini < total:
            tam = min(bloco, total - ini)
            b = fonte.ler(ini, tam)
            sha.update(b)
            qx.update(b)
            for k in range(int(t["tentativas"])):
                try:
                    nc.enviar_bloco(envio, n, b, alvo, total)
                    break
                except IOError:
                    if k == int(t["tentativas"]) - 1:
                        raise
                    time.sleep(min(10 * 2 ** k, 120))
            n, ini = n + 1, ini + tam
        nc.montar_se_ausente(envio, destino, total, cfg, timeout=3600)
        metodo = "CHUNK_V2"
    return {"metodo": metodo, "bytes": total, "segundos": round(time.time() - t0, 2),
            "sha256_origem": sha.hexdigest(), "quick_xor_origem": qx.base64()}


def conferir(nc: PARCEIRO, destino: str, total: int, sha256_origem: str, qxh_microsoft: str | None) -> dict:
    sha, qx, n = hashlib.sha256(), QuickXorHash(), 0
    for b in nc.ler_stream(destino):
        sha.update(b)
        qx.update(b)
        n += len(b)
    e = nc.estado(destino)
    ok_tam = n == total
    ok_sha = sha.hexdigest() == sha256_origem
    ok_qx = _mesmo_qxh(qx.base64(), qxh_microsoft) if qxh_microsoft else None
    return {"validado": bool(ok_tam and ok_sha and ok_qx is not False), "tamanho_lido": n,
            "sha256_destino": sha.hexdigest(), "quick_xor_destino": qx.base64(),
            "qx_igual_microsoft": ok_qx, "fileid": e.fileid if e else None, "etag": e.etag if e else None}
