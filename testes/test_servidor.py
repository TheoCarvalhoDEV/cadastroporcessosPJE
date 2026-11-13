"""Testes dos scripts do servidor (limpeza de 30 dias e escritório novo), sem servidor. Rodar: python test_servidor.py"""
import json
import os
import sys
import tempfile
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor"))
os.environ.setdefault("ROBO_CHAVE_SESSAO", "teste")
os.environ.setdefault("FIREBASE_API_KEY", "x")
import limpar
import escritorio_novo


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
    assert os.path.isdir(os.path.join(raiz, "jvo/clientes/Concluído/Beltrano"))


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
    with open(arq, encoding="utf-8") as f:
        dados = json.load(f)
    assert dados["escritorios"]["jvo"]["emails"] == ["a@x.com", "c@x.com"]
    assert dados["escritorios"]["outro"]["emails"] == ["b@x.com"]
    assert "novo" not in dados["escritorios"]


def test_montar_nao_derruba_cadastro():
    """Rodar montar.sh de novo (trocar domínio, chave...) não pode matar um cadastro pausado; e o horário é o de Cuiabá."""
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor", "montar.sh"), encoding="utf-8") as f:
        montar = f.read()
    assert 'systemctl restart "robo@' not in montar  # reinício só pelo atualizar.sh, que espera o robô parar
    assert "servidor/atualizar.sh" in montar
    assert "set-timezone America/Cuiaba" in montar  # relatório, registro e fotos com a hora certa
    assert "override_rc" in montar  # needrestart não reinicia robo@ sozinho depois de atualização do Ubuntu


def test_uv_nao_le_configuracao_de_quem_rodou():
    """sudo mantém a pasta de quem rodou: o uv do usuário robo procurava uv.toml ali e parava (visto no servidor)."""
    pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor")
    for nome in ("montar.sh", "atualizar.sh"):
        with open(os.path.join(pasta, nome), encoding="utf-8") as f:
            linhas = [l for l in f.read().replace("\\\n", " ").splitlines() if "uv pip install" in l or "uv venv" in l]
        assert linhas and all("--no-config" in l for l in linhas), (nome, linhas)


def test_administrador_e_retirar_emails():
    arquivo = os.path.join(tempfile.mkdtemp(), "escritorios.json")
    escritorio_novo.adicionar(arquivo, "jvo", "JVO", ["a@jvo.adv.br", "b@jvo.adv.br"])
    escritorio_novo.retirar(arquivo, "jvo", ["A@jvo.adv.br"])
    assert escritorio_novo.adicionar_admin(arquivo, " Chefe@X.com ") == "chefe@x.com"
    escritorio_novo.adicionar_admin(arquivo, "chefe@x.com")  # de novo não repete
    dados = escritorio_novo.ler(arquivo)
    assert dados["escritorios"]["jvo"]["emails"] == ["b@jvo.adv.br"] and dados["admins"] == ["chefe@x.com"]
    escritorio_novo.adicionar(arquivo, "jvo", "", ["c@jvo.adv.br"])  # mais e-mails mantêm a lista de admins
    assert escritorio_novo.ler(arquivo)["admins"] == ["chefe@x.com"]
    for chamada in (lambda: escritorio_novo.adicionar_admin(arquivo, "semarroba"),
                    lambda: escritorio_novo.adicionar(arquivo, "novo", "", ["x@y.com"]),  # escritório novo sem nome
                    lambda: escritorio_novo.retirar(arquivo, "naoexiste", ["x@y.com"])):
        try:
            chamada()
            assert False
        except SystemExit:
            pass


def test_ligar_escritorio_confere_o_que_recebe():
    """O comando que roda como root (pelo sudo do porteiro) recusa identificador e porta estranhos antes de tudo, e o
    atualizar.sh instala uma cópia com dono root e a regra do sudo conferida pelo visudo."""
    import shutil
    import subprocess
    pasta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor")
    bash = shutil.which("bash")
    if bash:
        for args in (["../etc", "8801"], ["ok", "22"], ["ok", "8800"], ["ok", "8801; rm -rf /"], ["UPPER", "8801"], []):
            r = subprocess.run([bash, os.path.join(pasta, "ligar_escritorio.sh"), *args], capture_output=True, text=True)
            assert r.returncode == 2, (args, r.stdout, r.stderr)
    with open(os.path.join(pasta, "atualizar.sh"), encoding="utf-8") as f:
        atualizar = f.read()
    assert "install -o root -g root -m 755 $RAIZ/codigo/servidor/ligar_escritorio.sh /usr/local/sbin/autus-ligar-escritorio" in atualizar
    assert "visudo -cqf /etc/sudoers.d/autus.novo && mv" in atualizar


def test_publicar_pagina_no_firebase_hosting():
    """A página vai para o Firebase com o endereço do servidor, as regras de segurança e só os arquivos que mudaram;
    e o atualizar.sh publica ANTES de reiniciar o porteiro (que passa a mandar o endereço do servidor para lá)."""
    import gzip
    import publicar_pagina
    from unittest import mock
    api = "https://1-2-3-4.sslip.io"
    arquivos = publicar_pagina.arquivos(api)
    assert f'<meta name="api" content="{api}">' in arquivos["/index.html"].decode("utf-8")
    assert any(c.startswith("/fontes/") and c.endswith(".woff2") for c in arquivos)
    for pagina_publica in ("/privacidade.html", "/termos.html"):  # o Google pede as duas para publicar o app
        assert b"Theo Brandini Carvalho" in arquivos[pagina_publica] and b"theotheteo@gmail.com" in arquivos[pagina_publica]
    assert publicar_pagina.configuracao(api)["cleanUrls"] is True  # abrem como /privacidade e /termos
    cfg = publicar_pagina.configuracao(api)
    assert cfg["rewrites"] == [{"glob": "**", "path": "/index.html"}]  # /nova-senha do e-mail mostra a página
    politica = cfg["headers"][0]["headers"]["Content-Security-Policy"]
    assert f"connect-src 'self' {api}" in politica and "frame-ancestors 'none'" in politica
    chamadas = []

    def chamar(token, metodo, url, corpo=None, tipo="application/json"):
        chamadas.append((metodo, url, corpo))
        if url.endswith(":populateFiles"):  # o Firebase pede só o index.html (as fontes já estavam lá)
            pedidos = {h for c, h in corpo["files"].items() if c == "/index.html"}
            return {"uploadRequiredHashes": sorted(pedidos), "uploadUrl": "https://upload/x"}
        return {"name": "sites/autus/versions/v1"} if url == "sites/autus/versions" else {}
    with mock.patch.object(publicar_pagina, "chamar", chamar):
        assert publicar_pagina.publicar("autus", api, "TOKEN") == 1
    enviados = [c for c in chamadas if c[1].startswith("https://upload/x/")]
    assert len(enviados) == 1 and api.encode() in gzip.decompress(enviados[0][2])
    assert [c[:2] for c in chamadas[-2:]] == [("PATCH", "sites/autus/versions/v1?update_mask=status"),
                                              ("POST", "sites/autus/releases?versionName=sites/autus/versions/v1")]
    # com o login do Google: o ID vai para a página e a política de segurança libera só o botão oficial do Google
    pagina = publicar_pagina.arquivos(api, "123-abc.apps.googleusercontent.com")["/index.html"].decode("utf-8")
    assert '<meta name="google-client" content="123-abc.apps.googleusercontent.com">' in pagina
    com_google = publicar_pagina.configuracao(api, True)["headers"][0]["headers"]["Content-Security-Policy"]
    assert "script-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/client" in com_google
    assert "frame-src https://accounts.google.com/gsi/" in com_google and "frame-src 'none'" in politica
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor", "atualizar.sh"), encoding="utf-8") as f:
        atualizar = f.read()
    assert atualizar.index("publicar_pagina.py") < atualizar.index("systemctl restart porteiro")
    # depois do git pull, roda a versão nova de si mesmo (a velha não tinha a publicação: endereços em círculo)
    assert atualizar.index("pull --ff-only") < atualizar.index("exec bash") < atualizar.index("uv pip install")



def test_desligar_quando_ninguem_usa():
    """A máquina desliga depois de 30 min sem uso, mas nunca com robô cadastrando, pausado ou com a IA lendo."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import desligar_ocioso
    raiz, agora = tempfile.mkdtemp(), time.time()
    estado = {"tarefa": "", "pausa": None, "ia_lendo": ""}

    class Robo(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(estado).encode())
        def log_message(self, *a): pass
    robo = HTTPServer(("127.0.0.1", 0), Robo)
    threading.Thread(target=robo.serve_forever, daemon=True).start()
    os.makedirs(os.path.join(raiz, "escritorios", "jvo"))
    with open(os.path.join(raiz, "escritorios", "jvo", "robo.env"), "w") as f:
        f.write(f"ROBO_SERVIDOR=1\nROBO_PORTA={robo.server_port}\n")
    open(os.path.join(raiz, "ultimo_uso"), "w").close()
    desligar_ocioso.RAIZ = raiz
    os.utime(os.path.join(raiz, "ultimo_uso"), (agora - 600, agora - 600))
    assert not desligar_ocioso.deve_desligar()  # usado há 10 min
    os.utime(os.path.join(raiz, "ultimo_uso"), (agora - 3600, agora - 3600))
    assert desligar_ocioso.deve_desligar()  # 1 h sem uso e robô parado
    for chave, valor in (("tarefa", "cadastrar"), ("pausa", {"etapa": "Assuntos"}), ("ia_lendo", "Fulana")):
        estado[chave] = valor
        assert not desligar_ocioso.deve_desligar(), chave
        estado[chave] = "" if chave != "pausa" else None
    robo.shutdown()
    assert desligar_ocioso.deve_desligar()  # robô fora do ar não segura a máquina ligada
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor", "atualizar.sh"), encoding="utf-8") as f:
        assert "enable --now desligar.timer" in f.read()


def test_pagina_sabe_ligar_a_maquina():
    import publicar_pagina
    from unittest import mock
    api, ligar = "https://1-2-3-4.sslip.io", "https://autus-ligar-1.southamerica-east1.run.app"
    with mock.patch.object(publicar_pagina, "LIGAR", ligar):
        pagina = publicar_pagina.arquivos(api)["/index.html"].decode("utf-8")
        politica = publicar_pagina.configuracao(api)["headers"][0]["headers"]["Content-Security-Policy"]
    assert f'<meta name="ligar" content="{ligar}">' in pagina and f"connect-src 'self' {api} {ligar}" in politica


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
