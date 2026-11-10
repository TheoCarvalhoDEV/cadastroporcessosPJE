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
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse
from flask import Flask, Response, abort, jsonify, redirect, request, send_file, send_from_directory
from itsdangerous import BadSignature, URLSafeTimedSerializer

RAIZ = os.environ.get("ROBO_RAIZ", "/srv/robo")
ARQUIVO_ESCRITORIOS = os.path.join(RAIZ, "escritorios.json")
ARQUIVO_ENCERRADAS = os.path.join(RAIZ, "sessoes_encerradas.json")  # quem clicou em Sair: a chave não vale mais
API_KEY = os.environ["FIREBASE_API_KEY"]
PORTA = int(os.environ.get("ROBO_PORTA_PORTEIRO", 8800))
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema", "web")
SITE = os.environ.get("ROBO_SITE", "")  # o site do Firebase Hosting onde a página fica
PAGINA = f"https://{SITE}.web.app" if SITE else ""
ORIGENS = {PAGINA, f"https://{SITE}.firebaseapp.com"} if SITE else set()  # quem pode chamar o servidor de fora
VALIDADE_SESSAO = 12 * 3600  # 12 h desde o login
# a regra de senha fica no Firebase (Authentication > Settings > Password policy): mínimo 10 e pelo menos um número
REGRA_DA_SENHA = "A senha precisa de pelo menos 10 caracteres, com pelo menos um número."

REPASSA_IDA = ("Content-Type", "Content-Length", "If-None-Match", "Accept-Encoding")  # nunca a chave da sessão
REPASSA_VOLTA = ("Content-Type", "Content-Encoding", "ETag", "Cache-Control", "Vary")

app = Flask(__name__, static_folder=None)
app.config.update(MAX_CONTENT_LENGTH=210 * 1024 * 1024)  # o robô do escritório confere os 200 MB e dá a mensagem
assinador = URLSafeTimedSerializer(os.environ["ROBO_CHAVE_SESSAO"], salt="sessao")
trava_encerradas = threading.Lock()


# ---------- escritórios e Firebase ----------

def escritorio_do_email(email):
    """{"id", "nome", "porta"} do escritório do e-mail, ou None. Relê o arquivo a cada pedido: tirar um e-mail
    de lá corta o acesso na hora."""
    try:
        with open(ARQUIVO_ESCRITORIOS, encoding="utf-8") as f:
            escritorios = json.load(f)["escritorios"]
    except FileNotFoundError:
        return None
    email = (email or "").strip().lower()
    for id_, e in escritorios.items():
        if email in e["emails"]:
            return {"id": id_, "nome": e["nome"], "porta": e["porta"]}
    return None


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
    """O Firebase manda o e-mail de criar senha nova. Não diz se o e-mail existe."""
    try:
        firebase("sendOobCode", {"requestType": "PASSWORD_RESET", "email": email}, como_maquina=True)
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
    """{"email", "id"} da chave do cabeçalho Authorization, ou None (sem chave, alterada, vencida ou encerrada)."""
    chave = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not chave:
        return None
    try:
        dados = assinador.loads(chave, max_age=VALIDADE_SESSAO)
    except BadSignature:  # inclui SignatureExpired
        return None
    return None if dados.get("id") in encerradas() else dados


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
                                 "Access-Control-Allow-Headers": "Authorization, Content-Type",
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
    if not escritorio_do_email(email):
        return jsonify(erro="Este e-mail não está ligado a nenhum escritório."), 403
    return jsonify(ok=True, sessao=nova_sessao(email))


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
    escritorio = escritorio_do_email((ler_sessao() or {}).get("email"))
    if not escritorio:
        return jsonify(erro="Entre com seu e-mail e senha.", entrar=True), 401
    return repassar(escritorio["porta"])


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
