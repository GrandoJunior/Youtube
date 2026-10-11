# Youtube

Aplicativo desktop completo com interface grafica para download e conversao de midia do YouTube com FFmpeg e Deno.

## Badges
![Release](https://img.shields.io/badge/release-v1.0.0-blue.svg)
![Platform](https://img.shields.io/badge/platform-Windows-brightgreen.svg)
![Security](https://img.shields.io/badge/security-sanitized-success.svg)

## Visão Geral
O **Youtube** foi desenvolvido com foco em robustez, alta disponibilidade e padrões seguros de engenharia de software.

## Requisitos de Sistema
* **Sistema Operacional:** Microsoft Windows 10 / 11 ou Windows Server (x64)
* **Ambiente de Execução:** Python 3.11+ / PowerShell 5.1+
* **Dependências de Sistema:** Visual C++ Redistributable (para módulos compilados)

## Instalação e Execução

### Opção 1: Pacote Standalone / Instalador (Recomendado)
Faça o download do pacote de distribuição na aba [Releases](https://github.com/GrandoJunior/Youtube/releases/tag/v1.0.0) e execute o arquivo correspondente.

### Opção 2: Execução a partir do Código-Fonte
1. Clone o repositório:
```bash
git clone https://github.com/GrandoJunior/Youtube.git
cd Youtube
```

2. Crie e ative o ambiente virtual:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

3. Instale as dependências:
```powershell
pip install -r requirements.txt
```

4. Configure as variáveis de ambiente baseando-se no arquivo `.env.example`:
```powershell
Copy-Item .env.example .env
```

5. Execute a aplicação:
```powershell
python main.py
```

## Atualização Automática (Git)
A cada inicialização, o `launcher.exe` executa `updater.py` **antes** de verificar dependências e abrir a interface — assim a aplicação sempre roda a versão mais recente publicada no repositório.

**Como funciona**
* **Instalação empacotada (instalador):** o HEAD da branch remota é consultado pelo protocolo HTTP do próprio git (`/info/refs?service=git-upload-pack`); se houver commit novo, `main.py`, `setup_env.py`, `updater.py` e `splash.png` são baixados fixados naquele commit e substituídos. O último commit aplicado fica registrado em `.update_state.json`, de modo que uma inicialização sem novidades custa uma única requisição.
* **Execução a partir do código-fonte:** se a pasta estiver dentro de um clone git, é feito `fetch` + `merge --ff-only`, somente quando a branch atual é a acompanhada. Alterações locais não commitadas, outra branch ou ausência do `git` no PATH bloqueiam a atualização (nada é sobrescrito).

**Garantias**
* **Falha aberta:** sem rede, com repositório indisponível ou timeout, a aplicação apenas inicia na versão instalada — a atualização nunca impede o uso.
* **Aplicação atômica:** os arquivos só são substituídos após todos os downloads terminarem e serem validados (arquivos `.py` precisam compilar); qualquer falha reverte tudo pelas cópias `.bak`.
* Registro de cada verificação em `updater.log`, no diretório de instalação.

**Configuração** (variáveis de ambiente, todas opcionais)

| Variável | Padrão | Descrição |
| --- | --- | --- |
| `YTDL_UPDATE_REPO` | `GrandoJunior/Youtube` | Repositório `owner/repo` de origem |
| `YTDL_UPDATE_BRANCH` | `main` | Branch acompanhada |
| `YTDL_UPDATE_TIMEOUT` | `10` | Timeout por requisição (segundos) |
| `YTDL_UPDATE_BUDGET` | `60` | Tempo total máximo da verificação (segundos) |
| `YTDL_UPDATE_TOKEN` | — | Token GitHub para repositório privado (opt-in explícito) |
| `YTDL_DISABLE_UPDATE` | — | `1` desativa completamente a verificação |

## Arquitetura e Segurança
* **Zero Hardcoded Secrets:** Todas as credenciais e parâmetros sensíveis são carregados via variáveis de ambiente.
* **Clean Architecture:** Desacoplamento entre regras de negócio, infraestrutura e interfaces de apresentação.

## Metadados do Projeto
* **Autor:** Guido Grando (`GrandoJunior`)
* **Tags:** python, youtube-downloader, ffmpeg, deno, desktop-app, inno-setup
* **Versão:** `v1.0.0`
