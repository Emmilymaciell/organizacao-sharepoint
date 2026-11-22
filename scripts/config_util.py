"""
config_util - leitor minimo de config/projeto.yaml e trava de fase.

Sem dependencia externa (nao usa PyYAML). O projeto.yaml e um YAML simples:
mapas aninhados por indentacao, escalares e listas em linha `[a, b]`.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ_REPO = Path(__file__).resolve().parents[1]
CONFIG = RAIZ_REPO / "config" / "projeto.yaml"


_ESCAPES = {"\\\\": "\\", '\\"': '"', "\\'": "'", "\\n": "\n", "\\t": "\t"}


def _escalar(texto: str):
    """Converte o valor textual do YAML para bool/int/str/lista."""
    texto = texto.strip()
    if not texto:
        return None

    # Valor entre aspas: para na aspa de fechamento e descarta o comentario
    # que venha depois. Caminhos do Windows usam \\ — desescapar.
    if texto[0] in "\"'":
        aspa = texto[0]
        fim = texto.find(aspa, 1)
        while fim != -1 and aspa == '"':
            barras = len(texto[:fim]) - len(texto[:fim].rstrip("\\"))
            if barras % 2 == 0:       # aspa nao escapada: e o fechamento
                break
            fim = texto.find(aspa, fim + 1)
        miolo = texto[1:fim] if fim != -1 else texto[1:]
        if aspa == '"':
            miolo = re.sub(r"\\(\\|\"|'|n|t)",
                           lambda m: _ESCAPES["\\" + m.group(1)], miolo)
        return miolo

    # comentario no fim da linha, fora de aspas e fora de lista
    if not texto.startswith("["):
        texto = texto.split("#", 1)[0].strip()
    if texto.startswith("[") and texto.endswith("]"):
        miolo = texto[1:-1].strip()
        if not miolo:
            return []
        return [_escalar(p) for p in re.split(r",\s*", miolo)]
    baixo = texto.lower()
    if baixo in ("true", "yes", "sim"):
        return True
    if baixo in ("false", "no", "nao"):
        return False
    if baixo in ("null", "none", "~"):
        return None
    if re.fullmatch(r"-?\d+", texto):
        return int(texto)
    if re.fullmatch(r"-?\d+\.\d+", texto):
        return float(texto)
    return texto


def ler_config(caminho: Path | str | None = None) -> dict:
    """Le o projeto.yaml em um dicionario aninhado."""
    caminho = Path(caminho) if caminho else CONFIG
    raiz: dict = {}
    # pilha de (indentacao, dicionario) para resolver o aninhamento
    pilha: list[tuple[int, dict]] = [(-1, raiz)]
    continuacao: list[str] | None = None
    chave_lista: tuple[dict, str] | None = None

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if not linha.strip() or linha.strip().startswith("#"):
            continue
        # lista de varias linhas: "[a, b," seguido de "c]"
        if continuacao is not None:
            continuacao.append(linha.strip())
            if linha.rstrip().endswith("]"):
                alvo, chave = chave_lista            # type: ignore[misc]
                alvo[chave] = _escalar(" ".join(continuacao))
                continuacao, chave_lista = None, None
            continue

        indent = len(linha) - len(linha.lstrip())
        if ":" not in linha:
            continue
        chave, _, valor = linha.strip().partition(":")
        chave, valor = chave.strip(), valor.strip()

        while pilha and pilha[-1][0] >= indent:
            pilha.pop()
        alvo = pilha[-1][1]

        if not valor:                                 # abre um mapa aninhado
            novo: dict = {}
            alvo[chave] = novo
            pilha.append((indent, novo))
        elif valor.startswith("[") and not valor.endswith("]"):
            continuacao, chave_lista = [valor], (alvo, chave)
        else:
            alvo[chave] = _escalar(valor)
    return raiz


def exigir_fase_analise(cfg: dict) -> None:
    """Aborta se a trava de fase nao estiver na posicao de somente leitura.

    Scripts de diagnostico so fazem GET; esta checagem existe para que uma
    mudanca acidental do projeto.yaml nao passe despercebida.
    """
    fase = cfg.get("fase")
    if fase != "analise":
        raise SystemExit(
            f"ABORTADO: config/projeto.yaml esta com fase={fase!r}. "
            "Os scripts de diagnostico so rodam na fase 'analise'."
        )


def caminho_banco(cfg: dict) -> Path:
    p = Path(cfg["banco"])
    return p if p.is_absolute() else RAIZ_REPO / p
