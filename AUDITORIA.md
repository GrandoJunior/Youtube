# Relatório de Auditoria — YouTube Downloader [Gamer Edition]

**Projeto:** `Youtube/` (YouTube Downloader Gamer Edition)
**Escopo:** Código, arquitetura, UI, UX, interface, usabilidade, segurança, distribuição
**Dimensões avaliadas:** eficácia, eficiência, efetividade e qualidade
**Data:** 2026-08-07
**Auditor:** Claude (Opus 4.8)

---

## 1. Sumário Executivo

O projeto é um **wizard de 4 fases em Tkinter** que orquestra `yt-dlp` + `ffmpeg` para baixar vídeo/áudio e normalizar volume (EBU R128, two-pass loudnorm). A base de código é **de qualidade acima da média para um projeto solo**: o modelo de threading está correto (worker thread + `self.after()` + `threading.Event`), há snapshot thread-safe das variáveis Tk, guarda contra callbacks obsoletos (`run_id`), tratamento de erros abrangente e log em arquivo. O histórico de bugs corrigidos documentado nos docstrings mostra disciplina de engenharia.

Apesar disso, a auditoria encontrou **1 defeito funcional crítico** (impossível cancelar um download em andamento pela interface), **um risco de cadeia de suprimentos** (download de binários executáveis sem verificação de integridade) e uma série de problemas de UX e consistência que reduzem a efetividade percebida.

### Nota por dimensão

| Dimensão | Nota | Comentário |
|---|---|---|
| **Eficácia** (faz o que promete) | 🟢 Boa | Download e normalização funcionam e são tecnicamente corretos |
| **Eficiência** (uso de recursos/esforço) | 🟡 Média | Wizard longo demais para tarefa simples; sem progresso real; re-encode desnecessário |
| **Efetividade** (resultado no mundo real) | 🟡 Média | Prejudicada pela ausência de cancelamento e feedback de progresso |
| **Qualidade** (código/robustez) | 🟢 Boa | Threading correto; falta testes, versionamento e verificação de integridade |

### Distribuição das constatações

| Severidade | Qtd. | Constatações |
|---|---|---|
| 🔴 **Crítico** | 2 | Sem cancelamento em execução · Binários sem verificação de integridade |
| 🟠 **Alto** | 4 | Sem progresso real · No-op silencioso ao reexecutar · Auto-abertura de pasta · Órfãos de processo no cancelamento |
| 🟡 **Médio** | 6 | Idioma misturado · IA da tela inicial · Contraste (WCAG) · Sem persistência de preferências · Re-encode fixo em 320k · Versionamento inconsistente |
| 🔵 **Baixo** | 6 | Sem testes/README · Paths hardcoded no build · Sem validação de URL · Arquivos residuais · Sem code signing · Timeout fixo |

---

## 1.1 Status de implementação (atualizado 2026-08-07)

Correções já aplicadas nesta rodada (Sprint 1 + pedidos adicionais):

| Item | Status | Onde |
|---|---|---|
| C-1 Botão **CANCELAR** durante a execução | ✅ Feito | `main.py` — `btn_cancel`, `_request_cancel` |
| C-2 **Verificação de checksum** dos binários (SHA-256 do fornecedor) | ✅ Feito | `setup_env.py` — `_verify_or_abort` |
| A-1 **Progresso real** (percentual + ETA) | ✅ Feito | `main.py` — `--progress-template`, `_set_progress` |
| A-2 Reexecução não falha mais em silêncio | ✅ Feito | `main.py` — `on_show` reagenda |
| A-4 Cancelamento mata a **árvore de processos** (ffmpeg) | ✅ Feito | `main.py` — `_kill_proc_tree` |
| M-4 **Lembrar últimas configurações** (payload, bitrate, cookies, pós-proc, destino) | ✅ Feito | `main.py` — `_load_prefs`/`_save_prefs` |
| Destino: reutiliza pasta salva se existir; senão usa padrão | ✅ Feito | `main.py` — `_ensure_destination` |
| Nome do arquivo: remove prefixo do canal, adiciona **sufixo `[canal]`** | ✅ Feito | `main.py` — template `-o` |
| Instalador atualiza instalação **em execução** (Restart Manager) | ✅ Feito | `installer.iss` — `CloseApplications=yes` |
| **NOVO: Remoção de vocais (instrumental) por IA** — só com GPU | ✅ Feito | `main.py` — motor Demucs |

Itens restantes (A-3, M-1..M-3, M-5..M-6, B-1..B-6) permanecem no roadmap da Seção 7.

### Funcionalidade nova — Remoção de vocais (instrumental)

- **Motor:** [Demucs v4 `htdemucs`](https://github.com/facebookresearch/demucs) (Meta, SOTA) com `--two-stems=vocals` → usa o stem `no_vocals` como instrumental.
- **Ambiente isolado:** como o app é um EXE congelado (sem pip), as dependências pesadas
  (PyTorch/CUDA) vão para um **venv separado** criado sob demanda pelo [`uv`](https://docs.astral.sh/uv/guides/integration/pytorch/)
  (binário único, verificado por SHA-256) com `--torch-backend=auto` (detecta a CUDA da GPU).
- **Habilitação:** checkbox na seção *Post-Processing*, ativa **apenas** com payload de áudio
  **e** GPU NVIDIA detectada via `nvidia-smi`. Sem GPU, fica desabilitada e é forçada a `False`.
- **Instalação na 1ª execução:** quando um usuário com GPU roda o 1º download com a opção
  ligada, o motor é baixado/instalado (~2 GB) com o progresso transmitido ao log da tela.
- **Pipeline:** separa → substitui o arquivo pelo instrumental (mantendo as tags ID3) →
  a normalização EBU R128 (se marcada) atua sobre o instrumental resultante.
- **Melhoria futura possível:** trocar `htdemucs` por `htdemucs_ft` (qualidade+, ~4× mais lento)
  ou por modelos *Mel-Band Roformer* via `audio-separator` para isolamento ainda melhor.

---

## 2. Constatações Críticas 🔴

### C-1 — É impossível cancelar um download em andamento pela interface

**Arquivo:** `sources/main.py:486-494`, `:518-523`, `:536-538`

Toda a infraestrutura de cancelamento thread-safe existe (`_cancel_event`, checagens entre URLs e entre linhas de stdout, `proc.terminate()`), mas **está inalcançável durante uma execução normal**. Na tela de progresso há apenas dois botões:

- `NOVA MISSÃO` (`btn_restart`) — único que chama `cancel()`
- `TERMINATE` (`btn_close`) — encerra o app inteiro

Ambos são **desabilitados** em `on_show()` no início da execução (`main.py:522-523`) e só reabilitados em `_on_done()`. Ou seja: enquanto um download roda, o usuário **não tem nenhum botão para pará-lo** — a única saída é matar o aplicativo pelo gerenciador de tarefas. Todo o código de cancelamento (bem escrito) fica efetivamente morto no caminho principal.

**Impacto:** Colar uma playlist errada ou 50 URLs equivocadas obriga a matar o processo. Alta frustração; contradiz a existência do mecanismo de cancelamento.

**Recomendação:**
1. Adicionar um botão **`■ CANCELAR`** (ou `ABORT`) ativo durante a execução, que chame `self.cancel()` e mude o rótulo para "CANCELANDO...".
2. Manter `NOVA MISSÃO`/`TERMINATE` desabilitados até `_on_done`, mas o cancelar deve viver separado e habilitado.

```python
# Em _build(), adicionar:
self.btn_cancel = ttk.Button(nav, text="■  CANCELAR",
                             command=self._request_cancel, state="disabled")
self.btn_cancel.pack(side='right', padx=(0, 8))

def _request_cancel(self):
    self.cancel()
    self.btn_cancel.config(state="disabled", text="CANCELANDO...")

# Em on_show(): habilitar btn_cancel ao iniciar; em _on_done(): desabilitar/resetar rótulo.
```

---

### C-2 — Binários executáveis são baixados sem verificação de integridade (risco de cadeia de suprimentos)

**Arquivo:** `sources/setup_env.py:40-53`, `:121-125`, `:166-189`

No primeiro uso (e a cada 7 dias), o app baixa e **executa** `yt-dlp.exe` (via `--update` e via download completo) e extrai/executa `ffmpeg.exe`/`ffprobe.exe` a partir de:

- `https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe`
- `https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip`

O download usa HTTPS (bom), mas **não há verificação de checksum nem de assinatura** dos binários antes de gravá-los em disco e executá-los com os privilégios do usuário. Um comprometimento do mirror, um ataque de MITM em rede hostil, ou envenenamento de DNS entregaria um executável arbitrário que roda como o usuário.

O contexto de segurança do `yt-dlp` reforça a importância disso: o `yt-dlp` teve CVEs recentes de injeção de comando ([CVE-2026-26331](https://github.com/advisories/GHSA-g3gw-q23r-pgqm) via `--netrc-cmd`; [GHSA-69qj-pvh9-c5wg](https://github.com/yt-dlp/yt-dlp/security/advisories/GHSA-69qj-pvh9-c5wg) via `--exec`). **O app não usa `--exec` nem `--netrc-cmd`, então NÃO está vulnerável a esses CVEs** (bom), mas isso mostra que a superfície de risco de terceiros é real — e uma versão adulterada do `yt-dlp.exe` seria o pior cenário.

**Recomendação (em ordem de esforço):**
1. **gyan.dev publica SHA256** para o ZIP do FFmpeg — baixar e comparar o hash antes de extrair. É a correção de maior retorno.
2. Para o `yt-dlp`, o repositório publica arquivos `SHA2-256SUMS` (e assinatura GPG) em cada release — baixar o arquivo de somas junto e validar.
3. Fixar (pin) uma versão conhecida em vez de `latest`, e atualizar o pin conscientemente (também melhora reprodutibilidade — ver B-6).

```python
import hashlib
def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

data = _download(FFMPEG_URL, ...)
if _sha256(data).lower() != EXPECTED_FFMPEG_SHA256:
    raise RuntimeError("Checksum do FFmpeg não confere — download abortado.")
```

---

## 3. Constatações Altas 🟠

### A-1 — A barra de progresso nunca mostra progresso real (só "spinner")

**Arquivo:** `sources/main.py:477` (modo `indeterminate`), `:653-654`

A `Progressbar` fica sempre em modo indeterminado (animação infinita). As linhas de progresso reais do yt-dlp são **descartadas de propósito**:

```python
if "[download]" in line and "%" in line:
    continue   # main.py:654 — joga fora justamente o percentual
```

O usuário nunca sabe se falta 10% ou 90%, nem a velocidade/ETA. Para downloads longos isso mina a confiança ("travou?").

**Recomendação:** Parsear o percentual dessas linhas e alimentar uma barra determinada (por arquivo) + contador de itens (item X/N já existe). O yt-dlp expõe `--newline` e `--progress-template` para saída estruturada e estável de parsear:

```python
cmd += ["--newline", "--progress-template",
        "download:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s"]
```

---

### A-2 — Reexecutar enquanto a thread anterior ainda encerra falha silenciosamente

**Arquivo:** `sources/main.py:498-499`

```python
def on_show(self):
    if self._worker and self._worker.is_alive():
        return   # sai sem qualquer aviso ao usuário
```

Após `NOVA MISSÃO` (que só sinaliza o cancelamento, sem `join`), a thread antiga pode continuar viva por alguns instantes. Se o usuário refizer o wizard rapidamente e clicar em `EXECUTE`, `on_show()` retorna **sem iniciar nada e sem mensagem** — a tela de progresso aparece "morta". Comportamento confuso e difícil de diagnosticar.

**Recomendação:** Se `_worker` ainda estiver vivo, exibir status "AGUARDANDO OPERAÇÃO ANTERIOR ENCERRAR..." e reagendar via `self.after(200, self.on_show)` até liberar, em vez de retornar em silêncio.

---

### A-3 — Abrir a pasta de destino automaticamente ao concluir é intrusivo

**Arquivo:** `sources/main.py:882-886`

```python
if dest and not self._cancel_event.is_set():
    os.startfile(dest)   # abre o Explorer sempre, inclusive no modo "normalizar pasta"
```

Abrir uma janela do Explorer sem pedir é surpreendente — especialmente no modo "normalizar pasta existente", onde o usuário já estava naquela pasta. Em lote pode empilhar janelas.

**Recomendação:** Trocar a abertura automática por um botão **`ABRIR PASTA`** na tela de conclusão (ou uma preferência opt-in). Menos intrusivo e ainda conveniente.

---

### A-4 — Cancelamento pode deixar processos `ffmpeg` órfãos

**Arquivo:** `sources/main.py:646-648`

```python
if self._cancel_event.is_set():
    proc.terminate()   # encerra só o yt-dlp, não a árvore de processos
    break
```

No Windows, `terminate()` chama `TerminateProcess` apenas no processo direto. O `yt-dlp` **gera o `ffmpeg` como subprocesso filho** (merge/extração). Cancelar durante um merge pode encerrar o yt-dlp e deixar o `ffmpeg` rodando órfão, segurando o arquivo e consumindo CPU.

**Recomendação:** Encerrar a árvore de processos. No Windows, criar o processo em um novo grupo (`CREATE_NEW_PROCESS_GROUP`) e, no cancelamento, usar `taskkill /T /F /PID` (ou `psutil` para matar filhos recursivamente):

```python
subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
               creationflags=subprocess.CREATE_NO_WINDOW)
```

---

## 4. Constatações Médias 🟡

### M-1 — Idioma da interface é inconsistente (inglês "gamer" + português)

**Arquivo:** `sources/main.py` (transversal)

A UI mistura jargão militar em inglês (`MISSION`, `DEPLOYMENT ZONE`, `PAYLOAD TYPE`, `TERMINATE`, `EXECUTE`) com rótulos e mensagens em português (`Pasta:`, `Incluir subpastas (recursivo)`, `somente áudio`, corpo dos `messagebox` em PT com título `ALERT`). Não há estratégia de i18n; os textos estão embutidos (hardcoded) por toda parte. Isso confunde e dificulta manutenção/tradução.

**Recomendação:** Escolher **um** idioma-base (dado o público, provavelmente PT-BR) e centralizar as strings em um dicionário/módulo `strings.py`. O tema "gamer" pode ser mantido em português ("MISSÃO", "ALVO", "EXECUTAR"). Facilita futura tradução e garante coerência.

### M-2 — A tela inicial mistura dois fluxos distintos (arquitetura de informação)

**Arquivo:** `sources/main.py:254-281`

A `PageUrls` (fase 1 de "baixar") também hospeda o painel **"NORMALIZE EXISTING FILES"**, que é um fluxo completamente diferente (não baixa nada). Colocar duas jornadas na mesma tela de entrada polui a IA e o rótulo "PHASE 1/4" fica enganoso para quem só quer normalizar.

**Recomendação:** Oferecer uma **tela de escolha inicial** ("Baixar mídia" vs. "Normalizar pasta existente") ou abas no topo. Cada fluxo então segue seu próprio caminho, com o indicador de fase correto.

### M-3 — Textos de dica/estado têm contraste abaixo do mínimo (WCAG)

**Arquivo:** `sources/main.py:93-94`, `:143-146`, `:248-250`

O verde neon `#00FF41` sobre `#121212` tem contraste excelente (~13:1 ✅). Porém o cinza `#555555` usado nas dicas (`Hint.TLabel`, fonte 8pt), no indicador `PHASE x/4` e no divisor "OU" tem contraste de **~2,4:1 sobre o fundo escuro** — abaixo do mínimo WCAG AA (4,5:1 para texto normal; 3:1 para texto grande). Em telas pequenas ou luz ambiente, as dicas ficam praticamente ilegíveis.

**Recomendação:** Clarear os textos secundários para no mínimo `#9A9A9A` (~4,6:1) e evitar fontes de 8pt para conteúdo informativo. Verificar com um checker de contraste.

### M-4 — Nenhuma preferência é persistida entre sessões

**Arquivo:** `sources/main.py:120-133` (tudo reinicia com valores fixos)

Destino, qualidade preferida, tipo (vídeo/áudio) e escolha de cookies são redefinidos a cada abertura. Um usuário que sempre baixa MP3 320k para a mesma pasta refaz as escolhas toda vez — atrito desnecessário.

**Recomendação:** Salvar preferências em um `config.json` em `%LocalAppData%` e recarregar no início. Baixo esforço, ganho de eficiência recorrente.

### M-5 — Normalização re-encoda sempre para 320 kbps

**Arquivo:** `sources/main.py:831`

O passo 2 do loudnorm grava sempre `-b:a 320k`. Se o MP3 de origem for 128 kbps, re-encodar para 320k **não recupera qualidade** — apenas infla o arquivo e adiciona uma geração de perda. Para o modo "normalizar pasta", isso pode inchar bibliotecas inteiras.

**Recomendação:** Detectar o bitrate de origem via `ffprobe` (já disponível) e preservar/limitar o bitrate de saída ao do original, ou expor a escolha ao usuário.

### M-6 — Versionamento inconsistente entre artefatos

**Arquivos:** `main.py:3` ("v2.6"), `main.py:104` (título "v2"), `installer/installer.iss:7` ("2.0"), `build.py` ("v2.0")

A versão aparece como 2.6, 2, e 2.0 em lugares diferentes. Dificulta suporte ("qual versão você tem?") e rastreabilidade.

**Recomendação:** Definir a versão em **um** único lugar (ex.: `__version__` em um `version.py`) e referenciá-la no título, no instalador e no log. Considerar tags git por release.

---

## 5. Constatações Baixas 🔵

| ID | Constatação | Arquivo | Recomendação |
|---|---|---|---|
| **B-1** | Sem `README`, sem `requirements.txt`, sem testes automatizados do app principal (os scripts em `downtest/` são experimentos soltos de OCR/transcrição, desconectados do app) | raiz | Adicionar README (uso + build), `requirements-dev.txt` (pyinstaller) e ao menos testes unitários de `_find_mp3_files`, parsing do loudnorm e construção do `cmd` |
| **B-2** | Paths hardcoded e pessoais no build (`D:\dev\programs\Antigravity`, "Antigravity IDE") | `build.py:32-37` | Priorizar `shutil.which("ISCC")` e variável de ambiente `ISCC_PATH`; deixar os paths absolutos apenas como último fallback |
| **B-3** | Nenhuma validação de URL antes de executar — qualquer texto é aceito e o erro só aparece no log | `main.py:306-313` | Validar por regex de esquema `http(s)://` e avisar sobre linhas inválidas antes de iniciar |
| **B-4** | Arquivos residuais versionados/locais (`yt-dlp.exe.old`, `dependences/temp_ffmpeg/` com build completo do FFmpeg ~154 MB, incluindo docs HTML) | `dependences/` | Remover `.old` após update bem-sucedido; apagar `temp_ffmpeg` após extração (já ignorado pelo git, mas ocupa disco) |
| **B-5** | Instalador e `launcher.exe` sem assinatura de código | build/instalador | Ao menos documentar; idealmente assinar para evitar alertas do SmartScreen |
| **B-6** | Timeout fixo de 300 s por passe de normalização pode falhar em arquivos longos (podcasts/sets de 2 h) | `main.py:800`, `:837` | Escalar o timeout pela duração do arquivo (via `ffprobe`) ou torná-lo configurável |

---

## 6. Pontos Fortes (a preservar)

- **Modelo de threading correto e idiomático** — worker daemon + `self.after()` para tocar a UI, snapshot das variáveis Tk antes de iniciar a thread (`main.py:558-570`), `threading.Event` para cancelamento e guarda `run_id` contra callbacks obsoletos (`main.py:864-865`). Alinhado às boas práticas de responsividade em Tkinter ([ref](https://www.bomberbot.com/python/threading-in-tkinter-enhancing-python-gui-responsiveness/)).
- **Segurança de subprocess** — todos os comandos usam **lista de argumentos** (nunca `shell=True`) e o app **não usa** `--exec`/`--netrc-cmd`, evitando as classes de injeção de comando conhecidas do yt-dlp.
- **Normalização tecnicamente correta** — two-pass `loudnorm` real (medição → aplicação com `linear=true`), alvo EBU R128 (-14 LUFS / TP -1.5 dBTP), limpeza do temporário via `try/finally` e substituição atômica com `os.replace`.
- **Robustez operacional** — `try/except` abrangente, log em arquivo com timestamp de microssegundos (evita colisão), `CREATE_NO_WINDOW` para não piscar consoles, atualização automática do yt-dlp a cada 7 dias.
- **Disciplina de manutenção** — histórico de bugs corrigidos documentado nos docstrings.

---

## 7. Roadmap Priorizado

**Sprint 1 — Corrigir o que quebra confiança (crítico/alto)**
1. C-1: Botão de **cancelar** ativo durante o download.
2. C-2: **Verificação de checksum** dos binários baixados.
3. A-1: **Progresso real** (percentual + ETA) na barra.
4. A-2 / A-4: Corrigir no-op silencioso e matar árvore de processos no cancelamento.

**Sprint 2 — Reduzir atrito (alto/médio)**
5. A-3: Botão "Abrir pasta" em vez de abertura automática.
6. M-4: Persistir preferências.
7. M-1: Unificar idioma da UI.
8. M-2: Separar os fluxos "baixar" e "normalizar".

**Sprint 3 — Polimento e higiene (médio/baixo)**
9. M-3: Contraste WCAG nos textos secundários.
10. M-5 / B-6: Bitrate adaptativo e timeout proporcional.
11. M-6 / B-1 / B-2: Versão única, README/testes, build portável.

---

## 8. Fontes consultadas

- [yt-dlp — Command Injection via `--netrc-cmd` (CVE-2026-26331)](https://github.com/advisories/GHSA-g3gw-q23r-pgqm)
- [yt-dlp — Arbitrary command injection via `--exec` (GHSA-69qj-pvh9-c5wg)](https://github.com/yt-dlp/yt-dlp/security/advisories/GHSA-69qj-pvh9-c5wg)
- [Threading in Tkinter: Enhancing Python GUI Responsiveness](https://www.bomberbot.com/python/threading-in-tkinter-enhancing-python-gui-responsiveness/)
- [Tkinter and Threading: Preventing Freezing GUIs](https://pythoneo.com/tkinter-and-threading/)
