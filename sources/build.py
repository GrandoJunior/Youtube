# -*- coding: utf-8 -*-
"""
build.py — Pipeline de Build Completo
--------------------------------------
Etapas:
  1. Verifica / instala PyInstaller
  2. Compila launcher.py → bin/launcher.exe  (bootstrapper mínimo)
  3. Localiza o Inno Setup Compiler (ISCC.exe)
  4. Gera o instalador  → bin/YoutubeDownloader_Setup_v<AppVersion>.exe
     (versao lida de installer/installer.iss — fonte unica)
  5. Executa o instalador silenciosamente (install local, usuário atual)
"""
import sys
import os
import subprocess
import shutil
import io
import re

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # raiz do projeto
SRC_DIR     = os.path.join(BASE_DIR, 'sources')
BIN_DIR     = os.path.join(BASE_DIR, 'bin')
BUILD_DIR   = os.path.join(BASE_DIR, 'build')
# Evita bloqueio de sincronizadores de nuvem (Google Drive, OneDrive) direcionando o cache do PyInstaller para a pasta TEMP local
WORK_DIR    = os.path.join(os.environ.get('TEMP', os.environ.get('USERPROFILE', '')), 'ytdl_pyinstaller_build')
INST_DIR    = os.path.join(BASE_DIR, 'installer')
SPLASH      = os.path.join(SRC_DIR, 'splash.png')
LAUNCHER    = os.path.join(SRC_DIR, 'launcher.py')
LAUNCHER_EXE = os.path.join(BIN_DIR, 'launcher.exe')
INSTALLER_ISS = os.path.join(INST_DIR, 'installer.iss')


def read_app_version(iss_path: str) -> str:
    """Le o AppVersion definido no installer.iss (fonte unica da versao)."""
    with io.open(iss_path, encoding='utf-8-sig') as fh:
        for line in fh:
            m = re.match(r'\s*#define\s+AppVersion\s+"([^"]+)"', line)
            if m:
                return m.group(1)
    raise RuntimeError(f'AppVersion nao encontrado em {iss_path}')


APP_VERSION   = read_app_version(INSTALLER_ISS)
INSTALLER_EXE = os.path.join(BIN_DIR, f'YoutubeDownloader_Setup_v{APP_VERSION}.exe')

# ── Candidatos de Inno Setup ──
ISCC_CANDIDATES = [
    os.path.join(os.environ.get('USERPROFILE', ''), r"AppData\Local\Programs\Antigravity IDE\resources\app\node_modules\innosetup\bin\ISCC.exe"),
    r"D:\dev\programs\Antigravity\resources\app\node_modules\innosetup\bin\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
    r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
]


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    print(f"\n>>> {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        print(f"[ERRO] Código de retorno: {result.returncode}")
        sys.exit(result.returncode)
    return result


def ensure_pyinstaller():
    try:
        import PyInstaller  # noqa: F401
        print("[OK] PyInstaller já instalado.")
    except ImportError:
        print("[INFO] Instalando PyInstaller...")
        run([sys.executable, "-m", "pip", "install", "pyinstaller", "--quiet"])


def find_iscc() -> str:
    for path in ISCC_CANDIDATES:
        if os.path.isfile(path):
            return path
    # Tenta via PATH
    found = shutil.which("ISCC.exe") or shutil.which("iscc")
    if found:
        return found
    raise FileNotFoundError(
        "Inno Setup Compiler (ISCC.exe) não encontrado.\n"
        "Instale em: https://jrsoftware.org/isinfo.php"
    )


def build_launcher():
    os.makedirs(BIN_DIR, exist_ok=True)
    os.makedirs(BUILD_DIR, exist_ok=True)
    os.makedirs(WORK_DIR, exist_ok=True)

    # Módulos usados nos scripts externos (setup_env.py, main.py) carregados via
    # runpy.run_path() — o PyInstaller não os detecta por análise estática.
    hidden_imports = [
        # urllib (setup_env.py)
        "urllib.request", "urllib.error", "urllib.parse", "urllib.response",
        "urllib.robotparser",
        # stdlib geral (setup_env.py + main.py)
        "zipfile", "shutil", "io", "threading", "subprocess",
        "datetime", "time", "os", "sys", "hashlib",
        # tkinter completo (setup_env.py + main.py)
        "tkinter", "tkinter.ttk", "tkinter.filedialog",
        "tkinter.messagebox", "tkinter.scrolledtext", "tkinter.font",
        # email/http usados internamente pelo urllib
        "email", "email.message", "email.parser", "http", "http.client",
        "ssl", "socket", "encodings.idna",
        # [A3] adicionados para normalização de áudio (main.py v2.4+)
        "json", "re",
    ]

    args = [
        sys.executable, "-m", "PyInstaller",
        LAUNCHER,
        "--onefile",
        "--noconsole",
        "--name=launcher",
        f"--distpath={BIN_DIR}",
        f"--workpath={WORK_DIR}",
        f"--specpath={BUILD_DIR}",
        "--clean",
    ]

    for mod in hidden_imports:
        args += ["--hidden-import", mod]

    if os.path.isfile(SPLASH):
        args.append(f"--splash={SPLASH}")

    run(args)

    if not os.path.isfile(LAUNCHER_EXE):
        print("[ERRO] launcher.exe não foi gerado.")
        sys.exit(1)
    print(f"[OK] Bootstrapper gerado: {LAUNCHER_EXE}")


def build_installer(iscc: str):
    run([iscc, INSTALLER_ISS])

    if not os.path.isfile(INSTALLER_EXE):
        print("[ERRO] Instalador não foi gerado.")
        sys.exit(1)
    print(f"[OK] Instalador gerado: {INSTALLER_EXE}")


def install_locally():
    """Executa o instalador de forma silenciosa para o usuário local."""
    print("[INFO] Instalando localmente (usuário atual, sem admin)...")
    run([INSTALLER_EXE, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
    print("[OK] Instalação concluída.")

    # Confirma que o atalho foi criado
    desktop = os.path.join(os.environ.get('USERPROFILE', ''), 'Desktop',
                           'YouTube Downloader.lnk')
    if os.path.isfile(desktop):
        print(f"[OK] Atalho criado: {desktop}")
    else:
        print("[AVISO] Atalho não encontrado no Desktop (pode ter sido instalado em outro local).")


def main():
    print("=" * 60)
    print(f"  YouTube Downloader — Pipeline de Build v{APP_VERSION}")
    print("=" * 60)

    ensure_pyinstaller()
    build_launcher()

    iscc = find_iscc()
    print(f"[OK] Inno Setup encontrado: {iscc}")
    build_installer(iscc)

    install_locally()

    print("\n" + "=" * 60)
    print("  BUILD E INSTALAÇÃO CONCLUÍDOS COM SUCESSO")
    print("=" * 60)


if __name__ == "__main__":
    main()
