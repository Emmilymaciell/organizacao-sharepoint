"""Governador de recursos da operacao G2 (29/10/2026): o G2 e secundario e cede ao projeto prioritario.

Medidor e relogio falsos: cada teste dita RAM/CPU e o tempo. O ultimo teste mede o Windows de verdade (so
leitura) e confere que nenhum processo de terceiro e alterado."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "scripts"))

from migra_g2 import governador as G  # noqa: E402


class Mundo:
    def __init__(self):
        self.t = 0.0
        self.l = {"ram_livre_mb": 8000, "commit_pct": 50, "cpu_pct": 10, "prioritario_proc": 0,
                  "prioritario_ws_mb": 0, "disco_livre_gib": 50}

    def medir(self):
        return dict(self.l)

    def relogio(self):
        return self.t


def gov(mundo, **conf):
    return G.Governador({"estabilidade_s": 300, "pausa_minima_s": 300, "workers_max": 2, **conf},
                        medir=mundo.medir, relogio=mundo.relogio)


def passar(m, g, segundos, passo=15, manual=False):
    d = None
    for _ in range(int(segundos // passo)):
        m.t += passo
        d = g.avaliar(manual)
    return d


def test_inicia_aguardando_e_so_trabalha_apos_estabilidade():
    m = Mundo()
    g = gov(m)
    assert g.avaliar().workers == 0 and g.estado == G.AGUARDANDO
    d = passar(m, g, 285)
    assert d.workers == 0                                # ainda sem 300 s estaveis
    d = passar(m, g, 30)
    assert d.estado == G.RETOMANDO and d.workers == 1
    d = passar(m, g, 315)
    assert d.estado == G.EXECUTANDO
    d = passar(m, g, 315)
    assert d.workers == 2                                # sobe 1 por janela estavel, ate o teto
    d = passar(m, g, 1200)
    assert d.workers == 2                                # nunca passa do teto


def test_reduz_com_pressao_moderada_e_pausa_com_alta():
    m = Mundo()
    g = gov(m)
    passar(m, g, 1300)
    assert g.workers == 2
    m.l["ram_livre_mb"] = 3800                           # entre 3072 e 4096
    d = passar(m, g, 15)
    assert d.estado == G.REDUZINDO and d.workers == 1
    m.l["ram_livre_mb"] = 2900
    d = passar(m, g, 15)
    assert d.estado == G.PAUSA_RECURSOS and d.workers == 0 and "RAM" in d.motivo


def test_prioridade_externa_e_rotulada_e_nao_e_falha():
    m = Mundo()
    g = gov(m)
    passar(m, g, 700)
    m.l.update(ram_livre_mb=2500, prioritario_proc=8, prioritario_ws_mb=3000)
    d = passar(m, g, 15)
    assert d.estado == G.PAUSA_PRIORIDADE and d.workers == 0 and "prioritario" in d.motivo


def test_cpu_alta_sustentada_pausa_pico_isolado_nao():
    m = Mundo()
    g = gov(m)
    passar(m, g, 700)
    m.l["cpu_pct"] = 99
    d = passar(m, g, 15)                                  # media (10,10,99) = 40: um pico nao pausa
    assert d.workers > 0
    d = passar(m, g, 30)                                  # media 99
    assert d.workers == 0 and d.estado == G.PAUSA_RECURSOS


def test_histerese_sem_oscilacao():
    m = Mundo()
    g = gov(m)
    passar(m, g, 700)
    m.l["ram_livre_mb"] = 2900
    passar(m, g, 15)
    assert g.workers == 0
    # oscila em torno do limiar: nunca retoma enquanto nao houver 300 s seguidos acima de 4096
    for k in range(40):
        m.l["ram_livre_mb"] = 4500 if k % 4 else 3500
        d = passar(m, g, 15)
        assert d.workers == 0, (k, d)
    m.l["ram_livre_mb"] = 4500
    d = passar(m, g, 315)
    assert d.estado == G.RETOMANDO and d.workers == 1


def test_pausa_minima_mesmo_com_recuperacao_rapida():
    m = Mundo()
    g = gov(m, estabilidade_s=30, pausa_minima_s=600)
    passar(m, g, 200)
    m.l["ram_livre_mb"] = 2000
    passar(m, g, 15)
    m.l["ram_livre_mb"] = 9000
    d = passar(m, g, 300)
    assert d.workers == 0                                # recuperou, mas a pausa minima nao venceu
    d = passar(m, g, 330)
    assert d.workers == 1


def test_pausa_manual():
    m = Mundo()
    g = gov(m)
    passar(m, g, 700)
    d = passar(m, g, 15, manual=True)
    assert d.estado == G.PAUSA_MANUAL and d.workers == 0
    d = passar(m, g, 15)
    assert d.estado == G.AGUARDANDO and d.workers == 0   # sem o arquivo, prova estabilidade de novo


def test_commit_e_disco_pausam():
    m = Mundo()
    g = gov(m)
    passar(m, g, 700)
    m.l["commit_pct"] = 93
    assert passar(m, g, 15).workers == 0
    m.l["commit_pct"] = 50
    m.l["disco_livre_gib"] = 3
    m.t += 1000
    assert passar(m, g, 15).workers == 0


def test_medicao_real_do_windows_e_so_leitura():
    import ctypes
    import os
    k32 = ctypes.windll.kernel32
    antes = k32.GetPriorityClass(k32.GetCurrentProcess())
    l = G.Medidor(["entregas-finais-projeto-prog"])()
    assert l["ram_total_mb"] > 1000 and 0 <= l["cpu_pct"] <= 100 and l["disco_livre_gib"] > 0
    assert l["prioritario_proc"] >= 0 and l["prioritario_ws_mb"] >= 0
    assert k32.GetPriorityClass(k32.GetCurrentProcess()) == antes   # medir nao muda nem a propria prioridade
    assert os.getpid()
