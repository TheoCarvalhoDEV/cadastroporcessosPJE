# Autus: estado do projeto

Atualizado em 10/11/2026. Este documento resume o que o Autus é, como está montado, tudo o que foi feito até
agora e o que ainda falta. O `LEIA-ME.txt` explica cada arquivo; aqui está a visão do todo.

---

## 1. O que é o Autus

O Autus cadastra processos no **PJe do TJMT** para escritórios de advocacia. O escritório envia a pasta de cada
cliente (petição inicial, anexos e, se quiser, uma ficha `.docx`). Um robô lê a inicial, identifica matéria,
jurisdição, competência, classe, assunto e partes, entra no PJe com o login do escritório e deixa o processo pronto
como **rascunho**. O robô **nunca assina nem protocola**: quem faz isso é o advogado, depois de conferir.

Quando o robô não consegue seguir, ele **pausa**, tira uma foto da tela do PJe e pede ajuda (repetir, continuar
depois de resolver à mão ou pular o cliente).

O produto vai ser vendido para outros escritórios. Por isso o robô roda **na nuvem**: o cliente só abre uma página
no navegador, e o desempenho do computador dele não importa.

---

## 2. Como está montado

```
cliente (navegador) ──► https://autus.web.app   (página, no Firebase Hosting)
                              │  pedidos com a chave de sessão (cabeçalho Authorization)
                              ▼
               https://34-39-255-214.sslip.io   (máquina do Google em São Paulo)
                     Caddy (HTTPS) ─► porteiro.py ─► robô do escritório (app_web.py, um por escritório)
                                          │                     └─► Google Chrome sem janela ─► PJe
                                          ├─► Firebase Authentication (e-mail e senha; contas criadas só pela máquina)
                                          ├─► botão "Fazer login com o Google" (token conferido no servidor)
                                          └─► Secret Manager (login do PJe de cada escritório)
```

| Peça | Onde | O que faz |
|---|---|---|
| Página | `sistema/web/index.html`, publicada no Firebase Hosting pelo `servidor/publicar_pagina.py` | Toda a tela: entrar, início, cadastro, ajustes, relatório, administrador. Fala com o servidor por trás. |
| Porteiro | `servidor/porteiro.py` | Login (Firebase e Google), chave de sessão, pedidos de acesso, painel do administrador, repasse para o robô de cada escritório. |
| Robô | `sistema/app_web.py` + `sistema/novo_processo.py` | Um processo por escritório. Lê as pastas, conversa com a página e dirige o Chrome no PJe (Playwright). |
| Leitura do PJe | `sistema/mapear_pje.py` → `sistema/mapa_pje.json` | As opções reais da tela Novo processo do TJMT (matéria > jurisdição > competência > classe...). |
| Máquina | Google Compute Engine, `robo-pje` (e2-medium, `southamerica-east1-a`) | Ubuntu 24.04. Caddy, porteiro, um `robo@<id>` por escritório, limpeza diária. |
| Comando protegido | `servidor/ligar_escritorio.sh` → `/usr/local/sbin/autus-ligar-escritorio` | A única parte que precisa de root: liga o robô de um escritório novo. O porteiro só pode rodar esse comando (sudo). |

**Modo local** (o de antes, ainda funciona): `Abrir cadastro.bat` roda o robô no próprio computador, com o Chrome
com janela, em `http://127.0.0.1:8765`. Os dados ficam em `dados_do_escritorio/`.

### Recursos na nuvem (sem senhas: elas ficam só no servidor)

| Item | Valor |
|---|---|
| Endereço dos clientes | https://autus.web.app |
| Endereço do servidor | https://34-39-255-214.sslip.io (IP fixo 34.39.255.214); quem entra por ele vai para o autus.web.app |
| Projeto Google Cloud / Firebase | `robo-pje-tjmt` (nome: Autus) |
| Site do Firebase Hosting | `autus` |
| Escritório em produção | `jvo` (serviço `robo@jvo`, porta 8801) |
| Administrador do Autus | theotheteo@gmail.com |
| Variáveis do porteiro | `/srv/robo/porteiro.env`: `FIREBASE_API_KEY`, `ROBO_CHAVE_SESSAO`, `ROBO_SITE`, `ROBO_API`, `ROBO_GOOGLE_CLIENT` |
| Arquivos de dados | `/srv/robo/escritorios.json` (e-mails e administradores), `/srv/robo/escritorios/<id>/`, `auditoria.jsonl`, `pedidos_acesso.json` |
| Cópias de segurança | foto diária do disco às 06:00, guardada por 7 dias |
| Limpeza | todo dia apaga o que passou de 30 dias em Concluído, Lixeira e fotos das pausas |

---

## 3. O que foi feito

### Base (04/11)
- Robô de cadastro no PJe com interface web, modo claro e escuro, raça/cor por cliente (`978340a`, `6a7c934`, `4cbaafd`).
- Troca do Selenium pelo Playwright (ver `!! MUDANDO AGORA - troca para Playwright.txt`).

### Robô na nuvem (09/11)
- Modo servidor, porteiro com login do Firebase e scripts da máquina do Google (`7da62a2`, `63c7e17`).
- Página Criar senha no próprio sistema e e-mail de criar senha pedido com a conta da máquina (`971b3c1`, `28a683d`).

### Correções do login no PJe (09/11)
- **Chrome travado no perfil:** quando o login parava, o Chrome ficava aberto e a tentativa seguinte falhava com
  `TargetClosedError`. Agora o robô reaproveita o mesmo Chrome (`34d4546`).
- **Chave do autenticador conferida:** espaços e hífens são tirados sozinhos; chave inválida é recusada ao salvar,
  com mensagem clara (`34d4546`).
- **Leitor de QR:** a chave do autenticador pode ser lida de um print ou foto do QR, inclusive do "Transferir
  contas" do Google Authenticator (`bd1b08c`).
- Imagens soltas na pasta do projeto ficam fora do GitHub (`b2cdd0b`).

### Página no Firebase e segurança do login (10/11)
- **autus.web.app na barra do navegador:** a página sai do Firebase Hosting e o servidor só faz o trabalho.
  A sessão virou uma chave assinada de 12 horas, "Sair" encerra a chave na hora, e só o autus.web.app pode chamar o
  servidor (CORS). A página tem política de segurança de conteúdo (CSP) e demais cabeçalhos de proteção (`b68e430`).
- `atualizar.sh` roda a versão nova de si mesmo depois do `git pull` (`072737e`).
- **Login mais seguro:** cadastro aberto do Firebase fechado (só a máquina cria contas) e senha com no mínimo
  10 caracteres e um número (`9dd7ddf`).

### Administrador do Autus (10/11)
- **Tela Escritórios:** todos os clientes com a situação do robô, entrar em qualquer escritório, criar escritório
  novo (o robô é ligado na hora), liberar e retirar e-mails, registro de toda ação do administrador (`c952b2e`).
- **Aba Erros:** problemas de todos os escritórios num lugar só, com período, filtro por escritório, ranking do que
  mais se repete, foto da tela do PJe e a página salva para baixar. As paradas antes do cadastro (login, Chrome,
  autenticador) passaram a ser registradas (`37f79eb`).

### Configuração simples (10/11)
- **Assistente de primeiro acesso**, uma pergunta por tela: nome e cor; login do PJe e o código de 6 números
  ("já está no meu celular" ou "não sei"), com **Testar e continuar**; pasta de exemplo do cliente; tudo pronto.
- **Ajustes > Básico:** três cartões (Escritório, Acesso ao PJe, Documentos). **Ajustes > Avançado:** as regras do
  robô, que no servidor só o administrador vê (`4478f88`).

### Nome, e-mails e login com o Google (10/11)
- **Autus** como nome do sistema na aba do navegador, tela de entrar, topo e sigla (`2da452b`).
- O e-mail de criar senha termina com o botão **Continuar** para o autus.web.app (`e230054`). O texto e o link do
  e-mail não dão para mudar: o Firebase bloqueia a edição dos modelos neste projeto.
- **Login com o Google** e **pedidos de acesso:** conta liberada entra; a que não está vira um pedido que o
  administrador libera (escolhendo o escritório) ou recusa no painel (`53487e2`).
- **Política de Privacidade** (`/privacidade`) e **Termos de Serviço** (`/termos`), exigidos pelo Google, com links
  na tela de entrar (`b4871f3`).

### No ar e no Google Cloud
- Servidor na versão `e230054`. Os commits `53487e2` e `b4871f3` (login com o Google e páginas legais) estão no
  GitHub, mas **ainda não foram publicados** no servidor.
- Firebase: login por e-mail e senha, cadastro aberto desligado, regra de senha, domínios autorizados
  (`autus.web.app`, `34-39-255-214.sslip.io`), nome público Autus.
- Google Auth Platform: app Autus publicado, cliente OAuth "Aplicativo da Web" com a origem `https://autus.web.app`.

---

## 4. Como fazer as tarefas do dia a dia

**Atualizar o servidor** (depois de enviar o código ao GitHub):
```
gcloud compute ssh robo-pje --zone=southamerica-east1-a --tunnel-through-iap --project=robo-pje-tjmt
sudo bash /srv/robo/codigo/servidor/atualizar.sh
```
Ele baixa o código, instala as bibliotecas, espera cada robô ficar parado antes de reiniciar, publica a página no
Firebase e reinicia o porteiro.

**Escritório novo:** pela página, em Escritórios > Escritório novo. Cada e-mail recebe o link de criar senha.
Quem usa conta Google também pode entrar com o Google e ser liberado pelos Pedidos de acesso.

**Administrador novo** (só pelo servidor, nunca pela página):
```
sudo /srv/robo/venv/bin/python /srv/robo/codigo/servidor/escritorio_novo.py --admin <email>
```

**Testes** (sem PJe), na pasta `testes/`:
```
python test_app_web.py      python test_porteiro.py      python test_servidor.py      python test_novo_processo.py
```
O `test_novo_processo.py` precisa da biblioteca `google-cloud-secret-manager` instalada no computador.

---

## 5. O que ainda falta fazer

### Para agora
1. **Publicar o login com o Google e as páginas legais.** Pôr
   `ROBO_GOOGLE_CLIENT=174757021649-1081nepkm353tq1t4vq2bobta0ndub9q.apps.googleusercontent.com` em
   `/srv/robo/porteiro.env` e rodar o `atualizar.sh`. Depois, testar o botão com uma conta Google de verdade.
2. **Revisar a Política de Privacidade e os Termos de Serviço** com alguém do escritório (principalmente a
   limitação de responsabilidade).
3. **Apagar a imagem do QR do autenticador** que está na pasta do projeto (`WhatsApp Image 2026-11-09 at
   15.36.23.jpeg`) e a mensagem do WhatsApp onde ela foi enviada: com ela, qualquer pessoa gera os códigos do PJe
   do escritório. Apagar também o arquivo `.txt` solto na pasta, se não for usado.
4. **Cancelar a verificação de domínio** em andamento no Firebase (Authentication > Modelos): sem domínio próprio
   ela nunca conclui.

### Testes com o PJe de verdade
5. **Um cadastro completo com o Playwright:** ainda não rodaram de verdade as etapas de clicar em Incluir, vincular
   parte (pessoa física e órgão público), endereço e raça/cor, Características (justiça gratuita, liminar, valor,
   segredo, prioridade) e subir a inicial e os anexos. Quando passar, apagar o arquivo
   `!! MUDANDO AGORA - troca para Playwright.txt`.
6. **Caminho "não sei" do código de 6 números** no assistente (o robô configura pelo link do e-mail do PJe).
7. **Criar um escritório pela página** e **entrar num escritório como administrador** com um robô real.

### Para o produto comercial
8. **Domínio próprio** (ex.: `autus.com.br`, cerca de R$ 40 por ano). Com ele:
   - o endereço com números (sslip.io, serviço gratuito de terceiros) sai do caminho;
   - o Autus passa a mandar os próprios e-mails de criar senha, com o visual dele e o link para
     `autus.web.app/nova-senha` (o Firebase gera o código e o Autus envia), contornando o bloqueio dos modelos;
   - remetente próprio (`noreply@seudominio`).
9. **Aviso de queda:** verificação automática de `/saude` a cada 5 minutos com alerta por e-mail (Cloud Monitoring).
10. **Verificação em 2 etapas para o administrador** (o usuário escolheu deixar para depois).
11. **Aviso ao administrador quando chegar um pedido de acesso** (hoje só aparece no painel).
12. **Capacidade:** a e2-medium aguenta 2 ou 3 escritórios cadastrando ao mesmo tempo. Com mais clientes, trocar o
    tipo da máquina (poucos minutos fora do ar).
13. **Contrato com os escritórios** prevendo que o suporte técnico acessa os registros de erro (nomes de clientes e
    telas do PJe), como diz a Política de Privacidade.
