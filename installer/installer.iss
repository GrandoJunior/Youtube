; ── YouTube Downloader — Inno Setup Script ──────────────────────────────────
; Gera instalador de usuário (sem admin) com atalho na área de trabalho.
; Arquivos instalados em: %LocalAppData%\YoutubeDownloader\
; ─────────────────────────────────────────────────────────────────────────────

#define AppName      "YouTube Downloader"
#define AppVersion   "2.1"
#define AppExe       "launcher.exe"
#define AppPublisher "GGJ Dev"
#define AppId        "{{B7A4C2D1-F3E5-4B8A-9C2D-1A3B5C7D9E2F}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\YoutubeDownloader
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=..\bin
OutputBaseFilename=YoutubeDownloader_Setup_v{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
CreateUninstallRegKey=yes
; ── Atualização de instalação já existente / em execução ──
; Usa o Restart Manager para detectar e fechar o launcher.exe em uso antes de
; sobrescrever os arquivos (evita falha "arquivo bloqueado" ao atualizar rodando).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "portuguese"; MessagesFile: "compiler:Languages\Portuguese.isl"

[Files]
; Bootstrapper compilado
Source: "..\bin\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion

; Scripts externos (atualizáveis sem recompilar o EXE)
Source: "..\sources\main.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\sources\setup_env.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\sources\splash.png";   DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Atalho na área de trabalho do usuário
Name: "{autodesktop}\{#AppName}"; \
      Filename: "{app}\{#AppExe}"; \
      WorkingDir: "{app}"; \
      Comment: "YouTube Downloader Gamer Edition"

[UninstallDelete]
; Remove toda a pasta de dependências ao desinstalar
Type: filesandordirs; Name: "{app}\dependences"

[Code]
// Garante que nenhuma instância esteja rodando antes de atualizar
function InitializeSetup(): Boolean;
begin
  Result := True;
end;
