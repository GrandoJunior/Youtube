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

## Arquitetura e Segurança
* **Zero Hardcoded Secrets:** Todas as credenciais e parâmetros sensíveis são carregados via variáveis de ambiente.
* **Clean Architecture:** Desacoplamento entre regras de negócio, infraestrutura e interfaces de apresentação.

## Metadados do Projeto
* **Autor:** Guido Grando (`GrandoJunior`)
* **Tags:** python, youtube-downloader, ffmpeg, deno, desktop-app, inno-setup
* **Versão:** `v1.0.0`
