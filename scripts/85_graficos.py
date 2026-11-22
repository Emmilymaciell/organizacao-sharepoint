"""
85_graficos - dois graficos executivos em PNG.

Le data/consolidado.json (produzido por 80_pastas.py). Nao chama a API.

Saidas:
    output/grafico_01_armazenamento_6_pastas.png
    output/grafico_02_composicao_quota.png

Uso:
    python scripts\\85_graficos.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib                                        # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                          # noqa: E402
from matplotlib.ticker import FuncFormatter              # noqa: E402

from config_util import ler_config                       # noqa: E402

GiB = 1024**3

# Paleta sobria, legivel em projetor e em impressao P&B (contraste de luminancia)
AZUL_ESCURO = "#1f3a5f"
AZUL_CLARO = "#6b9bc3"
LARANJA = "#c1660a"
CINZA = "#8a8f98"
VERDE = "#3d7a5a"
VERMELHO = "#9b3226"


def ptbr(t: str) -> str:
    return t.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def num(n, c: int = 0) -> str:
    return ptbr(f"{n:,.{c}f}")


def grafico_pastas(d: dict, caminho: Path, quando: str) -> None:
    pastas = sorted(d["pastas"], key=lambda p: -p["bytes"])
    nomes = [p["nome"] for p in pastas]
    unico = [(p["bytes"] - p["recuperavel"]) / GiB for p in pastas]
    dupl = [p["recuperavel"] / GiB for p in pastas]
    total = [p["bytes"] / GiB for p in pastas]
    V = d["vivos"]

    fig, ax = plt.subplots(figsize=(13, 6.2))
    y = range(len(nomes))
    b1 = ax.barh(y, unico, color=AZUL_ESCURO, label="Conteúdo único (1 cópia)",
                 height=0.62)
    b2 = ax.barh(y, dupl, left=unico, color=LARANJA,
                 label="Cópias excedentes (redundância)", height=0.62)

    ax.set_yticks(list(y))
    ax.set_yticklabels(nomes, fontsize=11)
    ax.invert_yaxis()
    ax.set_xlabel("GiB", fontsize=10)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: num(v)))
    ax.set_title("Armazenamento por pasta principal — SharePoint Programa",
                 fontsize=15, fontweight="bold", pad=16, loc="left")
    ax.grid(axis="x", alpha=0.25, linestyle=":")
    ax.set_axisbelow(True)
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)

    limite = max(total) * 1.30
    ax.set_xlim(0, limite)
    for i, p in enumerate(pastas):
        t = p["bytes"] / GiB
        rec = p["recuperavel"] / GiB
        pct_vivos = ptbr(f"{p['bytes'] * 100 / V:.1f}")
        txt = f"{num(t,1)} GiB · {pct_vivos}% dos vivos"
        if rec > 1:
            txt += f"  (redundância {num(rec,1)} GiB)"
        ax.text(t + limite * 0.012, i, txt, va="center", fontsize=9.5,
                color="#333333")

    ax.legend(loc="lower right", frameon=False, fontsize=10)
    fig.text(0.008, 0.015, f"Microsoft Graph — posição {quando} · "
             "somente leitura, nada foi alterado", fontsize=8, color="#666666")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(caminho, dpi=170)
    plt.close(fig)


def grafico_quota(d: dict, caminho: Path, quando: str) -> None:
    Q = d["quota"]
    unico = d["vivos"] - d["dup_recuperavel"]
    cats = [
        ("Conteúdo atual único", unico, AZUL_ESCURO),
        ("Cópias excedentes", d["dup_recuperavel"], LARANJA),
        ("Histórico de versões", d["ver_total"], AZUL_CLARO),
        ("Lixeira", d["lixeira"], VERDE),
        ("Não identificado", d["nao_id"], CINZA),
    ]
    fig, (ax, axl) = plt.subplots(
        2, 1, figsize=(13, 4.6), gridspec_kw={"height_ratios": [1.5, 1]})

    esq = 0
    for rotulo, val, cor in cats:
        ax.barh([0], [val / GiB], left=[esq / GiB], color=cor, height=0.5)
        p = val * 100 / Q
        if p >= 3.5:
            ax.text((esq + val / 2) / GiB, 0, ptbr(f"{p:.1f}") + "%",
                    ha="center", va="center", color="white",
                    fontsize=11, fontweight="bold")
        esq += val

    ax.set_xlim(0, Q / GiB)
    ax.set_ylim(-0.45, 0.45)
    ax.set_yticks([])
    ax.set_xlabel("GiB", fontsize=10)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: num(v)))
    ax.set_title("Composição do armazenamento do SharePoint Programa",
                 fontsize=15, fontweight="bold", pad=14, loc="left")
    for lado in ("top", "right", "left"):
        ax.spines[lado].set_visible(False)

    axl.axis("off")
    x = 0.0
    for rotulo, val, cor in cats:
        axl.add_patch(plt.Rectangle((x, 0.52), 0.017, 0.26, color=cor,
                                    transform=axl.transAxes, clip_on=False))
        axl.text(x + 0.024, 0.65, rotulo, transform=axl.transAxes,
                 fontsize=10, va="center")
        axl.text(x + 0.024, 0.28,
                 f"{num(val/GiB,1)} GiB · " + ptbr(f"{val*100/Q:.1f}") + "%",
                 transform=axl.transAxes, fontsize=10, va="center",
                 color="#444444", fontweight="bold")
        x += 0.205

    axl.text(0.0, -0.10,
             f"Total = {num(Q/GiB,1)} GiB (100%). Categorias mutuamente "
             "exclusivas — cada byte aparece uma única vez.",
             transform=axl.transAxes, fontsize=9, color="#555555")
    fig.text(0.008, 0.015, f"Microsoft Graph — posição {quando} · "
             "somente leitura, nada foi alterado", fontsize=8, color="#666666")
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(caminho, dpi=170)
    plt.close(fig)


def main() -> int:
    cfg = ler_config()
    saida = Path(cfg["saida"])
    d = json.loads((Path(cfg["banco"]).parent / "consolidado.json")
                   .read_text(encoding="utf-8"))
    quando = datetime.fromisoformat(d["timestamp"]).strftime("%d/%m/%Y %H:%M")

    g1 = saida / "grafico_01_armazenamento_6_pastas.png"
    g2 = saida / "grafico_02_composicao_quota.png"
    grafico_pastas(d, g1, quando)
    grafico_quota(d, g2, quando)
    print(f"  {g1}  ({g1.stat().st_size//1024} KB)")
    print(f"  {g2}  ({g2.stat().st_size//1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
