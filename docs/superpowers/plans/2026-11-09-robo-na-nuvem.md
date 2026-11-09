# Robô na nuvem: plano de construção

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O robô de cadastro no PJe roda num servidor do Google em São Paulo, para vários escritórios, e cada
escritório usa só o navegador.

**Architecture:**
- Um processo do `app_web.py` de hoje por escritório (systemd `robo@<id>`), ligado em "modo servidor" por
  variáveis de ambiente. Cada processo tem pasta própria, login do PJe no Secret Manager e Chrome sem janela.
- Um `porteiro.py` novo na frente: faz o login (Firebase Auth pela API REST), guarda a sessão em cookie e repassa
  `/api/*` ao processo do escritório.
- Caddy faz o HTTPS. Sem as variáveis, o modo local continua igual.

**Tech Stack:**
- Python 3.14, Flask, Playwright (Chrome), google-cloud-secret-manager, waitress.
- Firebase Authentication pela REST API: só `urllib`, sem SDK.
- Caddy, systemd, Ubuntu 24.04, Google Compute Engine (southamerica-east1).

**Spec:** `docs/superpowers/specs/2026-11-09-robo-na-nuvem-design.md`

## Global Constraints

- Python 3.14 no desenvolvimento (Windows) e no servidor (via `uv`).
- **Modo local intacto:** sem `ROBO_SERVIDOR=1`, tudo funciona como hoje (`Abrir cadastro.bat`, pasta dos clientes
  escolhida, `config.json` no disco), e os testes que já existem continuam passando.
- O código decide o modo em tempo de execução (`robo.SERVIDOR`), nunca em tempo de importação, para os testes
  poderem trocar.
- **Testes:** rodam com `python testes/<arquivo>.py`, sem pytest. Dados sintéticos (Fulana, Beltrano, Ciclano,
  CPF 111.444.777-35). Nunca nomes ou CPFs reais.
- **Repositório público:** nenhuma senha, chave, CPF, chave de API, id de faturamento ou dado de cliente em arquivo
  do repositório. Os valores ficam em `/srv/robo/*.env` no servidor (chmod 600).
- **Nunca:**
  - clicar em Protocolar ou Assinar;
  - pedir "Configurar novo dispositivo" sem ordem do usuário;
  - apagar sem ser para uma Lixeira;
  - mexer em arquivos fora de `CADASTRO DE PROCESSOS/`.
- **Commit/push:** nenhum sem o usuário pedir. Se pedir, o autor é `theocavalhoDEV <theotheteo@gmail.com>`
  (conferir com `git log --format="%an <%ae>"`).
- **Custo no Google:** nada que gere custo é criado sem o ok do usuário naquele momento (Task 7).
- **Valores fixos:**

  | Item | Valor |
  |---|---|
  | Envio de pastas | só `.pdf` e `.docx`, até 200 MB por envio |
  | Guarda de Concluído, Lixeira e fotos das pausas | 30 dias |
  | Sessão | 12 horas |
  | Chrome do servidor | sem janela, 1366×768 |
  | Portas | porteiro 8800; escritórios a partir de 8801 |
  | Máquina | e2-medium, Ubuntu 24.04 |
  | Snapshots | diários, guardados 7 dias |
  | Região | southamerica-east1, também para a replicação dos segredos |
- Textos da tela em português, no tom atual da página.

## Review Focus

1. **Escritório parado ou reiniciando:** o porteiro responde 502 com "O robô do escritório está reiniciando. Tente
   de novo em um minuto.", e não um erro técnico. Teste na Task 4.
2. **E-mail tirado do `escritorios.json` com a sessão aberta:** o próximo pedido dá 401 e volta para a tela de
   entrar. Teste na Task 4.
3. **Pasta enviada com nomes estranhos:**
   - `..`, `C:\`, `:`, arquivo solto sem pasta, extensão em maiúsculas, mais de 3 níveis, pasta "Concluído";
   - tudo o que for ignorado é listado, e nada é gravado fora de `clientes/`. Teste na Task 3.
4. **"Apagar" com cadastro rodando:** recusa com 409, em vez de mover uma pasta que o robô está usando. Teste na
   Task 3.
5. **Escritório novo sem login do PJe no Secret Manager:** `ler_config()` devolve `{}`, a página abre e mostra
   "Falta o login", sem quebrar. Teste na Task 1.

---

### Task 1: Robô em modo servidor (`novo_processo.py`)

**Files:**
- Modify: `sistema/novo_processo.py`:
  - constantes (linhas 23-31);
  - `pastas_de_clientes` (449-457);
  - `abrir_navegador` (599-621);
  - `ler_config` (648-654);
  - perto de `navegador` (815-838).
- Test: `testes/test_novo_processo.py`

**Interfaces:**
- Produces:
  - `robo.SERVIDOR: bool`. Vale `os.environ.get("ROBO_SERVIDOR") == "1"`, e os testes trocam.
  - `robo.DADOS`. Vale `os.environ.get("ROBO_DADOS")` ou, sem ela, a pasta `dados_do_escritorio` de hoje.
  - `robo.ARQUIVO_CONFIG = os.path.join(DADOS, "config.json")`.
  - `robo.SEGREDO`. Vale `f"projects/{ROBO_PROJETO}/secrets/pje-login-{ROBO_ESCRITORIO}"`, com os dois valores lidos
    do ambiente.
  - `robo.cliente_segredos()`: devolve um `secretmanager.SecretManagerServiceClient`, com importação tardia e
    `functools.cache`.
  - `robo.ler_config() -> dict`.
  - `robo.gravar_config(config: dict) -> None`.
  - `robo.fechar_navegador() -> None`.

- [ ] **Step 1: Instalar as bibliotecas novas no computador de desenvolvimento**

Run: `pip install google-cloud-secret-manager waitress`
Expected: `Successfully installed ...`. Depois, `python -c "import google.cloud.secretmanager, waitress"` não dá erro.

- [ ] **Step 2: Escrever os testes que falham** (no fim de `testes/test_novo_processo.py`; o `__main__` já roda todo
  `test_*`)

```python
from types import SimpleNamespace
from google.api_core.exceptions import NotFound


class SegredosFalsos:
    """Imita o SecretManagerServiceClient: versões [nome, dados, estado]."""
    def __init__(self):
        self.versoes = []
    def access_secret_version(self, request):
        ativas = [v for v in self.versoes if v[2] == "ENABLED"]
        if not ativas:
            raise NotFound("sem versão")
        return SimpleNamespace(payload=SimpleNamespace(data=ativas[-1][1]))
    def add_secret_version(self, request):
        nome = f"{request['parent']}/versions/{len(self.versoes) + 1}"
        self.versoes.append([nome, request["payload"]["data"], "ENABLED"])
        return SimpleNamespace(name=nome)
    def list_secret_versions(self, request):
        return [SimpleNamespace(name=v[0]) for v in self.versoes if v[2] == "ENABLED"]
    def destroy_secret_version(self, request):
        for v in self.versoes:
            if v[0] == request["name"]:
                v[2] = "DESTROYED"


def test_config_local():
    tmp = tempfile.mkdtemp()
    with mock.patch.object(robo, "SERVIDOR", False), mock.patch.object(robo, "ARQUIVO_CONFIG", os.path.join(tmp, "config.json")):
        assert robo.ler_config() == {}
        robo.gravar_config({"cpf": "11144477735"})
        assert robo.ler_config() == {"cpf": "11144477735"}
        assert os.listdir(tmp) == ["config.json"]  # gravação atômica não deixa .tmp


def test_config_no_servidor():
    falso = SegredosFalsos()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "cliente_segredos", lambda: falso), \
         mock.patch.object(robo, "SEGREDO", "projects/p/secrets/pje-login-x"):
        assert robo.ler_config() == {}  # escritório novo: ainda sem login
        robo.gravar_config({"cpf": "1", "senha": "a"})
        robo.gravar_config({"cpf": "1", "senha": "b"})
        assert robo.ler_config() == {"cpf": "1", "senha": "b"}
        assert [v[2] for v in falso.versoes] == ["DESTROYED", "ENABLED"]  # a senha velha não fica guardada
        assert falso.versoes[1][0] == "projects/p/secrets/pje-login-x/versions/2"


def test_fechar_navegador():
    fechou = []
    pagina = SimpleNamespace(context=SimpleNamespace(close=lambda: fechou.append(1)))
    with mock.patch.object(robo, "navegador_atual", pagina):
        robo.fechar_navegador()
        assert robo.navegador_atual is None and fechou == [1]
        robo.fechar_navegador()  # já fechado: não dá erro


def test_chrome_do_servidor_sem_janela():
    chamado = {}
    contexto = SimpleNamespace(set_default_timeout=lambda t: None, pages=[SimpleNamespace(on=lambda *a: None)])
    def abrir(perfil, **kw):
        chamado.update(kw)
        return contexto
    pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=abrir))
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "_playwright", pw):
        robo.abrir_navegador()
    assert chamado["headless"] is True and chamado["viewport"] == {"width": 1366, "height": 768}
    assert "no_viewport" not in chamado and "--start-maximized" not in chamado["args"]


def test_lixeira_e_concluido_fora_da_lista():
    raiz = tempfile.mkdtemp()
    for p in ("Fulana", "Lixeira", "Concluído"):
        os.makedirs(os.path.join(raiz, p))
        open(os.path.join(raiz, p, "Fulana [Ação].pdf"), "wb").close()
    assert [os.path.basename(p) for p in robo.pastas_de_clientes(raiz)] == ["Fulana"]
```

- [ ] **Step 3: Rodar e ver falhar**

Run: `python testes/test_novo_processo.py`
Expected: FAIL em `test_chrome_do_servidor_sem_janela` com `AttributeError` (falta `SERVIDOR`). Os testes antigos
passam.

- [ ] **Step 4: Implementar em `sistema/novo_processo.py`**

- **Constantes:**
  - `SERVIDOR`, `DADOS` (com o ambiente), `ARQUIVO_CONFIG` e `SEGREDO`, junto das constantes de hoje.
  - `ARQUIVO_ESCRITORIO` e `ARQUIVO_HISTORICO` continuam derivando de `DADOS`.
- **`ler_config()`:**
  - modo servidor: `cliente_segredos().access_secret_version(request={"name": SEGREDO + "/versions/latest"})`, depois
    `json.loads(payload.data)`. `NotFound` devolve `{}`.
  - modo local: o arquivo, como hoje. `FileNotFoundError` devolve `{}`.
- **`gravar_config(config)`:**
  - modo servidor: `add_secret_version(request={"parent": SEGREDO, "payload": {"data": json.dumps(config, ensure_ascii=False).encode()}})`.
    Depois, `destroy_secret_version(request={"name": v.name})` para cada versão de
    `list_secret_versions(request={"parent": SEGREDO, "filter": "state:ENABLED"})` que não seja a nova.
  - modo local: grava `ARQUIVO_CONFIG + ".tmp"` e faz `os.replace`.
- **`abrir_navegador()`:** no servidor, `headless=True`, `viewport={"width": 1366, "height": 768}` e `args` sem
  `--start-maximized`. No modo local, nada muda.
- **`fechar_navegador()`:** fecha `navegador_atual.context`, engolindo erro de Chrome já fechado, e põe
  `navegador_atual = None`.
- **`pastas_de_clientes`:** ignora também `"Lixeira"`.

- [ ] **Step 5: Rodar e ver passar**

Run: `python testes/test_novo_processo.py`
Expected: todos `ok`, terminando em `N testes passaram`.

---

### Task 2: `app_web.py` em modo servidor

**Files:**
- Modify: `sistema/app_web.py`:
  - topo (24-27);
  - `pedir_ajuda` (53-59);
  - `raiz` (65-67);
  - `mover_para_concluido` (78-86);
  - `trabalhador` (105-117);
  - `configurar_codigo` (192-209);
  - `erro` (227-231);
  - `ver_escritorio` e `salvar_escritorio` (257-301);
  - `ler_mapa` (327-331);
  - `iniciar_autenticador` e `ver_qr` (444-471);
  - `responder` (482-489);
  - `__main__` (591-593).
- Test: `testes/test_app_web.py`

**Interfaces:**
- Consumes: Task 1 (`robo.SERVIDOR`, `robo.DADOS`, `robo.ler_config`, `robo.gravar_config`,
  `robo.fechar_navegador`).
- Produces:
  - `app_web.PORTA`. Vale `int(os.environ.get("ROBO_PORTA", 8765))`.
  - `app_web.raiz() -> str`. No servidor, é `os.path.join(robo.DADOS, "clientes")`, criada se faltar.
  - `app_web.nome_livre(pasta_mae: str, nome: str) -> str`. Devolve `nome`, ou `"nome (2)"`, `"nome (3)"` e assim por
    diante, se já existir.
  - `app_web.mover_para(pasta: str, subpasta: str) -> str`. Move para `raiz-da-pasta/subpasta/nome-livre`, faz
    `os.utime(destino)` e devolve o destino. Substitui `mover_para_concluido`.
  - `GET /api/escritorio` passa a devolver também `servidor: bool`.
  - `estado["pausa"]` passa a ter também `rascunho: str`: o `URL_RASCUNHO` do cliente, ou `""`.
  - `app_web.CONFIG` deixa de existir. Tudo passa por `robo.ler_config()` e `robo.gravar_config()`.

- [ ] **Step 1: Ajustar os testes que existem e escrever os que falham**

- Em `test_configuracao_e_correcao` e `test_link_do_autenticador`, trocar
  `mock.patch.object(app_web, "CONFIG", x)` por `mock.patch.object(robo, "ARQUIVO_CONFIG", x)`, e
  `app_web.gravar_json(app_web.CONFIG, ...)` por `robo.gravar_config(...)`.
- Trocar o `__main__` pela descoberta automática de `test_*`, igual à de `test_novo_processo.py`.
- Acrescentar:

```python
import threading, time


def pausar(resultado):
    """Põe o robô numa pausa (thread) e espera ela aparecer no estado."""
    t = threading.Thread(target=lambda: resultado.append(app_web.pedir_ajuda(2, "Assuntos", "problema", "")))
    t.start()
    while not app_web.estado["pausa"]:
        time.sleep(0.01)
    return t


def test_modo_servidor():
    tmp = tempfile.mkdtemp()
    gravacoes = []
    antes = {n: getattr(robo, n) for n in GLOBAIS}
    web = app_web.app.test_client()
    try:
        with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp), \
             mock.patch.object(robo, "ARQUIVO_ESCRITORIO", os.path.join(tmp, "escritorio.json")), \
             mock.patch.object(robo, "ler_config", lambda: {"cpf": "11144477735", "senha": "s"}), \
             mock.patch.object(robo, "gravar_config", gravacoes.append):
            cfg = web.get("/api/escritorio").get_json()
            assert cfg["servidor"] is True and cfg["pasta_clientes"] == os.path.join(tmp, "clientes")
            corpo = {k: v for k, v in cfg.items() if k not in ("campos", "pessoas", "pje", "servidor")}
            corpo.update(pasta_clientes="C:/outro escritorio", pje={"cpf": "", "senha": "", "totp_key": ""})
            assert web.post("/api/escritorio", json=corpo).status_code == 200
            assert app_web.raiz() == os.path.join(tmp, "clientes")  # no servidor a página não escolhe pasta
            assert gravacoes == []  # login não mudou: não cria versão nova do segredo
            assert web.post("/api/mapa/ler", json={}).status_code == 403
    finally:
        for n, v in antes.items():
            setattr(robo, n, v)
        app_web.escritorio = robo.carregar_escritorio()


def test_pausa_no_servidor_mostra_o_rascunho():
    tmp = tempfile.mkdtemp()
    pasta = os.path.join(tmp, "clientes", "Fulana")
    os.makedirs(pasta)
    web, resultado = app_web.app.test_client(), []
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        app_web.estado["atual"] = "Fulana"
        try:
            t = pausar(resultado)
            assert app_web.estado["pausa"]["rascunho"] == ""
            assert web.post("/api/responder", json={"resposta": "continuar"}).status_code == 400  # sem rascunho
            web.post("/api/responder", json={"resposta": "repetir"})
            t.join()
            robo.salvar_rascunho(pasta, "123")
            t = pausar(resultado)
            assert app_web.estado["pausa"]["rascunho"] == robo.URL_RASCUNHO.format("123")
            assert web.post("/api/responder", json={"resposta": "continuar"}).status_code == 200
            t.join()
            assert resultado == ["repetir", "continuar"]
        finally:
            app_web.estado["atual"] = ""


def test_concluido_conta_os_30_dias_da_mudanca():
    raiz = tempfile.mkdtemp()
    pasta = os.path.join(raiz, "Fulana")
    os.makedirs(pasta)
    os.utime(pasta, (0, 0))  # pasta enviada há muito tempo
    destino = app_web.mover_para(pasta, "Concluído")
    assert destino == os.path.join(raiz, "Concluído", "Fulana") and time.time() - os.path.getmtime(destino) < 60
    os.makedirs(pasta)
    assert app_web.mover_para(pasta, "Concluído") == os.path.join(raiz, "Concluído", "Fulana (2)")
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python testes/test_app_web.py`
Expected: FAIL em `test_concluido_conta_os_30_dias_da_mudanca` (falta `mover_para`) ou em `test_modo_servidor`
(falta `servidor`).

- [ ] **Step 3: Implementar em `sistema/app_web.py`**

- **Config:** apagar `CONFIG`. Os 5 lugares que liam ou gravavam `CONFIG` passam a usar
  `robo.ler_config()` / `robo.gravar_config()`.
- **`salvar_escritorio`:**
  - no servidor, faz `novo.pop("pasta_clientes", None)` e não confere pasta;
  - só chama `robo.gravar_config` se o config mudou (comparar com o lido).
- **`raiz()`:** o modo servidor vem antes da regra local.
- **`ver_escritorio`:** acrescenta `servidor=robo.SERVIDOR`.
- **`ler_mapa`:** no servidor, `abort(403, "No servidor, as opções do PJe são atualizadas pelo administrador.")`.
  Registrar 403 no `@app.errorhandler`.
- **`pedir_ajuda`:** acrescenta
  `rascunho = robo.URL_RASCUNHO.format(id) if (id := robo.rascunho_salvo(os.path.join(raiz(), estado["atual"]))) else ""`,
  só quando `estado["atual"]` existe. Senão, `""`.
- **`responder`:** no servidor, `continuar` com `estado["pausa"]["rascunho"] == ""` dá
  `abort(400, "Ainda não existe rascunho no PJe: use Tentar de novo ou Pular.")`.
  - Não muda `novo_processo.cadastrar`: depois de `continuar` ele já põe `recarregar = True`, e a etapa seguinte
    reabre o rascunho com `recuperar()`. É isso que pega o que a pessoa fez no PJe dela.
- **`trabalhador`:** no `finally`, `if robo.SERVIDOR and tarefas.empty(): robo.fechar_navegador()`.
- **`nome_livre` e `mover_para`:** como em Interfaces. `cadastrar()` chama `mover_para(..., "Concluído")`.
- **`__main__`:**
  - `webbrowser.open` só no modo local;
  - `app.run(host="127.0.0.1", port=PORTA, threaded=True)`.
  - `# ponytail: servidor de desenvolvimento do Flask, só o porteiro fala com ele (127.0.0.1); trocar por waitress se travar com vários usuários`.

- [ ] **Step 4: Rodar e ver passar**

Run: `python testes/test_app_web.py; python testes/test_novo_processo.py`
Expected: todos `ok` nos dois.

---

### Task 3: Enviar e apagar pastas de clientes (`app_web.py`)

**Files:**
- Modify: `sistema/app_web.py`. Rotas novas perto de `/api/clientes`, e a config do app logo após `app = Flask(...)`.
- Test: `testes/test_app_web.py`

**Interfaces:**
- Consumes: Task 2 (`raiz()`, `nome_livre()`, `mover_para()`, `robo.SERVIDOR`).
- Produces:
  - `app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024`. O 413 devolve
    `{"erro": "Envio grande demais: o limite é 200 MB por vez. Envie menos pastas de cada vez."}`.
  - `app_web.nome_seguro(nome: str) -> bool`. Exige:
    - não vazio, não `.` nem `..`;
    - sem caracteres de `r'[\x00-\x1f<>:"/\\|?*]'`;
    - não termina em `.` nem em espaço;
    - no máximo 150 caracteres.
  - `app_web.destino_do_envio(caminho: str) -> tuple[str, str] | None`:
    1. troca `\` por `/` e separa as partes sem vazias;
    2. **confere todas as partes** com `nome_seguro`;
    3. com 3 partes, descarta a primeira (pasta de lote);
    4. exige exatamente 2 partes: pasta e arquivo;
    5. a pasta não pode ser `Concluído` nem `Lixeira`;
    6. a extensão do arquivo, em minúsculas, é `.pdf` ou `.docx`.
  - `POST /api/clientes/enviar` (multipart):
    - listas paralelas `arquivos` (arquivos) e `caminhos` (caminho relativo de cada um, na mesma ordem);
    - devolve `{"clientes": [nomes das pastas criadas], "ignorados": [caminhos]}`;
    - modo local: 403. Nada aproveitado: 400 "Nenhum arquivo .pdf ou .docx dentro de pastas de clientes.";
    - na mesma remessa, a primeira vez que uma pasta aparece ganha `nome_livre(raiz(), pasta)`, e os arquivos
      seguintes dela vão para o mesmo nome.
  - `POST /api/clientes/<path:nome>/apagar`, nesta ordem:
    1. modo local: 403;
    2. `estado["tarefa"] == "cadastrar"`: 409 "Espere o robô terminar para apagar.";
    3. `pasta_do_cliente(nome)` (404 se não existe);
    4. `mover_para(pasta, "Lixeira")`, e devolve `{"ok": true}`.

- [ ] **Step 1: Escrever os testes que falham**

```python
import io


def enviar(web, arquivos):  # [(caminho relativo, bytes)]
    return web.post("/api/clientes/enviar", content_type="multipart/form-data",
                    data={"arquivos": [(io.BytesIO(b), os.path.basename(c.replace("\\", "/"))) for c, b in arquivos],
                          "caminhos": [c for c, _ in arquivos]})


def test_envio_de_pastas():
    tmp = tempfile.mkdtemp()
    raiz = os.path.join(tmp, "clientes")
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        r = enviar(web, [("Fulana/Fulana [Ação].pdf", b"%PDF"), ("Fulana/DOC 01] RG.pdf", b"%PDF"),
                         ("Lote/Beltrano/Cadastro.docx", b"PK"), ("Ciclano/INICIAL.PDF", b"%PDF"),
                         ("../fora.pdf", b"x"), ("Fulana/../../x.pdf", b"x"), ("solto.pdf", b"x"),
                         ("Fulana/foto.jpg", b"x"), ("Concluído/a.pdf", b"x"), ("A/B/C/d.pdf", b"x"),
                         ("Fulana/con:.pdf", b"x"), ("C:\\Windows\\a.pdf", b"x")])
        assert r.status_code == 200
        assert sorted(r.get_json()["clientes"]) == ["Beltrano", "Ciclano", "Fulana"]
        assert len(r.get_json()["ignorados"]) == 8
        assert sorted(os.listdir(tmp)) == ["clientes"]  # nada fora da pasta dos clientes
        assert sorted(os.listdir(raiz)) == ["Beltrano", "Ciclano", "Fulana"]
        assert sorted(os.listdir(os.path.join(raiz, "Fulana"))) == ["DOC 01] RG.pdf", "Fulana [Ação].pdf"]
        assert enviar(web, [("Fulana/Outra.pdf", b"%PDF")]).get_json()["clientes"] == ["Fulana (2)"]
        assert enviar(web, [("solto.pdf", b"x")]).status_code == 400
        limite = app_web.app.config["MAX_CONTENT_LENGTH"]
        app_web.app.config["MAX_CONTENT_LENGTH"] = 10
        try:
            r = enviar(web, [("Fulana/grande.pdf", b"x" * 100)])
            assert r.status_code == 413 and "200 MB" in r.get_json()["erro"]
        finally:
            app_web.app.config["MAX_CONTENT_LENGTH"] = limite
    assert enviar(web, [("Fulana/a.pdf", b"x")]).status_code == 403  # modo local: o escritório usa a pasta do computador


def test_apagar_cliente():
    tmp = tempfile.mkdtemp()
    raiz = os.path.join(tmp, "clientes")
    os.makedirs(os.path.join(raiz, "Fulana"))
    open(os.path.join(raiz, "Fulana", "Fulana [Ação].pdf"), "wb").close()
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        app_web.estado["tarefa"] = "cadastrar"
        try:
            assert web.post("/api/clientes/Fulana/apagar").status_code == 409
        finally:
            app_web.estado["tarefa"] = ""
        assert web.post("/api/clientes/Fulana/apagar").status_code == 200
        assert os.listdir(os.path.join(raiz, "Lixeira")) == ["Fulana"] and "Fulana" not in os.listdir(raiz)
    assert web.post("/api/clientes/Fulana/apagar").status_code == 403
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python testes/test_app_web.py`
Expected: FAIL em `test_envio_de_pastas` (404 na rota nova).

- [ ] **Step 3: Implementar `nome_seguro`, `destino_do_envio`, as duas rotas, o `MAX_CONTENT_LENGTH` e o handler 413
  como em Interfaces.** Grava com `FileStorage.save(os.path.join(raiz(), pasta_final, arquivo))`, depois de
  `os.makedirs`. Usar a lista `caminhos`, nunca o `filename` do arquivo.
  `# ponytail: envio que cai no meio deixa a pasta pela metade; o escritório apaga e envia de novo`.

- [ ] **Step 4: Rodar e ver passar**

Run: `python testes/test_app_web.py`
Expected: todos `ok`.

---

### Task 4: Porteiro (`servidor/porteiro.py`)

**Files:**
- Create: `servidor/porteiro.py`
- Test: `testes/test_porteiro.py`

**Interfaces:**
- **Ambiente:**
  - `ROBO_RAIZ` (padrão `/srv/robo`);
  - `ROBO_CHAVE_SESSAO` (obrigatória);
  - `FIREBASE_API_KEY` (obrigatória);
  - `ROBO_PORTA_PORTEIRO` (padrão 8800).
- **`ARQUIVO_ESCRITORIOS = os.path.join(RAIZ, "escritorios.json")`.** Formato:
  `{"escritorios": {"<id>": {"nome": str, "porta": int, "emails": [str em minúsculas]}}}`.
- **Funções:**
  - `escritorio_do_email(email: str) -> dict | None`: relê o arquivo a cada chamada e compara em minúsculas. Devolve
    `{"id", "nome", "porta"}`.
  - `entrar_no_firebase(email: str, senha: str) -> bool`: POST
    `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=...` com
    `{"email", "password", "returnSecureToken": true}`, via `urllib`. 200 dá True, 400 dá False. Outro erro sobe.
  - `pedir_nova_senha(email: str) -> None`: POST `accounts:sendOobCode` com `{"requestType": "PASSWORD_RESET", "email"}`.
    Ignora erros.
  - `criar_usuario(email: str) -> None`: POST `accounts:signUp` com senha aleatória (`secrets.token_urlsafe(24)`).
    Ignora `EMAIL_EXISTS`. É usada pela Task 6.
- **Rotas:**

  | Rota | O que faz |
  |---|---|
  | `GET /` | `../sistema/web/index.html` |
  | `GET /fontes/<nome>` | só `.woff2` |
  | `POST /entrar` `{email, senha}` | Firebase falso: 401 "E-mail ou senha errados.". Sem escritório: 403 "Este e-mail não está ligado a nenhum escritório.". Certo: sessão com `email` e `session.permanent = True` |
  | `POST /sair` | apaga a sessão |
  | `POST /esqueci` `{email}` | sempre 200 |
  | `GET /saude` | `ok` |
  | `/api/<path:resto>` (GET, POST) | sem sessão, ou e-mail fora do arquivo: limpa a sessão e devolve 401 `{"erro": "Entre com seu e-mail e senha.", "entrar": true}`. Senão, `repassar(porta)` |

- **`repassar(porta: int) -> Response`:**
  - usa `http.client.HTTPConnection("127.0.0.1", porta, timeout=60)`, com método, caminho e query iguais e corpo
    `request.stream`;
  - repassa os cabeçalhos `Content-Type`, `Content-Length`, `If-None-Match` e `Accept-Encoding`, **nunca `Cookie`**;
  - devolve status, corpo e os cabeçalhos `Content-Type`, `Content-Encoding`, `ETag`, `Cache-Control` e `Vary`;
  - `OSError` dá 502 `{"erro": "O robô do escritório está reiniciando. Tente de novo em um minuto."}`;
  - `# ponytail: lê a resposta inteira na memória (fotos ~200 KB, mapa ~300 KB)`.
- **`before_request`:** em POST, um `Origin` com `urlparse(origin).netloc != request.host` dá 403.
- **Config do Flask:**

  | Chave | Valor |
  |---|---|
  | `SECRET_KEY` | `ROBO_CHAVE_SESSAO` |
  | `SESSION_COOKIE_SECURE` | `True` |
  | `SESSION_COOKIE_HTTPONLY` | `True` |
  | `SESSION_COOKIE_SAMESITE` | `"Strict"` |
  | `PERMANENT_SESSION_LIFETIME` | `datetime.timedelta(hours=12)` |
  | `MAX_CONTENT_LENGTH` | `210 * 1024 * 1024` |
- **`__main__`:** `waitress.serve(app, host="127.0.0.1", port=PORTA, threads=16)`.

- [ ] **Step 1: Escrever os testes que falham** (`testes/test_porteiro.py`, com a mesma descoberta automática de
  `test_*` no `__main__`)

```python
"""Testes do porteiro sem Firebase e sem servidor: um 'robô de escritório' falso responde numa porta local."""
import io, json, os, socket, sys, tempfile, threading
from unittest import mock
from flask import Flask, request
from werkzeug.serving import make_server

RAIZ = tempfile.mkdtemp()
os.environ.update(ROBO_RAIZ=RAIZ, ROBO_CHAVE_SESSAO="teste", FIREBASE_API_KEY="x")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
import porteiro

porteiro.app.config["SESSION_COOKIE_SECURE"] = False  # o cliente de teste usa http

falso = Flask("escritorio_falso")


@falso.route("/api/eco", methods=["GET", "POST"])
def eco():
    return {"metodo": request.method, "query": request.query_string.decode(), "tipo": request.content_type or "",
            "tamanho": len(request.get_data()), "cookie": request.headers.get("Cookie", "")}


servidor = make_server("127.0.0.1", 0, falso)
threading.Thread(target=servidor.serve_forever, daemon=True).start()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORTA_PARADA = s.getsockname()[1]  # ninguém escuta aqui: escritório "reiniciando"


def gravar_escritorios(**escritorios):
    with open(porteiro.ARQUIVO_ESCRITORIOS, "w", encoding="utf-8") as f:
        json.dump({"escritorios": escritorios}, f)


def entrar(web, email):
    with mock.patch.object(porteiro, "entrar_no_firebase", lambda e, s: s == "certa"):
        return web.post("/entrar", json={"email": email, "senha": "certa"})


def test_porteiro():
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]},
                       parado={"nome": "Parado", "porta": PORTA_PARADA, "emails": ["bia@x.com"]})
    web = porteiro.app.test_client()
    r = web.get("/api/estado")
    assert r.status_code == 401 and r.get_json()["entrar"] is True
    with mock.patch.object(porteiro, "entrar_no_firebase", lambda e, s: False):
        assert web.post("/entrar", json={"email": "ana@x.com", "senha": "errada"}).status_code == 401
    assert entrar(web, "carlos@x.com").status_code == 403  # senha certa, sem escritório
    assert entrar(web, "Ana@X.com").status_code == 200  # maiúsculas não importam
    j = web.get("/api/eco?x=1").get_json()
    assert j == {"metodo": "GET", "query": "x=1", "tipo": "", "tamanho": 0, "cookie": ""}  # sessão não vai ao robô
    j = web.post("/api/eco", data={"arquivos": (io.BytesIO(b"%PDF-1"), "a.pdf")},
                 content_type="multipart/form-data").get_json()
    assert j["metodo"] == "POST" and j["tipo"].startswith("multipart/form-data") and j["tamanho"] > 6
    assert web.post("/api/eco", json={}, headers={"Origin": "https://mal.exemplo"}).status_code == 403
    assert web.post("/api/eco", json={}, headers={"Origin": "http://localhost"}).status_code == 200
    gravar_escritorios(parado={"nome": "Parado", "porta": PORTA_PARADA, "emails": ["bia@x.com"]})
    assert web.get("/api/eco").status_code == 401  # tirar o e-mail corta o acesso na hora
    outro = porteiro.app.test_client()
    entrar(outro, "bia@x.com")
    r = outro.get("/api/estado")
    assert r.status_code == 502 and "reiniciando" in r.get_json()["erro"]
    with mock.patch.object(porteiro, "pedir_nova_senha", lambda e: None):
        assert web.post("/esqueci", json={"email": "ninguem@x.com"}).status_code == 200
    assert web.get("/saude").status_code == 200
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python testes/test_porteiro.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'porteiro'`.

- [ ] **Step 3: Implementar `servidor/porteiro.py` como em Interfaces.**

- [ ] **Step 4: Rodar e ver passar**

Run: `python testes/test_porteiro.py`
Expected: `ok  test_porteiro`.

---

### Task 5: Página (`sistema/web/index.html`)

**Files:**
- Modify: `sistema/web/index.html`:
  - `api()` (linha ~580);
  - `TELAS` / `mostrarTela`;
  - o topo (476-483);
  - a lista de clientes (485-504);
  - o campo "Pasta dos clientes" (~1277);
  - o bloco "Ler de novo do PJe" (~1369);
  - os botões da pausa (~1031-1041);
  - a ficha (botão Apagar).

**Interfaces:**
- Consumes:
  - `cfg.servidor` (Task 2) e `est.pausa.rascunho` (Task 2);
  - `POST /api/clientes/enviar` e `POST /api/clientes/<nome>/apagar` (Task 3);
  - `POST /entrar`, `/sair` e `/esqueci`, e o 401 com `entrar: true` (Task 4).

- [ ] **Step 1: Tela de entrar**
  - `<main id="tela-entrar" hidden>`: e-mail, senha, botão "Entrar" e link "Esqueci a senha". O link manda o
    e-mail e mostra "Se o e-mail estiver cadastrado, chega um link para criar uma senha nova.";
  - `"entrar"` entra em `TELAS`;
  - em `api()`, resposta 401 com `dados.entrar` chama `mostrarTela("entrar")` e lança o erro;
  - depois de entrar com sucesso, `location.reload()`.

- [ ] **Step 2: Modo servidor na tela** (tudo condicionado a `cfg.servidor`; no modo local nada aparece)
  - **Topo:** botão "Sair" (POST `/sair`, depois `location.reload()`).
  - **Lista de clientes:**
    - botão "Enviar pastas" (`<input type="file" webkitdirectory multiple hidden>`) e área de soltar no `aside.lista`;
    - ao soltar, juntar os arquivos com `DataTransferItem.webkitGetAsEntry()`, de forma recursiva, chamando
      `readEntries` até vir vazio, porque ele devolve em lotes. O caminho de cada arquivo é `entry.fullPath` sem a
      `/` inicial; no input, é `file.webkitRelativePath`;
    - envio por `fetch` com `FormData` (`arquivos` e `caminhos` na mesma ordem) e tempo limite de 10 minutos;
    - depois, `avisar()` com as pastas criadas e as ignoradas, e recarregar a lista.
  - **Ficha:** botão "Apagar este cliente". Confirma com
    `confirm("Mover <nome> para a Lixeira? Ela se esvazia sozinha em 30 dias.")`, faz POST `apagar` e recarrega a
    lista.
  - **Configurações:**
    - esconder o campo "Pasta dos clientes";
    - trocar o bloco "Ler de novo do PJe" pela dica "No servidor, o administrador atualiza as opções do PJe.";
    - não mostrar o caminho do servidor (`raizClientes`) em lugar nenhum.
  - **Pausa:**
    - com `p.rascunho`, link "Abrir o rascunho no PJe" (`target="_blank" rel="noopener"`), e o botão continuar com o
      texto "Já resolvi no meu PJe, continuar";
    - no servidor sem `p.rascunho`, sem o botão continuar;
    - no modo local, como hoje.

- [ ] **Step 3: Verificar no navegador**
  - Escrever `scratchpad/demo_servidor.py`, que:
    - liga um `app_web` com `robo.SERVIDOR = True`, `robo.DADOS` numa pasta temporária e `ler_config` e
      `gravar_config` falsos, na porta 8801;
    - liga o porteiro com `entrar_no_firebase` falso, `SESSION_COOKIE_SECURE = False` e um `escritorios.json`
      temporário apontando para 8801, na porta 8800.
  - Com o Playwright, tirar fotos de:
    - a tela de entrar;
    - a lista com "Enviar pastas", depois de enviar uma pasta **sintética** (Fulana, PDF gerado);
    - a ficha com "Apagar";
    - as Configurações sem "Pasta dos clientes";
    - a pausa com o link do rascunho, injetando `estado["pausa"]` direto no `app_web`, no claro e no escuro.
  - Olhar as fotos.

Run: `python <scratchpad>/demo_servidor.py`
Expected: as 6 fotos mostram o descrito, sem texto sobreposto. Rodar também `python testes/test_app_web.py` e
`python testes/test_porteiro.py`, que continuam todos `ok`.

---

### Task 6: Arquivos do servidor

**Files:**
- Create: `servidor/requisitos.txt`
- Create: `servidor/montar.sh`
- Create: `servidor/atualizar.sh`
- Create: `servidor/robo@.service`, `servidor/porteiro.service`, `servidor/limpar.service`, `servidor/limpar.timer`
- Create: `servidor/Caddyfile`
- Create: `servidor/limpar.py`, `servidor/escritorio_novo.py`
- Modify: `LEIA-ME.txt`. Seção nova, "NO SERVIDOR", com o que é cada arquivo de `servidor/` e como entrar um
  escritório.
- Test: `testes/test_servidor.py`

**Interfaces:**
- Consumes: `porteiro.criar_usuario` e `porteiro.pedir_nova_senha` (Task 4); o formato de `escritorios.json`
  (Task 4).
- Produces:
  - **`limpar.limpar(raiz_escritorios: str, dias: int = 30, agora: float | None = None) -> list[str]`:** apaga
    (`shutil.rmtree` ou `os.remove`) o que está em `*/clientes/Concluído/*`, `*/clientes/Lixeira/*` e `*/erros/*`
    com mtime mais velho que `dias`, e devolve os caminhos apagados. O `__main__` usa
    `os.path.join(ROBO_RAIZ, "escritorios")`.
  - **`escritorio_novo.adicionar(arquivo: str, id_: str, nome: str, emails: list[str]) -> int`:**
    - `id_` tem que casar com `^[a-z0-9-]{2,30}$`, senão `sys.exit`;
    - e-mails vão para minúsculas;
    - e-mail que já é de outro escritório dá `sys.exit`;
    - id que já existe recebe os e-mails a mais e mantém a porta;
    - id novo recebe `max(8801, maior porta + 1)`;
    - gravação atômica. Devolve a porta.
  - **`escritorio_novo` `__main__`** (`python escritorio_novo.py <id> "<nome>" <email>...`), depois de `adicionar`:
    - escritório novo:
      - cria `escritorios/<id>/clientes`;
      - grava `escritorios/<id>/robo.env` com `ROBO_PORTA=<porta>` e `ROBO_ESCRITORIO=<id>`;
      - roda `gcloud secrets create pje-login-<id> --replication-policy=user-managed --locations=southamerica-east1`;
      - roda `systemctl enable --now robo@<id>`;
    - para cada e-mail: `porteiro.criar_usuario` e `porteiro.pedir_nova_senha`. A pessoa recebe o link e cria a
      própria senha.
  - **`robo@.service`:**
    - `User=robo`;
    - `EnvironmentFile=/srv/robo/robo.env` e `/srv/robo/escritorios/%i/robo.env`;
    - `Environment=ROBO_DADOS=/srv/robo/escritorios/%i`;
    - `ExecStart=/srv/robo/venv/bin/python /srv/robo/codigo/sistema/app_web.py`;
    - `Restart=always`.
  - **`porteiro.service`:** igual, com `EnvironmentFile=/srv/robo/porteiro.env` e
    `ExecStart=.../servidor/porteiro.py`.
  - **`limpar.timer`:** diário, com `Persistent=true`.
  - **`Caddyfile`:** `{$ROBO_DOMINIO} { request_body { max_size 210MB }  reverse_proxy 127.0.0.1:8800 }`.
  - **`montar.sh <dominio> <firebase-api-key>`:** roda como root, e rodar de novo não estraga nada. Faz:
    - atualizações automáticas e `unattended-upgrades`;
    - usuário `robo`;
    - `uv`;
    - clone ou pull em `/srv/robo/codigo`;
    - `uv venv --python 3.14 /srv/robo/venv` e `uv pip install -r servidor/requisitos.txt`;
    - `playwright install --with-deps chrome`;
    - Caddy (repositório oficial apt);
    - `/srv/robo/robo.env` com `ROBO_SERVIDOR=1` e `ROBO_PROJETO`, vindo do metadata do servidor;
    - `/srv/robo/porteiro.env` com `FIREBASE_API_KEY`, `ROBO_RAIZ=/srv/robo` e `ROBO_CHAVE_SESSAO`. A chave de
      sessão é gerada uma vez só, com `openssl rand -hex 32`, e não é trocada se já existir;
    - `ROBO_DOMINIO` no ambiente do Caddy;
    - copiar as units, `daemon-reload` e `enable --now porteiro limpar.timer caddy`;
    - `chown -R robo /srv/robo` e `chmod 600 /srv/robo/*.env`.
  - **`atualizar.sh`:**
    - `git pull` e `uv pip install -r` como `robo`;
    - para cada `escritorios/*/robo.env`: espera `curl -s 127.0.0.1:$ROBO_PORTA/api/estado` ter `tarefa` vazia,
      perguntando a cada 10 s e mostrando qual escritório espera, e então `systemctl restart robo@<id>`;
    - por último, `systemctl restart porteiro`;
    - `# ponytail: uma tarefa pode começar entre a conferência e o reinício; aceitável com poucos escritórios`.
  - **`requisitos.txt`:** `flask`, `playwright==1.58.0`, `python-docx`, `pyotp`, `PyPDF2`,
    `google-cloud-secret-manager`, `waitress`.

- [ ] **Step 1: Escrever os testes que falham** (`testes/test_servidor.py`, com a mesma descoberta automática)

```python
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
os.environ.setdefault("ROBO_CHAVE_SESSAO", "teste"); os.environ.setdefault("FIREBASE_API_KEY", "x")
import limpar, escritorio_novo


def test_limpar():
    raiz, agora = tempfile.mkdtemp(), time.time()
    velho, novo = agora - 31 * 86400, agora - 29 * 86400
    itens = [("jvo/clientes/Concluído/Fulana", velho), ("jvo/clientes/Concluído/Beltrano", novo),
             ("jvo/clientes/Lixeira/Ciclano", velho), ("jvo/clientes/Pendente", velho)]
    for caminho, _ in itens:
        os.makedirs(os.path.join(raiz, caminho))
    os.makedirs(os.path.join(raiz, "jvo/erros"))
    for nome, quando in [("velha.png", velho), ("nova.png", novo)]:
        open(os.path.join(raiz, "jvo/erros", nome), "wb").close()
        itens.append((f"jvo/erros/{nome}", quando))
    for caminho, quando in itens:
        os.utime(os.path.join(raiz, caminho), (quando, quando))
    removidos = limpar.limpar(raiz, dias=30, agora=agora)
    assert sorted(os.path.basename(r) for r in removidos) == ["Ciclano", "Fulana", "velha.png"]
    assert os.path.isdir(os.path.join(raiz, "jvo/clientes/Pendente"))  # cliente não cadastrado nunca é apagado


def test_adicionar_escritorio():
    arq = os.path.join(tempfile.mkdtemp(), "escritorios.json")
    assert escritorio_novo.adicionar(arq, "jvo", "JVO", ["a@x.com"]) == 8801
    assert escritorio_novo.adicionar(arq, "outro", "Outro", ["B@x.com"]) == 8802
    assert escritorio_novo.adicionar(arq, "jvo", "JVO", ["c@x.com"]) == 8801  # mais um e-mail no mesmo escritório
    for id_, emails in [("Ruim/..", ["d@x.com"]), ("novo", ["b@x.com"])]:  # id inválido; e-mail de outro escritório
        try:
            escritorio_novo.adicionar(arq, id_, "X", emails)
            assert False, id_
        except SystemExit:
            pass
    dados = json.load(open(arq, encoding="utf-8"))
    assert dados["escritorios"]["jvo"]["emails"] == ["a@x.com", "c@x.com"]
    assert dados["escritorios"]["outro"]["emails"] == ["b@x.com"]
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python testes/test_servidor.py`
Expected: FAIL com `ModuleNotFoundError: No module named 'limpar'`.

- [ ] **Step 3: Implementar `limpar.py` e `escritorio_novo.py`, e escrever os outros arquivos como em Interfaces.
  Atualizar `LEIA-ME.txt`.**

- [ ] **Step 4: Rodar e ver passar**

Run: `python testes/test_servidor.py; bash -n servidor/montar.sh; bash -n servidor/atualizar.sh`
Expected: `ok` nos dois testes, e os `bash -n` sem saída.

- [ ] **Step 5: Rodar tudo**

Run: `python testes/test_novo_processo.py; python testes/test_app_web.py; python testes/test_porteiro.py; python testes/test_servidor.py`
Expected: todos `ok`.

---

### Task 7: Servidor no Google e primeiro escritório (gera custo: pedir o ok do usuário antes do Step 1)

**Files:** nenhum no repositório. Comandos no Google Cloud e no servidor.

**Interfaces:**
- Consumes: tudo das Tasks 1 a 6, já no GitHub.
  - O servidor clona o repositório público. Então **esta task exige que o usuário tenha pedido commit e push das
    Tasks 1 a 6**. Sem isso, parar e avisar.

- [ ] **Step 1: Projeto, APIs e Firebase** (`<P>` = `robo-pje-tjmt`; se o nome estiver ocupado, `robo-pje-tjmt-<4 dígitos>`)

```bash
gcloud projects create <P> --name="Robo PJe"
gcloud billing projects link <P> --billing-account=<conta aberta de `gcloud billing accounts list`>
gcloud services enable compute.googleapis.com secretmanager.googleapis.com identitytoolkit.googleapis.com firebase.googleapis.com --project=<P>
firebase projects:addfirebase <P>
firebase apps:create web "Robo PJe" --project <P>          # depois: firebase apps:sdkconfig web <appId> --project <P> -> apiKey
```

Ligar "E-mail/senha" em Authentication > Sign-in method, no console do Firebase, e pôr o modelo de e-mail em
português. Restringir a chave de API à "Identity Toolkit API" no console do Google Cloud.
Expected: `firebase apps:sdkconfig` mostra um `apiKey`. **Não colar o apiKey em arquivo do repositório.**

- [ ] **Step 2: Rede, máquina, permissões e backup**

```bash
gcloud compute addresses create robo-ip --region=southamerica-east1 --project=<P>
gcloud compute firewall-rules create web --allow=tcp:80,tcp:443 --target-tags=web --project=<P>
gcloud compute firewall-rules create ssh-iap --allow=tcp:22 --source-ranges=35.235.240.0/20 --project=<P>
gcloud compute firewall-rules delete default-allow-ssh default-allow-rdp --quiet --project=<P>
gcloud compute instances create robo-pje --zone=southamerica-east1-a --machine-type=e2-medium \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud --boot-disk-size=30GB --boot-disk-type=pd-balanced \
  --address=robo-ip --tags=web --scopes=cloud-platform --project=<P>
gcloud projects add-iam-policy-binding <P> --member=serviceAccount:<número>-compute@developer.gserviceaccount.com --role=roles/secretmanager.admin
gcloud compute resource-policies create snapshot-schedule diario --region=southamerica-east1 --daily-schedule --start-time=06:00 --max-retention-days=7 --project=<P>
gcloud compute disks add-resource-policies robo-pje --resource-policies=diario --zone=southamerica-east1-a --project=<P>
```

Expected: `gcloud compute instances list --project=<P>` mostra `robo-pje RUNNING` com o IP fixo.

- [ ] **Step 3: Montar**

```bash
gcloud compute ssh robo-pje --zone=southamerica-east1-a --tunnel-through-iap --project=<P> \
  --command="sudo apt-get install -y git && sudo git clone https://github.com/TheoCarvalhoDEV/cadastroporcessosPJE /srv/robo/codigo; sudo bash /srv/robo/codigo/servidor/montar.sh <ip-com-traços>.sslip.io <apiKey>"
```

Expected:
- `curl -s https://<ip-com-traços>.sslip.io/saude` responde `ok`;
- `systemctl is-active porteiro caddy` mostra `active` duas vezes;
- `systemd-analyze verify /etc/systemd/system/robo@.service` não mostra nada.

- [ ] **Step 4: Escritório JVO**

Rodar no servidor, como root, porque o script liga o serviço e acerta o dono das pastas:
`sudo /srv/robo/venv/bin/python /srv/robo/codigo/servidor/escritorio_novo.py jvo "JVO" <e-mails que o usuário indicar>`.

Copiar `escritorio.json` e `historico.jsonl` com
`gcloud compute scp --tunnel-through-iap ... robo-pje:/tmp/`, e depois `sudo mv` para `/srv/robo/escritorios/jvo/`
e `chown robo`.

O login do PJe é preenchido **pelo usuário** em Configurações > Login do PJe, na página do servidor. A chave do
autenticador ele copia de "Mostrar o QR para outro celular", no robô local. Só com ok explícito do usuário: enviar o
login daqui com `gcloud secrets versions add pje-login-jvo --data-file=-`.

Expected:
- `systemctl is-active robo@jvo` mostra `active`;
- o e-mail de criar senha chega;
- depois de entrar, a página mostra o nome e a cor da JVO e o Relatório com o histórico.

- [ ] **Step 5: Teste de fumaça e primeiro cadastro real**
  - Enviar uma pasta **sintética** (Fulana, CPF 111.444.777-35) e conferir a ficha. Depois, "Apagar".
  - Primeiro cadastro real com o usuário acompanhando:
    - um cliente pendente que **não** seja cópia de um cliente já cadastrado (duplicaria o rascunho no PJe);
    - testar o risco aberto: o advogado abre o rascunho no próprio navegador durante uma pausa. Anotar se alguma
      sessão caiu.

Expected: o rascunho fica pronto no PJe (sem assinar e sem protocolar) e a pasta vai para Concluído no servidor.

- [ ] **Step 6: Aviso de queda e limpeza**
  - Em Monitoring > Uptime checks: HTTPS `https://<domínio>/saude`, a cada 5 minutos, com alerta por e-mail para
    theotheteo@gmail.com.
  - Apagar o projeto de teste com `gcloud projects delete robo-pje-teste-1109` (pode ser recuperado por 30 dias).
  - Atualizar a memória `interface-web-escritorios` com o endereço e o projeto de produção.

Expected: o uptime check aparece verde e `gcloud projects list` não mostra mais o projeto de teste.
