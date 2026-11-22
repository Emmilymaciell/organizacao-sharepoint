"""
300_parceiro_unicos - pasta `5. Compartilhado PARCEIRO`: unicos x duplicatas.

Somente leitura. Nao chama a API, nao copia, nao move, nao decide.
Le o catalogo (`data/organizacao.db`), a fotografia de 21/09 e
`logs/lixeira.csv`.

Pergunta: quais arquivos da pasta 5 NAO existem no restante do SharePoint
Programa (precisam ser copiados para la antes da transferencia ao PARCEIRO) e quais
sao apenas copia do repositorio principal?

Regras:
    - Duplicata e hash igual (`quickXorHash`), nunca nome parecido (REGRA 4).
    - `06. CERTIFICADORA ENTREGA` fica FORA do escopo (pedido da Emmily, 09/10). Um
      arquivo que so tem copia dentro dela NAO conta como existente no
      repositorio principal.
    - Pasta vazia = nenhum arquivo vivo em toda a subarvore.

Classes:
    DUPLICATA       ha copia viva fora da pasta 5
    UNICO           nao ha copia fora da pasta 5 (copiar 1 exemplar)
    UNICO_REPETIDO  igual a outro UNICO da pasta 5 (nao copiar de novo)
    SEM_HASH        sem quickXorHash; conferir a mao

Saidas (output/parceiro/):
    PARCEIRO_INVENTARIO.csv      todo arquivo do escopo, com classe e destino
    PARCEIRO_UNICOS.csv          so o que precisa ir para o SharePoint principal
    PARCEIRO_PASTAS_VAZIAS.csv   pastas sem nenhum arquivo
    PARCEIRO_RESUMO_PASTAS.csv   numeros por pasta de 2o e 3o nivel
    ANALISE_PARCEIRO_UNICOS.md   resumo

Uso:
    python scripts\\300_parceiro_unicos.py
    python scripts\\300_parceiro_unicos.py --banco data\\outro.db
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config_util import caminho_banco, exigir_fase_analise, ler_config  # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass

RAIZ_REPO = Path(__file__).resolve().parents[1]
GiB = 1024**3
BRT = timezone(timedelta(hours=-3))
P5 = "5. Compartilhado PARCEIRO"
RAIZ5 = f"/{P5}"
FORA_ESCOPO = f"{RAIZ5}/06. CERTIFICADORA ENTREGA"
SNAPSHOT = RAIZ_REPO / "data" / "organizacao_snapshot_2026-09-21.db"
LIXEIRA = RAIZ_REPO / "logs" / "lixeira.csv"

# Para onde foi a copia que existia em 21/09 (frente do logs/lixeira.csv).
DESTINO_FRENTE = {
    "rd102a": "Google Drive (backup:RD102A_arquivo)",
    "video_copiado": "Google Drive (backup:RD102A_arquivo)",
    "prestacoes": "OneDrive pessoa@example.com (copia de 30/09)",
    "projetos": "OneDrive pessoa@example.com (copia de 30/09)",
}

# Destino quando nenhum arquivo irmao tem copia no SharePoint principal.
CI = "/2. Compartilhado interno"
REGRA_DESTINO = {
    "05. Produtos": "/4. Produtos",
    "16_Resposta MP 19 06 26": f"{CI}/11_MP-Estado/16_Resposta MP 19 06 26",
    "14_Resposta MP 06 26": f"{CI}/11_MP-Estado/14_Resposta MP 06 26",
    "07_Prestação de Contas Trimestral - Programa":
        f"{CI}/04_Resposta Prestacao de Contas - Trimestral - Programa",
    "11. Prestação de Contas Mensal Programa":
        f"{CI}/05_Resposta Prestação de Contas  - Mensal - Programa",
    "10. Prestação de Contas Anual Programa":
        f"{CI}/06_Resposta Prestação de Contas Anual - Programa",
    "8. Prestação de Contas Obras":
        f"{CI}/07_Resposta Prestação de Contas - Obras/8. Prestação de Contas Obras",
    "09. Prestação de Contas Obras - Set a Nov - 25":
        f"{CI}/07_Resposta Prestação de Contas - Obras/09. Prestação de Contas Obras - Set a Nov - 25",
    "5_Prestação de Contas (out a dez-25)":
        f"{CI}/07_Resposta Prestação de Contas - Obras/5_Prestação de Contas (out a dez-25)",
    "04. RD104": f"{CI}/12_TCE-Estado",
    "17_Resposta TCE 06 10 26": f"{CI}/12_TCE-Estado/04_RD101 Resposta TCE 10-26",
}


def ptbr(t): return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
def num(n, c=0): return ptbr(f"{n:,.{c}f}")
def gib(b): return f"{num((b or 0) / GiB, 2)} GiB"


def no_escopo(caminho: str) -> bool:
    return (caminho.startswith(RAIZ5 + "/")
            and not (caminho == FORA_ESCOPO or caminho.startswith(FORA_ESCOPO + "/")))


def ancestrais(pasta: str):
    """A propria pasta e cada ancestral ate a raiz da pasta 5 (inclusive)."""
    while pasta.startswith(RAIZ5):
        yield pasta
        if pasta == RAIZ5:
            return
        pasta = pasta.rsplit("/", 1)[0]


def cauda_comum(a: str, b: str) -> int:
    """Quantas pastas finais os dois caminhos de pasta tem em comum."""
    pa, pb = a.lower().split("/")[::-1], b.lower().split("/")[::-1]
    n = 0
    for x, y in zip(pa, pb):
        if x != y:
            break
        n += 1
    return n


def escrever_csv(caminho: Path, cab: list[str], linhas) -> int:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with caminho.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cab)
        for l in linhas:
            w.writerow(l)
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--banco", help="banco alternativo (padrao: config)")
    args = ap.parse_args()

    cfg = ler_config()
    exigir_fase_analise(cfg)
    banco = Path(args.banco) if args.banco else caminho_banco(cfg)
    saida = Path(cfg["saida"]) / "parceiro"
    con = sqlite3.connect(f"file:{banco.as_posix()}?mode=ro", uri=True)

    data_cat = con.execute("SELECT MAX(visto_em) FROM item_nuvem").fetchone()[0]
    vivos = con.execute(
        "SELECT item_id, caminho, pasta_pai, nome, tamanho, quick_xor_hash,"
        "       criado_em, modificado_em, modificado_por, web_url, nivel1"
        "  FROM item_nuvem WHERE eh_pasta=0 AND deletado=0").fetchall()
    pastas = [r[0] for r in con.execute(
        "SELECT caminho FROM item_nuvem WHERE eh_pasta=1 AND deletado=0")]
    pastas_vivas = set(pastas)

    escopo = [r for r in vivos if no_escopo(r[1])]
    cert = [r for r in vivos if r[1].startswith(FORA_ESCOPO + "/")]

    # copias vivas FORA da pasta 5, por hash
    fora: dict[str, list[tuple]] = defaultdict(list)
    for r in vivos:
        if r[5] and r[10] != P5:
            fora[r[5]].append(r)
    nomes_fora: dict[str, str] = {}
    for r in vivos:
        if r[10] != P5:
            nomes_fora.setdefault(r[3].lower(), r[1])

    # ---- historico: fotografia de 21/09 + lixeira.csv ----------------
    hist_fora: dict[str, str] = {}
    ids_snap: set[str] = set()
    if SNAPSHOT.exists():
        sc = sqlite3.connect(f"file:{SNAPSHOT.as_posix()}?mode=ro", uri=True)
        for iid, cam, h, n1 in sc.execute(
                "SELECT item_id, caminho, quick_xor_hash, nivel1 FROM item_nuvem"
                " WHERE eh_pasta=0 AND deletado=0"):
            ids_snap.add(iid)
            if h and n1 != P5:
                hist_fora.setdefault(h, cam)
        sc.close()
    removidas: list[tuple[str, str]] = []
    if LIXEIRA.exists():
        with LIXEIRA.open(encoding="utf-8-sig") as f:
            for x in csv.DictReader(f, delimiter=";"):
                if x["resultado"] == "OK":
                    removidas.append((x["caminho"], DESTINO_FRENTE.get(x["frente"], x["frente"])))

    def paradeiro(cam_antigo: str) -> str:
        for pref, dest in removidas:
            if cam_antigo == pref or cam_antigo.startswith(pref + "/"):
                return dest
        if cam_antigo.startswith("/6. Arquivos PARCEIRO/"):
            return "apagada com `6. Arquivos PARCEIRO` em 01-02/10 (sem backup no projeto)"
        return "removida ou alterada fora do projeto (sem registro)"

    # ---- 1a passada: duplicatas e copia de referencia -----------------
    ref: dict[str, tuple] = {}
    for r in escopo:
        h = r[5]
        if h and h in fora:
            cands = fora[h]
            ref[r[0]] = max(cands, key=lambda c: (
                c[3].lower() == r[3].lower(), cauda_comum(c[2], r[2]),
                -(datetime.fromisoformat(c[6].replace("Z", "+00:00")).timestamp()
                  if c[6] else 0)))

    # pasta da pasta 5 -> pasta mais comum das copias de referencia
    votos: dict[str, Counter] = defaultdict(Counter)
    for r in escopo:
        if r[0] in ref:
            votos[r[2]][ref[r[0]][2]] += 1
    mapa = {p: c.most_common(1)[0][0] for p, c in votos.items()}

    def destino(pasta: str) -> tuple[str, str]:
        for anc in ancestrais(pasta):
            if anc in mapa:
                rel = pasta[len(anc):]
                return mapa[anc] + rel, ("irmaos" if anc == pasta else "ancestral")
        partes = pasta.split("/")
        n2 = partes[2] if len(partes) > 2 else ""
        if n2 in REGRA_DESTINO:
            return REGRA_DESTINO[n2] + "/".join([""] + partes[3:]), "regra"
        return "", "definir"

    # ---- 2a passada: classes ------------------------------------------
    rep_unico: dict[str, str] = {}
    linhas_inv, unicos = [], []
    tot = Counter()
    by = Counter()
    por_pasta: dict[str, Counter] = defaultdict(Counter)

    for r in sorted(escopo, key=lambda x: x[1].lower()):
        iid, cam, pai, nome, tam, h, cri, mod, modpor, url, _ = r
        tam = tam or 0
        hist = ""
        dest, metodo = "", ""
        if not h:
            classe = "SEM_HASH"
        elif iid in ref:
            classe = "DUPLICATA"
        elif h in rep_unico:
            classe = "UNICO_REPETIDO"
        else:
            classe = "UNICO"
            rep_unico[h] = cam
        if classe in ("UNICO", "UNICO_REPETIDO", "SEM_HASH"):
            if iid not in ids_snap:
                hist = "arquivo novo (depois de 21/09)"
            elif h and h in hist_fora:
                hist = "tinha copia em 21/09 -> " + paradeiro(hist_fora[h])
            else:
                hist = "sem copia ja em 21/09"
            dest, metodo = destino(pai)
        rc = ref.get(iid)
        mesmo_nome = ""
        if classe != "DUPLICATA":
            mesmo_nome = nomes_fora.get(nome.lower(), "")
        dest_existe = ("sim" if dest in pastas_vivas else "criar") if dest else ""
        linha = [classe, cam, nome, tam, h or "", iid, cri, mod, modpor or "",
                 rc[1] if rc else "", rc[0] if rc else "",
                 len(fora.get(h, [])) if h else 0,
                 rep_unico.get(h, "") if classe == "UNICO_REPETIDO" else "",
                 hist, dest, metodo, dest_existe, mesmo_nome, url or ""]
        linhas_inv.append(linha)
        if classe in ("UNICO", "SEM_HASH"):
            unicos.append(linha)
        tot[classe] += 1
        by[classe] += tam
        partes = cam.split("/")
        for k in (3, 4):
            if len(partes) > k:
                chave = "/".join(partes[2:k])
                por_pasta[chave][classe] += 1
                por_pasta[chave][classe + "_b"] += tam

    cab = ["classe", "caminho", "nome", "bytes", "quick_xor_hash", "item_id",
           "criado_em", "modificado_em", "modificado_por",
           "copia_referencia", "copia_referencia_item_id", "n_copias_fora",
           "igual_ao_unico", "historico", "destino_sugerido", "metodo_destino",
           "destino_existe", "mesmo_nome_no_sharepoint (conteudo diferente)",
           "web_url"]
    escrever_csv(saida / "PARCEIRO_INVENTARIO.csv", cab, linhas_inv)
    escrever_csv(saida / "PARCEIRO_UNICOS.csv", cab, unicos)

    # ---- pastas vazias -------------------------------------------------
    contagem = Counter()
    for r in escopo:
        for anc in ancestrais(r[2]):
            contagem[anc] += 1
    p5_pastas = [p for p in pastas if no_escopo(p)]
    vazias = {p for p in p5_pastas if contagem[p] == 0}
    sub_vazias = Counter()
    for p in vazias:
        for anc in ancestrais(p.rsplit("/", 1)[0]):
            if anc in vazias:
                sub_vazias[anc] += 1
    topo = sorted((p for p in vazias if p.rsplit("/", 1)[0] not in vazias), key=str.lower)
    def contexto(p: str) -> tuple[str, str]:
        n2 = p.split("/")[2]
        if "Resposta" in n2 or "TCE" in p.split("/")[3:4][0:1] or n2 == "04. RD104":
            return "pacote enviado a órgão de controle", "manter (faz parte do pacote)"
        if "Prestação de Contas" in n2:
            return "pacote de prestação de contas", "avaliar (pode ser item sem documento)"
        if n2 == "05. Produtos" and "/Anexos/" in p:
            return "anexo de produto não usado no mês", "não levar"
        if p.count("/") == 2:
            return "pasta de 1º nível vazia", "não levar"
        return "estrutura de trabalho", "não levar"

    escrever_csv(saida / "PARCEIRO_PASTAS_VAZIAS.csv",
                 ["pasta_vazia", "subpastas_vazias_dentro", "nivel", "contexto", "sugestao"],
                 [[p, sub_vazias[p], p.count("/") - 1, *contexto(p)] for p in topo])
    ctx = Counter(contexto(p) for p in topo)

    # ---- resumo por pasta ---------------------------------------------
    classes = ["DUPLICATA", "UNICO", "UNICO_REPETIDO", "SEM_HASH"]
    escrever_csv(
        saida / "PARCEIRO_RESUMO_PASTAS.csv",
        ["pasta"] + [f"{c}_{u}" for c in classes for u in ("arq", "bytes")],
        [[k] + [v for c in classes for v in (por_pasta[k][c], por_pasta[k][c + "_b"])]
         for k in sorted(por_pasta, key=str.lower)])

    # ---- relatorio -----------------------------------------------------
    n_esc, b_esc = len(escopo), sum((r[4] or 0) for r in escopo)
    hist_c, hist_b = Counter(), Counter()
    met_c, met_b = Counter(), Counter()
    for l in unicos:
        chave = l[13].split(" -> ")[-1] if "->" in l[13] else l[13]
        hist_c[chave] += 1
        hist_b[chave] += l[3]
        met_c[l[15]] += 1
        met_b[l[15]] += l[3]
    n2u = Counter()
    n2ub = Counter()
    for l in unicos:
        n2 = l[1].split("/")[2]
        n2u[n2] += 1
        n2ub[n2] += l[3]
    quando = datetime.now(BRT).strftime("%d/%m/%Y %H:%M")

    md = [
        "# `5. Compartilhado PARCEIRO` — únicos × duplicatas",
        "",
        f"**Gerado em {quando}** · catálogo de `{data_cat}` · somente leitura · "
        "`scripts/300_parceiro_unicos.py`",
        "",
        "> Duplicata = mesmo `quickXorHash` com cópia **viva fora da pasta 5**. "
        "`06. CERTIFICADORA ENTREGA` está **fora do escopo** e não conta como cópia.",
        "",
        "## Números",
        "",
        "| Classe | Arquivos | Espaço | O que fazer |",
        "|---|---:|---:|---|",
        f"| DUPLICATA | {num(tot['DUPLICATA'])} | {gib(by['DUPLICATA'])} | nada — já está no SharePoint principal |",
        f"| **UNICO** | **{num(tot['UNICO'])}** | **{gib(by['UNICO'])}** | copiar 1 exemplar para o SharePoint principal |",
        f"| UNICO_REPETIDO | {num(tot['UNICO_REPETIDO'])} | {gib(by['UNICO_REPETIDO'])} | igual a outro UNICO — não copiar de novo |",
        f"| SEM_HASH | {num(tot['SEM_HASH'])} | {gib(by['SEM_HASH'])} | conferir a mão |",
        f"| **Total no escopo** | **{num(n_esc)}** | **{gib(b_esc)}** | |",
        f"| `06. CERTIFICADORA ENTREGA` (fora do escopo) | {num(len(cert))} | {gib(sum((r[4] or 0) for r in cert))} | não analisada, não mexer |",
        "",
        f"**A copiar para o SharePoint principal: {num(len(unicos))} arquivos · "
        f"{gib(sum(l[3] for l in unicos))}** (UNICO + SEM_HASH). "
        "Cópia dentro do mesmo site **soma na cota**.",
        "",
        "## Por que são únicos",
        "",
        "| Situação | Arquivos | Espaço |",
        "|---|---:|---:|",
    ]
    for k, v in hist_c.most_common():
        md.append(f"| {k} | {num(v)} | {gib(hist_b[k])} |")
    md += ["", "## Únicos por pasta de 2º nível", "",
           "| Pasta | Arquivos | Espaço |", "|---|---:|---:|"]
    for k, v in sorted(n2u.items(), key=lambda x: -n2ub[x[0]]):
        md.append(f"| `{k}` | {num(v)} | {gib(n2ub[k])} |")
    md += ["", "## Destino sugerido dos únicos", "",
           "| Método | Arquivos | Espaço | Significado |", "|---|---:|---:|---|"]
    sig = {"irmaos": "pasta onde estão as cópias dos outros arquivos da mesma pasta",
           "ancestral": "idem, herdado da pasta-mãe + subcaminho",
           "regra": "regra fixa por pasta de 2º nível (sem cópia de referência)",
           "definir": "sem referência — decidir"}
    for k in ("irmaos", "ancestral", "regra", "definir"):
        if met_c[k]:
            md.append(f"| {k} | {num(met_c[k])} | {gib(met_b[k])} | {sig[k]} |")
    md += ["", "## Pastas vazias", "",
           f"{num(len(vazias))} pastas sem nenhum arquivo; {num(len(topo))} no topo "
           "(as demais estão dentro delas). Lista em `PARCEIRO_PASTAS_VAZIAS.csv`.", ""]
    md += ["| Contexto | Pastas (topo) | Sugestão |", "|---|---:|---|"]
    for (c, sug), n in ctx.most_common():
        md.append(f"| {c} | {n} | {sug} |")
    md += ["", "Fora dos pacotes enviados (onde a pasta vazia faz parte do que foi entregue):", ""]
    for p in topo:
        c, sug = contexto(p)
        if c == "pacote enviado a órgão de controle":
            continue
        extra = f" (+{sub_vazias[p]} subpastas vazias)" if sub_vazias[p] else ""
        md.append(f"- `{p[len(RAIZ5) + 1:]}`{extra} — {sug}")
    md += ["", "## Arquivos", "",
           "- `PARCEIRO_INVENTARIO.csv` — todos os arquivos do escopo",
           "- `PARCEIRO_UNICOS.csv` — os que precisam ir para o SharePoint principal",
           "- `PARCEIRO_PASTAS_VAZIAS.csv`, `PARCEIRO_RESUMO_PASTAS.csv`", ""]
    (saida / "ANALISE_PARCEIRO_UNICOS.md").write_text("\n".join(md), encoding="utf-8")

    print(f"catalogo {data_cat}")
    print(f"escopo {num(n_esc)} arq {gib(b_esc)} · CERTIFICADORA ENTREGA {num(len(cert))} arq")
    for c in classes:
        print(f"  {c:15} {num(tot[c]):>8}  {gib(by[c])}")
    print(f"  pastas vazias {len(vazias)} (topo {len(topo)})")
    for k, v in hist_c.most_common():
        print(f"  [{num(v)} · {gib(hist_b[k])}] {k}")
    print(f"saida: {saida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
