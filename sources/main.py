# -*- coding: utf-8 -*-
"""
main.py — YouTube Downloader [Gamer Edition] v2.6
--------------------------------------------------
BUGS CORRIGIDOS (v2.3):
  [1] on_show() lançava nova thread mesmo com download em andamento (BACK+EXECUTE)
      → flag _running impede sobreposição de threads.
  [2] restart_mission() liberava botões antes da thread anterior terminar
      → sinaliza cancelamento via _cancelled; _on_done ignora se stale.
  [3] _make_nav_bar(): separador empacotado fora de ordem com side='bottom'
      → separador agora dentro do nav frame, na ordem correta.
  [4] _on_resize() iterava winfo_children() afetando ScrolledText e outros
      → filtrado por tag de referência (_subtitle_label).
  [5] on_show() com urls=[] causaria iteração vazia sem aviso
      → guard com redirect para PageUrls.
  [6] log_file com int(time.time()) colidia em reinícios rápidos
      → usa datetime com microssegundos.
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import subprocess
import os
import sys
import datetime
import time
import json
import re
import io
import zipfile
import hashlib
import shutil
import urllib.request
# glob REMOVIDO: não é incluído pelo PyInstaller via runpy; usamos os.walk em seu lugar.

_INSTALL_DIR = globals().get('INSTALL_DIR') or os.path.dirname(os.path.abspath(__file__))
_DEP_DIR = os.path.join(_INSTALL_DIR, 'dependences')


def _bin(name: str) -> str:
    return os.path.join(_DEP_DIR, name)


# ── Motor de separação de vocais (Demucs em ambiente isolado via uv) ──────────
# O app é um EXE congelado (PyInstaller) sem pip; por isso não instalamos torch
# dentro dele. Em vez disso baixamos o `uv` (binário único) que cria um venv
# isolado e instala Demucs + PyTorch (CUDA autodetectado). Tudo por subprocess.
_SEPARATOR_DIR = os.path.join(_DEP_DIR, 'separator')
_UV_EXE        = os.path.join(_SEPARATOR_DIR, 'uv.exe')
_SEP_VENV      = os.path.join(_SEPARATOR_DIR, 'venv')
_SEP_PYTHON    = os.path.join(_SEP_VENV, 'Scripts', 'python.exe')

_UV_URL     = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
_UV_SHA_URL = _UV_URL + ".sha256"


def _win_no_window():
    """STARTUPINFO/creationflags para não piscar console no Windows."""
    si, cf = None, 0
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        cf = subprocess.CREATE_NO_WINDOW
    return si, cf


def _has_nvidia_gpu() -> bool:
    """True se houver GPU NVIDIA (via nvidia-smi). Requisito da remoção de vocais."""
    si, cf = _win_no_window()
    try:
        r = subprocess.run(["nvidia-smi", "-L"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           text=True, timeout=8, startupinfo=si, creationflags=cf)
        return r.returncode == 0 and "GPU" in (r.stdout or "")
    except Exception:
        return False


def _separator_ready() -> bool:
    return os.path.isfile(_SEP_PYTHON)


def _http_get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_text(url: str) -> str:
    try:
        return _http_get(url, timeout=60).decode('utf-8', 'replace')
    except Exception:
        return ""


_HEX_DIGITS = '0123456789abcdef'


def _is_sha256(token: str) -> bool:
    """True se `token` for exatamente 64 digitos hexadecimais."""
    t = token.strip().lstrip('*').lower()
    return len(t) == 64 and all(c in _HEX_DIGITS for c in t)


def _extract_sha256(text: str, filename: str = "") -> str:
    """Extrai um SHA-256 de um arquivo de somas, tolerando os formatos que os
    fornecedores publicam -- e mudam sem aviso:

      - hash hexadecimal isolado          -> gyan.dev (.sha256)
      - 'hash  arquivo' / 'hash *arquivo' -> sha256sum classico, uv
      - blocos 'Algorithm/Hash/Path'      -> Get-FileHash do PowerShell (Deno)

    Quando `filename` e informado, prioriza a linha daquele arquivo, necessario
    em arquivos com varias entradas como o SHA2-256SUMS do yt-dlp. Retorna ""
    se nao houver hash reconhecivel (o chamador decide como tratar)."""
    if not text:
        return ""
    if filename:
        alvo = filename.strip().lower()
        for ln in text.splitlines():
            parts = ln.split()
            if len(parts) >= 2 and parts[-1].strip().lstrip('*').lower() == alvo:
                for tok in parts:
                    if _is_sha256(tok):
                        return tok.strip().lstrip('*').lower()
    for tok in text.split():
        if _is_sha256(tok):
            return tok.strip().lstrip('*').lower()
    return ""


def _verify_sha256(data: bytes, sums_text: str, label: str):
    """Falha FECHADA em divergência; falha ABERTA se não há hash de referência."""
    expected = _extract_sha256(sums_text)
    if not expected:
        return
    actual = hashlib.sha256(data).hexdigest().lower()
    if actual != expected:
        raise RuntimeError(f"Integridade de {label} falhou (hash divergente).")


def _run_stream(cmd: list, log, env=None):
    """Executa um comando transmitindo stdout linha a linha para `log`."""
    si, cf = _win_no_window()
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
        startupinfo=si, creationflags=cf, env=env)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log("  " + line)
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Comando falhou (código {proc.returncode}).")


def _install_separator(log):
    """Baixa uv, cria venv isolado e instala Demucs + PyTorch (CUDA auto).
    `log` é uma função thread-safe que recebe strings para exibir."""
    os.makedirs(_SEPARATOR_DIR, exist_ok=True)

    # [1/3] uv.exe (binário único, verificado por checksum)
    if not os.path.isfile(_UV_EXE):
        log("> [1/3] Baixando uv (gerenciador de ambiente)...")
        data = _http_get(_UV_URL)
        _verify_sha256(data, _http_text(_UV_SHA_URL), "uv (zip)")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if os.path.basename(member).lower() == 'uv.exe':
                    with zf.open(member) as src, open(_UV_EXE, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    break
        if not os.path.isfile(_UV_EXE):
            raise RuntimeError("uv.exe não encontrado no pacote baixado.")

    # [2/3] venv isolado com Python 3.11 (uv baixa o interpretador se preciso)
    log("> [2/3] Criando ambiente Python isolado (3.11)...")
    _run_stream([_UV_EXE, "venv", _SEP_VENV, "--python", "3.11"], log)

    # [3/3] Demucs + PyTorch — backend CUDA detectado automaticamente pelo uv
    log("> [3/3] Instalando Demucs + PyTorch CUDA (download grande, ~2 GB)...")
    _run_stream([_UV_EXE, "pip", "install", "--python", _SEP_PYTHON,
                 "--torch-backend=auto", "demucs"], log)

    # Validação
    si, cf = _win_no_window()
    r = subprocess.run([_SEP_PYTHON, "-m", "demucs", "--help"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       startupinfo=si, creationflags=cf)
    if r.returncode != 0:
        raise RuntimeError("Instalação do Demucs não pôde ser validada.")
    log("> Motor de separação instalado com sucesso.")


def _separate_vocals(audio_file: str, log, ffmpeg_path: str, ffmpeg_dir: str) -> tuple:
    """Gera a versão instrumental (stem 'no_vocals' do Demucs) e substitui o
    arquivo original. Retorna (True, '') ou (False, mensagem)."""
    si, cf = _win_no_window()
    out_dir = audio_file + ".sep_tmp"
    tmp_mp3 = audio_file + ".instr.mp3"
    try:
        env = os.environ.copy()
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")  # demucs lê via ffmpeg

        cmd = [_SEP_PYTHON, "-m", "demucs",
               "--two-stems", "vocals",      # gera 'vocals' e 'no_vocals'
               "-n", "htdemucs",
               "-o", out_dir, audio_file]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            startupinfo=si, creationflags=cf, env=env)
        for line in proc.stdout:
            line = line.rstrip()
            if line and ('%' in line or 'rror' in line):
                log("    " + line)
        proc.wait()
        if proc.returncode != 0:
            return False, f"demucs retornou código {proc.returncode}"

        # Localiza o stem instrumental (no_vocals.*) na árvore de saída.
        instrumental = None
        for root, _dirs, files in os.walk(out_dir):
            for f in files:
                if f.lower().startswith('no_vocals'):
                    instrumental = os.path.join(root, f)
                    break
            if instrumental:
                break
        if not instrumental:
            return False, "stem instrumental (no_vocals) não encontrado"

        # Converte o instrumental para MP3 320k preservando as tags do original.
        r = subprocess.run(
            [ffmpeg_path, "-y",
             "-i", instrumental, "-i", audio_file,
             "-map", "0:a", "-map_metadata", "1",
             "-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3",
             tmp_mp3],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            startupinfo=si, creationflags=cf)
        if r.returncode != 0 or not os.path.isfile(tmp_mp3):
            return False, "falha ao converter instrumental para MP3"

        os.replace(tmp_mp3, audio_file)   # substitui o original pelo instrumental
        return True, ""
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
        if os.path.exists(tmp_mp3):
            try:
                os.remove(tmp_mp3)
            except OSError:
                pass


def _find_mp3_files(folder: str, recursive: bool) -> list:
    """
    Retorna lista ordenada de caminhos .mp3 em `folder`.
    Usa os.walk para evitar dependência de `glob` (não disponível no bundle PyInstaller).
    """
    mp3s = []
    if recursive:
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith('.mp3'):
                    mp3s.append(os.path.join(root, f))
    else:
        try:
            for f in os.listdir(folder):
                full = os.path.join(folder, f)
                if f.lower().endswith('.mp3') and os.path.isfile(full):
                    mp3s.append(full)
        except OSError:
            pass
    return sorted(mp3s)


# ── Estilo GenZ / Gamer ──────────────────────────────────────────────────────
class GenZStyle(ttk.Style):
    BG_DARK  = "#121212"
    BG_LIGHT = "#1E1E1E"
    ACCENT   = "#00FF41"
    TEXT     = "#FFFFFF"

    def __init__(self, master=None):
        super().__init__(master)
        self.theme_use('clam')
        bg, bg2, ac, tx = self.BG_DARK, self.BG_LIGHT, self.ACCENT, self.TEXT
        self.configure(".",                  background=bg, foreground=tx, font=("Segoe UI", 10))
        self.configure("TFrame",             background=bg)
        self.configure("TLabel",             background=bg, foreground=tx)
        self.configure("Header.TLabel",      font=("Segoe UI", 16, "bold"), foreground=ac)
        self.configure("TButton",            background=bg2, foreground=ac, borderwidth=1,
                        focuscolor=ac, font=("Segoe UI", 10, "bold"))
        self.map("TButton",
                 background=[('active', bg2), ('pressed', ac)],
                 foreground=[('pressed', bg)])
        self.configure("TEntry",             fieldbackground=bg2, foreground=tx, insertcolor=ac)
        self.configure("TCombobox",          fieldbackground=bg2, background=bg,
                        foreground=tx, arrowcolor=ac,
                        selectbackground=bg2, selectforeground=tx)
        self.map("TCombobox",
                 fieldbackground=[('readonly', bg2)],
                 foreground=[('readonly', tx), ('disabled', '#666666')],
                 selectbackground=[('readonly', bg2)],
                 selectforeground=[('readonly', tx)])
        self.configure("TLabelframe",        background=bg, foreground=ac)
        self.configure("TLabelframe.Label",  background=bg, foreground=ac,
                        font=("Segoe UI", 10, "bold"))
        # [FIX contraste] No hover (estado 'active') o clam clareia o fundo; sem
        # mapear o foreground, o texto branco some. Mapeamos: hover = fundo escuro
        # (bg2) + texto verde neon (ac); desabilitado = cinza legível.
        self.configure("TRadiobutton",       background=bg, foreground=tx, indicatorcolor=ac)
        self.map("TRadiobutton",
                 background=[('active', bg2)],
                 foreground=[('active', ac), ('disabled', '#666666')],
                 indicatorcolor=[('selected', ac), ('active', ac), ('pressed', tx)])
        self.configure("TCheckbutton",       background=bg, foreground=tx, indicatorcolor=ac)
        self.map("TCheckbutton",
                 background=[('active', bg2)],
                 foreground=[('active', ac), ('disabled', '#666666')],
                 indicatorcolor=[('selected', ac), ('active', ac), ('pressed', tx)])
        self.configure("Horizontal.TProgressbar", background=ac, troughcolor=bg2, bordercolor=bg)
        self.configure("TSeparator",         background="#2A2A2A")
        self.configure("Hint.TLabel",         background=bg, foreground="#555555",
                        font=("Segoe UI", 8))  # [B1] estilo registrado no tema

        # Popup do Combobox é um tk.Listbox (não recebe estilo ttk): mantém o
        # tema escuro e garante contraste do item destacado (verde/fundo escuro).
        if master is not None:
            master.option_add('*TCombobox*Listbox.background', bg2)
            master.option_add('*TCombobox*Listbox.foreground', tx)
            master.option_add('*TCombobox*Listbox.selectBackground', ac)
            master.option_add('*TCombobox*Listbox.selectForeground', bg)


# ── Aplicação Principal ──────────────────────────────────────────────────────
class WizardApp(tk.Tk):
    INIT_W, INIT_H = 780, 700
    MIN_W,  MIN_H  = 560, 520

    def __init__(self):
        super().__init__()
        self.title("PROJECT: DOWNLOADER [GAMER EDITION] v2")
        self.configure(bg="#121212")
        GenZStyle(self)
        self.resizable(True, True)
        self.minsize(self.MIN_W, self.MIN_H)
        self._center_window(self.INIT_W, self.INIT_H)
        self._create_vars()
        self._load_prefs()          # aplica as últimas configurações salvas (sugestão)
        self._build_layout()
        self._ensure_destination()  # usa o padrão só se não houver destino salvo válido
        self.show_frame("PageUrls")

    def _center_window(self, w: int, h: int):
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _create_vars(self):
        self.urls            = []
        self.download_type   = tk.StringVar(master=self, value="video")
        self.quality         = tk.StringVar(master=self, value="best (MAX)")
        self.use_cookies     = tk.BooleanVar(master=self, value=False)
        self.browser_cookies = tk.StringVar(master=self, value="chrome")
        # Arquivo cookies.txt (formato Netscape). Tem prioridade sobre o browser:
        # no Windows, Chrome/Edge usam App-Bound Encryption e o yt-dlp não
        # consegue ler os cookies diretamente do perfil.
        self.cookies_file    = tk.StringVar(master=self, value="")
        self.destination     = tk.StringVar(master=self, value="")
        self.normalize_mode  = tk.StringVar(master=self, value="ebu_r128")
        # Modo de operação: "download" | "normalize_only"
        self.operation_mode      = tk.StringVar(master=self, value="download")
        self.normalize_folder    = tk.StringVar(master=self, value="")
        self.normalize_recursive = tk.BooleanVar(master=self, value=False)
        # Remoção de vocais (instrumental) — habilitável só com GPU e áudio.
        self.remove_vocals       = tk.BooleanVar(master=self, value=False)
        # Baixar a playlist inteira quando o link traz "&list=". Desmarcado por
        # padrão: só o vídeo do link (--no-playlist).
        self.download_playlist   = tk.BooleanVar(master=self, value=False)
        self.gpu_available       = _has_nvidia_gpu()
        self.yt_dlp_path     = _bin('yt-dlp.exe')
        self.ffmpeg_path     = _bin('ffmpeg.exe')
        self.deno_path       = _bin('deno.exe')
        # Memória por tipo da última qualidade escolhida (sugestão de próxima vez).
        self._last_video_quality = "best (MAX)"
        self._last_audio_quality = "best (MAX)"

    def _build_layout(self):
        # Header fixo
        hdr = tk.Frame(self, bg="#0A0A0A", height=48)
        hdr.pack(fill='x', side='top')
        hdr.pack_propagate(False)
        tk.Label(hdr, text="  ▶ PROJECT: DOWNLOADER [GAMER EDITION]",
                 bg="#0A0A0A", fg="#00FF41",
                 font=("Consolas", 11, "bold")).pack(side='left', pady=12)
        self.step_label = tk.Label(hdr, text="PHASE 1/4",
                                   bg="#0A0A0A", fg="#555555",
                                   font=("Consolas", 10))
        self.step_label.pack(side='right', padx=16)
        ttk.Separator(self, orient='horizontal').pack(fill='x')

        # Container expansível
        container = ttk.Frame(self)
        container.pack(side='top', fill='both', expand=True, padx=16, pady=12)
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self.frames = {}
        for F in (PageUrls, PageOptions, PageDestination, PageProgress):
            frame = F(parent=container, controller=self)
            self.frames[F.__name__] = frame
            frame.grid(row=0, column=0, sticky="nsew")

    def _setup_default_download_dir(self):  # [M2] falha silenciosa → try/except com fallback
        candidate = os.path.join(
            os.environ.get('USERPROFILE', os.path.expanduser('~')),
            'Downloads', 'YouTube')
        try:
            os.makedirs(candidate, exist_ok=True)
            self.destination.set(candidate)
        except OSError:
            fallback = os.path.expanduser('~')
            os.makedirs(fallback, exist_ok=True)
            self.destination.set(fallback)

    # ── Persistência de preferências ─────────────────────────────────────────
    def _prefs_path(self) -> str:
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'YoutubeDownloader', 'config.json')

    def _load_prefs(self):
        """Carrega as últimas configurações usadas como sugestão para o próximo
        processamento. Na primeira execução (arquivo ausente/ inválido) mantém
        os padrões definidos em _create_vars()."""
        try:
            with open(self._prefs_path(), 'r', encoding='utf-8') as f:
                prefs = json.load(f)
        except (OSError, ValueError):
            return

        dtype = prefs.get('download_type')
        if dtype in ('video', 'audio'):
            self.download_type.set(dtype)

        self._last_video_quality = prefs.get('video_quality', self._last_video_quality)
        self._last_audio_quality = prefs.get('audio_quality', self._last_audio_quality)
        self.quality.set(self._last_audio_quality if dtype == 'audio'
                         else self._last_video_quality)

        if isinstance(prefs.get('use_cookies'), bool):
            self.use_cookies.set(prefs['use_cookies'])
        if prefs.get('browser_cookies'):
            self.browser_cookies.set(prefs['browser_cookies'])
        saved_cookies = prefs.get('cookies_file')
        if saved_cookies and os.path.isfile(saved_cookies):
            self.cookies_file.set(saved_cookies)
        if prefs.get('normalize_mode') in ('ebu_r128', 'none'):
            self.normalize_mode.set(prefs['normalize_mode'])
        # Remoção de vocais só é sugerida se a máquina tiver GPU.
        if isinstance(prefs.get('download_playlist'), bool):
            self.download_playlist.set(prefs['download_playlist'])
        if prefs.get('remove_vocals') and self.gpu_available:
            self.remove_vocals.set(True)

        # Destino: só reutiliza se a pasta salva ainda existir (regra do usuário).
        saved_dest = prefs.get('destination')
        if saved_dest and os.path.isdir(saved_dest):
            self.destination.set(saved_dest)

    def _save_prefs(self):
        """Persiste as configurações atuais para sugeri-las no próximo processamento."""
        # Atualiza a memória por tipo antes de gravar.
        if self.download_type.get() == 'audio':
            self._last_audio_quality = self.quality.get()
        else:
            self._last_video_quality = self.quality.get()

        prefs = {
            'download_type':   self.download_type.get(),
            'video_quality':   self._last_video_quality,
            'audio_quality':   self._last_audio_quality,
            'use_cookies':     bool(self.use_cookies.get()),
            'browser_cookies': self.browser_cookies.get(),
            'cookies_file':    self.cookies_file.get().strip(),
            'normalize_mode':  self.normalize_mode.get(),
            'remove_vocals':   bool(self.remove_vocals.get()),
            'download_playlist': bool(self.download_playlist.get()),
            'destination':     self.destination.get(),
        }
        try:
            path = self._prefs_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, ensure_ascii=False, indent=2)
        except OSError:
            pass  # falha ao salvar preferências não deve impedir o uso do app

    def _ensure_destination(self):
        """Mantém o destino salvo se válido; caso contrário aplica o padrão."""
        dest = self.destination.get().strip()
        if dest and os.path.isdir(dest):
            return
        self._setup_default_download_dir()

    def show_frame(self, page_name: str):
        frame = self.frames[page_name]
        frame.tkraise()
        steps = {"PageUrls": "PHASE 1/4", "PageOptions": "PHASE 2/4",
                 "PageDestination": "PHASE 3/4", "PageProgress": "PHASE 4/4"}
        self.step_label.config(text=steps.get(page_name, ""))
        if hasattr(frame, 'on_show'):
            frame.on_show()

    def restart_mission(self):
        """Sinaliza cancelamento da thread atual e reinicia o fluxo, pré-preenchendo
        com as últimas configurações usadas (sugestão)."""  # [2]
        progress_page = self.frames.get('PageProgress')
        if progress_page:
            progress_page.cancel()           # sinaliza a thread para parar
        self.urls = []
        self.operation_mode.set("download")
        self.normalize_folder.set("")
        self.normalize_recursive.set(False)
        # Recarrega as últimas configurações salvas como sugestão para a próxima missão.
        self._load_prefs()
        self._ensure_destination()
        self.frames['PageUrls'].reset()
        self.frames['PageOptions'].reset()
        self.frames['PageDestination'].reset()
        self.show_frame("PageUrls")

    def get_ffmpeg_dir(self) -> str:
        return os.path.dirname(self.ffmpeg_path)


# ── Helper: barra de navegação ───────────────────────────────────────────────
def _make_nav_bar(parent: ttk.Frame) -> ttk.Frame:
    """[3] Separador + botões empacotados de baixo para cima, na ordem correta."""
    bar = ttk.Frame(parent)
    bar.pack(fill='x', side='bottom')
    ttk.Separator(bar, orient='horizontal').pack(fill='x', pady=(0, 8))
    btn_row = ttk.Frame(bar)
    btn_row.pack(fill='x')
    return btn_row


# ── Página 1: URLs ───────────────────────────────────────────────────────────
class PageUrls(ttk.Frame):
    def __init__(self, parent, controller: WizardApp):
        super().__init__(parent)
        self.controller = controller
        self._build()

    def _build(self):
        ttk.Label(self, text="MISSION: INPUT OBJECTIVES",
                  style="Header.TLabel").pack(anchor="w", pady=(4, 2))

        self._subtitle = ttk.Label(
            self,
            text="Paste Target URLs (YouTube, Instagram, Facebook) — one per line:",
            wraplength=600)
        self._subtitle.pack(anchor="w", pady=(0, 6))

        self.text_area = scrolledtext.ScrolledText(
            self, bg="#1E1E1E", fg="#FFFFFF", insertbackground="#00FF41",
            font=("Consolas", 10), relief="flat", wrap=tk.WORD)
        self.text_area.pack(fill='both', expand=True, pady=(0, 4))

        nav = _make_nav_bar(self)
        ttk.Button(nav, text="NEXT PHASE  ▶",
                   command=self._go_next).pack(side='right')

        # ── Separador visual ──
        div = ttk.Frame(self)
        div.pack(fill='x', pady=(10, 6))
        ttk.Separator(div, orient='horizontal').pack(
            fill='x', side='left', expand=True, pady=7)
        tk.Label(div, text="  OU  ",
                 bg="#121212", fg="#555555",
                 font=("Consolas", 9, "bold")).pack(side='left')
        ttk.Separator(div, orient='horizontal').pack(
            fill='x', side='left', expand=True, pady=7)

        # ── Painel: Normalizar Pasta Existente ──
        norm_lf = ttk.LabelFrame(self, text="  NORMALIZE EXISTING FILES  ")
        norm_lf.pack(fill='x', pady=(0, 8))

        path_row = ttk.Frame(norm_lf)
        path_row.pack(fill='x', padx=12, pady=(10, 4))
        ttk.Label(path_row, text="Pasta:").pack(side='left', padx=(0, 8))
        ttk.Entry(path_row,
                  textvariable=self.controller.normalize_folder
                  ).pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(path_row, text="BROWSE...",
                   command=self._browse_norm_folder).pack(side='right')

        ttk.Checkbutton(norm_lf,
                        text="Incluir subpastas (recursivo)",
                        variable=self.controller.normalize_recursive
                        ).pack(anchor='w', padx=16, pady=(0, 2))

        ttk.Label(norm_lf,
                  text="  Normaliza todos os .mp3 da pasta para EBU R128 / -14 LUFS  (two-pass ffmpeg).",
                  style="Hint.TLabel"
                  ).pack(anchor='w', padx=16, pady=(2, 6))

        btn_row = ttk.Frame(norm_lf)
        btn_row.pack(fill='x', padx=12, pady=(0, 10))
        ttk.Button(btn_row, text="▶  NORMALIZE FOLDER",
                   command=self._go_normalize).pack(side='right')

        self.bind("<Configure>", self._on_resize)

    def _on_resize(self, event):
        self._subtitle.configure(wraplength=max(200, event.width - 40))

    def reset(self):
        self.text_area.delete("1.0", tk.END)

    def _browse_norm_folder(self):
        d = filedialog.askdirectory()
        if d:
            self.controller.normalize_folder.set(d)

    def _go_normalize(self):
        folder = self.controller.normalize_folder.get().strip()
        if not folder:
            messagebox.showwarning("ALERT", "Nenhuma pasta selecionada.")
            return
        if not os.path.isdir(folder):
            messagebox.showerror("ALERT", f"Pasta não encontrada:\n{folder}")
            return
        self.controller.operation_mode.set("normalize_only")
        self.controller.show_frame("PageProgress")

    def _go_next(self):
        content = self.text_area.get("1.0", tk.END).strip()
        if not content:
            messagebox.showwarning("ALERT", "No Targets Detected.")
            return
        self.controller.urls = [ln.strip() for ln in content.splitlines() if ln.strip()]
        self.controller.operation_mode.set("download")
        self.controller.show_frame("PageOptions")


# ── Página 2: Opções ─────────────────────────────────────────────────────────
class PageOptions(ttk.Frame):
    def __init__(self, parent, controller: WizardApp):
        super().__init__(parent)
        self.controller = controller
        self._build()

    def _build(self):
        ttk.Label(self, text="CONFIGURATION",
                  style="Header.TLabel").pack(anchor="w", pady=(4, 12))

        # [FIX] Barra de navegação reservada no rodapé ANTES do conteúdo, para que
        # os botões nunca sejam cortados quando a página cresce (ex.: opção de vocais).
        nav = _make_nav_bar(self)
        ttk.Button(nav, text="◀  BACK",
                   command=lambda: self.controller.show_frame("PageUrls")
                   ).pack(side='left')
        ttk.Button(nav, text="NEXT PHASE  ▶",
                   command=lambda: self.controller.show_frame("PageDestination")
                   ).pack(side='right')

        tf = ttk.LabelFrame(self, text="  PAYLOAD TYPE  ")
        tf.pack(fill="x", pady=(0, 10))
        for text, val in [("Video Stream  (.mp4)", "video"),
                          ("Audio Stream  (.mp3)", "audio")]:
            ttk.Radiobutton(tf, text=text,
                            variable=self.controller.download_type,
                            value=val, command=self._update
                            ).pack(anchor='w', padx=16, pady=4)

        self.qual_frame = ttk.LabelFrame(self, text="  RESOLUTION / BITRATE  ")
        self.qual_frame.pack(fill="x", pady=(0, 10))
        self.qual_combo = ttk.Combobox(self.qual_frame,
                                       textvariable=self.controller.quality,
                                       state="readonly")
        self.qual_combo.pack(fill='x', padx=16, pady=10)
        # Memoriza a escolha por tipo (vídeo/áudio) para sugerir depois.
        self.qual_combo.bind("<<ComboboxSelected>>", self._on_quality_selected)

        pf = ttk.LabelFrame(self, text="  PLAYLIST  ")
        pf.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(pf,
                        text="Baixar a lista de reprodução inteira, se o link tiver uma",
                        variable=self.controller.download_playlist
                        ).pack(anchor='w', padx=16, pady=(8, 2))
        ttk.Label(pf, foreground="#888",
                  text="  Desmarcado: baixa só o vídeo do link, ignorando o \"&list=\"."
                  ).pack(anchor='w', padx=16, pady=(0, 8))

        cf = ttk.LabelFrame(self, text="  AUTHENTICATION (OPTIONAL)  ")
        cf.pack(fill="x", pady=(0, 10))
        ttk.Checkbutton(cf,
                        text="Use Browser Cookies  (Fixes Login / Age Restrictions)",
                        variable=self.controller.use_cookies,
                        command=self._update
                        ).pack(anchor='w', padx=16, pady=(8, 4))
        self.browser_combo = ttk.Combobox(
            cf, textvariable=self.controller.browser_cookies,
            values=["chrome", "firefox", "edge", "opera", "brave", "vivaldi"],
            state="readonly")
        self.browser_combo.pack(fill='x', padx=16, pady=(0, 4))
        ttk.Label(cf, foreground="#888",
                  text=("  Só necessário se o download falhar sem cookies. Chrome/Edge no "
                        "Windows bloqueiam a leitura direta; use Firefox ou um cookies.txt:")
                  ).pack(anchor='w', padx=16, pady=(0, 2))
        ck_row = ttk.Frame(cf)
        ck_row.pack(fill='x', padx=16, pady=(0, 10))
        self.cookies_entry = ttk.Entry(ck_row,
                                       textvariable=self.controller.cookies_file)
        self.cookies_entry.pack(side='left', fill='x', expand=True)
        self.cookies_btn = ttk.Button(ck_row, text="cookies.txt...",
                                      command=self._browse_cookies)
        self.cookies_btn.pack(side='left', padx=(8, 0))

        # ── Pós-processamento (apenas para áudio) ──────────────────────────
        self._norm_frame = ttk.LabelFrame(self, text="  POST-PROCESSING  (somente áudio)  ")
        self._norm_frame.pack(fill="x", pady=(0, 10))

        _NORM_OPTIONS = [
            ("ebu_r128",
             "★ EBU R128  —  -14 LUFS / True Peak -1.5 dBTP  (Recomendado)"),
            ("none",
             "Sem pós-processamento"),
        ]
        self._norm_radios = []
        for val, label in _NORM_OPTIONS:
            rb = ttk.Radiobutton(
                self._norm_frame, text=label,
                variable=self.controller.normalize_mode, value=val)
            rb.pack(anchor='w', padx=16, pady=4)
            self._norm_radios.append(rb)

        ttk.Label(self._norm_frame,
                  text="  Normaliza o volume de cada faixa após o download (two-pass ffmpeg).",
                  style="Hint.TLabel"  # [B1] usa estilo registrado no GenZStyle
                  ).pack(anchor='w', padx=16, pady=(0, 6))

        # ── Remoção de vocais (instrumental) — requer GPU NVIDIA ──
        ttk.Separator(self._norm_frame, orient='horizontal').pack(
            fill='x', padx=12, pady=(2, 6))
        self._vocal_chk = ttk.Checkbutton(
            self._norm_frame,
            text="🎤➜🎶  Remover vocais — gerar versão instrumental",
            variable=self.controller.remove_vocals)
        self._vocal_chk.pack(anchor='w', padx=16, pady=(0, 2))
        self._vocal_hint = ttk.Label(self._norm_frame, style="Hint.TLabel",
                                     text="")
        self._vocal_hint.pack(anchor='w', padx=16, pady=(0, 6))
        # ──────────────────────────────────────────────────────────────────

        ttk.Frame(self).pack(fill='both', expand=True)  # espaçador (empurra conteúdo p/ cima)

        self._update()

    def reset(self):
        self._update()

    def _browse_cookies(self):
        f = filedialog.askopenfilename(
            title="Selecione o arquivo cookies.txt (formato Netscape)",
            filetypes=[("Cookies", "*.txt"), ("Todos", "*.*")])
        if f:
            self.controller.cookies_file.set(f)

    def _on_quality_selected(self, _event=None):
        if self.controller.download_type.get() == 'audio':
            self.controller._last_audio_quality = self.controller.quality.get()
        else:
            self.controller._last_video_quality = self.controller.quality.get()

    def _update(self):
        is_audio = self.controller.download_type.get() == 'audio'
        if not is_audio:
            self.qual_frame.config(text="  RESOLUTION  ")
            self.qual_combo['values'] = ("best (MAX)", "1080p", "720p", "480p", "360p")
        else:
            self.qual_frame.config(text="  BITRATE  ")
            self.qual_combo['values'] = ("best (MAX)", "320k", "192k", "128k")
        # Restaura a última qualidade lembrada para este tipo, se ainda válida.
        remembered = (self.controller._last_audio_quality if is_audio
                      else self.controller._last_video_quality)
        values = self.qual_combo['values']
        if self.controller.quality.get() not in values:
            if remembered in values:
                self.controller.quality.set(remembered)
            else:
                self.qual_combo.current(0)
        ck_on = self.controller.use_cookies.get()
        self.browser_combo.config(state="readonly" if ck_on else "disabled")
        self.cookies_entry.config(state="normal" if ck_on else "disabled")
        self.cookies_btn.config(state="normal" if ck_on else "disabled")
        # Habilita/desabilita seção de normalização conforme o tipo
        norm_state = "normal" if is_audio else "disabled"
        for rb in self._norm_radios:
            rb.config(state=norm_state)

        # Remoção de vocais: exige áudio E GPU NVIDIA presente.
        gpu = getattr(self.controller, 'gpu_available', False)
        if is_audio and gpu:
            self._vocal_chk.config(state="normal")
            self._vocal_hint.config(
                text="  IA (Demucs htdemucs) na GPU. Na 1ª vez baixa ~2 GB de dependências.")
        else:
            self.controller.remove_vocals.set(False)  # impede rodar sem GPU/áudio
            self._vocal_chk.config(state="disabled")
            self._vocal_hint.config(
                text=("  Disponível apenas para áudio." if not is_audio
                      else "  Requer GPU NVIDIA — não detectada nesta máquina."))


# ── Página 3: Destino ────────────────────────────────────────────────────────
class PageDestination(ttk.Frame):
    def __init__(self, parent, controller: WizardApp):
        super().__init__(parent)
        self.controller = controller
        self._build()

    def _build(self):
        ttk.Label(self, text="DEPLOYMENT ZONE",
                  style="Header.TLabel").pack(anchor="w", pady=(4, 12))

        grp = ttk.LabelFrame(self, text="  OUTPUT DIRECTORY  ")
        grp.pack(fill='x', pady=(0, 10))
        path_row = ttk.Frame(grp)
        path_row.pack(fill='x', padx=16, pady=12)
        ttk.Entry(path_row, textvariable=self.controller.destination
                  ).pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Button(path_row, text="BROWSE...",
                   command=self._browse).pack(side='right')

        ttk.Label(self,
                  text="Downloads will be saved to the folder above.\n"
                       "Subfolders are created automatically.",
                  foreground="#555555").pack(anchor='w', padx=4, pady=(0, 8))

        ttk.Frame(self).pack(fill='both', expand=True)  # espaçador

        nav = _make_nav_bar(self)
        ttk.Button(nav, text="◀  BACK",
                   command=lambda: self.controller.show_frame("PageOptions")
                   ).pack(side='left')
        ttk.Button(nav, text="▶  EXECUTE",
                   command=self._execute).pack(side='right')

    def reset(self):
        pass

    def _browse(self):
        d = filedialog.askdirectory()
        if d:
            self.controller.destination.set(d)

    def _execute(self):
        if not self.controller.destination.get().strip():
            messagebox.showwarning("ALERT", "No deployment zone defined.")
            return
        self.controller.operation_mode.set("download")  # garante modo correto
        self.controller.show_frame("PageProgress")


# ── Página 4: Progresso ──────────────────────────────────────────────────────
class PageProgress(ttk.Frame):
    def __init__(self, parent, controller: WizardApp):
        super().__init__(parent)
        self.controller = controller
        self._worker: threading.Thread | None = None
        self._cancel_event = threading.Event()  # [M1] thread-safe, visível entre threads
        self._run_id       = 0                  # [1] identificador da execução corrente
        self._build()

    def _build(self):
        self.lbl_status = ttk.Label(self, text="STANDBY", style="Header.TLabel")
        self.lbl_status.pack(anchor="w", pady=(4, 6))

        self.progressbar = ttk.Progressbar(self, orient="horizontal", mode="indeterminate")
        self.progressbar.pack(fill="x", pady=(0, 8))

        self.log_area = scrolledtext.ScrolledText(
            self, state='disabled',
            bg="#000000", fg="#00FF41", insertbackground="#00FF41",
            font=("Consolas", 9), relief="flat", wrap=tk.WORD)
        self.log_area.pack(fill='both', expand=True, pady=(0, 4))

        nav = _make_nav_bar(self)
        self.btn_restart = ttk.Button(
            nav, text="◀  NOVA MISSÃO",
            command=self.controller.restart_mission, state="disabled")
        self.btn_restart.pack(side='left')
        self.btn_close = ttk.Button(
            nav, text="TERMINATE",
            command=self.controller.quit, state="disabled")
        self.btn_close.pack(side='right')
        # [C-1] Cancelar disponível DURANTE a execução (único caminho para o cancel_event).
        self.btn_cancel = ttk.Button(
            nav, text="■  CANCELAR",
            command=self._request_cancel, state="disabled")
        self.btn_cancel.pack(side='right', padx=(0, 8))

    def on_show(self):
        # [A-2] Se a execução anterior ainda está encerrando, aguarda e reagenda
        # em vez de falhar em silêncio (evita a tela de progresso "morta").
        if self._worker and self._worker.is_alive():
            self.lbl_status.config(text="AGUARDANDO OPERAÇÃO ANTERIOR ENCERRAR...")
            self.after(200, self.on_show)
            return

        # [5] Guard por modo de operação
        operation_mode = self.controller.operation_mode.get()
        if operation_mode == "download" and not self.controller.urls:
            messagebox.showwarning("ALERT", "No Targets Detected.")
            self.controller.show_frame("PageUrls")
            return
        if operation_mode == "normalize_only":
            folder = self.controller.normalize_folder.get().strip()
            if not folder or not os.path.isdir(folder):
                messagebox.showerror("ALERT", "Pasta inválida. Selecione novamente.")
                self.controller.show_frame("PageUrls")
                return

        # Salva as configurações desta execução como sugestão para a próxima.
        self.controller._save_prefs()

        self._cancel_event.clear()   # [M1] reseta o evento para nova execução
        self._run_id   += 1
        current_id      = self._run_id

        self.lbl_status.config(text="EXECUTING...")
        self.progressbar.stop()
        self.progressbar.config(mode="indeterminate")
        self.progressbar.start(10)
        self.btn_restart.config(state="disabled")
        self.btn_close.config(state="disabled")
        self.btn_cancel.config(state="normal", text="■  CANCELAR")  # [C-1]

        self.log_area.config(state='normal')
        self.log_area.delete('1.0', tk.END)
        self.log_area.config(state='disabled')

        self._log("> SYSTEM STARTED")
        self._log("> INITIALIZING DOWNLOAD SEQUENCE...")

        self._worker = threading.Thread(
            target=self._run, args=(current_id,), daemon=True)
        self._worker.start()

    def cancel(self):
        """[M1] Sinaliza cancelamento via Event (thread-safe e visível imediatamente)."""
        self._cancel_event.set()

    def _request_cancel(self):
        """[C-1] Acionado pelo botão CANCELAR durante a execução."""
        self.cancel()
        self.btn_cancel.config(state="disabled", text="CANCELANDO...")
        self.lbl_status.config(text="CANCELANDO...")

    # ── Progresso real (A-1) ─────────────────────────────────────────────────
    def _handle_progress(self, line: str, index: int, total: int):
        """Recebe linhas 'PROG|<percent>|<eta>' emitidas pelo --progress-template."""
        try:
            _, pct_str, eta_str = line.split("|", 2)
            pct = float(pct_str.strip().rstrip('%'))
        except ValueError:
            return
        self.after(0, self._set_progress, index, total, pct, eta_str.strip())

    def _set_progress(self, index: int, total: int, pct: float, eta: str):
        """Thread principal: atualiza barra determinada + rótulo de fase."""
        if str(self.progressbar['mode']) != 'determinate':
            self.progressbar.stop()
            self.progressbar.config(mode="determinate")
        self.progressbar['value'] = pct
        eta_txt = f"  (ETA {eta})" if eta and eta.lower() != 'unknown' else ""
        self.lbl_status.config(text=f"BAIXANDO {index + 1}/{total} — {pct:.0f}%{eta_txt}")

    def _indeterminate(self):
        """Thread principal: volta a barra ao modo animado (fases sem % por item)."""
        self.progressbar.stop()
        self.progressbar.config(mode="indeterminate")
        self.progressbar.start(10)

    def _kill_proc_tree(self, proc):
        """[A-4] Encerra o yt-dlp E seus filhos (ffmpeg) para não deixar órfãos."""
        try:
            if os.name == 'nt':
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            else:
                proc.terminate()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def _log(self, text: str):
        """Thread principal apenas."""
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, text + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def _safe_log(self, text: str):
        """Thread-safe: agenda _log() na thread principal."""
        self.after(0, self._log, text)

    def _run(self, run_id: int):
        """Worker thread — UI apenas via self.after(). Respeita cancel_event."""
        ctrl  = self.controller

        # [A1] Captura todos os valores Tk ANTES de qualquer operação na thread.
        # StringVar/BooleanVar.get() não são thread-safe — snapshot aqui é seguro
        # porque a thread é criada logo após os valores serem definidos na thread principal.
        dest              = ctrl.destination.get()
        dtype             = ctrl.download_type.get()
        quality           = ctrl.quality.get()
        use_cookies       = ctrl.use_cookies.get()
        browser           = ctrl.browser_cookies.get()
        cookies_file      = ctrl.cookies_file.get().strip()
        normalize_mode    = ctrl.normalize_mode.get()
        operation_mode    = ctrl.operation_mode.get()
        normalize_folder  = ctrl.normalize_folder.get().strip()
        normalize_recur   = ctrl.normalize_recursive.get()
        remove_vocals     = ctrl.remove_vocals.get()
        download_playlist = ctrl.download_playlist.get()
        yt_dlp_path       = ctrl.yt_dlp_path
        ffmpeg_path       = ctrl.ffmpeg_path
        deno_path         = ctrl.deno_path
        ffmpeg_dir        = ctrl.get_ffmpeg_dir()
        urls              = list(ctrl.urls)

        # ── Branch: normalizar pasta existente ──────────────────────────────
        if operation_mode == "normalize_only":
            self._run_normalize_only(run_id, normalize_folder, normalize_recur, ffmpeg_path)
            return
        # ────────────────────────────────────────────────────────────────────

        try:
            os.makedirs(dest, exist_ok=True)
        except Exception as exc:
            self._safe_log(f"[FATAL] Não foi possível criar o diretório: {exc}")
            self.after(0, self._on_done, run_id, "")
            return

        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # [6] microssegundos
        log_file = os.path.join(dest, f"system_log_{ts}.txt")
        self._safe_log(f"> LOG PROTOCOL: {log_file}")

        try:
            with open(log_file, "w", encoding="utf-8") as flog:
                flog.write(f"START: {datetime.datetime.now()}\n")
                total = len(urls)
                downloaded_audio_files = []

                for i, url in enumerate(urls):
                    if self._cancel_event.is_set():  # [M1] checagem entre URLs
                        self._safe_log("> [CANCELLED] Operation interrupted.")
                        flog.write("CANCELLED\n")
                        break

                    self._safe_log(f"> [{i+1}/{total}] TARGET: {url}")
                    flog.write(f"URL: {url}\n")

                    cmd = [
                        yt_dlp_path,
                        "--ffmpeg-location", ffmpeg_dir,
                        # Deno (JS runtime) para resolver challenges do YouTube.
                        "--js-runtimes", f"deno:{deno_path}",
                        # [UX] Título primeiro; nome do canal como sufixo entre colchetes.
                        "-o", os.path.join(dest, "%(title)s [%(uploader)s].%(ext)s"),
                        # Remove o " - Topic" que o YouTube Music adiciona ao nome do canal.
                        "--replace-in-metadata", "uploader", " - Topic$", "",
                        # [A-1] Progresso legível por linha para alimentar a barra.
                        "--newline",
                        "--progress-template",
                        "download:PROG|%(progress._percent_str)s|%(progress._eta_str)s",
                        "--ignore-errors", "--no-mtime", "--no-cache-dir",
                        # Link com "&list=": baixa a playlist inteira ou só o vídeo.
                        "--yes-playlist" if download_playlist else "--no-playlist",
                    ]
                    if download_playlist:
                        self._safe_log("> MODE: PLAYLIST (todos os itens da lista)")

                    if use_cookies:
                        if cookies_file and os.path.isfile(cookies_file):
                            cmd += ["--cookies", cookies_file]
                            self._safe_log(f"> AUTH: COOKIES FILE {cookies_file}")
                        else:
                            if cookies_file:
                                self._safe_log(
                                    f"[WARN] cookies.txt não encontrado: {cookies_file}")
                            cmd += ["--cookies-from-browser", browser]
                            self._safe_log(f"> AUTH: COOKIES FROM {browser.upper()}")
                    else:
                        # Sem cookies: tenta primeiro o cliente "web_embedded", que
                        # não cai no "Sign in to confirm you're not a bot" nem
                        # exige PO Token; "default" fica como fallback para vídeos
                        # que não permitem incorporação.
                        cmd += ["--extractor-args",
                                "youtube:player_client=web_embedded,default"]
                        self._safe_log("> AUTH: NONE (client web_embedded + default)")

                    if dtype == "audio":
                        cmd += ["-x", "--audio-format", "mp3",
                                "--embed-metadata", "--embed-thumbnail",
                                "--parse-metadata", "playlist_title:%(album)s"]
                        if "best" not in quality:
                            cmd += ["--audio-quality", quality]
                    else:
                        if "best" in quality:
                            cmd += ["-f", "bestvideo+bestaudio/best"]
                        else:
                            cmd += ["-S", f"res:{quality.replace('p', '')}"]
                        cmd += ["--merge-output-format", "mp4"]

                    cmd.append(url)

                    try:
                        si = None
                        cf = 0
                        if os.name == 'nt':
                            si = subprocess.STARTUPINFO()
                            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                            cf = subprocess.CREATE_NO_WINDOW

                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding='utf-8', errors='replace',
                            startupinfo=si, creationflags=cf)

                        for line in proc.stdout:
                            if self._cancel_event.is_set():  # [M1] cancela leitura
                                self._kill_proc_tree(proc)   # [A-4] mata árvore (ffmpeg)
                                break
                            line = line.strip()
                            if not line:
                                continue
                            flog.write(line + "\n")
                            if line.startswith("PROG|"):        # [A-1] progresso real
                                self._handle_progress(line, i, total)
                                continue
                            if "[download]" in line and "%" in line:
                                continue
                            if "ERROR:" in line:
                                self._safe_log(f"[ERR] {line}")
                            elif "[download] Destination:" in line:
                                fname = line.split("Destination:", 1)[1].strip()
                                self._safe_log(f"> DOWNLOADING: {os.path.basename(fname)}")
                            elif "[Merger] Merging" in line:
                                self._safe_log("> MERGING PROTOCOLS...")
                            elif "already been downloaded" in line:
                                self._safe_log("> EXISTS. SKIPPING.")
                            elif "[ExtractAudio]" in line and "Destination:" in line:
                                audio_path = line.split("Destination:", 1)[1].strip()
                                self._safe_log("> EXTRACTING AUDIO...")
                                # [FIX] Registra o caminho SEM checar existência aqui:
                                # o ffmpeg ainda não criou o arquivo neste instante.
                                # A existência é validada após o loop de downloads.
                                downloaded_audio_files.append(audio_path)
                            elif "[ExtractAudio]" in line:
                                self._safe_log("> EXTRACTING AUDIO...")
                            elif "Setting metadata" in line:
                                self._safe_log("> INJECTING METADATA...")

                        proc.wait()
                        if not self._cancel_event.is_set():
                            if proc.returncode == 0:
                                self._safe_log("> TARGET SECURED [OK]")
                            else:
                                self._safe_log(
                                    f"> TARGET: WARNINGS (code {proc.returncode}) [WARN]")

                    except FileNotFoundError:
                        self._safe_log(
                            f"[FATAL] yt-dlp não encontrado: {ctrl.yt_dlp_path}")
                        flog.write("FATAL: yt-dlp not found\n")
                    except Exception as exc:
                        self._safe_log(f"[FATAL]: {exc}")
                        flog.write(f"FATAL: {exc}\n")

                # [FIX] Já com todos os downloads concluídos, mantém apenas os
                # arquivos de áudio que realmente existem em disco.
                downloaded_audio_files = [f for f in downloaded_audio_files
                                          if os.path.isfile(f)]

                # ── Pós-processamento: remoção de vocais (instrumental) ──
                # Executa ANTES da normalização, substituindo o arquivo pelo
                # instrumental; a normalização seguinte já atua sobre ele.
                if (not self._cancel_event.is_set()
                        and dtype == "audio" and remove_vocals
                        and downloaded_audio_files):
                    self._safe_log("> ───────────────────────────")
                    self.after(0, self._indeterminate)
                    if not _separator_ready():
                        self._safe_log("> INSTALANDO MOTOR DE SEPARAÇÃO (primeira vez)...")
                        self._safe_log("> Baixa PyTorch/CUDA (~2 GB); pode demorar vários minutos.")
                        try:
                            _install_separator(self._safe_log)
                        except Exception as exc:
                            self._safe_log(f"[WARN] Falha ao instalar separador: {exc}")
                            self._safe_log("> Pulando remoção de vocais.")
                            remove_vocals = False
                    if remove_vocals and not self._cancel_event.is_set():
                        self._safe_log("> REMOVENDO VOCAIS (Demucs htdemucs, GPU)...")
                        n_sep = len(downloaded_audio_files)
                        for idx, audio_file in enumerate(downloaded_audio_files, 1):
                            if self._cancel_event.is_set():
                                break
                            self._safe_log(f">  [{idx}/{n_sep}] {os.path.basename(audio_file)}")
                            ok, msg = _separate_vocals(
                                audio_file, self._safe_log, ffmpeg_path, ffmpeg_dir)
                            if ok:
                                self._safe_log(">  INSTRUMENTAL [OK]")
                                flog.write(f"INSTRUMENTAL: {audio_file}\n")
                            else:
                                self._safe_log(f"[WARN] Separação falhou: {msg}")
                                flog.write(f"SEPARATE_FAIL: {audio_file} — {msg}\n")
                        self._safe_log("> REMOÇÃO DE VOCAIS COMPLETA.")

                # [A4] END gravado APÓS a normalização para log consistente
                # ── Pós-processamento: normalização EBU R128 ──
                if (not self._cancel_event.is_set()
                        and dtype == "audio"
                        and normalize_mode == "ebu_r128"
                        and downloaded_audio_files):
                    self._safe_log("> ───────────────────────────")
                    self._safe_log("> NORMALIZING AUDIO  (EBU R128 / -14 LUFS)...")
                    self.after(0, self._indeterminate)  # fase sem % por item
                    for idx, audio_file in enumerate(downloaded_audio_files, 1):
                        if self._cancel_event.is_set():
                            break
                        fname = os.path.basename(audio_file)
                        self._safe_log(f">  [{idx}/{len(downloaded_audio_files)}] {fname}")
                        ok, msg = self._normalize_audio(audio_file, ffmpeg_path)
                        if ok:
                            self._safe_log(">  NORMALIZED [OK]")
                            flog.write(f"NORMALIZED: {audio_file}\n")
                        else:
                            self._safe_log(f"[WARN] Normalize failed: {msg}")
                            flog.write(f"NORMALIZE_FAIL: {msg}\n")
                    self._safe_log("> NORMALIZATION COMPLETE.")

                flog.write(f"END: {datetime.datetime.now()}\n")  # [A4] após tudo

        except Exception as exc:
            self._safe_log(f"[FATAL] Erro ao gravar log: {exc}")

        self.after(0, self._on_done, run_id, dest)

    def _run_normalize_only(self, run_id: int, folder: str,
                            recursive: bool, ffmpeg_path: str):
        """
        Worker thread: normaliza todos os .mp3 de uma pasta existente.
        Não realiza nenhum download.
        """
        mp3_files = _find_mp3_files(folder, recursive)  # sem glob, compatível com PyInstaller

        if not mp3_files:
            self._safe_log("[WARN] Nenhum arquivo .mp3 encontrado na pasta selecionada.")
            self.after(0, self._on_done, run_id, folder)
            return

        total = len(mp3_files)
        self._safe_log(f"> PASTA: {folder}")
        self._safe_log(f"> {total} arquivo(s) .mp3 encontrado(s). Iniciando normalização...")

        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        log_file = os.path.join(folder, f"normalize_log_{ts}.txt")
        self._safe_log(f"> LOG: {log_file}")

        try:
            with open(log_file, "w", encoding="utf-8") as flog:
                flog.write(f"START: {datetime.datetime.now()}\n"
                           f"FOLDER: {folder}\nFILES: {total}\n")

                ok_count   = 0
                fail_count = 0

                for idx, mp3 in enumerate(mp3_files, 1):
                    if self._cancel_event.is_set():
                        self._safe_log("> [CANCELLED] Operação interrompida.")
                        flog.write("CANCELLED\n")
                        break

                    fname = os.path.basename(mp3)
                    self._safe_log(f"> [{idx}/{total}] {fname}")
                    ok, msg = self._normalize_audio(mp3, ffmpeg_path)

                    if ok:
                        self._safe_log(">  NORMALIZED [OK]")
                        flog.write(f"OK: {mp3}\n")
                        ok_count += 1
                    else:
                        self._safe_log(f"[WARN] {fname}: {msg}")
                        flog.write(f"FAIL: {mp3} — {msg}\n")
                        fail_count += 1

                flog.write(f"END: {datetime.datetime.now()}\n"
                           f"SUCCESS: {ok_count}  FAIL: {fail_count}\n")

            summary = f"> CONCLUÍDO — {ok_count} OK, {fail_count} falha(s)."
            self._safe_log(summary)

        except Exception as exc:
            self._safe_log(f"[FATAL] Erro ao gravar log: {exc}")

        self.after(0, self._on_done, run_id, folder)

    def _normalize_audio(self, audio_file: str, ffmpeg_path: str) -> tuple:
        """
        Normaliza um arquivo MP3 para EBU R128 (-14 LUFS, TP -1.5 dBTP)
        usando dois passes do filtro loudnorm do FFmpeg.
        Retorna (True, "") em caso de sucesso ou (False, mensagem_erro).
        """
        si = None
        cf = 0
        if os.name == 'nt':
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            cf = subprocess.CREATE_NO_WINDOW

        # ── Passo 1: medir loudness ──  [C1] stderr separado para capturar JSON
        try:
            p1 = subprocess.run(
                [ffmpeg_path, "-y", "-i", audio_file,
                 "-af", "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
                 "-f", "null", "-"],
                stdout=subprocess.DEVNULL,   # descarta stdout (vazio neste caso)
                stderr=subprocess.PIPE,      # [C1] JSON vem no stderr do ffmpeg
                text=True, encoding="utf-8", errors="replace",
                timeout=300,                 # [M3] 5 min máx por arquivo
                startupinfo=si, creationflags=cf)
        except subprocess.TimeoutExpired:
            return False, "Pass 1 excedeu timeout de 5 minutos"
        except Exception as exc:
            return False, f"Pass 1 falhou: {exc}"

        # [C1] JSON está em stderr
        json_match = re.search(r'\{[^{}]+\}', p1.stderr, re.DOTALL)
        if not json_match:
            return False, "Não foi possível parsear JSON do loudnorm (pass 1)"
        try:
            stats = json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            return False, f"JSON inválido: {exc}"

        # ── Passo 2: aplicar normalização ──  [C2] temp_file limpo com try/finally
        temp_file = audio_file + ".norm_tmp.mp3"
        af_filter = (
            f"loudnorm=I=-14:TP=-1.5:LRA=11"
            f":measured_I={stats['input_i']}"
            f":measured_LRA={stats['input_lra']}"
            f":measured_tp={stats['input_tp']}"
            f":measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}"
            f":linear=true"
        )
        try:
            p2 = subprocess.run(
                [ffmpeg_path, "-y", "-i", audio_file,
                 "-af", af_filter,
                 "-c:a", "libmp3lame", "-b:a", "320k",
                 "-id3v2_version", "3",
                 temp_file],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                timeout=300,
                startupinfo=si, creationflags=cf)
        except subprocess.TimeoutExpired:
            return False, "Pass 2 excedeu timeout de 5 minutos"
        except Exception as exc:
            return False, f"Pass 2 falhou: {exc}"
        finally:
            # garante limpeza do temp_file em qualquer cenário de falha
            if 'temp_file' in locals() and os.path.exists(temp_file) and not (
                    'p2' in locals() and hasattr(p2, 'returncode') and p2.returncode == 0):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

        if p2.returncode != 0:
            return False, f"FFmpeg retornou código {p2.returncode}: {p2.stderr[-200:]}"

        # Substitui o arquivo original pelo normalizado
        try:
            os.replace(temp_file, audio_file)
        except Exception as exc:
            return False, f"Falha ao substituir arquivo: {exc}"

        return True, ""

    def _on_done(self, run_id: int, dest: str):
        """[1] Ignora se não é a execução corrente (stale callback de run anterior)."""
        if run_id != self._run_id:
            return

        self.progressbar.stop()
        self.progressbar.config(mode="determinate", value=100)

        if self._cancel_event.is_set():
            self.lbl_status.config(text="OPERATION CANCELLED")
            self._log("> Cancelled. Press NOVA MISSÃO to restart.")
        else:
            self.lbl_status.config(text="MISSION ACCOMPLISHED  ✔")
            self._log("> ALL TASKS FINALIZED.")
            if dest:
                self._log(f"> FILES SAVED TO: {dest}")

        self.btn_restart.config(state="normal")
        self.btn_close.config(state="normal")
        self.btn_cancel.config(state="disabled", text="■  CANCELAR")  # [C-1]

        if dest and not self._cancel_event.is_set():
            try:
                os.startfile(dest)
            except Exception:
                pass


# ── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = WizardApp()
    app.mainloop()
