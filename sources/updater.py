# -*- coding: utf-8 -*-
"""
updater.py — Atualizacao Automatica via Git
-------------------------------------------
Executado pelo launcher ANTES de setup_env.py e main.py: confere se ha
commits novos no repositorio e atualiza os scripts da instalacao local,
de modo que a aplicacao ja inicie na versao mais recente.

Dois modos de operacao (detectados automaticamente):

  [1] Clone Git — quando o diretorio de instalacao e um repositorio git e o
      executavel `git` esta disponivel:  fetch + merge --ff-only.

  [2] Instalacao empacotada (padrao do instalador Inno Setup) — o diretorio
      nao e um clone e a maquina pode nao ter git instalado. Nesse caso o
      HEAD remoto e consultado pelo protocolo HTTP do proprio git
      (/info/refs?service=git-upload-pack) e os arquivos rastreados sao
      baixados de raw.githubusercontent.com, fixados no commit consultado.

Principios:
  * FALHA ABERTA: qualquer erro (sem rede, DNS, repo fora do ar, timeout)
    apenas registra log e deixa a aplicacao iniciar na versao atual.
  * ATOMICO: os arquivos so sao substituidos depois que TODOS os downloads
    terminam e sao validados; qualquer falha na aplicacao faz rollback via
    copias .bak.
  * SEM SEGREDOS FIXOS: repositorio, branch, timeout e token vem do ambiente.

Variaveis de ambiente (todas opcionais):
  YTDL_UPDATE_REPO     "owner/repo"   (padrao: GrandoJunior/Youtube)
  YTDL_UPDATE_BRANCH   branch alvo    (padrao: main)
  YTDL_UPDATE_TIMEOUT  segundos por requisicao (padrao: 10)
  YTDL_UPDATE_BUDGET   segundos totais para toda a checagem (padrao: 60)
  YTDL_UPDATE_TOKEN    token GitHub para repositorios privados
                       (opt-in explicito; GITHUB_TOKEN nao e usado)
  YTDL_DISABLE_UPDATE  1/true/yes/on desliga completamente a checagem
"""
import os
import re
import json
import time
import hashlib
import shutil
import subprocess
import urllib.request
import urllib.error

# INSTALL_DIR pode ser injetado pelo launcher via init_globals
_INSTALL_DIR = globals().get('INSTALL_DIR') or os.path.dirname(os.path.abspath(__file__))

DEFAULT_REPO   = "GrandoJunior/Youtube"
DEFAULT_BRANCH = "main"

STATE_FILE = os.path.join(_INSTALL_DIR, '.update_state.json')
LOG_FILE   = os.path.join(_INSTALL_DIR, 'updater.log')
LOG_MAX    = 256 * 1024

# Arquivo instalado  ->  caminho dentro do repositorio.
# Apenas o que o instalador implanta e o que roda em tempo de execucao;
# o launcher.exe e binario compilado e nao pode se auto-substituir em uso.
TRACKED = {
    'main.py':      'sources/main.py',
    'setup_env.py': 'sources/setup_env.py',
    'updater.py':   'sources/updater.py',
    'splash.png':   'sources/splash.png',
}

_UA = {"User-Agent": "YoutubeDownloader-Updater"}


# ── Utilitarios ──────────────────────────────────────────────────────────────
def _log(msg: str):
    """Registra em updater.log (app roda sem console). Nunca levanta excecao."""
    line = "[%s] %s" % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        mode = 'a'
        if os.path.isfile(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX:
            mode = 'w'
        with open(LOG_FILE, mode, encoding='utf-8', errors='replace') as f:
            f.write(line + "\n")
    except Exception:
        pass


def _flag(name: str) -> bool:
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, '').strip()))
    except Exception:
        return default


def _config() -> dict:
    repo = (os.environ.get('YTDL_UPDATE_REPO') or DEFAULT_REPO).strip().strip('/')
    branch = (os.environ.get('YTDL_UPDATE_BRANCH') or DEFAULT_BRANCH).strip()
    # Somente opt-in explicito: um GITHUB_TOKEN generico presente na maquina
    # (escopo de outro repositorio) faria o raw.githubusercontent responder 404
    # e a atualizacao seria silenciosamente descartada.
    token = (os.environ.get('YTDL_UPDATE_TOKEN') or '').strip()
    return {
        'repo': repo,
        'branch': branch,
        'token': token,
        'timeout': _int_env('YTDL_UPDATE_TIMEOUT', 10),
        'budget': _int_env('YTDL_UPDATE_BUDGET', 60),
    }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().lower()


def _sha256_file(path: str) -> str:
    try:
        with open(path, 'rb') as f:
            return _sha256_bytes(f.read())
    except Exception:
        return ''


def _http_get(url: str, timeout: int, token: str = '', accept: str = '') -> bytes:
    headers = dict(_UA)
    if accept:
        headers['Accept'] = accept
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ── Estado local (ultimo commit conhecido) ───────────────────────────────────
def _load_state() -> dict:
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(cfg: dict, commit: str):
    try:
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({
                'repo': cfg['repo'],
                'branch': cfg['branch'],
                'commit': commit,
                'updated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
            }, f, indent=2)
    except Exception as exc:
        _log("Nao foi possivel gravar %s: %s" % (STATE_FILE, exc))


# ── Consulta do HEAD remoto ──────────────────────────────────────────────────
def _remote_head_sha(cfg: dict) -> str:
    """SHA do topo da branch remota. String vazia se indisponivel."""
    repo, branch, timeout, token = cfg['repo'], cfg['branch'], cfg['timeout'], cfg['token']

    # 1) Protocolo HTTP do git: resposta minima, sem limite de taxa.
    try:
        url = "https://github.com/%s.git/info/refs?service=git-upload-pack" % repo
        text = _http_get(url, timeout, token).decode('utf-8', 'replace')
        m = re.search(r'([0-9a-f]{40})\srefs/heads/%s(?:[\x00\r\n]|$)'
                      % re.escape(branch), text)
        if m:
            return m.group(1)
        _log("Branch '%s' nao encontrada em refs de %s." % (branch, repo))
    except Exception as exc:
        _log("info/refs indisponivel (%s); tentando API." % exc)

    # 2) Fallback: API do GitHub devolvendo apenas o SHA em texto puro.
    try:
        url = "https://api.github.com/repos/%s/commits/%s" % (repo, branch)
        sha = _http_get(url, timeout, token,
                        accept="application/vnd.github.sha").decode('ascii', 'ignore').strip()
        if re.fullmatch(r'[0-9a-f]{40}', sha):
            return sha
    except Exception as exc:
        _log("API do GitHub indisponivel: %s" % exc)

    return ''


# ── Modo 1: diretorio de instalacao e um clone git ───────────────────────────
def _is_git_checkout() -> bool:
    return os.path.exists(os.path.join(_INSTALL_DIR, '.git'))


def _run_git(args: list, timeout: int) -> subprocess.CompletedProcess:
    si, cf = None, 0
    if os.name == 'nt':
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        cf = subprocess.CREATE_NO_WINDOW
    return subprocess.run(
        [_git_exe(), '-C', _INSTALL_DIR] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=timeout, startupinfo=si, creationflags=cf)


def _git_exe() -> str:
    return shutil.which('git') or shutil.which('git.exe') or ''


def _update_via_git(cfg: dict, ui) -> bool:
    """fetch + merge --ff-only no clone local. True se algo foi atualizado."""
    branch, timeout = cfg['branch'], cfg['timeout']
    before = _run_git(['rev-parse', 'HEAD'], timeout)
    if before.returncode != 0:
        _log("git rev-parse falhou: %s" % before.stdout.strip())
        return False

    # --untracked-files=no: arquivos nao rastreados (updater.log, dependences/,
    # .env) sao normais na pasta de execucao e nao devem bloquear o fast-forward;
    # apenas modificacoes em arquivos versionados impedem a atualizacao.
    dirty = _run_git(['status', '--porcelain', '--untracked-files=no'], timeout)
    if dirty.returncode == 0 and dirty.stdout.strip():
        _log("Clone com alteracoes locais nao commitadas; atualizacao ignorada.")
        return False

    ui.status("Verificando atualizacoes...", "git fetch origin %s" % branch)
    fetched = _run_git(['fetch', '--quiet', 'origin', branch], timeout * 6)
    if fetched.returncode != 0:
        _log("git fetch falhou: %s" % fetched.stdout.strip())
        return False

    head = _run_git(['rev-parse', 'FETCH_HEAD'], timeout)
    if head.returncode == 0 and head.stdout.strip() == before.stdout.strip():
        _log("Clone git ja esta atualizado (%s)." % before.stdout.strip()[:7])
        return False

    ui.status("Aplicando atualizacao...", "git merge --ff-only")
    merged = _run_git(['merge', '--ff-only', 'FETCH_HEAD'], timeout * 3)
    if merged.returncode != 0:
        _log("git merge --ff-only recusado (historico divergente): %s"
             % merged.stdout.strip())
        return False

    after = _run_git(['rev-parse', 'HEAD'], timeout)
    _log("Clone git atualizado: %s -> %s"
         % (before.stdout.strip()[:7], after.stdout.strip()[:7]))
    return True


# ── Modo 2: instalacao empacotada (download dos arquivos rastreados) ─────────
def _download_changes(cfg: dict, commit: str, deadline: float, ui) -> dict:
    """Baixa os arquivos rastreados e devolve {caminho_local: conteudo} apenas
    para os que diferem do que esta instalado."""
    repo, timeout, token = cfg['repo'], cfg['timeout'], cfg['token']
    changes = {}
    missing = 0
    for local_name, repo_path in sorted(TRACKED.items()):
        if time.time() > deadline:
            raise RuntimeError("tempo limite da checagem de atualizacao excedido")
        local_path = os.path.join(_INSTALL_DIR, local_name)
        url = "https://raw.githubusercontent.com/%s/%s/%s" % (repo, commit, repo_path)
        ui.status("Baixando atualizacao...", local_name)
        try:
            data = _http_get(url, timeout, token)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Arquivo nao existe nesta revisao (ex.: renomeado/removido no
                # repositorio) — ignora este, mas contabiliza.
                missing += 1
                _log("%s ausente em %s; ignorado." % (repo_path, commit[:7]))
                continue
            raise
        if not data:
            raise RuntimeError("conteudo vazio recebido para %s" % repo_path)
        if local_name.endswith('.py'):
            # Rejeita download truncado/corrompido antes de instalar.
            compile(data, local_path, 'exec')
        if _sha256_bytes(data) != _sha256_file(local_path):
            changes[local_path] = data

    if missing == len(TRACKED):
        # Nenhum arquivo rastreado pode ser lido: repositorio/branch errados ou
        # token sem escopo. Nao e "tudo em dia" — aborta sem gravar estado.
        raise RuntimeError(
            "nenhum arquivo rastreado encontrado em %s@%s" % (repo, commit[:7]))
    return changes


def _apply_changes(changes: dict) -> bool:
    """Grava .new, faz backup .bak e substitui atomicamente. Rollback total em
    caso de falha no meio do caminho."""
    applied = []
    try:
        for path, data in changes.items():
            tmp = path + '.new'
            with open(tmp, 'wb') as f:
                f.write(data)
            if os.path.isfile(path):
                shutil.copy2(path, path + '.bak')
            os.replace(tmp, path)
            applied.append(path)
            _log("Atualizado: %s" % os.path.basename(path))
        return True
    except Exception as exc:
        _log("Falha ao aplicar atualizacao (%s); revertendo." % exc)
        for path in applied:
            bak = path + '.bak'
            try:
                if os.path.isfile(bak):
                    shutil.copy2(bak, path)
            except Exception:
                _log("ATENCAO: rollback de %s falhou." % path)
        for path in changes:
            try:
                if os.path.isfile(path + '.new'):
                    os.remove(path + '.new')
            except Exception:
                pass
        return False


def _update_via_http(cfg: dict, ui) -> bool:
    deadline = time.time() + cfg['budget']

    ui.status("Verificando atualizacoes...", "%s @ %s" % (cfg['repo'], cfg['branch']))
    commit = _remote_head_sha(cfg)
    if not commit:
        _log("HEAD remoto indisponivel; mantendo versao instalada.")
        return False

    state = _load_state()
    same_target = (state.get('repo') == cfg['repo']
                   and state.get('branch') == cfg['branch'])
    files_present = all(os.path.isfile(os.path.join(_INSTALL_DIR, n)) for n in TRACKED)
    if same_target and state.get('commit') == commit and files_present:
        _log("Ja na versao mais recente (%s)." % commit[:7])
        return False

    changes = _download_changes(cfg, commit, deadline, ui)
    if not changes:
        _save_state(cfg, commit)
        _log("Sem diferencas de conteudo; marcado como %s." % commit[:7])
        return False

    ui.status("Aplicando atualizacao...", "%d arquivo(s)" % len(changes))
    if not _apply_changes(changes):
        return False

    _save_state(cfg, commit)
    _log("Atualizacao concluida para %s (%d arquivo(s))." % (commit[:7], len(changes)))
    return True


# ── Janela de progresso (criada sob demanda) ─────────────────────────────────
class _ProgressUI:
    """Toplevel sobre root oculto — mesmo padrao de setup_env.py, para nao
    conflitar com o tk.Tk que main.py cria depois. A janela so aparece se a
    checagem demorar o suficiente para haver algo a mostrar."""

    def __init__(self):
        self._root = None
        self._win = None
        self._lbl = None
        self._sub = None

    def _ensure(self):
        if self._win is not None:
            return
        import tkinter as tk
        self._root = tk.Tk()
        self._root.withdraw()
        self._win = tk.Toplevel(self._root)
        self._win.title("YouTube Downloader — Atualizacao")
        self._win.geometry("480x130")
        self._win.resizable(False, False)
        self._win.configure(bg="#121212")
        self._win.protocol("WM_DELETE_WINDOW", lambda: None)
        sw = self._win.winfo_screenwidth()
        sh = self._win.winfo_screenheight()
        self._win.geometry("+%d+%d" % ((sw - 480) // 2, (sh - 130) // 2))
        self._lbl = tk.Label(self._win, text="⟳ Verificando atualizações...",
                             bg="#121212", fg="#00FF41",
                             font=("Consolas", 11, "bold"))
        self._lbl.pack(pady=(26, 6))
        self._sub = tk.Label(self._win, text="", bg="#121212", fg="#A0A0A0",
                             font=("Consolas", 9))
        self._sub.pack()

    def status(self, text: str, sub: str = ""):
        try:
            self._ensure()
            self._lbl.config(text="⟳ " + text)
            self._sub.config(text=sub)
            self._root.update()
        except Exception:
            pass  # ambiente sem display nao deve impedir a atualizacao

    def destroy(self):
        try:
            if self._win is not None:
                self._win.destroy()
                self._root.destroy()
        except Exception:
            pass


# ── Ponto de entrada ─────────────────────────────────────────────────────────
def check_and_update() -> bool:
    """Confere o repositorio e atualiza a instalacao. True se algo mudou.
    Nunca levanta excecao: o app deve iniciar mesmo sem rede."""
    if _flag('YTDL_DISABLE_UPDATE'):
        _log("Atualizacao automatica desativada por YTDL_DISABLE_UPDATE.")
        return False

    cfg = _config()
    ui = _ProgressUI()
    try:
        if _is_git_checkout() and _git_exe():
            return _update_via_git(cfg, ui)
        return _update_via_http(cfg, ui)
    except Exception as exc:
        _log("Checagem de atualizacao abortada: %s" % exc)
        return False
    finally:
        ui.destroy()


if __name__ in ('__update__', '__main__'):
    check_and_update()
