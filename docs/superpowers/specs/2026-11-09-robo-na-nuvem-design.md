# Robô na nuvem: documento do projeto

Data: 2026-11-09. Situação: rascunho para o dono do projeto conferir. Escolhas marcadas com **(escolha minha)** são do
Claude, não foram combinadas, e podem ser trocadas.

## 1. Por quê

Hoje o robô roda no computador de cada escritório: Python, Chrome e o programa ligados durante minutos por cadastro.
O dono do projeto não conhece os computadores dos escritórios para onde quer levar o robô e não quer que o robô
"pese" neles. O objetivo é que **o escritório precise só de um navegador**: o robô, o Chrome dele e as pastas dos
clientes ficam num servidor.

## 2. Como fica para o escritório

1. Abre o endereço do robô no navegador e entra com e-mail e senha.
2. Arrasta as pastas dos clientes (inicial em PDF, "DOC 01] ..." etc., .docx opcional) para a página. Elas sobem
   para o servidor.
3. Daí em diante é a tela de hoje: ficha de cada cliente, corrigir, cadastrar, acompanhar, Relatório, Configurações.
4. Quando o robô pausa, a tela mostra a foto e os botões de hoje. A diferença é o "Já fiz no PJe": a pessoa resolve
   no **próprio PJe**, no rascunho que o robô criou (a pausa traz o link), e clica em continuar (ver 5.7).
5. O computador do escritório pode ser desligado durante o cadastro. O robô continua no servidor.

## 3. Objetivos, fora do escopo, e o que não pode acontecer

**Objetivos**
- Robô, Chrome e arquivos dos clientes no servidor. O escritório usa só o navegador.
- Vários escritórios no mesmo servidor, cada um isolado, com login próprio e configurações próprias.
- Uma pausa num escritório não segura o robô de outro escritório.
- Entrar um escritório novo leva minutos e não exige programar.
- O modo local de hoje ("Abrir cadastro.bat") continua funcionando: serve para desenvolver e é o plano B se o PJe
  um dia bloquear o servidor.

**Fora do escopo agora**
- Outros tribunais: continua só TJMT, 1º grau.
- Ver ou controlar ao vivo o Chrome do robô pela página (tela remota). Fica para depois, se o Relatório mostrar
  muitas pausas que só se resolvem mexendo no Chrome do robô.
- Cobrança dos escritórios, planos e pagamentos.
- Painel do administrador com todos os escritórios juntos.
- Permissões diferentes dentro de um escritório: quem entra no escritório vê tudo do escritório.
- Vários servidores ou escala automática.

**O que não pode acontecer, mesmo que o resto funcione**
- Um escritório ver pastas, clientes, fotos, relatório ou senha de outro escritório.
- O robô clicar em Protocolar ou Assinar. Continua proibido, como hoje.
- O robô pedir "Configurar novo dispositivo" sozinho. Continua só pelo botão, com ordem de quem usa.
- Senha, chave do autenticador ou dados de clientes no repositório do GitHub, que é público.
- O escritório precisar instalar qualquer coisa.
- Uma atualização do robô derrubar um cadastro no meio.

## 4. Restrições e o que já foi comprovado

- Conta Google Cloud do dono do projeto (a mesma do Firebase), região **São Paulo (southamerica-east1)**: IP
  brasileiro e dados no Brasil.
- Login do PJe por CPF, senha e código do autenticador (TOTP), sem certificado digital.
- O código é desenvolvido em Python 3.14 no Windows. No servidor, a versão tem que ser a mesma.
- **Testado em 2026-11-09**, numa máquina do Google em São Paulo:
  - a tela de login do PJe abre (HTTP 200) num Chrome sem janela (headless);
  - o PJe aceita o CPF e a senha e chega à tela do código.
  - O código falhou porque a chave guardada estava velha. A chave nova funcionou aqui no computador logo depois.
- **Ainda não testado:** um cadastro completo a partir do servidor (anexos, partes etc.) e o advogado abrindo o
  rascunho no próprio navegador enquanto o robô está logado com o mesmo CPF.

## 5. Como funciona

```
navegador do escritório
        │ HTTPS
        ▼
┌──────────── servidor (Google Compute Engine, São Paulo) ────────────┐
│ Caddy (HTTPS automático)                                            │
│   └─► porteiro.py: confere o login (Firebase), descobre o escritório│
│         ├─► robô do escritório A  (app_web.py, porta local 8801)    │
│         ├─► robô do escritório B  (app_web.py, porta local 8802)    │
│         └─► ...                                                     │
│ /srv/robo/escritorios/<id>/  dados do escritório + pastas clientes  │
└─────────────────────────────────────────────────────────────────────┘
Firebase Authentication: e-mail e senha dos usuários
Secret Manager: CPF, senha e chave do autenticador do PJe de cada escritório
```

### 5.1 Servidor

- Uma máquina no Compute Engine em São Paulo, com IP fixo. Ubuntu 24.04 LTS. Para começar, **e2-medium** (2 vCPU,
  4 GB) **(escolha minha)**.
- Não usar Cloud Functions nem Cloud Run. Um cadastro leva minutos e uma pausa pode esperar horas por uma pessoa,
  e esses serviços são feitos para tarefas curtas.
- Instalados: Google Chrome estável (o robô usa `channel="chrome"`, igual nos escritórios); Python 3.14 via `uv`
  **(escolha minha: mesma versão do desenvolvimento, sem surpresas de compatibilidade)**; Caddy; o código, clonado
  do GitHub.
- Os serviços rodam com um usuário `robo` sem poderes de administrador. Firewall abre só 80 e 443. Acesso SSH
  só pelo IAP do Google. Atualizações de segurança do Ubuntu são automáticas (unattended-upgrades).
- **Teto:** cada escritório trabalhando ao mesmo tempo é um Chrome aberto. A e2-medium aguenta uns 2 ou 3 ao mesmo
  tempo. Para mais, troca-se o tipo da máquina, com poucos minutos fora do ar.

### 5.2 Um robô por escritório (um processo cada)

- O `novo_processo.py` guarda o escritório em variáveis do módulo: `DADOS`, `carregar_escritorio()` preenchendo
  globais e `navegador_atual`. O Playwright só aceita o Chrome na linha de execução que o abriu. Rodar dois
  escritórios no mesmo processo exigiria refazer esse módulo.
- **Escolha minha:** cada escritório roda o `app_web.py` de hoje num processo separado, como unidade systemd
  `robo@<id>`. Cada processo tem a própria pasta de dados, a própria fila, o próprio Chrome e as próprias pausas.
  Pausa no A não segura o B, e o código do robô quase não muda.
- Mudanças no código, ligadas por variáveis de ambiente (sem elas, o modo local continua igual a hoje):
  - `ROBO_DADOS`: pasta de dados do escritório, no lugar de `CADASTRO DE PROCESSOS/dados_do_escritorio`.
  - `ROBO_PORTA`: porta local do processo.
  - `ROBO_SERVIDOR=1`, que liga o modo servidor:
    - as pastas dos clientes ficam fixas em `ROBO_DADOS/clientes`, e o campo "Pasta dos clientes" some das
      Configurações. Hoje esse campo aceita qualquer pasta do computador, o que deixaria um escritório apontar
      para a pasta de outro;
    - o login do PJe fica no Secret Manager (5.6);
    - o Chrome roda sem janela, num tamanho fixo de 1366×768 ou maior, para as fotos das pausas continuarem úteis;
    - não abre o navegador ao iniciar;
    - fecha o Chrome quando a fila do escritório esvazia, para liberar memória;
    - "Opções do PJe > Ler de novo" fica desligado para os escritórios. O `mapa_pje.json` é um só para todos, e
      quem atualiza é o administrador.
- Cada processo só escuta em 127.0.0.1, como hoje. Só o porteiro fala com eles.

### 5.3 Porteiro (`porteiro.py`)

Um Flask pequeno, novo:
- Entrega a página (`web/index.html` e as fontes).
- `POST /entrar`: recebe e-mail e senha e confere no Firebase Auth pela API REST (`signInWithPassword`), sem SDK
  na página. Depois procura o e-mail em `/srv/robo/escritorios.json` (e-mail → escritório). Se achar, cria uma
  sessão em cookie HttpOnly, Secure, SameSite=Strict, válida por 12 horas **(escolha minha: cookie em vez de token
  em cada chamada, porque as fotos das pausas são `<img src>` e não mandam cabeçalho)**.
- `/api/*`: confere o cookie, **confere de novo o e-mail no `escritorios.json` a cada pedido** (tirar um e-mail de
  lá corta o acesso na hora) e repassa o pedido ao processo do escritório. O escritório nunca vem do pedido, só da
  sessão.
- Limite de tamanho por envio. Não repassa cabeçalhos de roteamento vindos de fora.
- `GET /saude`: responde se o porteiro está no ar.
- Caddy fica na frente só para o HTTPS automático.

### 5.4 Login dos usuários (Firebase Authentication)

- E-mail e senha pelo Firebase Auth, com "esqueci a senha" pronto, sem construir nada. O porteiro fala com o
  Firebase pela API REST, e a chave de API fica só no servidor.
- **Escolha minha:** o script do item 8 cria os usuários no Firebase, manda a cada um o e-mail de "criar senha" e
  liga e-mail ↔ escritório. Sem cadastro aberto.
- Endereço: no começo, um nome gratuito a partir do IP fixo (`<ip-com-traços>.sslip.io`), para não depender de
  comprar domínio. Trocar por um domínio próprio é mudar uma linha do Caddy e os domínios autorizados no Firebase
  **(escolha minha)**.
- **Escolha minha: a página sai do próprio servidor, não do Firebase Hosting.** A página precisa falar com o
  servidor de qualquer jeito, e separar as duas só acrescentaria uma segunda publicação e regras de CORS. O
  Firebase entra onde poupa trabalho: o login.

### 5.5 Pastas dos clientes (envio pela página)

- Área de arrastar e soltar, e um botão "Escolher pastas" (`<input type="file" webkitdirectory multiple>`). Dá para
  enviar várias pastas de cliente de uma vez.
- Rota nova no `app_web.py`: `POST /api/clientes/enviar` (multipart, com o caminho relativo de cada arquivo). Grava
  em `clientes/<pasta do cliente>/<arquivo>`. Regras:
  - só dois níveis (cliente/arquivo). Recusa `..`, caminhos absolutos e caracteres de controle;
  - só aceita `.pdf` e `.docx`. Os outros arquivos são ignorados e listados na resposta;
  - limite de tamanho por envio: 200 MB **(escolha minha)**;
  - se o nome da pasta já existe, ganha " (2)", como o "Concluído" faz hoje.
- O resto já funciona sobre a pasta: `correcoes.json`, `rascunho.json`, mudar para "Concluído" e o
  `gerar_cadastro_docx.py`.
- Botão "Apagar" em cada cliente: move para `clientes/Lixeira/`, que se esvazia após 30 dias **(escolha minha,
  seguindo a regra "apagar sempre para a Lixeira")**.

### 5.6 Login do PJe (Secret Manager)

- No modo servidor, `ler_config()` e a gravação do `config.json` passam a ler e gravar um segredo
  `pje-login-<id>` no Secret Manager. O conteúdo é o mesmo JSON de hoje: cpf, senha, totp_key, totp_qr. A conta de
  serviço da máquina tem acesso.
- Assim a senha não fica no disco nem nos backups (snapshots), e cada leitura fica registrada.
- O botão "Configurar o código pelo robô" e o "Mostrar o QR para outro celular" funcionam como hoje.

### 5.7 Pausas sem acesso ao Chrome do robô

- Hoje "Já fiz no PJe" (`continuar`) supõe que a pessoa mexeu no Chrome do robô. No servidor isso não é possível.
- No modo servidor:
  - a pausa mostra **"Abrir o rascunho no PJe"** (`URL_RASCUNHO` com o id do `rascunho.json`) quando o rascunho já
    existe;
  - ao receber `continuar`, o robô **recarrega o rascunho** antes de seguir, para ver o que a pessoa mudou no PJe dela.
- Antes de o rascunho existir (falha em "Dados iniciais"), a pausa oferece só "Tentar de novo", "Corrigir na ficha"
  e "Pular".
- O Relatório já registra cada pausa e a resposta dada. Ele mostra se vale fazer depois a tela remota (fora do
  escopo agora).

### 5.8 Pastas no servidor

```
/srv/robo/
  codigo/                    clone do GitHub (só código)
  escritorios.json           e-mail → escritório, porta (fora do repositório)
  escritorios/<id>/
    escritorio.json          configurações do escritório (como hoje)
    historico.jsonl          relatório (como hoje)
    erros/                   fotos das pausas
    Perfil_PJe/              Chrome do robô deste escritório
    clientes/                pastas dos clientes, Concluído/, Lixeira/
```

## 6. Segurança e LGPD

- Dados no Brasil (São Paulo). HTTPS sempre. Disco criptografado pelo Google por padrão.
- Isolamento entre escritórios: processo separado, pasta separada, escritório decidido só pela sessão no porteiro.
  Todo caminho vindo da página continua conferido contra a lista do próprio escritório, como `pasta_do_cliente()`
  e `/api/foto` já fazem.
- **Guarda mínima (escolha minha):** pastas em "Concluído" e fotos de pausas apagadas após 30 dias, por um timer
  do systemd. O escritório tem os originais, porque foi ele que enviou.
- O escritório é o controlador dos dados dos clientes. Quem opera o servidor é operador, e isso pede um termo com
  cada escritório. Não é código, mas tem que existir antes do primeiro escritório de fora.
- Logs não mostram senha, chave nem CPF, como já é hoje no `log()`.

## 7. Operação

- **Atualizar:** `atualizar.sh` faz `git pull` e espera cada `robo@*` ficar sem tarefa (`/api/estado` com `tarefa`
  vazia) antes de reiniciar aquele processo. Reinicia o porteiro por último. Nunca derruba um cadastro no meio.
- **Backup:** snapshot diário do disco, guardado por 7 dias.
- **Se cair:** systemd com `Restart=always` em tudo. Uma verificação de disponibilidade do Google em `/saude`
  manda e-mail ao administrador **(escolha minha)**.
- **Montar o servidor:** um script (`servidor/montar.sh`) que dá para rodar de novo sem estragar nada. Cria
  usuário, Chrome, uv/Python, Caddy, unidades systemd, timer de limpeza, firewall e agenda de snapshots.
- **Custo estimado (a confirmar na calculadora do Google):** e2-medium em São Paulo + disco de 30 GB + IP fixo +
  snapshots ≈ **US$ 50 por mês**. Firebase Auth e Secret Manager ficam no gratuito ou perto disso nesse volume.
- **Projeto do Google:** um projeto novo de produção, com o Firebase adicionado a ele. O projeto de teste de
  2026-11-09 é apagado quando a produção estiver no ar **(escolha minha)**.

## 8. Migração da JVO e escritórios novos

- **Escritório novo:** `servidor/escritorio_novo.py <id> "<nome>" <email>...`:
  - cria `escritorios/<id>/` (com `clientes/`);
  - escolhe a próxima porta;
  - grava o `escritorios.json`;
  - liga `robo@<id>`.
  - Depois, o escritório entra na página e preenche Configurações > Login do PJe, como hoje.
- **JVO, primeiro escritório:**
  - copiar `escritorio.json` e `historico.jsonl`;
  - o `config.json` vai para o Secret Manager. Não copiar `Perfil_PJe`: o Chrome do servidor faz login sozinho;
  - os clientes pendentes sobem pela página;
  - o primeiro cadastro real no servidor é acompanhado de perto, e é ele que testa os pontos "ainda não testados"
    do item 4.
- O modo local continua instalado na JVO até o servidor provar que funciona.

## 9. Escolhas minhas, para conferir

1. Uma máquina do Google (e2-medium, São Paulo) no lugar de serviços "sem servidor".
2. Um processo do robô por escritório, em vez de reescrever o robô para vários escritórios num processo só.
3. A página sai do servidor. O Firebase entra só no login.
4. Usuários criados pelo script do administrador, e cada um recebe o e-mail para criar a própria senha. Sem
   cadastro aberto.
5. Endereço inicial `sslip.io`, até haver domínio próprio.
6. "Já fiz no PJe" vira "resolvi no meu PJe": o robô recarrega o rascunho. Sem tela remota por enquanto.
7. Guarda de 30 dias para Concluído, fotos e Lixeira.
8. Limite de 200 MB por envio, só `.pdf` e `.docx`.
9. Python 3.14 via `uv` no servidor.
10. Projeto novo de produção no Google. O de teste é apagado depois.

## 10. Deixado para quem construir

- Detalhes do porteiro: como repassar (streaming de envio e de foto), mensagens de erro e o tempo de sessão exato.
- Formato exato de `escritorios.json`, das unidades systemd e do Caddyfile.
- Visual da área de envio e do link "Abrir o rascunho no PJe", no estilo atual da página.
- Como o robô "recarrega o rascunho e segue" em cada etapa (qual tela abrir e o que conferir).
- Testes: o modo servidor e o modo local precisam passar nos testes que já existem, mais testes do envio (caminhos
  maliciosos, extensões, tamanho) e do porteiro (sem sessão, e-mail removido, escritório trocado).

## 11. Riscos ainda abertos

- **Sessão dupla no PJe:** o advogado abrir o rascunho no navegador dele, com o mesmo CPF, pode derrubar a sessão
  do robô ou a dele. Testar no primeiro cadastro real. Se derrubar, o robô entra de novo sozinho (`entrar()` já faz
  isso), e o link da pausa precisa avisar.
- **PJe passar a bloquear servidores ou Chrome sem janela:** hoje não bloqueia. O plano B é o modo local, que
  continua existindo.
- **Memória:** muitos escritórios cadastrando ao mesmo tempo. O teto e a saída estão no item 5.1.
