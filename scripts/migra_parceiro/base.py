"""
base - banco, configuracao e utilitarios comuns do modulo migra_parceiro.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "scripts"))

from config_util import ler_config  # noqa: E402

CONFIG = RAIZ / "config" / "migracao_parceiro.yaml"
ESQUEMA = RAIZ / "sql" / "03_migracao_parceiro.sql"
BRT = timezone(timedelta(hours=-3))


def agora() -> str:
    return datetime.now(BRT).isoformat(timespec="seconds")


def cfg() -> dict:
    return ler_config(CONFIG)


def saida(c: dict | None = None) -> Path:
    c = c or cfg()
    pasta = RAIZ / c["saida"]
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    caminho = Path(caminho) if caminho else RAIZ / cfg()["banco"]
    caminho.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: o motor usa a conexao em varias threads, sempre sob um Lock proprio
    con = sqlite3.connect(caminho, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    return con


def abrir_execucao(con: sqlite3.Connection, script: str, parametros: dict) -> int:
    cur = con.execute(
        "INSERT INTO execucao (script, inicio, parametros) VALUES (?, ?, ?)",
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


def gib(b: int | float | None) -> str:
    return f"{(b or 0) / 2**30:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def num(n: int | None) -> str:
    return f"{n or 0:,}".replace(",", ".")
