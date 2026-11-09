"""Porteiro do servidor: entrega a página, faz o login (Firebase Authentication pela API REST), guarda a sessão em
cookie e repassa /api/* ao robô do escritório da pessoa (um app_web.py por escritório, em 127.0.0.1).
O escritório sai SEMPRE da sessão + escritorios.json, nunca do pedido.
Ambiente: ROBO_RAIZ (/srv/robo), ROBO_CHAVE_SESSAO, FIREBASE_API_KEY, ROBO_PORTA_PORTEIRO (8800).
Uso (servidor/porteiro.service): python porteiro.py
"""
import datetime
import http.client
import json
import os
import secrets
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, session

RAIZ = os.environ.get("ROBO_RAIZ", "/srv/robo")
ARQUIVO_ESCRITORIOS = os.path.join(RAIZ, "escritorios.json")
API_KEY = os.environ["FIREBASE_API_KEY"]
PORTA = int(os.environ.get("ROBO_PORTA_PORTEIRO", 8800))
WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema", "web")

REPASSA_IDA = ("Content-Type", "Content-Length", "If-None-Match", "Accept-Encoding")  # nunca o Cookie da sessão
REPASSA_VOLTA = ("Content-Type", "Content-Encoding", "ETag", "Cache-Control", "Vary")

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=os.environ["ROBO_CHAVE_SESSAO"],
    SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=datetime.timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=False,  # 12 h desde o login (a página consulta a cada segundo e renovaria sempre)
    MAX_CONTENT_LENGTH=210 * 1024 * 1024)  # o robô do escritório confere os 200 MB e dá a mensagem


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


def firebase(acao, corpo):
    """Chama a API REST do Firebase Authentication. Devolve (status, resposta)."""
    pedido = urllib.request.Request(f"https://identitytoolkit.googleapis.com/v1/accounts:{acao}?key={API_KEY}",
                                    data=json.dumps(corpo).encode("utf-8"), headers={"Content-Type": "application/json"})
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
        firebase("sendOobCode", {"requestType": "PASSWORD_RESET", "email": email})
    except Exception:
        pass


def criar_usuario(email):
    """Cria o usuário com uma senha aleatória (a pessoa cria a dela pelo e-mail de pedir_nova_senha)."""
    status, resposta = firebase("signUp", {"email": email, "password": secrets.token_urlsafe(24)})
    if status != 200 and "EMAIL_EXISTS" not in json.dumps(resposta):
        raise SystemExit(f"Não consegui criar o usuário {email} no Firebase: {resposta}")


# ---------- rotas ----------

@app.before_request
def mesma_origem():
    """POST só da própria página (além do cookie SameSite=Strict)."""
    origem = request.headers.get("Origin")
    if request.method == "POST" and origem and urlparse(origem).netloc != request.host:
        abort(403)


@app.errorhandler(403)
def proibido(e):
    return jsonify(erro="Pedido recusado."), 403


@app.errorhandler(413)
def grande_demais(e):
    return jsonify(erro="Envio grande demais: o limite é 200 MB por vez. Envie menos pastas de cada vez."), 413


@app.get("/")
@app.get("/nova-senha")  # link do e-mail de criar senha (a página monta o formulário a partir do caminho)
def pagina():
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
    session.clear()
    session["email"] = email
    session.permanent = True
    return jsonify(ok=True)


@app.post("/sair")
def sair():
    session.clear()
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
    if "WEAK_PASSWORD" in motivo:
        return jsonify(erro="A senha precisa de pelo menos 6 caracteres."), 400
    return jsonify(erro="O link venceu ou já foi usado. Na tela de entrar, clique em Esqueci a senha para receber outro."), 400


@app.route("/api/<path:resto>", methods=["GET", "POST"])
def api(resto):
    escritorio = escritorio_do_email(session.get("email"))
    if not escritorio:
        session.clear()
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
