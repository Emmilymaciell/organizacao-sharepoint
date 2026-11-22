"""
332_migra_inventario_destino - estrutura atual do PARCEIRO INFRA, lida pela
unidade montada (I:\\), para a frente de migracao.

SOMENTE LEITURA: os.scandir (listagem de diretorio). Nao abre arquivos,
nao cria, nao renomeia, nao apaga. Pastas em `destino.nao_varrer` do
config (ex.: PARCEIRO ADMINISTRATIVO, 336 GB de terceiros) so tem o 1o nivel
registrado.

A montagem do app PARCEIRO INFRA lista via WebDAV com cache de diretorio de
30 s: a varredura e lenta (~0,7 s por pasta) mas nao baixa conteudo.

Grava destino_item em data/migracao_parceiro.db e
output/migracao_parceiro/destino_inventario.csv.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_parceiro.base import (BRT, abrir_execucao, agora, cfg, conectar,  # noqa: E402
                             escrever_csv, fechar_execucao, gib, num, saida)


def main() -> int:
    c = cfg()
    raiz = c["destino"]["montagem"]
    nao_varrer = set(c["destino"].get("nao_varrer") or [])
    if not os.path.isdir(raiz):
        print(f"ERRO: {raiz} nao esta montado. Abrir o app PARCEIRO INFRA (Conectar).")
        return 1

    con = conectar()
    ex_id = abrir_execucao(con, Path(__file__).name, {"raiz": raiz, "nao_varrer": sorted(nao_varrer)})
    quando = agora()
    inicio = time.time()
    linhas, erros = [], []
    pilha = [""]
    while pilha:
        rel = pilha.pop()
        try:
            entradas = list(os.scandir(os.path.join(raiz, rel)))
        except OSError as e:
            erros.append((rel, str(e)))
            continue
        for e in entradas:
            r = f"{rel}/{e.name}" if rel else e.name
            st = e.stat(follow_symlinks=False)
            pasta = e.is_dir(follow_symlinks=False)
            mod = datetime.fromtimestamp(st.st_mtime, BRT).isoformat(timespec="seconds")
            linhas.append((r, int(pasta), None if pasta else st.st_size, mod))
            if pasta and not (rel == "" and e.name in nao_varrer):
                pilha.append(r)

    con.execute("DELETE FROM destino_item")
    con.executemany("INSERT INTO destino_item (caminho, eh_pasta, tamanho, modificado_em, "
                    "inventariado_em, execucao_id) VALUES (?,?,?,?,?,?)",
                    [(*l, quando, ex_id) for l in linhas])
    con.commit()
    escrever_csv(saida(c) / "destino_inventario.csv",
                 ["caminho", "eh_pasta", "bytes", "modificado_em"], sorted(linhas))
    pastas = sum(l[1] for l in linhas)
    arquivos = len(linhas) - pastas
    b = sum(l[2] or 0 for l in linhas)
    resumo = {"pastas": pastas, "arquivos": arquivos, "bytes": b, "erros": len(erros),
              "amostra_erros": erros[:5], "segundos": round(time.time() - inicio, 1)}
    fechar_execucao(con, ex_id, "concluida" if not erros else "erro", resumo)
    print(f"Destino: {num(pastas)} pastas · {num(arquivos)} arquivos · {gib(b)} GiB · "
          f"erros: {len(erros)} · {resumo['segundos']} s")
    return 0 if not erros else 1


if __name__ == "__main__":
    raise SystemExit(main())
