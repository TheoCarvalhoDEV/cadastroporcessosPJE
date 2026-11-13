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
os.environ.pop("ROBO_SITE", None)  # sem ROBO_SITE: o site padrão, https://autus.web.app
PAGINA = {"Origin": "https://autus.web.app"}
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
import porteiro

falso = Flask("escritorio_falso")


@falso.route("/api/eco", methods=["GET", "POST"])
def eco():
    return {"metodo": request.method, "query": request.query_string.decode(), "tipo": request.content_type or "",
            "tamanho": len(request.get_data()), "cookie": request.headers.get("Cookie", "")}


@falso.route("/api/autenticador/qr")
def qr():
    return {"qr": "data:image/png;base64,x", "chave": "ABCD EFGH"}


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


def chave(web, email):
    """O cabeçalho com a chave de sessão que o login devolve (a página guarda e manda em cada pedido)."""
    return {"Authorization": "Bearer " + entrar(web, email).get_json()["sessao"]}


def test_porteiro():
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]},
                       parado={"nome": "Parado", "porta": PORTA_PARADA, "emails": ["bia@x.com"]})
    web = porteiro.app.test_client()
    r = web.get("/api/estado")
    assert r.status_code == 401 and r.get_json()["entrar"] is True
    with mock.patch.object(porteiro, "entrar_no_firebase", lambda e, s: False):
        assert web.post("/entrar", json={"email": "ana@x.com", "senha": "errada"}).status_code == 401
    assert entrar(web, "carlos@x.com").status_code == 403  # senha certa, sem escritório
    ana = chave(web, "Ana@X.com")  # maiúsculas não importam
    assert "Set-Cookie" not in entrar(web, "ana@x.com").headers  # a sessão é a chave, não cookie
    j = web.get("/api/eco?x=1", headers=ana).get_json()
    assert j == {"metodo": "GET", "query": "x=1", "tipo": "", "tamanho": 0, "cookie": ""}  # sessão não vai ao robô
    j = web.post("/api/eco", data={"arquivos": (io.BytesIO(b"%PDF-1"), "a.pdf")},
                 content_type="multipart/form-data", headers=ana).get_json()
    assert j["metodo"] == "POST" and j["tipo"].startswith("multipart/form-data") and j["tamanho"] > 6
    # nome de pasta de cliente com espaço, acento, colchete e % chega igual ao robô
    j = web.get("/api/clientes/Fulana%20de%20Tal%20%5BA%C3%A7%C3%A3o%5D%2050%25?x=%C3%A9", headers=ana).get_json()
    assert j == {"nome": "Fulana de Tal [Ação] 50%", "query": "x=%C3%A9"}, j
    assert web.post("/api/eco", json={}, headers={**ana, "Origin": "https://mal.exemplo"}).status_code == 403
    assert web.post("/api/eco", json={}, headers={**ana, **PAGINA}).status_code == 200
    # chave alterada (outro e-mail colado no meio) ou vencida não vale
    falsa = ana["Authorization"][:-3] + ("AAA" if not ana["Authorization"].endswith("AAA") else "BBB")
    assert web.get("/api/eco", headers={"Authorization": falsa}).status_code == 401
    with mock.patch.object(porteiro, "VALIDADE_SESSAO", -1):
        assert web.get("/api/eco", headers=ana).status_code == 401
    gravar_escritorios(parado={"nome": "Parado", "porta": PORTA_PARADA, "emails": ["bia@x.com"]})
    assert web.get("/api/eco", headers=ana).status_code == 401  # tirar o e-mail corta o acesso na hora
    r = web.get("/api/estado", headers=chave(web, "bia@x.com"))
    assert r.status_code == 502 and "reiniciando" in r.get_json()["erro"]
    with mock.patch.object(porteiro, "pedir_nova_senha", lambda e: None):
        assert web.post("/esqueci", json={"email": "ninguem@x.com"}).status_code == 200
    assert web.get("/saude").status_code == 200


def test_sair_encerra_a_chave():
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]})
    web = porteiro.app.test_client()
    ana, outra = chave(web, "ana@x.com"), chave(web, "ana@x.com")  # dois navegadores da mesma pessoa
    assert web.post("/sair", headers=ana).status_code == 200
    assert web.get("/api/eco", headers=ana).status_code == 401  # a chave de quem saiu não vale mais
    assert web.get("/api/eco", headers=outra).status_code == 200  # o outro navegador continua
    assert web.post("/sair").status_code == 200  # sair sem chave não dá erro


def test_qr_do_autenticador_so_logo_depois_de_entrar():
    """A chave do código do PJe não sai para um computador esquecido aberto: só até 5 min depois do login."""
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]})
    web = porteiro.app.test_client()
    ana = chave(web, "ana@x.com")
    assert web.get("/api/autenticador/qr", headers=ana).get_json()["chave"] == "ABCD EFGH"
    with mock.patch.object(porteiro, "VALIDADE_QR", -1):  # "entrou faz tempo"
        for caminho in ("/api/autenticador/qr", "/api/autenticador/qr/", "/api/autenticador%2Fqr"):
            r = web.get(caminho, headers=ana)
            assert r.status_code == 403 and r.get_json()["reconfirmar"] is True, caminho
        assert web.get("/api/eco", headers=ana).status_code == 200  # o resto continua


def test_uso_para_a_maquina_desligar_sozinha():
    """Cada pedido da página conta como uso (desligar_ocioso.py lê ultimo_uso), menos a consulta automática do estado:
    aba esquecida aberta não segura a máquina ligada."""
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]})
    web = porteiro.app.test_client()
    ana = chave(web, "ana@x.com")
    os.utime(porteiro.ARQUIVO_USO, (1, 1))
    web.get("/api/estado", headers=ana)
    web.get("/saude")
    assert os.path.getmtime(porteiro.ARQUIVO_USO) == 1
    assert web.post("/ativo", headers=ana).status_code == 200  # a pessoa mexeu na página (só lendo a ficha)
    assert os.path.getmtime(porteiro.ARQUIVO_USO) > 1


def test_pagina_no_firebase_hosting():
    """Com ROBO_SITE, a página fica em https://<site>.web.app: só ela chama o servidor de fora (CORS), e quem chega
    pelo endereço do servidor vai para lá (com o caminho e o código do e-mail de criar senha)."""
    gravar_escritorios(teste={"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]})
    web = porteiro.app.test_client()
    r = web.get("/nova-senha?mode=resetPassword&oobCode=abc")
    assert r.status_code == 302 and r.headers["Location"] == "https://autus.web.app/nova-senha?mode=resetPassword&oobCode=abc"
    assert web.get("/").headers["Location"] == "https://autus.web.app/"
    r = web.options("/api/estado", headers={**PAGINA, "Access-Control-Request-Method": "GET",
                                             "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 204 and r.headers["Access-Control-Allow-Origin"] == "https://autus.web.app"
    assert "Authorization" in r.headers["Access-Control-Allow-Headers"]
    assert web.options("/api/estado", headers={"Origin": "https://mal.exemplo"}).status_code == 403
    r = web.post("/entrar", json={"email": "ana@x.com", "senha": "certa"}, headers=PAGINA)
    assert r.status_code != 403 and r.headers["Access-Control-Allow-Origin"] == "https://autus.web.app"
    ana = {**PAGINA, **chave(web, "ana@x.com")}
    r = web.post("/api/eco", json={}, headers=ana)
    assert r.status_code == 200 and r.headers["Access-Control-Allow-Origin"] == "https://autus.web.app"
    r = web.get("/api/eco", headers={"Origin": "https://mal.exemplo", "Authorization": ana["Authorization"]})
    assert "Access-Control-Allow-Origin" not in r.headers  # outro site não lê a resposta
    assert web.post("/entrar", json={}, headers={"Origin": "https://mal.exemplo"}).status_code == 403


def test_administrador():
    """O administrador entra sem escritório, abre qualquer um (X-Escritorio), cria escritório e libera/retira e-mails;
    quem não é administrador não consegue nada disso, nem trocando o cabeçalho. Tudo fica no registro."""
    porteiro.escritorio_novo.gravar(porteiro.ARQUIVO_ESCRITORIOS, {
        "escritorios": {"teste": {"nome": "Teste", "porta": servidor.server_port, "emails": ["ana@x.com"]},
                        "outro": {"nome": "Outro", "porta": PORTA_PARADA, "emails": ["bia@x.com"]}},
        "admins": ["chefe@x.com"]})
    if os.path.exists(porteiro.ARQUIVO_AUDITORIA):
        os.remove(porteiro.ARQUIVO_AUDITORIA)
    web = porteiro.app.test_client()
    chefe, ana = chave(web, "chefe@x.com"), chave(web, "ana@x.com")  # o administrador entra sem escritório
    assert web.get("/eu", headers=chefe).get_json() == {"email": "chefe@x.com", "admin": True, "escritorio": None}
    assert web.get("/eu", headers=ana).get_json()["admin"] is False
    r = web.get("/api/eco", headers=chefe)
    assert r.status_code == 409 and r.get_json()["escolher"] is True  # ainda não abriu um escritório
    assert web.get("/api/eco", headers={**chefe, "X-Escritorio": "teste"}).status_code == 200
    assert web.get("/api/eco", headers={**chefe, "X-Escritorio": "naoexiste"}).status_code == 409
    # quem não é administrador: o cabeçalho é ignorado (continua no próprio escritório) e /admin é recusado
    assert web.get("/api/eco", headers={**ana, "X-Escritorio": "outro"}).status_code == 200  # o "teste", que responde
    for metodo, url in (("get", "/admin/escritorios"), ("post", "/admin/abrir"), ("post", "/admin/escritorios"),
                        ("post", "/admin/escritorios/teste/emails"), ("get", "/admin/registro")):
        assert getattr(web, metodo)(url, json={}, headers=ana).status_code == 403, url
        assert getattr(web, metodo)(url, json={}).status_code == 401, url
    lista = web.get("/admin/escritorios", headers=chefe).get_json()
    assert [(e["id"], e["robo"]) for e in lista["escritorios"]] == [("outro", "fora do ar"), ("teste", "fora do ar")]
    assert web.post("/admin/abrir", json={"id": "teste"}, headers=chefe).status_code == 200
    # escritório novo: entra no arquivo, liga o robô (sudo) e manda os links
    ligados, links = [], []
    with mock.patch.object(porteiro.subprocess, "run", lambda cmd, **k: ligados.append(cmd)), \
         mock.patch.object(porteiro, "mandar_links", lambda emails: links.extend(emails)):
        r = web.post("/admin/escritorios", json={"id": "silva", "nome": "Silva & Souza", "emails": ["Caio@X.com"]}, headers=chefe)
        assert r.status_code == 200, r.get_json()
        assert web.post("/admin/escritorios", json={"id": "silva", "nome": "De novo"}, headers=chefe).status_code == 400
        assert web.post("/admin/escritorios", json={"id": "Ruim!", "nome": "X"}, headers=chefe).status_code == 400
        assert web.post("/admin/escritorios", json={"id": "novo2", "nome": "X", "emails": ["semarroba"]}, headers=chefe).status_code == 400
        assert web.post("/admin/escritorios", json={"id": "novo3", "nome": "X", "emails": ["ana@x.com"]}, headers=chefe).status_code == 400  # e-mail de outro
        r = web.post("/admin/escritorios/silva/emails", json={"adicionar": ["dani@x.com"], "retirar": ["caio@x.com"]}, headers=chefe)
        assert r.status_code == 200
    dados = porteiro.escritorio_novo.ler(porteiro.ARQUIVO_ESCRITORIOS)
    silva = dados["escritorios"]["silva"]
    assert silva["nome"] == "Silva & Souza" and silva["emails"] == ["dani@x.com"]
    assert silva["porta"] == max(servidor.server_port, PORTA_PARADA, 8800) + 1
    assert ligados == [porteiro.LIGAR_ESCRITORIO + ["silva", str(silva["porta"])]] and links == ["caio@x.com", "dani@x.com"]
    assert dados["admins"] == ["chefe@x.com"]  # a página não muda a lista de administradores
    # robô que não liga: avisa e fica no registro
    def falha(cmd, **k):
        raise porteiro.subprocess.CalledProcessError(1, cmd, stderr="systemctl falhou")
    with mock.patch.object(porteiro.subprocess, "run", falha):
        r = web.post("/admin/escritorios", json={"id": "quebra", "nome": "Quebra"}, headers=chefe)
    assert r.status_code == 502 and "systemctl falhou" in r.get_json()["erro"]
    acoes = [(l["acao"], l.get("escritorio")) for l in web.get("/admin/registro", headers=chefe).get_json()["registro"]]
    assert acoes[0] == ("criou o escritório (robô NÃO ligou)", "quebra")
    assert ("abriu o escritório", "teste") in acoes and ("criou o escritório", "silva") in acoes
    assert ("retirou e-mails", "silva") in acoes and ("entrou", None) in acoes


def test_erros_de_todos_os_escritorios():
    porteiro.escritorio_novo.gravar(porteiro.ARQUIVO_ESCRITORIOS, {"escritorios": {
        "a": {"nome": "Alfa", "porta": 1, "emails": []}, "b": {"nome": "Beta", "porta": 2, "emails": []},
        "c": {"nome": "Caído", "porta": 3, "emails": []}}, "admins": ["chefe@x.com"]})
    erros = {1: [{"quando": "2026-11-10T10:00:00", "desfecho": "pausou", "grupo": "Login do PJe", "problema": "login"},
                 {"quando": "2026-11-10T08:00:00", "desfecho": "resolveu sozinho", "grupo": "PJe lento", "problema": "net"}],
             2: [{"quando": "2026-11-10T09:00:00", "desfecho": "parou com erro", "grupo": "Login do PJe", "problema": "login 2"}],
             3: None}
    web = porteiro.app.test_client()
    chefe, ana = chave(web, "chefe@x.com"), None
    with mock.patch.object(porteiro, "erros_do_robo", lambda porta, dias: erros[porta]):
        r = web.get("/admin/erros?dias=30", headers=chefe).get_json()
        assert [(x["escritorio"], x["quando"][11:13]) for x in r["erros"]] == [("a", "10"), ("b", "09"), ("a", "08")]
        assert r["sem_resposta"] == ["Caído"] and r["dias"] == 30
        assert r["ranking"] == [{"grupo": "Login do PJe", "vezes": 2, "escritorios": ["Alfa", "Beta"], "ultimo": r["erros"][0]}]
        assert [x["escritorio"] for x in web.get("/admin/erros?escritorio=b", headers=chefe).get_json()["erros"]] == ["b"]
    porteiro.escritorio_novo.gravar(porteiro.ARQUIVO_ESCRITORIOS, {"escritorios": {
        "a": {"nome": "Alfa", "porta": 1, "emails": ["ana@x.com"]}}, "admins": []})
    assert web.get("/admin/erros", headers=chave(web, "ana@x.com")).status_code == 403


def test_login_com_o_google_e_pedidos_de_acesso():
    """Conta Google liberada entra; a que não está vira pedido de acesso, que o administrador libera ou recusa."""
    porteiro.escritorio_novo.gravar(porteiro.ARQUIVO_ESCRITORIOS, {"escritorios": {
        "alfa": {"nome": "Alfa", "porta": servidor.server_port, "emails": ["ana@x.com"]}}, "admins": ["chefe@x.com"]})
    if os.path.exists(porteiro.ARQUIVO_PEDIDOS):
        os.remove(porteiro.ARQUIVO_PEDIDOS)
    web = porteiro.app.test_client()
    contas = {"t-ana": {"email": "ana@x.com", "nome": "Ana"}, "t-chefe": {"email": "chefe@x.com", "nome": "Chefe"},
              "t-novo": {"email": "novo@gmail.com", "nome": "Novo Advogado"}}
    entrar_g = lambda token: web.post("/entrar-google", json={"credencial": token})
    with mock.patch.object(porteiro, "GOOGLE_CLIENT", ""):
        assert entrar_g("t-ana").status_code == 404  # sem o ID do cliente OAuth, o botão não existe
    with mock.patch.object(porteiro, "GOOGLE_CLIENT", "123-abc.apps.googleusercontent.com"), \
         mock.patch.object(porteiro, "conferir_google", lambda t: contas.get(t)):
        assert entrar_g("falso").status_code == 401 and entrar_g("").status_code == 401
        r = entrar_g("t-ana")
        assert r.status_code == 200 and web.get("/api/eco", headers={"Authorization": "Bearer " + r.get_json()["sessao"]}).status_code == 200
        assert entrar_g("t-chefe").status_code == 200  # administrador sem escritório também entra
        r = entrar_g("t-novo")
        assert r.status_code == 403 and r.get_json()["pedido"] is True and "administrador" in r.get_json()["erro"]
        entrar_g("t-novo")  # de novo: continua um pedido só
        chefe = chave(web, "chefe@x.com")
        lista = web.get("/admin/escritorios", headers=chefe).get_json()
        assert [(p["email"], p["nome"]) for p in lista["pedidos"]] == [("novo@gmail.com", "Novo Advogado")] and lista["google"] is True
        ana = chave(web, "ana@x.com")
        for url in ("/admin/pedidos/liberar", "/admin/pedidos/recusar"):
            assert web.post(url, json={"email": "novo@gmail.com", "escritorio": "alfa"}, headers=ana).status_code == 403
        assert web.post("/admin/pedidos/liberar", json={"email": "novo@gmail.com", "escritorio": "naoexiste"}, headers=chefe).status_code == 400
        assert web.post("/admin/pedidos/liberar", json={"email": "novo@gmail.com", "escritorio": "alfa"}, headers=chefe).status_code == 200
        assert "novo@gmail.com" in porteiro.escritorio_novo.ler(porteiro.ARQUIVO_ESCRITORIOS)["escritorios"]["alfa"]["emails"]
        assert web.get("/admin/escritorios", headers=chefe).get_json()["pedidos"] == []
        assert entrar_g("t-novo").status_code == 200  # liberado: agora entra
        contas["t-outro"] = {"email": "outro@gmail.com", "nome": ""}
        entrar_g("t-outro")
        assert web.post("/admin/pedidos/recusar", json={"email": "outro@gmail.com"}, headers=chefe).status_code == 200
        assert web.post("/admin/pedidos/recusar", json={"email": "outro@gmail.com"}, headers=chefe).status_code == 404
        with mock.patch.object(porteiro, "MAX_PEDIDOS", 0):
            contas["t-spam"] = {"email": "spam@gmail.com", "nome": ""}
            entrar_g("t-spam")
        assert porteiro.ler_json(porteiro.ARQUIVO_PEDIDOS) == {}  # a lista não cresce sem fim
    acoes = [l["acao"] for l in web.get("/admin/registro", headers=chave(web, "chefe@x.com")).get_json()["registro"]]
    assert "liberou pedido de acesso (Google)" in acoes and "recusou pedido de acesso (Google)" in acoes and "entrou (Google)" in acoes


def test_conferir_google_exige_email_confirmado():
    """O token do Google é conferido pela biblioteca do Google (assinatura, destinatário, validade) e o e-mail tem de
    estar confirmado pelo Google."""
    import types
    respostas = []
    oauth2 = types.ModuleType("google.oauth2")
    id_token = types.ModuleType("google.oauth2.id_token")
    def verificar(token, pedido, cliente):
        assert cliente == "123-abc.apps.googleusercontent.com"
        if respostas[0] is ValueError:
            raise ValueError("assinatura errada")
        return respostas[0]
    id_token.verify_oauth2_token = verificar
    oauth2.id_token = id_token
    transporte = types.ModuleType("google.auth.transport.requests")
    transporte.Request = lambda: None
    falsos = {"google": types.ModuleType("google"), "google.oauth2": oauth2, "google.oauth2.id_token": id_token,
              "google.auth": types.ModuleType("google.auth"), "google.auth.transport": types.ModuleType("google.auth.transport"),
              "google.auth.transport.requests": transporte}
    falsos["google.auth.transport"].requests = transporte
    falsos["google.auth"].transport = falsos["google.auth.transport"]
    falsos["google"].auth, falsos["google"].oauth2 = falsos["google.auth"], oauth2
    with mock.patch.dict(sys.modules, falsos), mock.patch.object(porteiro, "GOOGLE_CLIENT", "123-abc.apps.googleusercontent.com"):
        for resposta, esperado in (({"email": "Ana@X.com", "email_verified": True, "name": "Ana"}, {"email": "ana@x.com", "nome": "Ana"}),
                                   ({"email": "ana@x.com", "email_verified": False}, None), (ValueError, None)):
            respostas[:] = [resposta]
            assert porteiro.conferir_google("token") == esperado, resposta


def test_criar_senha_pelo_link_do_email():
    """O link do e-mail do Firebase aponta para /nova-senha (a página do Firebase vinha sem a chave): o porteiro manda
    para a página no Firebase Hosting e cria a senha com o código."""
    web = porteiro.app.test_client()
    pagina = web.get("/nova-senha?mode=resetPassword&oobCode=abc&apiKey=&lang=pt-BR")
    assert pagina.status_code == 302 and "oobCode=abc" in pagina.headers["Location"]
    chamadas = []

    def firebase(acao, corpo, resposta=(200, {})):
        chamadas.append((acao, corpo))
        return resposta
    with mock.patch.object(porteiro, "firebase", firebase):
        assert web.post("/nova-senha", json={"codigo": "abc", "senha": "segredo1"}).status_code == 200
        assert chamadas == [("resetPassword", {"oobCode": "abc", "newPassword": "segredo1"})]
        assert web.post("/nova-senha", json={"codigo": "abc", "senha": ""}).status_code == 400
        assert web.post("/nova-senha", json={"codigo": "", "senha": "segredo1"}).status_code == 400
    for erro, trecho in (("EXPIRED_OOB_CODE", "venceu"), ("INVALID_OOB_CODE", "venceu"), ("WEAK_PASSWORD : x", "10"),
                         ("PASSWORD_DOES_NOT_MEET_REQUIREMENTS : Missing password requirements: [numeric]", "número")):
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
        porteiro.criar_usuario("b@x.com")  # o cadastro aberto do Firebase é desligado: só a máquina cria conta
    assert pedidos[0].get_header("Authorization") == "Bearer TOKEN" and "sendOobCode" in pedidos[0].full_url
    assert pedidos[1].get_header("Authorization") is None  # o login da pessoa não usa a conta da máquina
    assert pedidos[2].get_header("Authorization") == "Bearer TOKEN" and "signUp" in pedidos[2].full_url
    assert json.loads(pedidos[0].data)["continueUrl"] == "https://autus.web.app/"  # Continuar leva ao Autus
    def sem_maquina():
        raise OSError("fora do Google")
    with mock.patch.object(porteiro, "token_da_maquina", sem_maquina), mock.patch.object(porteiro.urllib.request, "urlopen", abrir):
        porteiro.pedir_nova_senha("a@x.com")
    assert pedidos[3].get_header("Authorization") is None  # fora da máquina do Google: vai sem a conta dela
    senha = json.loads(pedidos[2].data)["password"]
    assert len(senha) >= 10 and any(c.isdigit() for c in senha)  # a senha provisória cumpre a regra de senha


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
