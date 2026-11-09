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


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
