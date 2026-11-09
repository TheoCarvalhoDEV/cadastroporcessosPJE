"""Testes do porteiro sem Firebase e sem servidor: um 'robô de escritório' falso responde numa porta local.
Rodar: python test_porteiro.py"""
import io
import json
import os
import socket
import sys
import tempfile
import threading
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


@falso.route("/api/clientes/<path:nome>")
def cliente(nome):
    return {"nome": nome, "query": request.query_string.decode()}


import logging
logging.getLogger("werkzeug").setLevel(logging.ERROR)  # sem o registro de cada pedido do robô falso
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
    assert "Set-Cookie" not in web.get("/api/eco").headers  # as 12 h contam do login (a página consulta a cada segundo)
    j = web.get("/api/eco?x=1").get_json()
    assert j == {"metodo": "GET", "query": "x=1", "tipo": "", "tamanho": 0, "cookie": ""}  # sessão não vai ao robô
    j = web.post("/api/eco", data={"arquivos": (io.BytesIO(b"%PDF-1"), "a.pdf")},
                 content_type="multipart/form-data").get_json()
    assert j["metodo"] == "POST" and j["tipo"].startswith("multipart/form-data") and j["tamanho"] > 6
    # nome de pasta de cliente com espaço, acento, colchete e % chega igual ao robô
    j = web.get("/api/clientes/Fulana%20de%20Tal%20%5BA%C3%A7%C3%A3o%5D%2050%25?x=%C3%A9").get_json()
    assert j == {"nome": "Fulana de Tal [Ação] 50%", "query": "x=%C3%A9"}, j
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


def test_criar_senha_pelo_link_do_email():
    """O link do e-mail do Firebase aponta para /nova-senha do próprio robô (a página do Firebase vinha sem a chave)."""
    web = porteiro.app.test_client()
    pagina = web.get("/nova-senha?mode=resetPassword&oobCode=abc&apiKey=&lang=pt-BR")
    assert pagina.status_code == 200 and b"<html" in pagina.data.lower()
    chamadas = []

    def firebase(acao, corpo, resposta=(200, {})):
        chamadas.append((acao, corpo))
        return resposta
    with mock.patch.object(porteiro, "firebase", firebase):
        assert web.post("/nova-senha", json={"codigo": "abc", "senha": "segredo1"}).status_code == 200
        assert chamadas == [("resetPassword", {"oobCode": "abc", "newPassword": "segredo1"})]
        assert web.post("/nova-senha", json={"codigo": "abc", "senha": ""}).status_code == 400
        assert web.post("/nova-senha", json={"codigo": "", "senha": "segredo1"}).status_code == 400
    for erro, trecho in (("EXPIRED_OOB_CODE", "venceu"), ("INVALID_OOB_CODE", "venceu"), ("WEAK_PASSWORD : x", "6")):
        with mock.patch.object(porteiro, "firebase", lambda a, c: (400, {"error": {"message": erro}})):
            r = web.post("/nova-senha", json={"codigo": "abc", "senha": "123"})
            assert r.status_code == 400 and trecho in r.get_json()["erro"], (erro, r.get_json())


def test_email_de_criar_senha_vai_com_a_conta_da_maquina():
    """Pedido sem a conta da máquina: o Firebase manda o link com 'apiKey=' vazio e a página dele dá 'The selected
    page mode is invalid' (visto em 2026-11-09). Com a conta da máquina, o link sai com a chave."""
    pedidos = []

    class Resposta:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, *a): return b"{}"

    def abrir(pedido, timeout=None):
        pedidos.append(pedido)
        return Resposta()
    with mock.patch.object(porteiro, "token_da_maquina", lambda: "TOKEN"), \
         mock.patch.object(porteiro.urllib.request, "urlopen", abrir):
        porteiro.pedir_nova_senha("a@x.com")
        porteiro.entrar_no_firebase("a@x.com", "s")
    assert pedidos[0].get_header("Authorization") == "Bearer TOKEN" and "sendOobCode" in pedidos[0].full_url
    assert pedidos[1].get_header("Authorization") is None  # o login da pessoa não usa a conta da máquina


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
