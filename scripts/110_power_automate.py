"""
110_power_automate - gera os artefatos para a transferencia.

NAO MOVE NADA. So le o plano e produz arquivos de apoio.

Saidas:
    output/pa_lista_pastas.json     array [{origem, destino}] para colar no fluxo
    output/pa_lista_pastas.csv      mesma lista, para conferencia no Excel
    output/MAPA_RENOMEACAO.md       de-para dos nomes de pasta raiz
    output/PASSO_A_PASSO_POWER_AUTOMATE.md
    data/conferencia_esperada.json  hash de cada arquivo, para validar depois

Uso:
    python scripts\\110_power_automate.py
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GiB = 1024**3
SITE_ORIGEM = "https://contoso.sharepoint.com/sites/exemplo"
BIBLIOTECA = "Programa"

# nome curto, sem acento e sem caractere especial, para a raiz no OneDrive
RENOMEAR = {
    "04_Resposta Prestacao de Contas - Trimestral - Programa": "PC Trimestral",
    "05_Resposta Prestação de Contas  - Mensal - Programa": "PC Mensal",
    "06_Resposta Prestação de Contas Anual - Programa": "PC Anual",
    "07_Resposta Prestação de Contas - Obras": "PC Obras",
}


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def gib(b): return f"{num((b or 0)/GiB, 2)} GiB"


def destino_de(linha: dict) -> str:
    """Caminho de destino dentro de 'Meus arquivos' do OneDrive."""
    origem, frente = linha["origem"], linha["frente"]
    if frente == "1-PROJETOS":
        return linha["destino"]                   # PE<rod>/<estrutura>
    if frente == "2-PRODUTOS":
        return f"Produtos/{linha['pasta']}"
    # 3-INTERNO: raiz renomeada + o que vier depois dela
    partes = [p for p in origem.split("/") if p]
    raiz = partes[1] if len(partes) > 1 else ""
    novo = RENOMEAR.get(raiz, raiz)
    resto = "/".join(partes[2:])
    return f"{novo}/{resto}" if resto else novo


def main() -> int:
    cfg = ler_config()
    exigir_fase_analise(cfg)
    saida = Path(cfg["saida"])
    con = sqlite3.connect(caminho_banco(cfg))
    try:
        with (saida / "PLANO_TRANSFERENCIA.csv").open(encoding="utf-8-sig") as fh:
            plano = [r for r in csv.DictReader(fh, delimiter=";")]
        mover = [r for r in plano if r["acao"] == "MOVER"]

        itens = []
        for r in mover:
            itens.append({
                "origem": r["origem"].lstrip("/"),
                "destino": destino_de(r),
                "arquivos": int(r["arquivos"]),
                "bytes": int(r["bytes"]),
                "frente": r["frente"],
            })
        # Remove pastas ANINHADAS: se o pai ja esta na lista, copiar o filho
        # de novo duplicaria os arquivos no destino. O "Copiar pasta" leva a
        # subarvore inteira.
        itens.sort(key=lambda x: x["origem"])
        origens = [i["origem"] for i in itens]
        aninhados = {b for a in origens for b in origens if b.startswith(a + "/")}
        if aninhados:
            for a in sorted(aninhados):
                print(f"  [aninhada, removida da lista] {a[-72:]}")
            itens = [i for i in itens if i["origem"] not in aninhados]
        itens.sort(key=lambda x: (x["frente"], x["destino"]))

        (saida / "pa_lista_pastas.json").write_text(
            json.dumps([{"origem": i["origem"], "destino": i["destino"]}
                        for i in itens], ensure_ascii=False, indent=2),
            encoding="utf-8")

        with (saida / "pa_lista_pastas.csv").open("w", encoding="utf-8-sig",
                                                  newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["ordem", "frente", "origem_sharepoint",
                        "destino_onedrive", "arquivos", "bytes", "gib"])
            for n, i in enumerate(itens, 1):
                w.writerow([n, i["frente"], i["origem"], i["destino"],
                            i["arquivos"], i["bytes"],
                            ptbr(f"{i['bytes']/GiB:.3f}")])

        # ---- conferencia: hash de cada arquivo que deve chegar -------
        conf = {}
        for i in itens:
            cam = "/" + i["origem"]
            for c, nome, tamanho, h in con.execute(
                    "SELECT caminho, nome, tamanho, quick_xor_hash FROM item_nuvem"
                    " WHERE eh_pasta=0 AND deletado=0 AND (caminho=? OR caminho LIKE ?)",
                    (cam, cam + "/%")):
                conf[c] = {"nome": nome, "bytes": tamanho, "hash": h,
                           "destino": i["destino"]}
        Path(cfg["banco"]).parent.joinpath("conferencia_esperada.json").write_text(
            json.dumps(conf, ensure_ascii=False), encoding="utf-8")

        # ---- mapa de renomeacao --------------------------------------
        L = ["# Mapa de renomeação das pastas raiz", "",
             "Nomes curtos, sem acento e sem caractere especial, para o "
             "OneDrive de destino.", "",
             "| Origem no SharePoint | Destino em *Meus arquivos* |",
             "|---|---|"]
        for velho, novo in RENOMEAR.items():
            L.append(f"| `2. Compartilhado interno/{velho}` | **`{novo}`** |")
        L += ["", "As pastas de projeto vão para as PE já existentes, "
              "mantendo a estrutura interna:", "",
              "| Rodovia | Pasta no OneDrive |", "|---|---|",
              "| RD102A | `PE102A` |", "| RD103 | `PE103` |",
              "| RD101 | `PE101` |", "| RD104 | `PE104` |", "",
              "Produtos vão para `Produtos/<mês>`, mantendo o nome.", ""]
        (saida / "MAPA_RENOMEACAO.md").write_text("\n".join(L), encoding="utf-8")

        tot_a = sum(i["arquivos"] for i in itens)
        tot_b = sum(i["bytes"] for i in itens)
        por_frente = {}
        for i in itens:
            a = por_frente.setdefault(i["frente"], [0, 0, 0])
            a[0] += 1; a[1] += i["arquivos"]; a[2] += i["bytes"]

        print("=" * 66)
        print("ARTEFATOS PARA O POWER AUTOMATE (nada foi movido)")
        print("=" * 66)
        for f, (n, a, b) in sorted(por_frente.items()):
            print(f"  {f:<12} {n:>4} pastas  {a:>7,} arq  {gib(b):>12}")
        print(f"  {'TOTAL':<12} {len(itens):>4} pastas  {tot_a:>7,} arq  {gib(tot_b):>12}")
        print()
        print(f"  arquivos no mapa de conferencia: {len(conf):,}")
        for p in ("pa_lista_pastas.json", "pa_lista_pastas.csv",
                  "MAPA_RENOMEACAO.md"):
            print(f"  {saida/p}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
