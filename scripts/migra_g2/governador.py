"""
governador - controle de recursos da operacao G2. A migracao G2 e SECUNDARIA: so trabalha com capacidade
ociosa do notebook e cede ao projeto prioritario (decisao da Emmily, 29/10/2026).

Mede (so leitura, ctypes, sem dependencia externa; nada de WMI nem PowerShell):
  - RAM disponivel e carga de commit (GlobalMemoryStatusEx);
  - CPU total (GetSystemTimes, media movel das ultimas leituras);
  - processos do projeto prioritario, reconhecidos pelo CAMINHO DO EXECUTAVEL (ex.: o python.exe do venv
    `entregas-finais-projeto-prog`), com a soma do working set. Nenhum processo de terceiro e aberto com
    permissao de escrita, alterado, priorizado ou encerrado: so PROCESS_QUERY_LIMITED_INFORMATION;
  - espaco livre do disco do projeto.

Estados: EXECUTANDO, REDUZINDO_CARGA, PAUSA_POR_RECURSOS, PAUSADO_POR_PRIORIDADE_EXTERNA, AGUARDANDO_RECURSOS,
RETOMANDO, PAUSADO_MANUALMENTE. O executor pergunta `avaliar()` antes de abrir cada envio novo e obedece ao
numero de workers devolvido (0 = nenhum envio novo; os ativos terminam).

Histerese: pausa abaixo de `pausar_abaixo_mb`; so volta com RAM >= `retomar_acima_mb` e CPU baixa por
`estabilidade_s` seguidos (AGUARDANDO_RECURSOS -> RETOMANDO com 1 worker -> EXECUTANDO apos outra janela).
Uma pausa dura no minimo `pausa_minima_s`: nada de oscilar.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import time
from collections import deque
from ctypes import wintypes
from dataclasses import dataclass, field

EXECUTANDO = "EXECUTANDO"
REDUZINDO = "REDUZINDO_CARGA"
PAUSA_RECURSOS = "PAUSA_POR_RECURSOS"
PAUSA_PRIORIDADE = "PAUSADO_POR_PRIORIDADE_EXTERNA"
AGUARDANDO = "AGUARDANDO_RECURSOS"
RETOMANDO = "RETOMANDO"
PAUSA_MANUAL = "PAUSADO_MANUALMENTE"
PAUSAS = (PAUSA_RECURSOS, PAUSA_PRIORIDADE, AGUARDANDO, PAUSA_MANUAL)

PADRAO = {
    "pausar_abaixo_mb": 3072,          # abaixo disto nenhum envio novo (pressao ALTA)
    "reduzir_abaixo_mb": 4096,         # abaixo disto 1 worker (pressao MODERADA); tambem e o minimo para iniciar
    "retomar_acima_mb": 4096,          # para sair da pausa, sustentado por estabilidade_s
    "cpu_reduzir_pct": 70,
    "cpu_pausar_pct": 90,
    "cpu_retomar_pct": 60,
    "commit_pausar_pct": 90,
    "disco_livre_min_gib": 8,
    "estabilidade_s": 300,
    "pausa_minima_s": 300,
    "workers_max": 2,
    "marcadores_prioritario": ["\\entregas-finais-projeto-prog\\"],
}


# ---------------------------------------------------------------- medicao (Windows, so leitura)
class _MEMSTAT(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD), ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


_QUERY_LIMITED = 0x1000


def memoria() -> dict:
    m = _MEMSTAT()
    m.dwLength = ctypes.sizeof(_MEMSTAT)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    commit = 100 * (1 - m.ullAvailPageFile / m.ullTotalPageFile) if m.ullTotalPageFile else 0
    return {"ram_livre_mb": int(m.ullAvailPhys / 2**20), "ram_total_mb": int(m.ullTotalPhys / 2**20),
            "carga_memoria_pct": int(m.dwMemoryLoad), "commit_pct": round(commit, 1)}


def _tempos_cpu() -> tuple[int, int]:
    ocioso, kernel, usuario = (wintypes.FILETIME() for _ in range(3))
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(ocioso), ctypes.byref(kernel), ctypes.byref(usuario))
    v = [(f.dwHighDateTime << 32) | f.dwLowDateTime for f in (ocioso, kernel, usuario)]
    return v[0], v[1] + v[2]                            # kernel inclui o ocioso


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


def _arvore_processos() -> dict[int, int]:
    """pid -> pid do pai (CreateToolhelp32Snapshot)."""
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    pais: dict[int, int] = {}
    if not snap or snap == wintypes.HANDLE(-1).value:
        return pais
    try:
        pe = _PE32()
        pe.dwSize = ctypes.sizeof(_PE32)
        ok = k32.Process32FirstW(snap, ctypes.byref(pe))
        while ok:
            pais[pe.th32ProcessID] = pe.th32ParentProcessID
            ok = k32.Process32NextW(snap, ctypes.byref(pe))
    finally:
        k32.CloseHandle(snap)
    return pais


def processos_prioritarios(marcadores: list[str]) -> tuple[int, int]:
    """(quantidade, working set MB) dos processos cujo executavel contem um dos marcadores E dos seus
    descendentes (o python.exe de um venv no Windows e um lancador: o interpretador real e um filho com o
    caminho do Python base). So leitura: PROCESS_QUERY_LIMITED_INFORMATION."""
    k32 = ctypes.windll.kernel32
    k32.OpenProcess.restype = wintypes.HANDLE
    pais = _arvore_processos()
    marc = [m.lower() for m in marcadores]
    buf = ctypes.create_unicode_buffer(1024)
    raizes = set()
    for pid in pais:
        if not pid or pid == os.getpid():
            continue
        h = k32.OpenProcess(_QUERY_LIMITED, False, pid)
        if not h:
            continue
        try:
            tam = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(tam)) and \
                    any(m in buf.value.lower() for m in marc):
                raizes.add(pid)
        finally:
            k32.CloseHandle(h)
    grupo = set(raizes)
    for pid in pais:                                     # descendentes (pid reaproveitado: so se o pai ainda vive)
        cadeia, p, passos = [], pid, 0
        while p and p not in grupo and p in pais and passos < 32:
            cadeia.append(p)
            p, passos = pais[p], passos + 1
        if p in grupo:
            grupo.update(cadeia)
    ws = 0
    for pid in grupo:
        h = k32.OpenProcess(_QUERY_LIMITED, False, pid)
        if not h:
            continue
        try:
            pmc = _PMC()
            pmc.cb = ctypes.sizeof(_PMC)
            if k32.K32GetProcessMemoryInfo(h, ctypes.byref(pmc), pmc.cb):
                ws += pmc.WorkingSetSize
        finally:
            k32.CloseHandle(h)
    return len(grupo), int(ws / 2**20)


class Medidor:
    def __init__(self, marcadores: list[str], disco: str = "."):
        self.marcadores, self.disco = marcadores, disco
        self.ult = _tempos_cpu()
        self.ult_t = time.monotonic()

    def __call__(self) -> dict:
        # janela minima de 1 s: medida logo apos a anterior, a CPU sai com ruido (lia "100%" e pausava a toa;
        # achado de 29/10/2026 no planejamento da RD-102A)
        espera = 1.0 - (time.monotonic() - self.ult_t)
        if espera > 0:
            time.sleep(espera)
        self.ult_t = time.monotonic()
        ocioso, total = _tempos_cpu()
        d_oc, d_tot = ocioso - self.ult[0], total - self.ult[1]
        self.ult = (ocioso, total)
        cpu = round(100 * (1 - d_oc / d_tot), 1) if d_tot > 0 else 0.0
        n, ws = processos_prioritarios(self.marcadores)
        return {**memoria(), "cpu_pct": cpu, "prioritario_proc": n, "prioritario_ws_mb": ws,
                "disco_livre_gib": round(shutil.disk_usage(self.disco).free / 2**30, 1)}


def baixar_prioridade_propria() -> str:
    """BelowNormal + modo background (E/S e memoria em prioridade baixa) SO no proprio processo."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)   # instancia propria: nao mexe nas assinaturas globais
    k32.GetCurrentProcess.restype = wintypes.HANDLE       # pseudo-handle -1: sem restype e truncado em 64 bits
    k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.GetPriorityClass.argtypes = [wintypes.HANDLE]
    h = k32.GetCurrentProcess()
    # nesta ordem: o BEGIN do modo background repoe a classe em Normal (medido em 29/10/2026); a BelowNormal
    # aplicada depois fica valendo junto com a E/S e a memoria em prioridade baixa do modo background
    ok2 = k32.SetPriorityClass(h, 0x00100000)           # PROCESS_MODE_BACKGROUND_BEGIN
    ok1 = k32.SetPriorityClass(h, 0x00004000)           # BELOW_NORMAL_PRIORITY_CLASS
    classe = k32.GetPriorityClass(h)
    return (f"background={'ok' if ok2 else 'falhou'}; BelowNormal={'ok' if ok1 and classe == 0x4000 else 'falhou'}"
            f" (classe 0x{classe:x})")


# ---------------------------------------------------------------- decisao
@dataclass
class Decisao:
    estado: str
    workers: int
    motivo: str
    leitura: dict = field(default_factory=dict)


class Governador:
    def __init__(self, conf: dict | None = None, medir=None, relogio=time.monotonic):
        self.p = {**PADRAO, **(conf or {})}
        self.medir = medir or Medidor(self.p["marcadores_prioritario"])
        self.relogio = relogio
        self.cpu = deque(maxlen=3)
        self.estado = AGUARDANDO                         # ao iniciar, prova estabilidade antes de trabalhar
        self.workers = 0
        self.desde = relogio()                           # inicio do estado atual
        self.estavel_desde: float | None = None          # inicio da janela continua de recursos folgados
        self.contagem = {"pausas": 0}

    def _nivel(self, l: dict) -> tuple[str, str]:
        p = self.p
        cpu = sum(self.cpu) / len(self.cpu) if self.cpu else 0
        if l["ram_livre_mb"] < p["pausar_abaixo_mb"]:
            return "ALTA", f"RAM livre {l['ram_livre_mb']} MB < {p['pausar_abaixo_mb']} MB"
        if l["commit_pct"] >= p["commit_pausar_pct"]:
            return "ALTA", f"commit {l['commit_pct']}% >= {p['commit_pausar_pct']}% (paginacao)"
        if cpu >= p["cpu_pausar_pct"]:
            return "ALTA", f"CPU media {cpu:.0f}% >= {p['cpu_pausar_pct']}%"
        if l.get("disco_livre_gib", 999) < p["disco_livre_min_gib"]:
            return "ALTA", f"disco livre {l['disco_livre_gib']} GiB < {p['disco_livre_min_gib']} GiB"
        if l["ram_livre_mb"] < p["reduzir_abaixo_mb"]:
            return "MODERADA", f"RAM livre {l['ram_livre_mb']} MB < {p['reduzir_abaixo_mb']} MB"
        if cpu >= p["cpu_reduzir_pct"]:
            return "MODERADA", f"CPU media {cpu:.0f}% >= {p['cpu_reduzir_pct']}%"
        if l["ram_livre_mb"] < p["retomar_acima_mb"] or cpu >= p["cpu_retomar_pct"]:
            return "LIMIAR", f"RAM {l['ram_livre_mb']} MB / CPU {cpu:.0f}%: abaixo do nivel de retomada"
        return "OK", f"RAM livre {l['ram_livre_mb']} MB, CPU media {cpu:.0f}%"

    def _ir(self, estado: str, workers: int, agora_: float):
        if estado != self.estado:
            if estado in (PAUSA_RECURSOS, PAUSA_PRIORIDADE) and self.estado not in PAUSAS:
                self.contagem["pausas"] += 1
            self.estado, self.desde = estado, agora_
        self.workers = workers

    def avaliar(self, pausa_manual: bool = False) -> Decisao:
        t = self.relogio()
        l = self.medir()
        self.cpu.append(l["cpu_pct"])
        nivel, motivo = self._nivel(l)
        p = self.p
        prio = l.get("prioritario_proc", 0) > 0
        self.estavel_desde = (self.estavel_desde or t) if nivel == "OK" else None
        estavel = self.estavel_desde is not None and t - self.estavel_desde >= p["estabilidade_s"]
        if pausa_manual:
            self._ir(PAUSA_MANUAL, 0, t)
            return Decisao(self.estado, 0, "arquivo PAUSAR presente", l)
        if nivel == "ALTA":
            self._ir(PAUSA_PRIORIDADE if prio else PAUSA_RECURSOS, 0, t)
            return Decisao(self.estado, 0, motivo + (" (projeto prioritario ativo)" if prio else ""), l)
        if self.estado in PAUSAS:
            em_pausa_ok = t - self.desde >= p["pausa_minima_s"] or self.estado == AGUARDANDO
            if nivel == "OK" and estavel and em_pausa_ok:
                self._ir(RETOMANDO, 1, t)
                self.estavel_desde = t                    # nova janela para promover a EXECUTANDO
                return Decisao(self.estado, 1, f"recursos estaveis por {p['estabilidade_s']} s: {motivo}", l)
            if self.estado == PAUSA_MANUAL:
                self._ir(AGUARDANDO, 0, t)
            elif self.estado == AGUARDANDO and prio and nivel != "OK":
                self._ir(PAUSA_PRIORIDADE, 0, t)
            return Decisao(self.estado, 0, f"aguardando estabilidade: {motivo}", l)
        if nivel == "MODERADA":
            self._ir(REDUZINDO, 1, t)
            return Decisao(self.estado, 1, motivo, l)
        if self.estado == RETOMANDO:
            if nivel == "OK" and estavel:
                self._ir(EXECUTANDO, 1, t)
                self.estavel_desde = t
            return Decisao(self.estado, self.workers, motivo, l)
        if self.estado == REDUZINDO and nivel != "OK":
            return Decisao(self.estado, 1, motivo, l)
        # EXECUTANDO (ou saindo de REDUZINDO com folga): sobe 1 worker por janela estavel, ate o teto
        w = max(1, self.workers)
        if nivel == "OK" and estavel and w < p["workers_max"]:
            w += 1
            self.estavel_desde = t
        self._ir(EXECUTANDO, w if nivel == "OK" else 1, t)
        return Decisao(self.estado, self.workers, motivo, l)


_MEDIDOR_LEVE: Medidor | None = None


def esperar_folga(conf: dict | None = None, log=print, medir=None, dormir=time.sleep) -> int:
    """Para tarefas LEVES de analise (inventario, planejamento, leitura de hash): espera enquanto a pressao for
    ALTA (RAM < pausar_abaixo_mb, commit ou CPU alta). Devolve os segundos esperados. O trabalho pesado (envios)
    usa o Governador completo, com estabilidade e histerese."""
    global _MEDIDOR_LEVE
    if medir is None:
        marc = (conf or {}).get("marcadores_prioritario") or PADRAO["marcadores_prioritario"]
        if _MEDIDOR_LEVE is None or _MEDIDOR_LEVE.marcadores != marc:
            _MEDIDOR_LEVE = Medidor(marc)
        medir = _MEDIDOR_LEVE
    g = Governador(conf, medir=medir)
    esperou = 0
    while True:
        l = g.medir()
        g.cpu.append(l["cpu_pct"])
        nivel, motivo = g._nivel(l)
        if nivel != "ALTA":
            return esperou
        if esperou % 300 == 0:
            log(f"aguardando folga de recursos ({motivo}; projeto prioritario: {l.get('prioritario_proc', 0)} "
                f"processos, {l.get('prioritario_ws_mb', 0)} MB)")
        dormir(15)
        esperou += 15
