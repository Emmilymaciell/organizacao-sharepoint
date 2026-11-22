"""
50_consumo - E3: onde esta o 1,3 TB.

Junta tudo que o catalogo sabe e responde com numero:
  a) consumo por pasta de 1o e 2o nivel
  b) quanto e HISTORICO DE VERSOES      (de 40_versoes.py, se ja rodou)
  c) quanto e LIXEIRA                    (quota.deleted do site)
  d) quanto e arquivo pesado sem valor documental (.zip, .dav, video, .bak)
  e) quanto e material superado          (pastas com padrao de descarte)

Saidas:
    output/consumo.md
    output/consumo_por_pasta.csv

Somente leitura: este script nao chama a API, so le o SQLite.

Uso:
    python scripts\\50_consumo.py
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

GB = 1024**3


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, casas: int = 0) -> str:
    return ptbr(f"{n:,.{casas}f}")


def tam(n) -> str:
    """Tamanho legivel. O acervo todo e da ordem de centenas de GB, entao
    so vira TB acima de 10 TB — senao '1,4 TB' esconderia a diferenca
    entre 1.384 GB e 1.194 GB, que e justamente o que se quer enxergar."""
    n = n or 0
    if abs(n) >= 10 * 1024**4:
        return f"{num(n / 1024**4, 2)} TB"
    for unidade, divisor in ((" GB", GB), (" MB", 1024**2), (" KB", 1024)):
        if abs(n) >= divisor:
            return f"{num(n / divisor, 1)}{unidade}"
    return f"{num(n)} B"


def pct(parte, todo) -> str:
    return ptbr(f"{(parte * 100 / todo):.1f}") + "%" if todo else "-"


# ----------------------------------------------------------------------
# Consultas
# ----------------------------------------------------------------------

def por_nivel(con, coluna: str, limite: int | None = None) -> list[tuple]:
    """Consumo agrupado por pasta de 1o ou 2o nivel.

    Arquivo solto na raiz da biblioteca tem profundidade 1: o 'nivel1' dele
    e o proprio nome do arquivo, nao uma pasta. Esses vao para '(raiz)'.
    """
    extra = f"LIMIT {limite}" if limite else ""
    if coluna == "nivel1":
        campos = "CASE WHEN profundidade <= 1 THEN '(raiz da biblioteca)' ELSE nivel1 END"
        filtro = ""
    else:
        campos = "nivel1, nivel2"
        filtro = "AND nivel2 IS NOT NULL AND profundidade > 2"
    return con.execute(f"""
        SELECT {campos}, COUNT(*), COALESCE(SUM(tamanho),0)
          FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND nivel1 IS NOT NULL {filtro}
         GROUP BY {campos}
         ORDER BY SUM(tamanho) DESC {extra}""").fetchall()


def por_extensao(con, extensoes: list[str]) -> list[tuple]:
    marcas = ",".join("?" * len(extensoes))
    return con.execute(f"""
        SELECT extensao, COUNT(*), COALESCE(SUM(tamanho),0)
          FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND extensao IN ({marcas})
         GROUP BY extensao ORDER BY SUM(tamanho) DESC""", extensoes).fetchall()


def todas_extensoes(con, limite: int = 25) -> list[tuple]:
    return con.execute("""
        SELECT COALESCE(extensao,'(sem extensao)'), COUNT(*), COALESCE(SUM(tamanho),0)
          FROM item_nuvem WHERE eh_pasta=0 AND deletado=0
         GROUP BY extensao ORDER BY SUM(tamanho) DESC LIMIT ?""", (limite,)).fetchall()


def superados(con, padroes: list[str]) -> tuple[list, int, int]:
    """Arquivos sob pasta com padrao de descarte no caminho."""
    detalhe, vistos = [], set()
    arquivos = bytes_ = 0
    for p in padroes:
        linha = con.execute("""
            SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem
             WHERE eh_pasta=0 AND deletado=0 AND LOWER(pasta_pai) LIKE ?""",
                            (f"%{p}%",)).fetchone()
        if linha[0]:
            detalhe.append((p, linha[0], linha[1]))
    # total sem contar o mesmo arquivo duas vezes
    condicao = " OR ".join(["LOWER(pasta_pai) LIKE ?"] * len(padroes))
    linha = con.execute(f"""
        SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem
         WHERE eh_pasta=0 AND deletado=0 AND ({condicao})""",
                        [f"%{p}%" for p in padroes]).fetchone()
    arquivos, bytes_ = linha
    detalhe.sort(key=lambda d: -d[2])
    return detalhe, arquivos, bytes_


def maiores_arquivos(con, limite: int = 30) -> list[tuple]:
    return con.execute("""
        SELECT caminho, nome, extensao, tamanho, criado_por, modificado_em, item_id
          FROM item_nuvem WHERE eh_pasta=0 AND deletado=0
         ORDER BY tamanho DESC LIMIT ?""", (limite,)).fetchall()


def maiores_pastas(con, limite: int = 30) -> list[tuple]:
    """Pastas que mais consomem, em qualquer profundidade (pelo pasta_pai)."""
    return con.execute("""
        SELECT pasta_pai, COUNT(*), SUM(tamanho)
          FROM item_nuvem WHERE eh_pasta=0 AND deletado=0
         GROUP BY pasta_pai ORDER BY SUM(tamanho) DESC LIMIT ?""", (limite,)).fetchall()


CATEGORIAS_PESADAS = {
    "Compactados":  (".zip", ".rar", ".7z", ".gz", ".tar"),
    "Video":        (".mp4", ".mov", ".avi", ".dav", ".mkv", ".wmv"),
    "Backup/imagem de disco": (".bak", ".iso", ".vhd", ".vhdx"),
    "Temporarios":  (".tmp", ".temp", ".~tmp", ".part"),
    "Exportacao/BI": (".pbix", ".csv", ".xml", ".json"),
}


def por_categoria_pesada(con) -> list[tuple]:
    saida = []
    for rotulo, exts in CATEGORIAS_PESADAS.items():
        marcas = ",".join("?" * len(exts))
        qtd, bts = con.execute(
            f"""SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND extensao IN ({marcas})""",
            exts).fetchone()
        maiores = con.execute(
            f"""SELECT caminho, tamanho FROM item_nuvem
                 WHERE eh_pasta=0 AND deletado=0 AND extensao IN ({marcas})
                 ORDER BY tamanho DESC LIMIT 3""", exts).fetchall()
        saida.append((rotulo, ", ".join(exts), qtd, bts, maiores))
    return sorted(saida, key=lambda x: -x[3])


def versoes_por_arquivo(con, limite: int = 30) -> list[tuple]:
    """Os arquivos que mais acumularam historico — alvo direto da E4.

    Liberar versao antiga nao apaga o documento: a versao corrente fica.
    """
    try:
        return con.execute("""
            SELECT i.caminho, i.nome, v.n_versoes, v.bytes_atual, v.bytes_antigas,
                   i.modificado_por, i.item_id
              FROM versao_resumo v
              JOIN item_nuvem i ON i.drive_id = v.drive_id AND i.item_id = v.item_id
             WHERE v.erro IS NULL AND v.bytes_antigas > 0
             ORDER BY v.bytes_antigas DESC LIMIT ?""", (limite,)).fetchall()
    except sqlite3.OperationalError:
        return []


def versoes_por_pasta(con, limite: int = 20) -> list[tuple]:
    """Historico de versoes agregado por pasta de 1o/2o nivel."""
    try:
        return con.execute("""
            SELECT i.nivel1, i.nivel2, COUNT(*), SUM(v.bytes_antigas)
              FROM versao_resumo v
              JOIN item_nuvem i ON i.drive_id = v.drive_id AND i.item_id = v.item_id
             WHERE v.erro IS NULL AND v.bytes_antigas > 0 AND i.nivel2 IS NOT NULL
             GROUP BY i.nivel1, i.nivel2
             ORDER BY SUM(v.bytes_antigas) DESC LIMIT ?""", (limite,)).fetchall()
    except sqlite3.OperationalError:
        return []


def versoes(con) -> dict | None:
    try:
        linhas = con.execute("""
            SELECT estrato, arquivos_total, bytes_total, arquivos_medidos,
                   bytes_medidos, bytes_antigas_medidos
              FROM versao_estrato ORDER BY estrato""").fetchall()
    except sqlite3.OperationalError:
        return None
    if not any(l[3] for l in linhas):
        return None

    total = medido = 0
    faixas = []
    for estrato, arq_tot, b_tot, arq_med, b_med, antigas_med in linhas:
        if not arq_med:
            faixas.append((estrato, arq_tot, b_tot, arq_med, None, False))
            continue
        completo = arq_med >= arq_tot
        antigas = antigas_med if completo else (
            (antigas_med / b_med * b_tot) if b_med else 0)
        total += antigas
        medido += antigas_med
        faixas.append((estrato, arq_tot, b_tot, arq_med, antigas, completo))
    return {"total": total, "medido": medido, "faixas": faixas}


# ----------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="E3 — analise de consumo")
    ap.add_argument("--top-pastas", type=int, default=40)
    args = ap.parse_args()

    cfg = ler_config()
    exigir_fase_analise(cfg)

    saida = Path(cfg["saida"])
    saida.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(caminho_banco(cfg))

    try:
        arquivos, vivos = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tamanho),0) FROM item_nuvem"
            " WHERE eh_pasta=0 AND deletado=0").fetchone()
        if not arquivos:
            print("Catalogo vazio. Rodar antes: python scripts\\20_inventario.py")
            return 1

        pastas = con.execute(
            "SELECT COUNT(*) FROM item_nuvem WHERE eh_pasta=1 AND deletado=0").fetchone()[0]
        quota_usada, lixeira = con.execute(
            "SELECT MAX(quota_usada), MAX(quota_lixeira) FROM biblioteca").fetchone()
        folga = (quota_usada or 0) - vivos

        n1 = por_nivel(con, "nivel1")
        n2 = por_nivel(con, "nivel2")
        pesadas = por_extensao(con, cfg["analise"]["extensoes_pesadas"])
        ext_todas = todas_extensoes(con)
        padroes = [p.lower() for p in cfg["analise"]["padroes_descarte"]]
        sup_detalhe, sup_arquivos, sup_bytes = superados(con, padroes)
        ver = versoes(con)
        bytes_pesadas = sum(p[2] for p in pesadas)

        # ---------------- CSV por pasta ----------------
        caminho_csv = saida / "consumo_por_pasta.csv"
        with caminho_csv.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["nivel", "pasta_nivel1", "pasta_nivel2", "arquivos",
                        "bytes", "gb", "pct_do_acervo"])
            for nome, qtd, bts in n1:
                w.writerow([1, nome, "", qtd, bts, f"{bts/GB:.2f}".replace(".", ","),
                            f"{bts*100/vivos:.2f}".replace(".", ",")])
            for a, b, qtd, bts in n2:
                w.writerow([2, a, b, qtd, bts, f"{bts/GB:.2f}".replace(".", ","),
                            f"{bts*100/vivos:.2f}".replace(".", ",")])

        # ---------------- Relatorio ----------------
        L: list[str] = []
        ad = L.append
        ad("# E3 — Analise de consumo")
        ad("")
        ad("> Todos os numeros vem do catalogo montado pela API do SharePoint "
           "(`data/organizacao.db`). Nenhum arquivo foi baixado e **nada foi apagado**.")
        ad("")
        ad("## 1. O fechamento da cota")
        ad("")
        ad("| | Bytes | |")
        ad("|---|---:|---:|")
        ad(f"| Cota usada do site | {num(quota_usada)} | **{tam(quota_usada)}** |")
        ad(f"| Arquivos vivos (catalogados) | {num(vivos)} | {tam(vivos)} |")
        ad(f"| **Folga a explicar** | {num(folga)} | **{tam(folga)}** |")
        ad("")
        ad(f"O acervo tem **{num(arquivos)} arquivos** e {num(pastas)} pastas.")
        ad("")
        ad("A folga e o espaco que o SharePoint cobra mas que **nao corresponde a "
           "nenhum arquivo vivo**. Ela se divide em:")
        ad("")
        ad("| Componente | Espaco | % da folga |")
        ad("|---|---:|---:|")
        if ver:
            ad(f"| Historico de versoes | {tam(ver['total'])} | {pct(ver['total'], folga)} |")
        else:
            ad("| Historico de versoes | *(rodar `40_versoes.py`)* | - |")
        ad(f"| Lixeira (`quota.deleted`) | {tam(lixeira)} | {pct(lixeira, folga)} |")
        se_conhecido = (ver["total"] if ver else 0) + (lixeira or 0)
        ad(f"| Nao explicado | {tam(folga - se_conhecido)} | {pct(folga - se_conhecido, folga)} |")
        ad("")
        ad("> **Versao antiga e lixeira nao sao documento.** Liberar esse espaco "
           "nao apaga nenhum arquivo do acervo — e a primeira coisa a olhar.")
        ad("")
        nao_expl = folga - se_conhecido
        if nao_expl > 1 * GB:
            ad(f"#### Sobre os {tam(nao_expl)} nao explicados")
            ad("")
            ad("Nao foi possivel atribuir essa parcela com o acesso atual. "
               "O que ficou de fora do alcance da medicao:")
            ad("")
            ad("- **Lixeira detalhada nao e exposta pelo Microsoft Graph** "
               "(`/recycleBin` responde `400 BadRequest` na v1.0). O unico "
               "numero disponivel e `quota.deleted`, que pode nao incluir a "
               "lixeira de segundo estagio do site.")
            ad("- **Versoes de arquivos que ja estao na lixeira** nao entram "
               "no catalogo de itens vivos.")
            ad("- **Overhead de metadados/indice** dos itens de lista.")
            ad("- Margem da extrapolacao das faixas D e E, que seguem "
               "amostradas.")
            ad("")
            ad("Ja foram **descartados por medicao direta**: Preservation Hold "
               "Library (nao existe no site), sub-sites (nenhum) e outra "
               "biblioteca com conteudo (`Documentos` tem 3,2 MB).")
            ad("")
            ad(f"A parcela representa {pct(nao_expl, quota_usada)} da cota total "
               "e nao muda a ordem de prioridade da limpeza.")
            ad("")

        if ver:
            ad("### 1.1 Como o historico foi medido")
            ad("")
            ad("| Faixa | Arquivos | Vivos | Medidos | Versoes antigas |")
            ad("|---|---:|---:|---:|---:|")
            for estrato, arq_tot, b_tot, arq_med, antigas, completo in ver["faixas"]:
                if antigas is None:
                    ad(f"| {estrato} | {num(arq_tot)} | {tam(b_tot)} | 0 | *nao medido* |")
                else:
                    marca = "" if completo else " *(extrapolado)*"
                    ad(f"| {estrato} | {num(arq_tot)} | {tam(b_tot)} | "
                       f"{num(arq_med)} | {tam(antigas)}{marca} |")
            ad("")
            ad(f"Medicao direta somou {tam(ver['medido'])}; as faixas amostradas "
               "foram extrapoladas pela razao medida na propria faixa.")
            ad("")

            v_pasta = versoes_por_pasta(con)
            if v_pasta:
                ad("### 1.2 Onde o historico se concentra")
                ad("")
                ad("Entre os arquivos ja medidos, por pasta de 2o nivel.")
                ad("")
                ad("| Pasta | Arquivos com historico | Versoes antigas |")
                ad("|---|---:|---:|")
                for a, b, qtd, bts in v_pasta:
                    ad(f"| `/{a}/{b}` | {num(qtd)} | {tam(bts)} |")
                ad("")

            v_arq = versoes_por_arquivo(con)
            if v_arq:
                ad("### 1.3 Arquivos que mais acumularam versao")
                ad("")
                ad("Alvo mais seguro da limpeza: **liberar versao antiga nao "
                   "apaga o documento** — a versao corrente permanece.")
                ad("")
                ad("| Arquivo | Versoes | Atual | Versoes antigas | item_id |")
                ad("|---|---:|---:|---:|---|")
                for caminho, nome, nv, atual, antigas, _quem, item_id in v_arq:
                    ad(f"| `{caminho}` | {num(nv)} | {tam(atual)} | "
                       f"**{tam(antigas)}** | `{item_id}` |")
                ad("")

        ad("## 2. Onde esta o espaco — pastas de 1o nivel")
        ad("")
        ad("| Pasta | Arquivos | Espaco | % |")
        ad("|---|---:|---:|---:|")
        for nome, qtd, bts in n1:
            ad(f"| `{nome}` | {num(qtd)} | {tam(bts)} | {pct(bts, vivos)} |")
        ad(f"| **TOTAL** | **{num(arquivos)}** | **{tam(vivos)}** | 100% |")
        ad("")

        ad(f"## 3. Pastas de 2o nivel — as {min(args.top_pastas, len(n2))} maiores")
        ad("")
        ad("| Pasta | Arquivos | Espaco | % |")
        ad("|---|---:|---:|---:|")
        for a, b, qtd, bts in n2[:args.top_pastas]:
            ad(f"| `/{a}/{b}` | {num(qtd)} | {tam(bts)} | {pct(bts, vivos)} |")
        ad("")
        ad(f"Lista completa: `output/consumo_por_pasta.csv` ({num(len(n2))} pastas).")
        ad("")

        ad("## 3.1 Maiores pastas do acervo (qualquer profundidade)")
        ad("")
        ad("| Pasta | Arquivos | Espaco |")
        ad("|---|---:|---:|")
        for pasta, qtd, bts in maiores_pastas(con, 25):
            ad(f"| `{pasta}` | {num(qtd)} | {tam(bts)} |")
        ad("")

        ad("## 3.2 Maiores arquivos do acervo")
        ad("")
        ad("| Arquivo | Tamanho | Modificado | item_id |")
        ad("|---|---:|---|---|")
        for caminho, nome, ext, bts, autor, modif, iid in maiores_arquivos(con, 25):
            ad(f"| `{caminho}` | **{tam(bts)}** | {(modif or '')[:10]} | `{iid}` |")
        ad("")

        ad("## 4. Arquivos pesados sem valor documental")
        ad("")
        ad("Extensoes que costumam pesar sem serem o documento em si "
           "(compactados, video, backup, imagem de disco).")
        ad("")
        ad("| Extensao | Arquivos | Espaco | % do acervo |")
        ad("|---|---:|---:|---:|")
        for ext, qtd, bts in pesadas:
            ad(f"| `{ext}` | {num(qtd)} | {tam(bts)} | {pct(bts, vivos)} |")
        ad(f"| **TOTAL** | **{num(sum(p[1] for p in pesadas))}** | "
           f"**{tam(bytes_pesadas)}** | **{pct(bytes_pesadas, vivos)}** |")
        ad("")
        ad("> Atencao: `.zip` pode ser entrega contratual compactada e `.dav` pode "
           "ser gravacao de reuniao com valor. Esta tabela **nao e lista de "
           "exclusao** — e onde olhar primeiro.")
        ad("")

        ad("### 4.1 Por categoria de formato")
        ad("")
        ad("| Categoria | Extensoes | Arquivos | Espaco | Maior arquivo |")
        ad("|---|---|---:|---:|---|")
        for rotulo, exts, qtd, bts, maiores in por_categoria_pesada(con):
            maior = f"`{maiores[0][0][-58:]}` ({tam(maiores[0][1])})" if maiores else "-"
            ad(f"| **{rotulo}** | `{exts}` | {num(qtd)} | {tam(bts)} | {maior} |")
        ad("")
        ad("> `.csv`, `.xml` e `.json` aparecem como *exportacao* por serem "
           "formatos de saida de sistema, mas **podem ser dado primario** "
           "(topografia, GNSS). Nao sao lixo por definicao.")
        ad("")

        ad("## 5. Material superado (padrao de descarte no caminho)")
        ad("")
        ad("| Padrao na pasta | Arquivos | Espaco |")
        ad("|---|---:|---:|")
        for p, qtd, bts in sup_detalhe:
            ad(f"| `{p}` | {num(qtd)} | {tam(bts)} |")
        ad(f"| **TOTAL (sem repetir arquivo)** | **{num(sup_arquivos)}** | "
           f"**{tam(sup_bytes)}** |")
        ad("")
        ad("> Os padroes se sobrepoem (um caminho pode ter 'backup' e 'antigo'); "
           "por isso o total nao e a soma das linhas.")
        ad("")

        ad("## 6. Maiores extensoes do acervo (todas)")
        ad("")
        ad("| Extensao | Arquivos | Espaco | % |")
        ad("|---|---:|---:|---:|")
        for ext, qtd, bts in ext_todas:
            ad(f"| `{ext}` | {num(qtd)} | {tam(bts)} | {pct(bts, vivos)} |")
        ad("")

        ad("---")
        ad("")
        ad("## Resumo executivo")
        ad("")
        ad(f"- A cota cobra **{tam(quota_usada)}**; os arquivos vivos somam "
           f"**{tam(vivos)}**.")
        if ver:
            ad(f"- **{tam(ver['total'])}** e historico de versoes — espaco que se "
               "libera **sem apagar documento nenhum**.")
        ad(f"- **{tam(lixeira)}** esta na lixeira.")
        ad(f"- **{tam(bytes_pesadas)}** sao arquivos pesados sem valor documental "
           "obvio (a revisar caso a caso).")
        ad(f"- **{tam(sup_bytes)}** estao em pastas com nome de descarte "
           "(a revisar caso a caso).")
        ad("- Duplicatas exatas: ver `output/duplicatas.md`.")
        ad("")
        ad("Nada foi apagado. A lista priorizada do que pode sair e a E4, "
           "e depende da sua aprovacao.")
        ad("")

        (saida / "consumo.md").write_text("\n".join(L), encoding="utf-8")

        # ---------------- Console ----------------
        print("=" * 72)
        print("E3 — ANALISE DE CONSUMO")
        print("=" * 72)
        print(f"  cota usada ............. {tam(quota_usada)}")
        print(f"  arquivos vivos ......... {tam(vivos)}  ({num(arquivos)} arquivos)")
        print(f"  folga a explicar ....... {tam(folga)}")
        print()
        if ver:
            print(f"  historico de versoes ... {tam(ver['total'])}   "
                  f"({pct(ver['total'], folga)} da folga)")
        else:
            print("  historico de versoes ... nao medido (rodar 40_versoes.py)")
        print(f"  lixeira ................ {tam(lixeira)}")
        print(f"  nao explicado .......... {tam(folga - se_conhecido)}")
        print()
        print(f"  arquivos pesados ....... {tam(bytes_pesadas)}")
        print(f"  material superado ...... {tam(sup_bytes)}")
        print()
        print("  pastas de 1o nivel:")
        for nome, qtd, bts in n1:
            print(f"    {nome:<28} {tam(bts):>10}  {pct(bts, vivos):>6}  "
                  f"{num(qtd):>8} arq")
        print()
        print(f"  {saida / 'consumo.md'}")
        print(f"  {caminho_csv}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
