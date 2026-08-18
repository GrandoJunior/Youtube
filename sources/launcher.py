# -*- coding: utf-8 -*-
"""
YouTube Downloader — Bootstrapper
----------------------------------
Este EXE é mínimo: apenas localiza o diretório de instalação,
verifica dependências e lança main.py externamente.

Para atualizar a aplicação: substitua main.py e/ou setup_env.py
no diretório de instalação. Não é necessário recompilar.
"""
import sys
import os
import runpy
import time

# ── Splash Screen (só funciona quando frozen via PyInstaller) ──
try:
    import pyi_splash
    for _msg in [
        "Initializing Core Systems...",
        "Loading Network Protocols...",
        "Detecting GPU Acceleration...",
        "System Ready.",
    ]:
        pyi_splash.update_text(_msg)
        time.sleep(0.22)
    pyi_splash.close()
except ImportError:
    pass

# ── Diretório de instalação ──
if getattr(sys, 'frozen', False):
    INSTALL_DIR = os.path.dirname(sys.executable)
else:
    INSTALL_DIR = os.path.dirname(os.path.abspath(__file__))

if INSTALL_DIR not in sys.path:
    sys.path.insert(0, INSTALL_DIR)

# ── Verificação de dependências ──
_setup = os.path.join(INSTALL_DIR, 'setup_env.py')
if os.path.isfile(_setup):
    runpy.run_path(_setup, init_globals={'INSTALL_DIR': INSTALL_DIR}, run_name='__setup__')

# ── Lança aplicação principal ──
_main = os.path.join(INSTALL_DIR, 'main.py')
if not os.path.isfile(_main):
    import tkinter as tk
    from tkinter import messagebox
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror("Erro Crítico",
        f"main.py não encontrado em:\n{INSTALL_DIR}\n\nReinstale a aplicação.")
    _r.destroy()
    sys.exit(1)

runpy.run_path(_main, init_globals={'INSTALL_DIR': INSTALL_DIR}, run_name='__main__')
