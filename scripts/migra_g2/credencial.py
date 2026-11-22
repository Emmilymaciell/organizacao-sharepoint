"""
credencial - acesso ao PARCEIRO com a MESMA senha de aplicativo DPAPI da migracao anterior
(%APPDATA%/prog-migracao-parceiro/parceiro-app.senha.dpapi), decifrada so em memoria. Nao grava, nao
imprime e nao copia a senha.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from .base import cfg
from .dav import PARCEIRO


def senha_dpapi() -> str:
    blob = bytes.fromhex((Path(os.environ["APPDATA"]) / "prog-migracao-parceiro" / "parceiro-app.senha.dpapi")
                         .read_text(encoding="ascii").strip())

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    entrada = DATA_BLOB(len(blob), ctypes.cast(ctypes.create_string_buffer(blob, len(blob)), ctypes.POINTER(ctypes.c_char)))
    saida = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(entrada), None, None, None, None, 0, ctypes.byref(saida)):
        raise OSError("DPAPI nao decifrou a senha (usuario diferente?)")
    try:
        return ctypes.string_at(saida.pbData, saida.cbData).decode("utf-16-le")
    finally:
        ctypes.windll.kernel32.LocalFree(saida.pbData)


def parceiro(c: dict | None = None) -> PARCEIRO:
    c = c or cfg()
    d = c["destino"]
    return PARCEIRO(d["base_dav"], d["uid"], d["uid"], senha_dpapi())
