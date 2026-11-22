"""
20_inventario - E1: inventario completo do SharePoint pela nuvem.

Percorre cada biblioteca do site com `GET /drives/{id}/root/delta`, paginado,
e grava tudo em SQLite. Nenhum arquivo e baixado: nome, tamanho, datas, autor
e o `quickXorHash` vem na propria listagem.

SOMENTE LEITURA. Este script so usa GET (ver graph_util.Graph.get).

Retomada (REGRA 7): o `@odata.nextLink` e gravado a cada lote. Se cair, roda
de novo e continua de onde parou. Ao final grava o `@odata.deltaLink` para o
incremental futuro.

Uso:
    python scripts\\20_inventario.py                 # varre tudo
    python scripts\\20_inventario.py --amostra 2000  # so 2000 itens (validar)
    python scripts\\20_inventario.py --biblioteca Programa
    python scripts\\20_inventario.py --reiniciar     # descarta e recomeca
    python scripts\\20_inventario.py --incremental   # usa o deltaLink salvo
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402
from graph_util import ErroGraph, Graph, explicar  # noqa: E402

RAIZ_REPO = Path(__file__).resolve().parents[1]
ESQUEMA = RAIZ_REPO / "sql" / "01_esquema.sql"

# Console do Windows nem sempre e UTF-8; nao deixar um acento derrubar a
# varredura de 139 mil itens.
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except AttributeError:
    pass


# ----------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------

def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ptbr(texto: str) -> str:
    """Troca o separador ingles pelo brasileiro: 1,193.9 -> 1.193,9"""
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n: int | float, casas: int = 0) -> str:
    return ptbr(f"{n:,.{casas}f}")


def gb(n: int | None) -> str:
    return f"{num((n or 0) / 1024**3, 1)} GB"


class Log:
    """Escreve no console e no arquivo de log ao mesmo tempo."""

    def __init__(self, caminho: Path):
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.arquivo = caminho.open("a", encoding="utf-8")

    def __call__(self, texto: str = "") -> None:
        print(texto)
        self.arquivo.write(f"{texto}\n")
        self.arquivo.flush()

    def fechar(self) -> None:
        self.arquivo.close()


# ----------------------------------------------------------------------
# Banco
# ----------------------------------------------------------------------

def abrir_banco(caminho: Path) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho)
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    con.commit()
    return con


def registrar_erro(con, etapa, drive_id, item_id, caminho, codigo, mensagem):
    """REGRA 5: item problematico e registrado, nunca some e nunca para a varredura."""
    con.execute(
        "INSERT INTO erro (quando, etapa, drive_id, item_id, caminho, codigo, mensagem)"
        " VALUES (?,?,?,?,?,?,?)",
        (agora(), etapa, drive_id, item_id, caminho, codigo, str(mensagem)[:1000]))
    con.commit()


# ----------------------------------------------------------------------
# Traducao do driveItem do Graph para a linha do catalogo
# ----------------------------------------------------------------------

def montar_caminho(item: dict) -> str:
    """Caminho completo dentro da biblioteca, comecando com '/'.

    O Graph devolve `parentReference.path` como
    '/drives/{driveId}/root:/Pasta/Sub' (percent-encoded).
    """
    if "root" in item:
        return "/"
    bruto = (item.get("parentReference") or {}).get("path") or ""
    base = bruto.split("root:", 1)[1] if "root:" in bruto else ""
    base = urllib.parse.unquote(base)
    caminho = f"{base}/{item.get('name') or ''}"
    return re.sub(r"/{2,}", "/", caminho) or "/"


def _pessoa(item: dict, chave: str) -> tuple[str | None, str | None]:
    """displayName e email de createdBy/lastModifiedBy (pode ser app, nao usuario)."""
    bloco = item.get(chave) or {}
    for tipo in ("user", "application", "device"):
        if tipo in bloco:
            dados = bloco[tipo] or {}
            return dados.get("displayName"), dados.get("email")
    return None, None


def traduzir(item: dict, drive_id: str, biblioteca: str) -> tuple:
    eh_pasta = "folder" in item
    eh_raiz = "root" in item
    caminho = montar_caminho(item)
    nome = item.get("name") or ""

    partes = [p for p in caminho.split("/") if p]
    nivel1 = partes[0] if partes else None
    nivel2 = partes[1] if len(partes) > 1 else None
    pasta_pai = "/" + "/".join(partes[:-1]) if len(partes) > 1 else "/"

    extensao = None if eh_pasta else (os.path.splitext(nome)[1].lower() or None)

    arquivo = item.get("file") or {}
    hashes = arquivo.get("hashes") or {}
    fs = item.get("fileSystemInfo") or {}

    criado_por, criado_email = _pessoa(item, "createdBy")
    modif_por, modif_email = _pessoa(item, "lastModifiedBy")

    return (
        drive_id,
        item.get("id"),
        biblioteca,
        (item.get("parentReference") or {}).get("id"),
        caminho,
        pasta_pai,
        nome,
        extensao,
        1 if eh_pasta else 0,
        1 if eh_raiz else 0,
        nivel1,
        nivel2,
        len(partes),
        item.get("size"),
        (item.get("folder") or {}).get("childCount") if eh_pasta else None,
        item.get("createdDateTime") or fs.get("createdDateTime"),
        item.get("lastModifiedDateTime") or fs.get("lastModifiedDateTime"),
        criado_por,
        criado_email,
        modif_por,
        modif_email,
        hashes.get("quickXorHash"),
        hashes.get("sha1Hash"),
        hashes.get("sha256Hash"),
        arquivo.get("mimeType"),
        item.get("webUrl"),
        item.get("eTag"),
        item.get("cTag"),
        1 if "deleted" in item else 0,
        agora(),
    )


INSERIR = """
INSERT INTO item_nuvem (
    drive_id, item_id, biblioteca, pai_id, caminho, pasta_pai, nome, extensao,
    eh_pasta, eh_raiz, nivel1, nivel2, profundidade, tamanho, filhos,
    criado_em, modificado_em, criado_por, criado_por_email,
    modificado_por, modificado_por_email,
    quick_xor_hash, sha1_hash, sha256_hash, mime_type, web_url, etag, ctag,
    deletado, visto_em
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(drive_id, item_id) DO UPDATE SET
    biblioteca=excluded.biblioteca, pai_id=excluded.pai_id,
    caminho=excluded.caminho, pasta_pai=excluded.pasta_pai,
    nome=excluded.nome, extensao=excluded.extensao,
    eh_pasta=excluded.eh_pasta, eh_raiz=excluded.eh_raiz,
    nivel1=excluded.nivel1, nivel2=excluded.nivel2,
    profundidade=excluded.profundidade, tamanho=excluded.tamanho,
    filhos=excluded.filhos, criado_em=excluded.criado_em,
    modificado_em=excluded.modificado_em, criado_por=excluded.criado_por,
    criado_por_email=excluded.criado_por_email,
    modificado_por=excluded.modificado_por,
    modificado_por_email=excluded.modificado_por_email,
    quick_xor_hash=excluded.quick_xor_hash, sha1_hash=excluded.sha1_hash,
    sha256_hash=excluded.sha256_hash, mime_type=excluded.mime_type,
    web_url=excluded.web_url, etag=excluded.etag, ctag=excluded.ctag,
    deletado=excluded.deletado, visto_em=excluded.visto_em
"""


# ----------------------------------------------------------------------
# Varredura de uma biblioteca
# ----------------------------------------------------------------------

def varrer(g: Graph, con: sqlite3.Connection, drive: dict, cfg: dict, log: Log,
           amostra: int | None, reiniciar: bool, incremental: bool) -> dict:
    drive_id, nome = drive["id"], drive["name"]
    por_pagina = cfg["varredura"]["itens_por_pagina"]
    lote = cfg["varredura"]["commit_a_cada"]

    linha = con.execute(
        "SELECT estado, link_proximo, delta_link, itens, paginas FROM varredura"
        " WHERE drive_id=?", (drive_id,)).fetchone()

    if reiniciar:
        con.execute("DELETE FROM item_nuvem WHERE drive_id=?", (drive_id,))
        con.execute("DELETE FROM varredura WHERE drive_id=?", (drive_id,))
        con.commit()
        linha = None

    url = f"/drives/{drive_id}/root/delta?$top={por_pagina}"
    itens = paginas = 0
    retomando = False

    if linha:
        estado, link_proximo, delta_link, itens_ant, paginas_ant = linha
        if incremental and delta_link:
            url, retomando = delta_link, True
            log(f"    incremental: usando deltaLink salvo ({num(itens_ant)} itens ja catalogados)")
        elif link_proximo:
            url, itens, paginas, retomando = link_proximo, itens_ant, paginas_ant, True
            log(f"    RETOMANDO da pagina {paginas + 1} ({num(itens)} itens ja gravados)")
        elif estado == "concluida":
            log("    ja concluida (use --reiniciar ou --incremental)")
            return {"drive_id": drive_id, "nome": nome, "itens": itens_ant, "pulou": True}

    if not retomando:
        con.execute(
            "INSERT INTO varredura (drive_id, biblioteca, estado, itens, paginas, inicio)"
            " VALUES (?,?,'andamento',0,0,?)"
            " ON CONFLICT(drive_id) DO UPDATE SET estado='andamento', inicio=excluded.inicio,"
            " itens=0, paginas=0, link_proximo=NULL, mensagem=NULL",
            (drive_id, nome, agora()))
        con.commit()

    t0 = time.time()
    delta_link = None
    pendentes: list[tuple] = []

    def gravar(link_proximo: str | None, estado: str = "andamento") -> None:
        if pendentes:
            con.executemany(INSERIR, pendentes)
            pendentes.clear()
        con.execute(
            "UPDATE varredura SET estado=?, link_proximo=?, itens=?, paginas=? WHERE drive_id=?",
            (estado, link_proximo, itens, paginas, drive_id))
        con.commit()

    try:
        while True:
            pagina = g.get(url)
            paginas += 1
            valores = pagina.get("value", [])

            for item in valores:
                try:
                    pendentes.append(traduzir(item, drive_id, nome))
                    itens += 1
                except Exception as exc:                      # REGRA 5
                    registrar_erro(con, "E1/traduzir", drive_id, item.get("id"),
                                   item.get("name"), type(exc).__name__, exc)

            proxima = pagina.get("@odata.nextLink")
            delta_link = pagina.get("@odata.deltaLink") or delta_link

            if len(pendentes) >= lote:
                gravar(proxima)

            if paginas % 20 == 0 or not proxima:
                taxa = itens / max(time.time() - t0, 0.001)
                log(f"    {num(itens):>9} itens · {paginas:>4} paginas · {num(taxa)} itens/s")

            if amostra and itens >= amostra:
                gravar(proxima, "andamento")
                log(f"    AMOSTRA: parou em {num(itens)} itens (estado salvo, da para continuar)")
                return {"drive_id": drive_id, "nome": nome, "itens": itens,
                        "amostra": True, "segundos": time.time() - t0}

            if not proxima:
                break
            url = proxima

        gravar(None, "concluida")
        con.execute("UPDATE varredura SET delta_link=?, fim=? WHERE drive_id=?",
                    (delta_link, agora(), drive_id))
        con.commit()
        return {"drive_id": drive_id, "nome": nome, "itens": itens,
                "paginas": paginas, "segundos": time.time() - t0,
                "delta_link": bool(delta_link)}

    except ErroGraph as exc:
        gravar(url, "erro")
        con.execute("UPDATE varredura SET mensagem=? WHERE drive_id=?",
                    (f"{exc.codigo}: {exc.mensagem}"[:500], drive_id))
        con.commit()
        registrar_erro(con, "E1/pagina", drive_id, None, url, exc.codigo, exc.mensagem)
        log(f"    ERRO {exc.status} {exc.codigo} — progresso salvo, da para retomar")
        log(f"    {explicar(exc)}")
        return {"drive_id": drive_id, "nome": nome, "itens": itens, "erro": str(exc)}
    except KeyboardInterrupt:
        gravar(url, "andamento")
        log(f"    interrompido — {num(itens)} itens salvos, da para retomar")
        raise


# ----------------------------------------------------------------------
# Conferencia (o teste objetivo da E1)
# ----------------------------------------------------------------------

def conferir(con: sqlite3.Connection, log: Log) -> None:
    log()
    log("=" * 70)
    log("CONFERENCIA — E1")
    log("=" * 70)

    for nome, quota_usada, quota_lixeira, raiz in con.execute(
            "SELECT nome, quota_usada, quota_lixeira, raiz_tamanho FROM biblioteca"):
        log(f"  biblioteca {nome!r}: root.size={gb(raiz)}")

    quota = con.execute("SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()

    arquivos, bytes_vivos = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
        " WHERE eh_pasta=0 AND deletado=0").fetchone()
    pastas = con.execute(
        "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0").fetchone()[0]
    com_hash = con.execute(
        "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0"
        " AND quick_xor_hash IS NOT NULL").fetchone()[0]
    erros = con.execute("SELECT COUNT(*) FROM erro").fetchone()[0]

    log()
    log(f"  arquivos catalogados ....... {num(arquivos)}")
    log(f"  pastas ..................... {num(pastas)}")
    log(f"  com quickXorHash ........... {num(com_hash)}"
        f"  ({ptbr(f'{com_hash*100/max(arquivos,1):.1f}')}%)")
    log(f"  itens com erro ............. {erros}")
    log()
    log(f"  bytes de arquivos vivos .... {num(bytes_vivos)} B = {gb(bytes_vivos)}")
    log(f"  cota usada do site ......... {num(quota[0])} B = {gb(quota[0])}")
    log(f"  cota em lixeira ............ {num(quota[1])} B = {gb(quota[1])}")
    log()

    folga = (quota[0] or 0) - bytes_vivos
    log(f"  FOLGA (cota - arquivos vivos) = {gb(folga)}")
    log("    -> candidatos: historico de versoes, lixeira, metadados de lista")
    log("    -> quantificar na E3")

    log()
    esperado_arquivos, esperado_bytes = 139_459, 1.168 * 1024**4
    d_arq = (arquivos - esperado_arquivos) * 100 / esperado_arquivos
    d_byt = (bytes_vivos - esperado_bytes) * 100 / esperado_bytes
    log(f"  vs varredura local (139.459 arq · 1,168 TB):")
    log(f"    arquivos {ptbr(f'{d_arq:+.1f}')}%   bytes {ptbr(f'{d_byt:+.1f}')}%")
    ok = abs(d_arq) < 25 and abs(d_byt) < 25
    log()
    log(f"  TESTE DA E1: {'PASSOU' if ok else 'DIVERGENCIA GRANDE — investigar'}")


# ----------------------------------------------------------------------
# Principal
# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="E1 — inventario do SharePoint pela nuvem")
    ap.add_argument("--biblioteca", help="so esta biblioteca (padrao: todas)")
    ap.add_argument("--amostra", type=int, help="para apos N itens (validacao)")
    ap.add_argument("--reiniciar", action="store_true", help="descarta e recomeca")
    ap.add_argument("--incremental", action="store_true", help="usa o deltaLink salvo")
    ap.add_argument("--so-conferir", action="store_true", help="so roda a conferencia")
    args = ap.parse_args()

    cfg = ler_config()
    exigir_fase_analise(cfg)                      # REGRA 1

    banco = caminho_banco(cfg)
    log = Log(Path(cfg["logs"]) / f"inventario_{datetime.now():%Y%m%d_%H%M%S}.log")
    con = abrir_banco(banco)

    try:
        log("=" * 70)
        log("E1 — INVENTARIO PELA NUVEM (somente leitura, nenhum download)")
        log("=" * 70)
        log(f"  banco: {banco}")
        log(f"  fase : {cfg['fase']}  ·  escrita no SharePoint: {cfg['permitir_escrita']}")
        log()

        if args.so_conferir:
            conferir(con, log)
            return 0

        g = Graph()
        site_id = g.site_id()
        drives = g.bibliotecas(site_id)
        if args.biblioteca:
            drives = [d for d in drives if d["name"] == args.biblioteca]
            if not drives:
                log(f"  biblioteca {args.biblioteca!r} nao encontrada")
                return 1

        log(f"  {len(drives)} biblioteca(s) no site:")
        for d in drives:
            q = d.get("quota") or {}
            raiz = g.get(f"/drives/{d['id']}/root")
            con.execute(
                "INSERT INTO biblioteca (drive_id, nome, tipo, web_url, quota_usada,"
                " quota_total, quota_lixeira, raiz_tamanho, raiz_filhos, visto_em)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(drive_id) DO UPDATE SET quota_usada=excluded.quota_usada,"
                " quota_lixeira=excluded.quota_lixeira, raiz_tamanho=excluded.raiz_tamanho,"
                " raiz_filhos=excluded.raiz_filhos, visto_em=excluded.visto_em",
                (d["id"], d["name"], d.get("driveType"), d.get("webUrl"), q.get("used"),
                 q.get("total"), q.get("deleted"), raiz.get("size"),
                 (raiz.get("folder") or {}).get("childCount"), agora()))
            con.commit()
            log(f"    {d['name']:<14} root.size={gb(raiz.get('size')):>12}"
                f"   lixeira do site={gb(q.get('deleted'))}")

        log()
        for d in drives:
            log(f"  >> {d['name']}")
            r = varrer(g, con, d, cfg, log, args.amostra, args.reiniciar, args.incremental)
            if r.get("segundos"):
                log(f"    {num(r['itens'])} itens em {r['segundos']:.0f}s")
            log()

        conferir(con, log)
        return 0

    except ErroGraph as exc:
        log(f"\nERRO DO GRAPH: {exc}")
        log(explicar(exc))
        return 1
    except KeyboardInterrupt:
        log("\ninterrompido pelo usuario — progresso salvo")
        return 130
    finally:
        con.close()
        log.fechar()


if __name__ == "__main__":
    sys.exit(main())
