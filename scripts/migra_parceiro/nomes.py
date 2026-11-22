"""
nomes - chave de comparacao, validacao de nomes e colisoes.

O destino (Nextcloud via WebDAV, visto no Windows por WinFsp) nao permite
renomear depois do envio. Por isso todo nome final passa por aqui ANTES:
caracteres proibidos, nomes reservados do Windows, espaco/ponto no fim,
comprimento e colisao apos normalizacao (maiusculas, acentos, NFC/NFD).
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import defaultdict

PROIBIDOS = set('\\/:*?"<>|')
RESERVADOS = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
              *(f"LPT{i}" for i in range(1, 10))}
MAX_SEGMENTO = 250        # limite de nome de arquivo do Nextcloud
RE_NUMERO = re.compile(r"^(\d+)\s*\.\s*")


def chave(nome: str) -> str:
    """Forma canonica para comparar nomes de pasta.

    '01.Gestão do Contrato' e '01. Gestao do contrato' dao a mesma chave.
    Nao trata plural nem sinonimo: isso e equivalencia, decidida por gente.
    """
    s = unicodedata.normalize("NFKD", nome)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold().strip()
    s = RE_NUMERO.sub(lambda m: f"{int(m.group(1)):02d}. ", s)
    s = re.sub(r"\s+", " ", s)
    return s


def similaridade(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, chave(a), chave(b)).ratio()


def problemas_segmento(nome: str) -> list[str]:
    """Lista o que impede `nome` de existir no destino. Vazia = valido."""
    p = []
    if not nome:
        return ["vazio"]
    if any(ch in PROIBIDOS for ch in nome):
        p.append("caractere proibido " + "".join(sorted({ch for ch in nome if ch in PROIBIDOS})))
    if any(ord(ch) < 32 for ch in nome):
        p.append("caractere de controle")
    if nome != nome.rstrip(" ."):
        p.append("termina em espaco ou ponto")
    if nome != nome.lstrip(" "):
        p.append("comeca com espaco")
    if nome.split(".")[0].upper() in RESERVADOS:
        p.append("nome reservado do Windows")
    if len(nome) > MAX_SEGMENTO:
        p.append(f"nome com {len(nome)} caracteres (max {MAX_SEGMENTO})")
    if unicodedata.normalize("NFC", nome) != nome:
        p.append("unicode nao normalizado (NFC)")
    return p


def colisoes(caminhos: list[str]) -> dict[str, list[str]]:
    """Caminhos distintos que viram o mesmo no destino (sem distincao de caixa/NFC)."""
    grupos: dict[str, list[str]] = defaultdict(list)
    for c in caminhos:
        grupos[unicodedata.normalize("NFC", c).casefold()].append(c)
    return {k: v for k, v in grupos.items() if len(set(v)) > 1}
