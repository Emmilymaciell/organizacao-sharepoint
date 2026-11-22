"""
30_duplicatas - E2: duplicatas exatas por quickXorHash.

Duplicata e HASH IGUAL (REGRA 4). Nome parecido nao entra. O hash vem do
proprio SharePoint, calculado pelo servidor — nenhum arquivo e baixado.

Este script NAO apaga e NAO decide: ele sugere qual copia manter e mostra
o espaco que seria recuperado. A decisao e da Emmily (E4).

Saidas:
    output/duplicatas.csv  — uma linha por copia, com item_id e caminho
    output/duplicatas.md   — resumo e os maiores grupos

Uso:
    python scripts\\30_duplicatas.py
    python scripts\\30_duplicatas.py --min-bytes 0     # inclui arquivos pequenos
    python scripts\\30_duplicatas.py --top 100         # grupos detalhados no .md
"""

from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


def ptbr(texto: str) -> str:
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, casas: int = 0) -> str:
    return ptbr(f"{n:,.{casas}f}")


def pct(parte, todo) -> str:
    return ptbr(f"{parte * 100 / todo:.1f}") + "%" if todo else "-"


def tamanho_legivel(n: int) -> str:
    for unidade, divisor in (("TB", 1024**4), ("GB", 1024**3),
                             ("MB", 1024**2), ("KB", 1024)):
        if n >= divisor:
            return f"{num(n / divisor, 1)} {unidade}"
    return f"{num(n)} B"


# ----------------------------------------------------------------------
# Qual copia sugerir para manter
# ----------------------------------------------------------------------

# Marcas de que o arquivo e a copia, nao o original.
MARCA_COPIA = re.compile(
    r"(\(\d+\)|\bc[oó]pia\b|\bcopy\b|\-\s*c[oó]pia|_c[oó]pia|\bduplicad)", re.I)


def pontuar(copia: dict, padroes_descarte: list[str]) -> tuple:
    """Chave de ordenacao: a MENOR pontuacao e a copia sugerida para manter.

    Criterios, nesta ordem:
      1. nao estar em pasta de descarte (backup/antigo/temp/...)
      2. nome sem marca de copia ('(1)', '- Copia')
      3. MAIS ANTIGA — quem nasceu primeiro tende a ser o original;
         a copia nasce depois, quando o material e replicado para uma
         entrega, resposta ou prestacao de contas
      4. caminho mais raso, so para desempatar

    Profundidade NAO serve como criterio de "pasta oficial": neste acervo
    as pastas de entrega (respostas a orgao, prestacao de contas) sao mais
    rasas que a pasta de projeto de onde o arquivo saiu. Qual pasta e a
    oficial e julgamento do contrato, nao do script — por isso a sugestao
    se apoia na data, que e fato, e a decisao fica com a Emmily (E4).
    """
    caminho_baixo = (copia["caminho"] or "").lower()
    em_descarte = any(p in caminho_baixo for p in padroes_descarte)
    marca = bool(MARCA_COPIA.search(copia["nome"] or ""))
    return (int(em_descarte), int(marca), copia["criado_em"] or "9999",
            copia["profundidade"] or 99, copia["caminho"] or "")


def motivo_da_sugestao(copia: dict, padroes_descarte: list[str]) -> str:
    caminho_baixo = (copia["caminho"] or "").lower()
    achados = [p for p in padroes_descarte if p in caminho_baixo]
    partes = []
    if achados:
        partes.append(f"pasta de descarte ({achados[0]})")
    if MARCA_COPIA.search(copia["nome"] or ""):
        partes.append("nome marcado como copia")
    return "; ".join(partes) or "-"


# ----------------------------------------------------------------------
# Leitura dos grupos
# ----------------------------------------------------------------------

def carregar_grupos(con: sqlite3.Connection, min_bytes: int) -> tuple[list, list]:
    """Devolve (grupos, colisoes). Grupo = mesmo hash com 2+ copias vivas."""
    con.row_factory = sqlite3.Row
    linhas = con.execute("""
        SELECT quick_xor_hash, item_id, drive_id, biblioteca, caminho, nome,
               extensao, tamanho, profundidade, criado_em, modificado_em,
               criado_por, modificado_por, web_url
          FROM item_nuvem
         WHERE eh_pasta = 0 AND deletado = 0
           AND quick_xor_hash IS NOT NULL
           AND tamanho >= ?
           AND quick_xor_hash IN (
               SELECT quick_xor_hash FROM item_nuvem
                WHERE eh_pasta = 0 AND deletado = 0
                  AND quick_xor_hash IS NOT NULL AND tamanho >= ?
                GROUP BY quick_xor_hash HAVING COUNT(*) > 1)
         ORDER BY quick_xor_hash
    """, (min_bytes, min_bytes)).fetchall()

    por_hash: dict[str, list[dict]] = {}
    for linha in linhas:
        por_hash.setdefault(linha["quick_xor_hash"], []).append(dict(linha))

    grupos, colisoes = [], []
    for hash_, copias in por_hash.items():
        tamanhos = {c["tamanho"] for c in copias}
        if len(tamanhos) > 1:
            # quickXorHash igual com tamanho diferente = colisao de hash.
            # Nao tratar como duplicata: separar e reportar.
            colisoes.append({"hash": hash_, "tamanhos": sorted(tamanhos),
                             "copias": copias})
            continue
        unitario = copias[0]["tamanho"]
        grupos.append({
            "hash": hash_,
            "copias": copias,
            "n": len(copias),
            "unitario": unitario,
            "recuperavel": unitario * (len(copias) - 1),
        })
    grupos.sort(key=lambda g: g["recuperavel"], reverse=True)
    return grupos, colisoes


# ----------------------------------------------------------------------
# Saidas
# ----------------------------------------------------------------------

def gravar_csv(grupos: list, caminho: Path, padroes: list[str]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["grupo", "hash_quickxor", "copias_no_grupo", "tamanho_unitario_bytes",
                    "tamanho_total_ocupado_bytes", "espaco_recuperavel_bytes",
                    "sugestao", "motivo", "item_id",
                    "biblioteca", "caminho", "nome", "extensao", "tamanho_bytes",
                    "criado_em", "modificado_em", "criado_por", "modificado_por",
                    "mais_antiga", "mais_recente", "web_url"])
        for i, g in enumerate(grupos, 1):
            ordenadas = sorted(g["copias"], key=lambda c: pontuar(c, padroes))
            manter = ordenadas[0]
            antiga = min(g["copias"], key=lambda c: c["criado_em"] or "9999")
            recente = max(g["copias"], key=lambda c: c["criado_em"] or "")
            for c in ordenadas:
                eh_manter = c["item_id"] == manter["item_id"]
                w.writerow([
                    i, g["hash"], g["n"], g["unitario"],
                    g["unitario"] * g["n"], g["recuperavel"],
                    "MANTER (sugerido)" if eh_manter else "candidata a remocao",
                    "-" if eh_manter else motivo_da_sugestao(c, padroes),
                    c["item_id"], c["biblioteca"], c["caminho"], c["nome"],
                    c["extensao"], c["tamanho"], c["criado_em"], c["modificado_em"],
                    c["criado_por"], c["modificado_por"],
                    "sim" if c["item_id"] == antiga["item_id"] else "nao",
                    "sim" if c["item_id"] == recente["item_id"] else "nao",
                    c["web_url"]])


def gravar_md(grupos: list, colisoes: list, caminho: Path, padroes: list[str],
              top: int, min_bytes: int, total_arquivos: int) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    total_recuperavel = sum(g["recuperavel"] for g in grupos)
    total_ocupado = sum(g["unitario"] * g["n"] for g in grupos)
    total_copias = sum(g["n"] for g in grupos)
    excedentes = sum(g["n"] - 1 for g in grupos)

    L = []
    L.append("# E2 — Duplicatas exatas")
    L.append("")
    L.append("> Duplicata aqui e **conteudo identico byte a byte**: mesmo "
             "`quickXorHash` calculado pelo proprio SharePoint. Nome parecido "
             "nao entra nesta lista.")
    L.append(">")
    L.append("> **Este relatorio nao apaga nada e nao decide nada.** Ele sugere "
             "qual copia manter e mostra o espaco envolvido.")
    L.append("")
    L.append("## Resumo")
    L.append("")
    L.append("| | |")
    L.append("|---|---:|")
    L.append(f"| Arquivos analisados | {num(total_arquivos)} |")
    L.append(f"| Tamanho minimo considerado | {tamanho_legivel(min_bytes)} |")
    L.append(f"| Grupos de duplicatas | {num(len(grupos))} |")
    L.append(f"| Arquivos envolvidos | {num(total_copias)} |")
    L.append(f"| Copias excedentes | {num(excedentes)} |")
    L.append(f"| Espaco HOJE ocupado pelas duplicatas | {tamanho_legivel(total_ocupado)} |")
    L.append(f"| Espaco se ficasse 1 copia de cada | {tamanho_legivel(total_ocupado - total_recuperavel)} |")
    L.append(f"| **Espaco MAXIMO recuperavel** | **{tamanho_legivel(total_recuperavel)}** |")
    L.append("")
    L.append("O espaco recuperavel considera **manter uma copia de cada grupo**.")
    L.append("")

    if colisoes:
        L.append("## ⚠ Colisoes de hash (NAO sao duplicatas)")
        L.append("")
        L.append(f"{len(colisoes)} hash(es) apareceram com tamanhos diferentes. "
                 "Isso e colisao do quickXorHash, nao conteudo igual. "
                 "**Foram excluidos da lista acima.**")
        L.append("")
        for c in colisoes[:20]:
            L.append(f"- `{c['hash']}` — tamanhos: "
                     f"{', '.join(num(t) for t in c['tamanhos'])} bytes")
        L.append("")

    L.append("## Por extensao")
    L.append("")
    L.append("| Extensao | Grupos | Copias excedentes | Espaco recuperavel |")
    L.append("|---|---:|---:|---:|")
    por_ext: dict[str, list[int]] = {}
    for g in grupos:
        ext = g["copias"][0]["extensao"] or "(sem extensao)"
        acc = por_ext.setdefault(ext, [0, 0, 0])
        acc[0] += 1
        acc[1] += g["n"] - 1
        acc[2] += g["recuperavel"]
    for ext, (ng, nc, esp) in sorted(por_ext.items(), key=lambda kv: -kv[1][2])[:25]:
        L.append(f"| `{ext}` | {num(ng)} | {num(nc)} | {tamanho_legivel(esp)} |")
    L.append("")

    L.append("## TOP 20 grupos por espaco recuperavel")
    L.append("")
    L.append("| # | Arquivo | Copias | Tam. unitario | Ocupa hoje | Recuperavel |")
    L.append("|---:|---|---:|---:|---:|---:|")
    for i, g in enumerate(grupos[:20], 1):
        nome = g["copias"][0]["nome"]
        L.append(f"| {i} | `{nome}` | {g['n']} | {tamanho_legivel(g['unitario'])} | "
                 f"{tamanho_legivel(g['unitario']*g['n'])} | "
                 f"**{tamanho_legivel(g['recuperavel'])}** |")
    L.append("")
    soma20 = sum(g["recuperavel"] for g in grupos[:20])
    L.append(f"Os 20 maiores grupos concentram **{tamanho_legivel(soma20)}** "
             f"({pct(soma20, total_recuperavel)}) de todo o espaco recuperavel.")
    L.append("")

    # --- consumo de duplicatas por pasta de 1o e 2o nivel -------------
    def agrega(nivel: int) -> dict:
        acc: dict[str, list] = {}
        for g in grupos:
            ordenadas = sorted(g["copias"], key=lambda c: pontuar(c, padroes))
            for pos, c in enumerate(ordenadas):
                partes = [x for x in (c["caminho"] or "").split("/") if x]
                if len(partes) <= nivel:
                    continue
                chave = "/" + "/".join(partes[:nivel])
                a = acc.setdefault(chave, [0, 0, 0])   # copias, ocupado, excedente
                a[0] += 1
                a[1] += c["tamanho"] or 0
                if pos > 0:                    # nao e a copia sugerida para manter
                    a[2] += c["tamanho"] or 0
        return acc

    for nivel, titulo in ((1, "1o"), (2, "2o")):
        acc = agrega(nivel)
        L.append(f"## Duplicatas por pasta de {titulo} nivel")
        L.append("")
        L.append("| Pasta | Copias | Espaco ocupado | Excedente (recuperavel) |")
        L.append("|---|---:|---:|---:|")
        for pasta, (qtd, ocup, exc) in sorted(acc.items(), key=lambda kv: -kv[1][1])[:25]:
            L.append(f"| `{pasta}` | {num(qtd)} | {tamanho_legivel(ocup)} | "
                     f"{tamanho_legivel(exc)} |")
        L.append("")
        L.append("> 'Excedente' = tudo menos a copia sugerida para manter em cada grupo. "
                 "A soma das linhas pode passar do total geral quando copias do mesmo "
                 "grupo estao em pastas diferentes — cada pasta aparece com o que ela "
                 "propria guarda.")
        L.append("")

    L.append("## De onde saem as copias excedentes")
    L.append("")
    L.append("Agrupado pela pasta de 1o/2o nivel de cada copia **que nao e a "
             "sugerida para manter**. Mostra qual area do acervo esta "
             "replicando material que ja existe em outro lugar.")
    L.append("")
    L.append("| Pasta | Copias excedentes | Espaco |")
    L.append("|---|---:|---:|")
    origem: dict[str, list[int]] = {}
    for g in grupos:
        ordenadas = sorted(g["copias"], key=lambda c: pontuar(c, padroes))
        for c in ordenadas[1:]:
            partes = [p for p in (c["caminho"] or "").split("/") if p]
            chave = "/" + "/".join(partes[:2]) if len(partes) > 2 else "/" + (partes[0] if partes else "?")
            acc = origem.setdefault(chave, [0, 0])
            acc[0] += 1
            acc[1] += c["tamanho"] or 0
    for pasta, (qtd, bts) in sorted(origem.items(), key=lambda kv: -kv[1][1])[:20]:
        L.append(f"| `{pasta}` | {num(qtd)} | {tamanho_legivel(bts)} |")
    L.append("")
    L.append("> **Cuidado antes de decidir.** Pasta de resposta a orgao de "
             "controle, prestacao de contas ou entrega formal pode precisar "
             "ficar intacta por motivo contratual ou juridico, mesmo sendo "
             "copia exata. Esta tabela aponta onde ha redundancia — nao "
             "afirma que pode sair.")
    L.append("")

    L.append(f"## Os {min(top, len(grupos))} maiores grupos")
    L.append("")
    for i, g in enumerate(grupos[:top], 1):
        ordenadas = sorted(g["copias"], key=lambda c: pontuar(c, padroes))
        manter = ordenadas[0]
        antiga = min(g["copias"], key=lambda c: c["criado_em"] or "9999")
        L.append(f"### {i}. {manter['nome']}")
        L.append("")
        L.append(f"- **{g['n']} copias** de {tamanho_legivel(g['unitario'])} cada")
        L.append(f"- **Recuperavel: {tamanho_legivel(g['recuperavel'])}**")
        L.append(f"- hash `{g['hash']}`")
        L.append("")
        L.append("| | Caminho | Criado em | Autor | item_id |")
        L.append("|---|---|---|---|---|")
        for c in ordenadas:
            if c["item_id"] == manter["item_id"]:
                marca = "**MANTER**"
            else:
                marca = "remover?"
            mais_antiga = " ⏱" if c["item_id"] == antiga["item_id"] else ""
            L.append(f"| {marca}{mais_antiga} | `{c['caminho']}` | "
                     f"{(c['criado_em'] or '')[:10]} | {c['criado_por'] or ''} | "
                     f"`{c['item_id']}` |")
        L.append("")
        motivos = {motivo_da_sugestao(c, padroes) for c in ordenadas
                   if c["item_id"] != manter["item_id"]} - {"-"}
        if motivos:
            L.append(f"Motivo da sugestao: {'; '.join(sorted(motivos))}")
            L.append("")
        L.append("⏱ = copia mais antiga do grupo")
        L.append("")

    L.append("---")
    L.append("")
    L.append("Lista completa, copia a copia: `output/duplicatas.csv` "
             "(separador `;`, abre no Excel).")
    L.append("")
    caminho.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="E2 — duplicatas exatas")
    ap.add_argument("--min-bytes", type=int, help="tamanho minimo (padrao: config)")
    ap.add_argument("--top", type=int, default=40, help="grupos detalhados no .md")
    args = ap.parse_args()

    cfg = ler_config()
    exigir_fase_analise(cfg)

    min_bytes = (args.min_bytes if args.min_bytes is not None
                 else cfg["analise"]["duplicata_min_bytes"])
    padroes = [p.lower() for p in cfg["analise"]["padroes_descarte"]]
    saida = Path(cfg["saida"])

    con = sqlite3.connect(caminho_banco(cfg))
    try:
        total_arquivos = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=0 AND deletado=0").fetchone()[0]
        if not total_arquivos:
            print("Catalogo vazio. Rodar primeiro: python scripts\\20_inventario.py")
            return 1

        print(f"  analisando {num(total_arquivos)} arquivos "
              f"(minimo {tamanho_legivel(min_bytes)})...")
        grupos, colisoes = carregar_grupos(con, min_bytes)

        gravar_csv(grupos, saida / "duplicatas.csv", padroes)
        gravar_md(grupos, colisoes, saida / "duplicatas.md", padroes,
                  args.top, min_bytes, total_arquivos)

        recuperavel = sum(g["recuperavel"] for g in grupos)
        excedentes = sum(g["n"] - 1 for g in grupos)
        print()
        print("=" * 62)
        print(f"  grupos de duplicatas ..... {num(len(grupos))}")
        print(f"  copias excedentes ........ {num(excedentes)}")
        print(f"  ESPACO RECUPERAVEL ....... {tamanho_legivel(recuperavel)}")
        if colisoes:
            print(f"  colisoes de hash ......... {len(colisoes)} (excluidas, ver .md)")
        print("=" * 62)
        print(f"  {saida / 'duplicatas.csv'}")
        print(f"  {saida / 'duplicatas.md'}")
        print()
        print("  Nada foi apagado. O relatorio sugere; a decisao e sua (E4).")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
