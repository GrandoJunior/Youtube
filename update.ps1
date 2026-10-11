# update.ps1 - Atualiza o YouTube Downloader para a ultima versao publicada.
# Funciona com qualquer versao instalada (ou nenhuma). Nao precisa de admin.
#
# Uso (Iniciar > Executar, Win+R):
#   powershell -NoP -Ep Bypass -C "irm https://raw.githubusercontent.com/GrandoJunior/Youtube/main/update.ps1 | iex"
#
# Para reinstalar mesmo ja estando na ultima versao:
#   powershell -NoP -Ep Bypass -C "$env:YTDL_FORCE=1; irm https://raw.githubusercontent.com/GrandoJunior/Youtube/main/update.ps1 | iex"

$ErrorActionPreference = 'Stop'
$repo = 'GrandoJunior/Youtube'
# Chave que o instalador Inno Setup grava (AppId do installer.iss + "_is1")
$key  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{B7A4C2D1-F3E5-4B8A-9C2D-1A3B5C7D9E2F}_is1'

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$rel = Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest" `
        -Headers @{ 'User-Agent' = 'YoutubeDownloader-update' }
$latest    = $rel.tag_name.TrimStart('v')
$installed = (Get-ItemProperty $key -ErrorAction SilentlyContinue).DisplayVersion

$instTxt = if ($installed) { "v$installed" } else { 'nenhuma' }
Write-Host "Instalada: $instTxt   Disponivel: v$latest"

if ($installed -eq $latest -and -not $env:YTDL_FORCE) {
    Write-Host 'Ja esta na ultima versao. Nada a fazer.'
    Start-Sleep 3
    return
}

$asset = $rel.assets | Where-Object { $_.name -like '*.exe' } | Select-Object -First 1
if (-not $asset) { throw 'O release mais recente nao tem instalador .exe anexado.' }

$exe = Join-Path $env:TEMP $asset.name
Write-Host "Baixando $($asset.name) ..."
Invoke-WebRequest $asset.browser_download_url -OutFile $exe -UseBasicParsing

Write-Host 'Instalando (silencioso; fecha o app se estiver aberto)...'
$p = Start-Process $exe -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART' -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Instalador retornou codigo $($p.ExitCode)." }
Remove-Item $exe -ErrorAction SilentlyContinue

Write-Host "Atualizado para v$latest. Atalho: area de trabalho > YouTube Downloader."
Start-Sleep 3
