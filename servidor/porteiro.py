"""Porteiro do servidor: faz o login (Firebase Authentication pela API REST), dá a chave de sessão e repassa /api/*
ao robô do escritório da pessoa (um app_web.py por escritório, em 127.0.0.1).
A página sai do Firebase Hosting (https://<ROBO_SITE>.web.app, publicada por publicar_pagina.py) e fala com este
servidor em outro endereço: por isso a sessão é uma chave assinada que a página manda no cabeçalho Authorization
(cookie não vai entre endereços diferentes) e só a página do Firebase pode chamar (CORS). Sem ROBO_SITE, a página
sai daqui mesmo, como antes.
O escritório sai SEMPRE da sessão + escritorios.json, nunca do pedido.
Ambiente: ROBO_RAIZ (/srv/robo), ROBO_CHAVE_SESSAO, FIREBASE_API_KEY, ROBO_PORTA_PORTEIRO (8800), ROBO_SITE (autus).
Uso (servidor/porteiro.service): python porteiro.py
"""
import http.client
import json
import os
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse
from flask import Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer
import escritorio_novo

RAIZ = os.environ.get("ROBO_RAIZ", "/srv/robo")
ARQUIVO_ESCRITORIOS = os.path.join(RAIZ, "escritorios.json")
ARQUIVO_ENCERRADAS = os.path.join(RAIZ, "sessoes_encerradas.json")  # quem clicou em Sair: a chave não vale mais
ARQUIVO_AUDITORIA = os.path.join(RAIZ, "auditoria.jsonl")  # tudo o que um administrador faz (e em qual escritório)
ARQUIVO_PEDIDOS = os.path.join(RAIZ, "pedidos_acesso.json")  # quem entrou com o Google e ainda não foi liberado
GOOGLE_CLIENT = os.environ.get("ROBO_GOOGLE_CLIENT", "")  # ID do cliente OAuth do botão "Fazer login com o Google"
MAX_PEDIDOS = 300  # a lista de pedidos não cresce sem fim (qualquer conta Google pode pedir)
LIGAR_ESCRITORIO = ["sudo", "-n", "/usr/local/sbin/autus-ligar-escritorio"]  # ligar_escritorio.sh, dono root
API_KEY = os.environ["FIREBASE_API_KEY"]
PORTA = int(os.environ.get("ROBO_PORTA_PORTEIRO", 8800))
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema", "web")
SITE = os.environ.get("ROBO_SITE", "")  # o site do Firebase Hosting onde a página fica
PAGINA = f"https://{SITE}.web.app" if SITE else ""
ORIGENS = {PAGINA, f"https://{SITE}.firebaseapp.com"} if SITE else set()  # quem pode chamar o servidor de fora
VALIDADE_SESSAO = 12 * 3600  # 12 h desde o login
VALIDADE_QR = 5 * 60  # o QR do autenticador (a chave do código do PJe) só sai até 5 min depois de entrar
# a regra de senha fica no Firebase (Authentication > Settings > Password policy): mínimo 10 e pelo menos um número
REGRA_DA_SENHA = "A senha precisa de pelo menos 10 caracteres, com pelo menos um número."

REPASSA_IDA = ("Content-Type", "Content-Length", "If-None-Match", "Accept-Encoding")  # nunca a chave da sessão
REPASSA_VOLTA = ("Content-Type", "Content-Encoding", "ETag", "Cache-Control", "Vary")

app = Flask(__name__, static_folder=None)
app.config.update(MAX_CONTENT_LENGTH=210 * 1024 * 1024)  # o robô do escritório confere os 200 MB e dá a mensagem
assinador = URLSafeTimedSerializer(os.environ["ROBO_CHAVE_SESSAO"], salt="sessao")
trava_encerradas = threading.Lock()
trava_escritorios = threading.Lock()  # o administrador muda o escritorios.json pela página
trava_pedidos = threading.Lock()


# ---------- escritórios e Firebase ----------

def ler_escritorios():
    """O escritorios.json relido a cada pedido: tirar um e-mail (ou um administrador) de lá corta o acesso na hora."""
    return escritorio_novo.ler(ARQUIVO_ESCRITORIOS)


def escritorio_do_email(email):
    """{"id", "nome", "porta"} do escritório do e-mail, ou None."""
    email = (email or "").strip().lower()
    for id_, e in ler_escritorios()["escritorios"].items():
        if email in e["emails"]:
            return {"id": id_, "nome": e["nome"], "porta": e["porta"]}
    return None


def eh_admin(email):
    return bool(email) and email.strip().lower() in ler_escritorios().get("admins", [])


def escritorio_do_pedido(email):
    """O escritório deste pedido. Administrador escolhe qualquer um pelo cabeçalho X-Escritorio (a página manda o
    que ele abriu); os outros, sempre o do próprio e-mail (o cabeçalho é ignorado)."""
    escolhido = request.headers.get("X-Escritorio", "")
    if escolhido and eh_admin(email):
        e = ler_escritorios()["escritorios"].get(escolhido)
        return {"id": escolhido, "nome": e["nome"], "porta": e["porta"]} if e else None
    return escritorio_do_email(email)


def auditar(email, acao, **detalhes):
    """Uma linha por ação de administrador: quem, quando, o quê."""
    linha = {"quando": time.strftime("%Y-%m-%dT%H:%M:%S"), "email": email, "acao": acao, **detalhes}
    with open(ARQUIVO_AUDITORIA, "a", encoding="utf-8") as f:
        f.write(json.dumps(linha, ensure_ascii=False) + "\n")


def token_da_maquina():
    """Token da conta de serviço da máquina do Google (None fora dela)."""
    try:
        pedido = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/"
                                        "default/token", headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(pedido, timeout=3) as r:
            return json.load(r)["access_token"]
    except Exception:
        return None


def firebase(acao, corpo, como_maquina=False):
    """Chama a API REST do Firebase Authentication. Devolve (status, resposta).
    como_maquina: vai também com a conta da máquina (e-mail de criar senha: sem ela, o link sai sem a chave de API
    e a página do Firebase dá 'The selected page mode is invalid')."""
    cabecalhos = {"Content-Type": "application/json"}
    if como_maquina and (token := token_da_maquina()):
        cabecalhos["Authorization"] = "Bearer " + token
    pedido = urllib.request.Request(f"https://identitytoolkit.googleapis.com/v1/accounts:{acao}?key={API_KEY}",
                                    data=json.dumps(corpo).encode("utf-8"), headers=cabecalhos)
    try:
        with urllib.request.urlopen(pedido, timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except ValueError:
            return e.code, {}


def entrar_no_firebase(email, senha):
    """True se e-mail e senha conferem. Erro de rede ou do Firebase sobe (vira 'tente de novo')."""
    status, resposta = firebase("signInWithPassword", {"email": email, "password": senha, "returnSecureToken": True})
    if status not in (200, 400):
        raise RuntimeError(f"Firebase respondeu {status}: {resposta}")
    return status == 200


def pedir_nova_senha(email):
    """O Firebase manda o e-mail de criar senha nova. Não diz se o e-mail existe. Com a página no Firebase Hosting, a
    página de criar senha do Firebase termina com um botão Continuar que leva para ela (o link do e-mail não dá
    para mudar: o Firebase bloqueia os modelos de e-mail deste projeto sem domínio próprio)."""
    corpo = {"requestType": "PASSWORD_RESET", "email": email, **({"continueUrl": PAGINA + "/"} if PAGINA else {})}
    try:
        firebase("sendOobCode", corpo, como_maquina=True)
    except Exception:
        pass


def criar_usuario(email):
    """Cria o usuário com uma senha aleatória (a pessoa cria a dela pelo e-mail de pedir_nova_senha). Vai com a conta
    da máquina: o cadastro aberto do Firebase fica desligado (ninguém cria conta só com a chave de API)."""
    status, resposta = firebase("signUp", {"email": email, "password": secrets.token_urlsafe(24) + "9"}, como_maquina=True)
    if status != 200 and "EMAIL_EXISTS" not in json.dumps(resposta):
        raise SystemExit(f"Não consegui criar o usuário {email} no Firebase: {resposta}")


# ---------- sessão ----------

def nova_sessao(email):
    """Chave assinada (não dá para inventar nem alterar sem ROBO_CHAVE_SESSAO) com o e-mail e um número só dela."""
    return assinador.dumps({"email": email, "id": secrets.token_urlsafe(12)})


def ler_sessao():
    """{"email", "id", "desde"} da chave do cabeçalho Authorization, ou None (sem chave, alterada, vencida ou
    encerrada). desde: quando a pessoa entrou (segundos)."""
    chave = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not chave:
        return None
    try:
        dados, desde = assinador.loads(chave, max_age=VALIDADE_SESSAO, return_timestamp=True)
    except BadSignature:  # inclui SignatureExpired
        return None
    return None if dados.get("id") in encerradas() else {**dados, "desde": desde.timestamp()}


def encerradas():
    try:
        with open(ARQUIVO_ENCERRADAS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def encerrar(id_):
    """Sair: a chave para de valer na hora (fica anotada até vencer sozinha; as vencidas saem da lista)."""
    with trava_encerradas:
        agora = time.time()
        lista = {i: fim for i, fim in encerradas().items() if fim > agora}
        lista[id_] = agora + VALIDADE_SESSAO
        with open(ARQUIVO_ENCERRADAS + ".tmp", "w", encoding="utf-8") as f:
            json.dump(lista, f)
        os.replace(ARQUIVO_ENCERRADAS + ".tmp", ARQUIVO_ENCERRADAS)


# ---------- rotas ----------

def origem_permitida(origem):
    return origem in ORIGENS or urlparse(origem).netloc == request.host


@app.before_request
def mesma_origem():
    """POST só da própria página: a do Firebase Hosting ou a servida aqui. O pedido de licença do navegador (OPTIONS,
    antes de mandar a chave para outro endereço) é respondido aqui."""
    origem = request.headers.get("Origin")
    if request.method == "OPTIONS":
        return ("", 204) if origem and origem_permitida(origem) else abort(403)
    if request.method == "POST" and origem and not origem_permitida(origem):
        abort(403)


@app.after_request
def liberar_para_a_pagina(resposta):
    """CORS: só a página do Firebase Hosting lê as respostas e manda a chave da sessão. Sem cookies."""
    origem = request.headers.get("Origin")
    if origem in ORIGENS:
        resposta.headers.update({"Access-Control-Allow-Origin": origem, "Access-Control-Allow-Methods": "GET, POST",
                                 "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Escritorio",
                                 "Access-Control-Max-Age": "600"})
        resposta.vary.add("Origin")
    return resposta


@app.errorhandler(403)
def proibido(e):
    return jsonify(erro="Pedido recusado."), 403


@app.errorhandler(413)
def grande_demais(e):
    return jsonify(erro="Envio grande demais: o limite é 200 MB por vez. Envie menos pastas de cada vez."), 413


@app.get("/")
@app.get("/nova-senha")  # link do e-mail de criar senha (a página monta o formulário a partir do caminho)
def pagina():
    if PAGINA:  # a página fica no Firebase Hosting: quem chega pelo endereço do servidor vai para lá
        return redirect(PAGINA + request.full_path.rstrip("?"), 302)
    r = send_file(os.path.join(WEB, "index.html"))
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/fontes/<path:nome>")
def fonte(nome):
    if not nome.endswith(".woff2"):
        abort(404)
    return send_from_directory(os.path.join(WEB, "fontes"), nome, max_age=31536000)


@app.get("/saude")
def saude():
    return "ok"


@app.post("/entrar")
def entrar():
    corpo = request.get_json(silent=True) or {}
    email, senha = str(corpo.get("email", "")).strip().lower(), str(corpo.get("senha", ""))
    if not email or not senha:
        return jsonify(erro="Preencha o e-mail e a senha."), 400
    try:
        certo = entrar_no_firebase(email, senha)
    except Exception:
        return jsonify(erro="Não consegui falar com o serviço de login. Tente de novo em instantes."), 502
    if not certo:
        return jsonify(erro="E-mail ou senha errados."), 401
    if not escritorio_do_email(email) and not eh_admin(email):
        return jsonify(erro="Este e-mail não está ligado a nenhum escritório."), 403
    if eh_admin(email):
        auditar(email, "entrou")
    return jsonify(ok=True, sessao=nova_sessao(email))


def conferir_google(credencial):
    """{"email", "nome"} da conta Google do botão "Fazer login com o Google", ou None. Confere a assinatura do Google,
    para quem o token foi feito (GOOGLE_CLIENT), a validade e se o Google confirmou o e-mail."""
    from google.auth.transport import requests as transporte
    from google.oauth2 import id_token
    try:
        dados = id_token.verify_oauth2_token(credencial, transporte.Request(), GOOGLE_CLIENT)
    except ValueError:  # assinatura, emissor, destinatário ou validade errados
        return None
    if not dados.get("email_verified") or not dados.get("email"):
        return None
    return {"email": dados["email"].strip().lower(), "nome": str(dados.get("name", ""))[:120]}


def ler_pedidos():
    try:
        with open(ARQUIVO_PEDIDOS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def gravar_pedidos(pedidos):
    with open(ARQUIVO_PEDIDOS + ".tmp", "w", encoding="utf-8") as f:
        json.dump(pedidos, f, ensure_ascii=False, indent=2)
    os.replace(ARQUIVO_PEDIDOS + ".tmp", ARQUIVO_PEDIDOS)


def registrar_pedido(email, nome):
    """Conta Google que ainda não foi liberada: fica esperando o administrador (um pedido por e-mail)."""
    with trava_pedidos:
        pedidos = ler_pedidos()
        if email in pedidos or len(pedidos) >= MAX_PEDIDOS:
            return
        pedidos[email] = {"nome": nome, "quando": time.strftime("%Y-%m-%dT%H:%M:%S")}
        gravar_pedidos(pedidos)


@app.post("/entrar-google")
def entrar_google():
    """Login pelo botão do Google. Liberado (escritório ou administrador): entra. Senão: vira pedido de acesso."""
    if not GOOGLE_CLIENT:
        return jsonify(erro="O login com o Google não está ligado."), 404
    credencial = str((request.get_json(silent=True) or {}).get("credencial", ""))
    try:
        quem = conferir_google(credencial) if credencial else None
    except Exception:  # sem rede para buscar as chaves do Google
        return jsonify(erro="Não consegui falar com o Google. Tente de novo em instantes."), 502
    if not quem:
        return jsonify(erro="Não consegui confirmar a sua conta Google. Tente de novo."), 401
    email = quem["email"]
    if escritorio_do_email(email) or eh_admin(email):
        if eh_admin(email):
            auditar(email, "entrou (Google)")
        return jsonify(ok=True, sessao=nova_sessao(email))
    registrar_pedido(email, quem["nome"])
    return jsonify(erro=f"A conta {email} ainda não tem acesso. O pedido foi enviado ao administrador do Autus: "
                        "assim que o acesso for liberado, é só entrar de novo com o Google.", pedido=True), 403


@app.get("/eu")
def eu():
    """Quem está na página: administrador vê a tela de Escritórios antes de abrir um."""
    email = (ler_sessao() or {}).get("email")
    if not email or not (eh_admin(email) or escritorio_do_email(email)):
        return jsonify(erro="Entre com seu e-mail e senha.", entrar=True), 401
    proprio = escritorio_do_email(email)
    return jsonify(email=email, admin=eh_admin(email), escritorio=proprio["id"] if proprio else None)


@app.post("/sair")
def sair():
    if dados := ler_sessao():
        encerrar(dados["id"])
    return jsonify(ok=True)


@app.post("/esqueci")
def esqueci():
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    if email:
        pedir_nova_senha(email)
    return jsonify(ok=True)


@app.post("/nova-senha")
def nova_senha():
    """Cria a senha com o código do link do e-mail (o e-mail aponta para cá: a página do próprio Firebase vinha
    sem a chave de API e dava 'The selected page mode is invalid')."""
    corpo = request.get_json(silent=True) or {}
    codigo, senha = str(corpo.get("codigo", "")), str(corpo.get("senha", ""))
    if not codigo or not senha:
        return jsonify(erro="Escreva a senha nova."), 400
    try:
        status, resposta = firebase("resetPassword", {"oobCode": codigo, "newPassword": senha})
    except Exception:
        return jsonify(erro="Não consegui falar com o serviço de login. Tente de novo em instantes."), 502
    if status == 200:
        return jsonify(ok=True)
    motivo = json.dumps(resposta)
    if "WEAK_PASSWORD" in motivo or "PASSWORD_DOES_NOT_MEET_REQUIREMENTS" in motivo:  # regra de senha do Firebase
        return jsonify(erro=REGRA_DA_SENHA), 400
    return jsonify(erro="O link venceu ou já foi usado. Na tela de entrar, clique em Esqueci a senha para receber outro."), 400


@app.route("/api/<path:resto>", methods=["GET", "POST"])
def api(resto):
    sessao = ler_sessao() or {}
    email = sessao.get("email")
    escritorio = escritorio_do_pedido(email)
    if not escritorio:
        if eh_admin(email):  # administrador sem escritório aberto (ou um que foi apagado do arquivo)
            return jsonify(erro="Escolha um escritório.", escolher=True), 409
        return jsonify(erro="Entre com seu e-mail e senha.", entrar=True), 401
    # computador esquecido aberto (ou chave de sessão copiada) não basta para levar a chave do código do PJe
    if resto.rstrip("/") == "autenticador/qr" and time.time() - sessao["desde"] > VALIDADE_QR:
        return jsonify(erro="Por segurança, o QR só aparece logo depois de entrar. Saia, entre de novo e abra o QR "
                            "em até 5 minutos.", reconfirmar=True), 403
    return repassar(escritorio["porta"])


# ---------- administrador do Autus ----------

def admin_da_sessao():
    """O e-mail do administrador deste pedido; para (401/403) se não for."""
    email = (ler_sessao() or {}).get("email")
    if not email:
        abort(Response(json.dumps({"erro": "Entre com seu e-mail e senha.", "entrar": True}), 401,
                       content_type="application/json"))
    if not eh_admin(email):
        abort(403)
    return email


def situacao_do_robo(porta):
    """parado, trabalhando, pausado (esperando alguém) ou fora do ar, pela consulta do próprio robô."""
    try:
        conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=2)
        conexao.request("GET", "/api/estado?desde=999999999")
        estado = json.loads(conexao.getresponse().read())
        conexao.close()
    except (OSError, ValueError):
        return "fora do ar"
    return "pausado" if estado.get("pausa") else "trabalhando" if estado.get("tarefa") else "parado"


@app.get("/admin/escritorios")
def admin_escritorios():
    admin_da_sessao()
    dados = ler_escritorios()
    lista = [{"id": id_, "nome": e["nome"], "emails": e["emails"], "robo": situacao_do_robo(e["porta"])}
             for id_, e in sorted(dados["escritorios"].items(), key=lambda par: par[1]["nome"].lower())]
    return jsonify(escritorios=lista, admins=dados.get("admins", []), google=bool(GOOGLE_CLIENT),
                   pedidos=sorted(({"email": e, **p} for e, p in ler_pedidos().items()), key=lambda p: p["quando"]))


@app.post("/admin/pedidos/liberar")
def admin_liberar_pedido():
    """Pedido de acesso (login com o Google) liberado num escritório: a pessoa entra de novo com o Google."""
    email_admin = admin_da_sessao()
    corpo = request.get_json(silent=True) or {}
    email, id_ = str(corpo.get("email", "")).strip().lower(), str(corpo.get("escritorio", ""))
    with trava_escritorios:
        if id_ not in ler_escritorios()["escritorios"]:
            return jsonify(erro="Escolha o escritório."), 400
        try:
            escritorio_novo.adicionar(ARQUIVO_ESCRITORIOS, id_, "", [email])
        except SystemExit as e:
            return jsonify(erro=str(e)), 400
    with trava_pedidos:
        pedidos = ler_pedidos()
        pedidos.pop(email, None)
        gravar_pedidos(pedidos)
    auditar(email_admin, "liberou pedido de acesso (Google)", escritorio=id_, emails=[email])
    return jsonify(ok=True)


@app.post("/admin/pedidos/recusar")
def admin_recusar_pedido():
    email_admin = admin_da_sessao()
    email = str((request.get_json(silent=True) or {}).get("email", "")).strip().lower()
    with trava_pedidos:
        pedidos = ler_pedidos()
        if pedidos.pop(email, None) is None:
            return jsonify(erro="Esse pedido não existe mais."), 404
        gravar_pedidos(pedidos)
    auditar(email_admin, "recusou pedido de acesso (Google)", emails=[email])
    return jsonify(ok=True)


@app.post("/admin/abrir")
def admin_abrir():
    """Administrador abre um escritório na página (fica no registro); depois a página manda X-Escritorio."""
    email = admin_da_sessao()
    id_ = str((request.get_json(silent=True) or {}).get("id", ""))
    if id_ not in ler_escritorios()["escritorios"]:
        return jsonify(erro="Esse escritório não existe."), 404
    auditar(email, "abriu o escritório", escritorio=id_)
    return jsonify(ok=True)


def mandar_links(emails):
    """Cria cada usuário no Firebase e manda o link de criar senha."""
    for e in emails:
        criar_usuario(e)
        pedir_nova_senha(e)


@app.post("/admin/escritorios")
def admin_criar_escritorio():
    """Escritório novo: entra no escritorios.json, o robô é ligado (ligar_escritorio.sh, com sudo) e cada e-mail
    recebe o link de criar senha."""
    email = admin_da_sessao()
    corpo = request.get_json(silent=True) or {}
    id_, nome = str(corpo.get("id", "")).strip().lower(), str(corpo.get("nome", ""))
    emails = [str(e) for e in corpo.get("emails", []) if str(e).strip()]
    with trava_escritorios:
        if id_ in ler_escritorios()["escritorios"]:
            return jsonify(erro=f"Já existe um escritório com o identificador '{id_}'."), 400
        try:
            porta = escritorio_novo.adicionar(ARQUIVO_ESCRITORIOS, id_, nome, emails)
        except SystemExit as e:
            return jsonify(erro=str(e)), 400
    try:
        subprocess.run(LIGAR_ESCRITORIO + [id_, str(porta)], check=True, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as e:
        detalhe = getattr(e, "stderr", "") or str(e)
        auditar(email, "criou o escritório (robô NÃO ligou)", escritorio=id_, erro=detalhe[-300:])
        return jsonify(erro=f"O escritório entrou na lista, mas o robô dele não ligou: {detalhe.strip()[-200:]}"), 502
    try:
        mandar_links(escritorio_novo.limpar_emails(emails))
    except (SystemExit, Exception) as e:
        auditar(email, "criou o escritório (links NÃO enviados)", escritorio=id_, erro=str(e)[-300:])
        return jsonify(erro=f"Escritório criado, mas os links de criar senha não foram: {e}"), 502
    auditar(email, "criou o escritório", escritorio=id_, nome=nome.strip(), emails=emails)
    return jsonify(ok=True)


@app.post("/admin/escritorios/<id_>/emails")
def admin_emails(id_):
    """{"adicionar": [...]} libera e-mails (com o link de criar senha); {"retirar": [...]} corta o acesso na hora."""
    email = admin_da_sessao()
    corpo = request.get_json(silent=True) or {}
    adicionar = [str(e) for e in corpo.get("adicionar", []) if str(e).strip()]
    retirar = [str(e) for e in corpo.get("retirar", []) if str(e).strip()]
    with trava_escritorios:
        if id_ not in ler_escritorios()["escritorios"]:
            return jsonify(erro="Esse escritório não existe."), 404
        try:
            if adicionar:
                escritorio_novo.adicionar(ARQUIVO_ESCRITORIOS, id_, "", adicionar)
            if retirar:
                escritorio_novo.retirar(ARQUIVO_ESCRITORIOS, id_, retirar)
        except SystemExit as e:
            return jsonify(erro=str(e)), 400
    if adicionar:
        try:
            mandar_links(escritorio_novo.limpar_emails(adicionar))
        except (SystemExit, Exception) as e:
            auditar(email, "liberou e-mails (links NÃO enviados)", escritorio=id_, emails=adicionar, erro=str(e)[-300:])
            return jsonify(erro=f"E-mails liberados, mas os links de criar senha não foram: {e}"), 502
        auditar(email, "liberou e-mails", escritorio=id_, emails=adicionar)
    if retirar:
        auditar(email, "retirou e-mails", escritorio=id_, emails=retirar)
    return jsonify(ok=True)


def erros_do_robo(porta, dias):
    """Os problemas recentes de um escritório (/api/erros do robô dele); None se o robô estiver fora do ar."""
    try:
        conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
        conexao.request("GET", f"/api/erros?dias={dias}")
        resposta = conexao.getresponse()
        dados = json.loads(resposta.read()) if resposta.status == 200 else None
        conexao.close()
        return dados["erros"] if dados else None
    except (OSError, ValueError, KeyError):
        return None


@app.get("/admin/erros")
def admin_erros():
    """Os problemas de todos os escritórios juntos (ou de um, ?escritorio=), do mais novo ao mais antigo, e os tipos
    que mais se repetem. Escritório com o robô fora do ar aparece em 'sem_resposta'."""
    admin_da_sessao()
    dias = request.args.get("dias", "7")
    dias = min(max(int(dias), 1), 90) if dias.isdigit() else 7
    so = request.args.get("escritorio", "")
    lista, sem_resposta = [], []
    for id_, e in ler_escritorios()["escritorios"].items():
        if so and id_ != so:
            continue
        erros = erros_do_robo(e["porta"], dias)
        if erros is None:
            sem_resposta.append(e["nome"])
            continue
        lista += [{**x, "escritorio": id_, "escritorio_nome": e["nome"]} for x in erros]
    lista.sort(key=lambda x: x["quando"], reverse=True)
    grupos = {}
    for x in lista:
        if x["desfecho"] == "resolveu sozinho":
            continue  # o ranking é do que precisou de alguém (ou parou)
        g = grupos.setdefault(x["grupo"], {"grupo": x["grupo"], "vezes": 0, "escritorios": set(), "ultimo": x})
        g["vezes"] += 1
        g["escritorios"].add(x["escritorio_nome"])
    ranking = sorted(({**g, "escritorios": sorted(g["escritorios"])} for g in grupos.values()), key=lambda g: -g["vezes"])
    return jsonify(erros=lista[:1000], ranking=ranking, sem_resposta=sem_resposta, dias=dias)


@app.get("/admin/registro")
def admin_registro():
    """As últimas 100 ações de administrador, da mais nova para a mais antiga."""
    admin_da_sessao()
    try:
        with open(ARQUIVO_AUDITORIA, encoding="utf-8") as f:
            linhas = f.readlines()[-100:]
    except FileNotFoundError:
        linhas = []
    return jsonify(registro=[json.loads(l) for l in reversed(linhas) if l.strip()])


def repassar(porta):
    """Repassa o pedido ao robô do escritório e devolve a resposta dele."""
    # o Flask já decodificou o caminho (espaço, acento, colchete do nome da pasta do cliente): codifica de novo
    caminho = quote(request.path, safe="/") + ("?" + request.query_string.decode("latin-1") if request.query_string else "")
    cabecalhos = {k: v for k in REPASSA_IDA if (v := request.headers.get(k))}
    conexao = http.client.HTTPConnection("127.0.0.1", porta, timeout=60)
    try:
        conexao.request(request.method, caminho, body=request.stream if request.method == "POST" else None,
                        headers=cabecalhos)
        resposta = conexao.getresponse()
        dados = resposta.read()  # ponytail: lê a resposta inteira na memória (fotos ~200 KB, mapa ~300 KB)
    except OSError:
        return jsonify(erro="O robô do escritório está reiniciando. Tente de novo em um minuto."), 502
    finally:
        conexao.close()
    return Response(dados, status=resposta.status,
                    headers={k: v for k in REPASSA_VOLTA if (v := resposta.getheader(k))})


if __name__ == "__main__":
    import waitress
    waitress.serve(app, host="127.0.0.1", port=PORTA, threads=16)
