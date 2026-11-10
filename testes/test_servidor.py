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
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "servidor", "atualizar.sh"), encoding="utf-8") as f:
        atualizar = f.read()
    assert atualizar.index("publicar_pagina.py") < atualizar.index("systemctl restart porteiro")
    # depois do git pull, roda a versão nova de si mesmo (a velha não tinha a publicação: endereços em círculo)
    assert atualizar.index("pull --ff-only") < atualizar.index("exec bash") < atualizar.index("uv pip install")


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
