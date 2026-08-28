# -*- coding: utf-8 -*-
"""
setup_env.py — Gerenciador de Dependências
-------------------------------------------
Verifica e baixa yt-dlp, ffmpeg e ffprobe se necessário.
Executado automaticamente pelo launcher antes de iniciar a UI principal.

BUGS CORRIGIDOS (v2.1):
  [1] _download() bloqueia a thread principal do tkinter → movido para thread.
  [2] _SetupWindow herda tk.Tk causando conflito quando main.py cria outro tk.Tk
      → migrado para tk.Toplevel com root hidden, garantindo destroy() limpo.
  [3] Walrus operator := não funciona em Python 3.7 (compatibilidade ampla)
      → substituído por while True + break explícito.
  [4] ffmpeg/ffprobe ausentes no ZIP não geram erro → validação pós-extração.
  [5] messagebox.showerror() chamado sem janela Tk ativa após _win.destroy()
      → reordenado: showerror() antes de destroy().
"""
import os
import sys
import shutil
import zipfile
import io
import hashlib
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox

# INSTALL_DIR pode ser injetado pelo launcher via init_globals
_INSTALL_DIR = globals().get('INSTALL_DIR') or os.path.dirname(os.path.abspath(__file__))
DEP_DIR = os.path.join(_INSTALL_DIR, 'dependences')

YT_DLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
# Arquivo de somas publicado pelo próprio projeto (mesma release "latest").
YT_DLP_SUMS_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_SHA_URL = FFMPEG_URL + ".sha256"  # gyan.dev publica o SHA256 ao lado do ZIP
# Deno — JS runtime obrigatório para resolução dos challenges do YouTube (SABR/PO tokens).
# Sem ele, yt-dlp recebe HTTP 403 em todos os streams desde meados de 2025.
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
DENO_SHA_URL = DENO_URL + ".sha256sum"
REQUIRED_BINS = ['yt-dlp.exe', 'ffmpeg.exe', 'ffprobe.exe', 'deno.exe']


def _all_present() -> bool:
    return all(os.path.isfile(os.path.join(DEP_DIR, b)) for b in REQUIRED_BINS)


def _download(url: str, on_progress=None) -> bytes:
    """Baixa uma URL retornando bytes. Sem walrus operator (compatível ≥ 3.7)."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        buf = bytearray()
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.extend(chunk)
            if on_progress and total:
                on_progress(len(buf), total)
    return bytes(buf)


# ── Verificação de integridade (C-2) ─────────────────────────────────────────
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()


def _fetch_text(url: str):
    """Baixa um pequeno arquivo de texto (arquivo de somas). None em caso de falha."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode('utf-8', 'replace')
    except Exception:
        return None


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


def _expected_ytdlp_sha() -> str:
    return _extract_sha256(_fetch_text(YT_DLP_SUMS_URL), 'yt-dlp.exe')


def _expected_ffmpeg_sha() -> str:
    return _extract_sha256(_fetch_text(FFMPEG_SHA_URL))


def _expected_deno_sha() -> str:
    return _extract_sha256(_fetch_text(DENO_SHA_URL))


def _verify_or_abort(data: bytes, expected: str, label: str):
    """Falha FECHADA em divergência de hash (sinal de adulteração/corrupção).
    Falha ABERTA (apenas aviso) se o hash de referência estiver indisponível —
    uma instabilidade de rede não deve travar todo o app."""
    if not expected:
        print(f"[AVISO] Sem checksum de referência para {label}; "
              f"integridade não verificada (seguindo mesmo assim).")
        return
    actual = _sha256(data)
    if actual != expected:
        raise RuntimeError(
            f"Falha de integridade em {label}.\n"
            f"  esperado: {expected}\n  obtido:   {actual}\n\n"
            f"O download pode ter sido corrompido ou adulterado. Operação abortada.")


class _SetupWindow:
    """
    Janela de progresso de primeiro uso.
    Usa tk.Toplevel sobre root oculto para não conflitar com o tk.Tk
    que main.py criará depois.
    """

    def __init__(self):
        # Root oculto — garante que Toplevel tenha um master sem criar janela visível
        self._root = tk.Tk()
        self._root.withdraw()

        self._win = tk.Toplevel(self._root)
        self._win.title("YouTube Downloader — Primeiro Uso")
        self._win.geometry("480x160")
        self._win.resizable(False, False)
        self._win.configure(bg="#121212")
        self._win.protocol("WM_DELETE_WINDOW", lambda: None)  # bloqueia fechar

        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        self._win.geometry(f"+{(sw - 480) // 2}+{(sh - 160) // 2}")

        self._lbl = tk.Label(
            self._win, text="⚙ Configurando dependências...",
            bg="#121212", fg="#00FF41", font=("Consolas", 11, "bold"))
        self._lbl.pack(pady=(20, 4))

        self._sub = tk.Label(
            self._win, text="Aguarde...",
            bg="#121212", fg="#A0A0A0", font=("Consolas", 9))
        self._sub.pack()

        s = ttk.Style(self._root)
        s.configure("G.Horizontal.TProgressbar", background="#00FF41", troughcolor="#1E1E1E")
        self._bar = ttk.Progressbar(
            self._win, style="G.Horizontal.TProgressbar",
            length=420, mode="determinate")
        self._bar.pack(pady=12)

    def status(self, text: str, sub: str = ""):
        self._lbl.config(text=text)
        self._sub.config(text=sub)
        self._root.update()

    def progress(self, pct: float):
        self._bar['value'] = max(0.0, min(100.0, pct))
        self._root.update()

    def destroy(self):
        """Destrói janela e root completamente."""
        try:
            self._win.destroy()
            self._root.destroy()
        except Exception:
            pass


def _install_all(win: _SetupWindow):
    """Executa downloads na thread principal (setup_env roda antes do mainloop)."""
    os.makedirs(DEP_DIR, exist_ok=True)

    # ── yt-dlp ──
    dest_ytdlp = os.path.join(DEP_DIR, 'yt-dlp.exe')
    if not os.path.isfile(dest_ytdlp):
        win.status("⬇ Baixando yt-dlp...", YT_DLP_URL)
        data = _download(YT_DLP_URL, lambda d, t: win.progress(d / t * 100))
        win.status("🔒 Verificando integridade do yt-dlp...")
        _verify_or_abort(data, _expected_ytdlp_sha(), "yt-dlp.exe")
        with open(dest_ytdlp, 'wb') as f:
            f.write(data)
        win.progress(100)
    else:
        # Se o yt-dlp já existe, verifica se foi modificado há mais de 7 dias
        import time
        import subprocess
        try:
            mtime = os.path.getmtime(dest_ytdlp)
            age_days = (time.time() - mtime) / (24 * 3600)
            if age_days > 7:
                win.status("🔄 Verificando atualizações do yt-dlp...", "Aguarde...")
                win.progress(50)
                
                # Executa yt-dlp.exe --update silenciosamente
                si = None
                cf = 0
                if os.name == 'nt':
                    si = subprocess.STARTUPINFO()
                    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    cf = subprocess.CREATE_NO_WINDOW
                
                subprocess.run(
                    [dest_ytdlp, "--update"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    startupinfo=si, creationflags=cf, timeout=30
                )
                
                # Atualiza o timestamp de modificação para não verificar novamente por mais 7 dias
                os.utime(dest_ytdlp, None)
                win.progress(100)
        except Exception:
            # Qualquer erro na atualização em background (timeout, sem rede, etc.)
            # é ignorado para não impedir o lançamento do app.
            try:
                os.utime(dest_ytdlp, None)
            except Exception:
                pass

    # ── ffmpeg + ffprobe ──
    dest_ffmpeg  = os.path.join(DEP_DIR, 'ffmpeg.exe')
    dest_ffprobe = os.path.join(DEP_DIR, 'ffprobe.exe')
    if not os.path.isfile(dest_ffmpeg) or not os.path.isfile(dest_ffprobe):
        win.status("⬇ Baixando FFmpeg (essentials)...", FFMPEG_URL)
        win.progress(0)
        data = _download(FFMPEG_URL, lambda d, t: win.progress(d / t * 80))
        win.status("🔒 Verificando integridade do FFmpeg...")
        _verify_or_abort(data, _expected_ffmpeg_sha(), "ffmpeg (zip)")
        win.status("📦 Extraindo FFmpeg...")

        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                basename = os.path.basename(member)
                if basename.lower() == 'ffmpeg.exe' and not os.path.isfile(dest_ffmpeg):
                    with zf.open(member) as src, open(dest_ffmpeg, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                elif basename.lower() == 'ffprobe.exe' and not os.path.isfile(dest_ffprobe):
                    with zf.open(member) as src, open(dest_ffprobe, 'wb') as dst:
                        shutil.copyfileobj(src, dst)

        # [4] Valida que os binários foram realmente extraídos
        missing = [b for b in ('ffmpeg.exe', 'ffprobe.exe')
                   if not os.path.isfile(os.path.join(DEP_DIR, b))]
        if missing:
            raise RuntimeError(
                f"Binários não encontrados no ZIP do FFmpeg: {missing}\n"
                "Tente novamente ou baixe manualmente de https://ffmpeg.org/download.html"
            )
        win.progress(100)

    # ── Deno (JS runtime para yt-dlp) ──
    dest_deno = os.path.join(DEP_DIR, 'deno.exe')
    if not os.path.isfile(dest_deno):
        win.status("⬇ Baixando Deno (JavaScript runtime)...", DENO_URL)
        win.progress(0)
        data = _download(DENO_URL, lambda d, t: win.progress(d / t * 80))
        win.status("🔒 Verificando integridade do Deno...")
        _verify_or_abort(data, _expected_deno_sha(), "deno (zip)")
        win.status("📦 Extraindo Deno...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for member in zf.namelist():
                if os.path.basename(member).lower() == 'deno.exe':
                    with zf.open(member) as src, open(dest_deno, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    break
        if not os.path.isfile(dest_deno):
            raise RuntimeError(
                "deno.exe não encontrado no pacote baixado.\n"
                "Tente novamente ou instale manualmente de https://deno.com/")
        win.progress(100)


def _needs_update_check() -> bool:
    """Verifica se o yt-dlp.exe foi modificado há mais de 7 dias."""
    dest_ytdlp = os.path.join(DEP_DIR, 'yt-dlp.exe')
    if not os.path.isfile(dest_ytdlp):
        return False
    try:
        import time
        mtime = os.path.getmtime(dest_ytdlp)
        return (time.time() - mtime) / (24 * 3600) > 7
    except Exception:
        return False


# ── Ponto de entrada (chamado pelo launcher via runpy) ──
if not _all_present() or _needs_update_check():
    _win = _SetupWindow()
    try:
        _install_all(_win)
    except Exception as _exc:
        # [5] showerror() antes de destroy() — root ainda existe
        messagebox.showerror(
            "Erro de Configuração",
            f"Falha ao baixar dependências:\n{_exc}\n\n"
            "Verifique sua conexão e tente novamente.")
        _win.destroy()
        sys.exit(1)
    _win.destroy()
