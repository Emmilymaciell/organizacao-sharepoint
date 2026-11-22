"""
365_g2_testar_motor - teste REAL do motor G2 contra o Nextcloud do PARCEIRO, SO na pasta de teste
(destino.pasta_teste, autorizada pela Emmily em 28/10/2026). Exige fase 'teste'.

Cada rodada usa uma subpasta nova <pasta_teste>/<AAAAMMDD-HHMMSS>/; so cria. Nao ha DELETE.
Casos:
  T01 PUT de arquivo ausente (If-None-Match: *)              -> 201 e VALIDADO na releitura
  T02 PUT sobre caminho ocupado, conteudo diferente           -> 412; existente intacto (sha/etag/fileid)
  T03 repeticao identica (idempotencia)                       -> 412; nada duplicado
  T04 chunking v2 de arquivo ausente (MOVE Overwrite:F)       -> 201 e VALIDADO
  T05 chunking v2 sobre caminho ocupado                       -> 412; existente intacto
  T06 CORRIDA: outro usuario cria o destino durante o upload  -> MOVE 412; arquivo do outro intacto
  T07 MKCOL de pasta existente                                -> 405 tratado como JA_EXISTIA; fileid igual
  T08 upload interrompido (falha da origem no meio)           -> nada aparece no destino; novo envio conclui
  T09 nenhum arquivo "(1)", "copy", "copia" criado pelo servidor
  T10 trava de escopo: rodovia bloqueada, Eventograma, DUP      -> recusado pela trava (consulta direta, sem HTTP)
  T11 conteudo identico com outro nome                        -> servidor aceita: a deduplicacao e do
                                                                 planejador (hash), registrado como premissa
Saida: output/migracao_parceiro_g2/TESTE_MOTOR_<rodada>.json. Sai 0 so se todos passarem.

Uso: python scripts/365_g2_testar_motor.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2.base import agora, cfg, digital_codigo_escrita, escrita_permitida, saida  # noqa: E402
from migra_g2.credencial import parceiro  # noqa: E402
from migra_g2.dav import Ocupado  # noqa: E402
from migra_g2.envio import conferir, enviar  # noqa: E402
from migra_parceiro.quickxor import QuickXorHash  # noqa: E402


class FonteMem:
    def __init__(self, dados: bytes, falhar_em: int | None = None, ao_ler_ultimo=None):
        self.dados, self.total, self.falhar_em, self.ao_ler_ultimo = dados, len(dados), falhar_em, ao_ler_ultimo

    def ler(self, ini: int, n: int) -> bytes:
        if self.falhar_em is not None and ini >= self.falhar_em:
            raise IOError("falha simulada da origem")
        if self.ao_ler_ultimo and ini + n >= self.total:
            f, self.ao_ler_ultimo = self.ao_ler_ultimo, None
            f()
        return self.dados[ini:ini + n]


def qx(d: bytes) -> str:
    return QuickXorHash().update(d).base64()


def main() -> int:
    c = cfg()
    if c["fase"] != "teste":
        print("ABORTADO: o teste do motor exige fase 'teste' em config/migracao_parceiro_g2.yaml")
        return 1
    ct = copy.deepcopy(c)                  # blocos pequenos para exercitar o chunking com poucos MiB
    ct["transferencia"]["limite_put_simples_mib"] = 1
    ct["transferencia"]["bloco_mib"] = 5
    nc = parceiro(c)
    rodada = time.strftime("%Y%m%d-%H%M%S")
    base = f"{c['destino']['pasta_teste']}/{rodada}"
    res, ok_geral = [], True

    def caso(cod, desc, fn):
        nonlocal ok_geral
        t0 = time.time()
        try:
            ok, ev = fn()
        except Exception as e:  # noqa: BLE001
            ok, ev = False, {"excecao": f"{type(e).__name__}: {str(e)[:300]}"}
        ok_geral &= bool(ok)
        res.append({"caso": cod, "descricao": desc, "passou": bool(ok), "evidencia": ev,
                    "segundos": round(time.time() - t0, 2)})
        print(f"[{'PASSOU' if ok else 'FALHOU'}] {cod} {desc}  {json.dumps(ev, ensure_ascii=False)[:180]}")

    print(f"Rodada {rodada} em {base}")
    print("pasta de teste:", nc.criar_pasta(c["destino"]["pasta_teste"], c))
    print("subpasta da rodada:", nc.criar_pasta(base, c))

    a = os.urandom(300_000)
    pa = f"{base}/T01_pequeno.bin"

    def t01():
        r = enviar(nc, FonteMem(a), pa, c)
        v = conferir(nc, pa, len(a), r["sha256_origem"], qx(a))
        return r["metodo"] == "PUT_INM" and v["validado"], {**r, **v}
    caso("T01", "PUT de arquivo ausente com If-None-Match", t01)
    antes = nc.estado(pa)

    def intacto(caminho, dados, e0):
        e = nc.estado(caminho)
        sha = hashlib.sha256(b"".join(nc.ler_stream(caminho))).hexdigest()
        return (sha == hashlib.sha256(dados).hexdigest() and e.etag == e0.etag and e.fileid == e0.fileid,
                {"etag_antes": e0.etag, "etag_depois": e.etag, "fileid": e.fileid, "sha_igual": sha == hashlib.sha256(dados).hexdigest()})

    def t02():
        try:
            enviar(nc, FonteMem(os.urandom(300_000)), pa, c)
            return False, {"erro": "o servidor ACEITOU sobrescrever"}
        except Ocupado:
            return intacto(pa, a, antes)
    caso("T02", "PUT sobre caminho ocupado (conteudo diferente) e recusado", t02)

    def t03():
        n0 = len(nc.listar(base))
        try:
            enviar(nc, FonteMem(a), pa, c)
            return False, {"erro": "repeticao aceita"}
        except Ocupado:
            return len(nc.listar(base)) == n0, {"itens_antes": n0, "itens_depois": len(nc.listar(base))}
    caso("T03", "repeticao identica e idempotente (412, sem duplicar)", t03)

    g = os.urandom(12 * 2**20 + 12345)
    pg = f"{base}/T04_grande.bin"

    def t04():
        r = enviar(nc, FonteMem(g), pg, ct)
        v = conferir(nc, pg, len(g), r["sha256_origem"], qx(g))
        return r["metodo"] == "CHUNK_V2" and v["validado"], {**r, **v}
    caso("T04", "chunking v2 de arquivo ausente (MOVE Overwrite:F)", t04)
    antes_g = nc.estado(pg)

    def t05():
        try:
            enviar(nc, FonteMem(os.urandom(11 * 2**20)), pg, ct)
            return False, {"erro": "montagem sobrescreveu"}
        except Ocupado:
            return intacto(pg, g, antes_g)
    caso("T05", "chunking v2 sobre caminho ocupado e recusado na montagem", t05)

    pr = f"{base}/T06_corrida.bin"
    outro = os.urandom(200_000)

    def outro_usuario():                   # simula o estagiario criando o MESMO caminho durante o upload
        assert escrita_permitida(pr, False, c)[0]      # so dentro da pasta de teste (fase 'teste')
        st, _ = nc._req("PUT", nc.url_arquivo(pr), {"If-None-Match": "*"}, outro)
        assert st == 201, st

    def t06():
        try:
            enviar(nc, FonteMem(os.urandom(11 * 2**20), ao_ler_ultimo=outro_usuario), pr, ct)
            return False, {"erro": "a montagem sobrescreveu o arquivo do outro usuario"}
        except Ocupado:
            e = nc.estado(pr)
            sha = hashlib.sha256(b"".join(nc.ler_stream(pr))).hexdigest()
            return sha == hashlib.sha256(outro).hexdigest(), {"arquivo_do_outro_intacto": sha == hashlib.sha256(outro).hexdigest(),
                                                               "tamanho": e.tamanho}
    caso("T06", "corrida: destino criado por terceiro durante o upload -> 412, sem sobrescrever", t06)

    def t07():
        e0 = nc.estado(base)
        r = nc.criar_pasta(base, c)
        return r == "JA_EXISTIA" and nc.estado(base).fileid == e0.fileid, {"resultado": r, "fileid": e0.fileid}
    caso("T07", "MKCOL de pasta existente nao altera nada", t07)

    pi = f"{base}/T08_interrompido.bin"

    def t08():
        d = os.urandom(12 * 2**20)
        try:
            enviar(nc, FonteMem(d, falhar_em=5 * 2**20), pi, ct)
            return False, {"erro": "nao interrompeu"}
        except IOError:
            pass
        ausente = nc.estado(pi) is None
        r = enviar(nc, FonteMem(d), pi, ct)
        v = conferir(nc, pi, len(d), r["sha256_origem"], qx(d))
        return ausente and v["validado"], {"destino_ausente_apos_falha": ausente, "retomado_validado": v["validado"]}
    caso("T08", "falha no meio: nada parcial no destino; novo envio conclui e valida", t08)

    def t09():
        nomes = [e.caminho.rsplit("/", 1)[-1] for e in nc.listar(base) if e.caminho != base]
        ruins = [n for n in nomes if any(s in n.lower() for s in ("(1)", "(2)", "copy", "copia", "cópia", "conflict"))]
        return not ruins and sorted(nomes) == sorted(["T01_pequeno.bin", "T04_grande.bin", "T06_corrida.bin",
                                                      "T08_interrompido.bin"]), {"itens": sorted(nomes)}
    caso("T09", "servidor nao criou copias renomeadas", t09)

    def t10():
        # SEM nenhuma requisicao ao servidor: a trava e consultada direto. (Antes o T10 tentava escrever de verdade
        # e dependia da propria trava para nao gravar em producao - achado 2 da 3a revisao de QA.)
        cp = copy.deepcopy(c)
        cp["fase"] = "execucao"
        cp["destino"]["rodovias_liberadas"] = ["RD-103"]       # a trava em si, independente do config do dia
        bloqueios = []
        for p, pasta_ in [("PARCEIRO-INFRA/03. RD-101/04. Engenharia/x.bin", False),
                          ("PARCEIRO-INFRA/05. RD-103/04. Engenharia/09. Eventograma/x.bin", False),
                          ("PARCEIRO-INFRA/05. RD-103/04. Engenharia/04. Desapropriação/01. DUP/x.bin", False),
                          ("PARCEIRO-INFRA/04. RD-102A/04. Engenharia/01. Projetos/x", True),
                          ("PARCEIRO-INFRA/02. Programa/x.bin", False)]:
            ok, motivo = escrita_permitida(p, pasta_, cp)
            bloqueios.append((p, "PERMITIRIA" if ok else motivo))
        return all(b[1] != "PERMITIRIA" for b in bloqueios), {"bloqueios": bloqueios}
    caso("T10", "trava de escopo recusa outras rodovias, Eventograma, DUP e fora da Engenharia", t10)

    def t11():
        p = f"{base}/T11_mesmo_conteudo_outro_nome.bin"
        nc.put_se_ausente(p, a, c)
        return True, {"premissa": "o servidor nao deduplica; o planejador compara hash antes de enfileirar"}
    caso("T11", "conteudo identico com outro nome e aceito pelo servidor (dedup e do planejador)", t11)

    out = saida(c) / f"TESTE_MOTOR_{rodada}.json"
    out.write_text(json.dumps({"rodada": rodada, "pasta": base, "quando": agora(), "todos_passaram": ok_geral,
                               "codigo_escrita_sha256": digital_codigo_escrita(), "casos": res},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(("TODOS OS CASOS PASSARAM" if ok_geral else "HA CASOS FALHANDO") + f" -> {out}")
    return 0 if ok_geral else 1


if __name__ == "__main__":
    raise SystemExit(main())
