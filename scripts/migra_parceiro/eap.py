"""
eap - leitura da EAP oficial (EAP_Repositorio.xlsx) e cruzamento com o destino.

A aba "Fluxograma" e a fonte da hierarquia: a coluna da celula da o nivel
(B=0, E=1, H=2, K=3, N=4, Q=5; passo de 3 colunas). A aba "EAP de Pastas"
traz os mesmos nos, na mesma ordem, com o codigo (01, 03.02.01...): o
parser confere que as duas abas concordam e acusa qualquer divergencia.
O mesmo vale para "Fluxograma Consorciadas" x "EAP Consorciadas".

[colchetes] = modelo de pasta que se repete (ex.: [AAAA-MM - Mes]).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .nomes import chave, similaridade

ABAS = {
    "principal": ("Fluxograma", "EAP de Pastas"),
    "consorciadas": ("Fluxograma Consorciadas", "EAP Consorciadas"),
}
CABECALHO_DESCRICAO = "Descrição / o que armazenar"


@dataclass
class No:
    arvore: str
    nome: str
    nivel: int
    caminho: str
    pai: str | None
    descricao: str
    ordem: int
    codigo: str | None = None
    filhos: list = field(default_factory=list)

    @property
    def modelo(self) -> bool:
        return self.nome.startswith("[") and self.nome.endswith("]")


def _ler_fluxograma(ws, arvore: str) -> list[No]:
    col_desc = None
    for row in ws.iter_rows(min_row=1, max_row=10):
        for c in row:
            if c.value == CABECALHO_DESCRICAO:
                col_desc = c.column
    if col_desc is None:
        raise ValueError(f"aba {ws.title!r}: cabecalho {CABECALHO_DESCRICAO!r} nao encontrado")

    nos: list[No] = []
    pilha: list[No] = []
    for row in ws.iter_rows(min_row=7):
        nome_cel = [c for c in row if c.value not in (None, "") and c.column < col_desc]
        if not nome_cel:
            continue
        if len(nome_cel) > 1:
            raise ValueError(f"aba {ws.title!r} linha {row[0].row}: mais de um nome na linha")
        cel = nome_cel[0]
        if (cel.column - 2) % 3:
            raise ValueError(f"aba {ws.title!r} {cel.coordinate}: coluna fora do passo de 3")
        nivel = (cel.column - 2) // 3
        nome = str(cel.value).strip()
        desc = ws.cell(row=cel.row, column=col_desc).value or ""
        while pilha and pilha[-1].nivel >= nivel:
            pilha.pop()
        if pilha and pilha[-1].nivel != nivel - 1:
            raise ValueError(f"aba {ws.title!r} {cel.coordinate}: salto de nivel")
        if not pilha and nivel != 0:
            raise ValueError(f"aba {ws.title!r} {cel.coordinate}: no sem pai")
        pai = pilha[-1] if pilha else None
        no = No(arvore, nome, nivel, f"{pai.caminho}/{nome}" if pai else nome,
                pai.caminho if pai else None, str(desc).strip(), len(nos))
        if pai:
            pai.filhos.append(no)
        nos.append(no)
        pilha.append(no)
    return nos


def _ler_codigos(ws) -> list[tuple[str, str]]:
    linhas = []
    for row in ws.iter_rows(min_row=4, values_only=True):
        cod, nome = row[0], row[1]
        if nome in (None, ""):
            continue
        linhas.append(("" if cod in (None, "-") else str(cod).strip(), str(nome).strip()))
    return linhas


def ler_eap(caminho_xlsx) -> tuple[list[No], list[str]]:
    """Devolve (nos, divergencias). Divergencia nao aborta: e relatada."""
    import openpyxl
    wb = openpyxl.load_workbook(caminho_xlsx, data_only=True, read_only=False)
    todos: list[No] = []
    divergencias: list[str] = []
    for arvore, (aba_fluxo, aba_lista) in ABAS.items():
        if aba_fluxo not in wb.sheetnames:
            divergencias.append(f"{arvore}: aba {aba_fluxo!r} ausente")
            continue
        nos = _ler_fluxograma(wb[aba_fluxo], arvore)
        if aba_lista in wb.sheetnames:
            codigos = _ler_codigos(wb[aba_lista])
            if len(codigos) != len(nos):
                divergencias.append(f"{arvore}: {aba_fluxo} tem {len(nos)} nos e "
                                    f"{aba_lista} tem {len(codigos)}")
            for no, (cod, nome) in zip(nos, codigos):
                if nome != no.nome:
                    divergencias.append(f"{arvore}: ordem {no.ordem}: fluxograma={no.nome!r} "
                                        f"lista={nome!r}")
                no.codigo = cod or None
        else:
            divergencias.append(f"{arvore}: aba {aba_lista!r} ausente")
        todos.extend(nos)
    return todos, divergencias


# ----------------------------------------------------------------------
# Cruzamento EAP x estrutura real do destino
# ----------------------------------------------------------------------

def cruzar(nos: list[No], pastas_destino: set[str], limiar: float = 0.85) -> list[dict]:
    """Para cada no fixo da EAP principal, acha a pasta real que o atende.

    situacao:
      existe       - mesmo nome exato no lugar esperado
      equivalente  - mesma chave (so espaco/acento/caixa/zero a esquerda mudam): reusar
      semelhante   - nome parecido mas diferente (ex.: plural): DECISAO da Emmily
      ausente      - nao ha nada: criar, com autorizacao
      sob_modelo   - fica abaixo de um [modelo]: resolvido por arquivo, no plano
    Nunca propoe renomear o que existe no destino.
    """
    filhos_reais: dict[str, list[str]] = {}
    for p in pastas_destino:
        pai, _, nome = p.rpartition("/")
        filhos_reais.setdefault(pai, []).append(nome)

    real_de: dict[str, str | None] = {}
    saida = []
    for no in (n for n in nos if n.arvore == "principal"):
        if no.modelo or (no.pai and real_de.get(no.pai, "") == "<modelo>"):
            real_de[no.caminho] = "<modelo>"
            saida.append(_linha(no, None, "sob_modelo" if not no.modelo else "modelo", ""))
            continue
        pai_real = real_de.get(no.pai, "") if no.pai else ""
        if pai_real is None:                      # pai ausente: filho tambem
            real_de[no.caminho] = None
            saida.append(_linha(no, None, "ausente", "pai ausente"))
            continue
        candidatos = filhos_reais.get(pai_real, [])
        exato = [c for c in candidatos if c == no.nome]
        mesma = [c for c in candidatos if chave(c) == chave(no.nome)]
        if exato:
            real = f"{pai_real}/{exato[0]}" if pai_real else exato[0]
            real_de[no.caminho] = real
            saida.append(_linha(no, real, "existe", ""))
        elif len(mesma) == 1:
            real = f"{pai_real}/{mesma[0]}" if pai_real else mesma[0]
            real_de[no.caminho] = real
            saida.append(_linha(no, real, "equivalente", f"real={mesma[0]!r}"))
        elif len(mesma) > 1:
            real_de[no.caminho] = None
            saida.append(_linha(no, None, "semelhante", f"mais de uma pasta equivalente: {mesma}"))
        else:
            parecidos = sorted(((similaridade(c, no.nome), c) for c in candidatos), reverse=True)
            if parecidos and parecidos[0][0] >= limiar:
                s, c = parecidos[0]
                real_de[no.caminho] = None
                saida.append(_linha(no, None, "semelhante",
                                    f"parecida: {c!r} ({s:.0%}) - decidir se e a mesma"))
            else:
                real_de[no.caminho] = None
                saida.append(_linha(no, None, "ausente", ""))
    return saida


def _linha(no: No, real, situacao, obs) -> dict:
    acao = {"existe": "reusar", "equivalente": "reusar", "ausente": "criar",
            "semelhante": "decidir"}.get(situacao, "-")
    return {"caminho_eap": no.caminho, "nivel": no.nivel, "codigo": no.codigo or "",
            "caminho_real": real or "", "situacao": situacao, "acao": acao,
            "observacao": obs, "descricao": no.descricao}
