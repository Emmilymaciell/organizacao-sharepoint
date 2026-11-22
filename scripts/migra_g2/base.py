"""
base - configuracao, banco e utilitarios da operacao migracao_parceiro_g2.

Espelha migra_parceiro/base.py, mas com config, esquema, banco e saidas PROPRIOS: nada aqui abre
data/migracao_parceiro.db nem config/migracao_parceiro.yaml.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

from config_util import ler_config  # noqa: E402

CONFIG = RAIZ / "config" / "migracao_parceiro_g2.yaml"
ESQUEMA = RAIZ / "sql" / "04_migracao_parceiro_g2.sql"
BRT = timezone(timedelta(hours=-3))
FASES = ("diagnostico", "teste", "piloto", "execucao")


def agora() -> str:
    return datetime.now(BRT).isoformat(timespec="seconds")


def cfg() -> dict:
    c = ler_config(CONFIG)
    if c.get("fase") not in FASES:
        raise SystemExit(f"config/migracao_parceiro_g2.yaml: fase invalida {c.get('fase')!r}")
    if "migracao_parceiro.db" in str(c.get("banco")) and "_g2" not in str(c.get("banco")):
        raise SystemExit("ABORTADO: a operacao G2 nunca usa o banco da migracao anterior")
    return c


def pasta(chave: str, c: dict | None = None) -> Path:
    c = c or cfg()
    p = RAIZ / c[chave]
    p.mkdir(parents=True, exist_ok=True)
    return p


def saida(c: dict | None = None) -> Path:
    return pasta("saida", c)


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    caminho = Path(caminho) if caminho else RAIZ / cfg()["banco"]
    caminho.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    return con


def abrir_execucao(con: sqlite3.Connection, script: str, parametros: dict) -> int:
    cur = con.execute("INSERT INTO execucao (script, inicio, parametros) VALUES (?, ?, ?)",
                      (script, agora(), json.dumps(parametros, ensure_ascii=False)))
    con.commit()
    return cur.lastrowid


def fechar_execucao(con: sqlite3.Connection, ex_id: int, estado: str, resumo: dict) -> None:
    con.execute("UPDATE execucao SET fim=?, estado=?, resumo=? WHERE id=?",
                (agora(), estado, json.dumps(resumo, ensure_ascii=False), ex_id))
    con.commit()


def escrever_csv(caminho: Path, cabecalho: list[str], linhas) -> int:
    """CSV no padrao do projeto: separador ';' e UTF-8 com BOM."""
    n = 0
    with open(caminho, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cabecalho)
        for linha in linhas:
            w.writerow(linha)
            n += 1
    return n


def sha256_arquivo(caminho: Path) -> str:
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for b in iter(lambda: f.read(2**20), b""):
            h.update(b)
    return h.hexdigest()


# tudo o que decide ou executa uma escrita no PARCEIRO ou o VALIDADO (achado 2 da 5a revisao de QA)
CODIGO_ESCRITA = ("scripts/migra_g2/base.py", "scripts/migra_g2/dav.py", "scripts/migra_g2/envio.py",
                  "scripts/migra_g2/governador.py",
                  "scripts/367_g2_executar.py", "scripts/migra_parceiro/retomavel.py", "scripts/migra_parceiro/motor.py",
                  "scripts/migra_parceiro/quickxor.py")


def digital_codigo_escrita() -> dict:
    """SHA-256 dos modulos do caminho de escrita: o teste do motor (365) grava, o executor (367) exige igual."""
    return {f: sha256_arquivo(RAIZ / f) for f in CODIGO_ESCRITA}


def gib(b: int | float | None) -> str:
    return f"{(b or 0) / 2**30:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(n: int | None) -> str:
    return f"{n or 0:,}".replace(",", ".")


def rodovia_de(caminho: str, c: dict | None = None) -> str | None:
    c = c or cfg()
    for go, raiz in c["destino"]["arvores"].items():
        if caminho == raiz or caminho.startswith(raiz + "/"):
            return go
    return None


def escrita_permitida(caminho: str, eh_pasta: bool, c: dict | None = None) -> tuple[bool, str]:
    """Trava de escopo do motor: so dentro das quatro arvores Engenharia; Eventograma nunca;
    RPFO e DUP so pasta. Todo PUT/MKCOL passa por aqui."""
    c = c or cfg()
    caminho = unicodedata.normalize("NFC", caminho)
    if any(s in ("", ".", "..") for s in caminho.split("/")):
        return False, "segmento de caminho vazio, '.' ou '..' (achado 2 do QA do checkpoint)"
    teste = c["destino"]["pasta_teste"]
    if caminho == teste or caminho.startswith(teste + "/"):
        if c["fase"] == "teste":
            return True, ""
        return False, "pasta de teste so na fase 'teste'"
    if c["fase"] not in ("piloto", "execucao"):
        return False, f"fase {c['fase']!r} nao permite escrita nas arvores Engenharia"
    go = rodovia_de(caminho, c)
    if not go:
        return False, "fora das quatro arvores Engenharia"
    if go not in (c["destino"].get("rodovias_liberadas") or []):
        return False, f"rodovia {go} bloqueada para escrita (rodovias_liberadas)"
    raiz = c["destino"]["arvores"][go]
    rel = caminho[len(raiz):].lstrip("/")
    if not rel:
        return False, "a raiz Engenharia nao e alvo de escrita"
    for b in c["destino"]["bloqueados"]:
        if rel == b or rel.startswith(b + "/"):
            return False, f"ramo bloqueado: {b}"
    so_pasta = list(c["destino"]["so_pasta"])
    if go != "RD-102A":                     # nota 06: Fase Executoria so na RD-102A (achado A5)
        so_pasta.append("04. Desapropriação/02. Fase Executória")
    for s in so_pasta:
        if rel.startswith(s + "/") or (rel == s and not eh_pasta):
            if not (eh_pasta and rel == s):
                return False, f"ramo so admite a pasta da EAP: {s}"
    segs = rel.split("/")
    if "02. Documentação Supervisora" in segs and not (eh_pasta and segs[-1] == "02. Documentação Supervisora"):
        return False, "nota 04: Documentacao Supervisora nao recebe arquivos nesta operacao"
    return True, ""
