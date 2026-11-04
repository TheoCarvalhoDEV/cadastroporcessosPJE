"""Testes da interface web sem PJe e sem mexer nos arquivos reais. Rodar: python test_app_web.py"""
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema"))  # o código
import tempfile
from unittest import mock
import docx
import app_web
import novo_processo as robo

GLOBAIS = ("DICIONARIO", "PADRAO", "CNPJ_CONHECIDO", "RACA_PADRAO", "PAUSA_SEGUNDOS", "PREFIXO_ANEXO", "TIPOS_ANEXO", "ROTULOS_DOCX")


def test_configuracao_e_correcao():
    tmp = tempfile.mkdtemp()
    raiz = os.path.join(tmp, "clientes")
    pasta = os.path.join(raiz, "Fulana")
    os.makedirs(pasta)
    doc = docx.Document()
    for linha in ("Nome do Autor: FULANA DE TAL", "CPF: 111.444.777-35", "Valor da Causa: R$ 1.000,00"):
        doc.add_paragraph(linha)
    doc.save(os.path.join(pasta, "Cadastro.docx"))
    open(os.path.join(pasta, "Fulana [Ação].pdf"), "wb").close()
    config = os.path.join(tmp, "config.json")
    with open(config, "w", encoding="utf-8") as f:
        json.dump({"cpf": "11144477735", "senha": "segredo", "totp_key": "ABC", "headless": False}, f)
    antes = {n: getattr(robo, n) for n in GLOBAIS}
    web = app_web.app.test_client()
    try:
        with mock.patch.object(robo, "ARQUIVO_ESCRITORIO", os.path.join(tmp, "escritorio.json")), \
             mock.patch.object(app_web, "CONFIG", config), mock.patch.object(app_web, "escritorio", {"pasta_clientes": raiz}), \
             mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
            cfg = web.get("/api/escritorio").get_json()
            assert cfg["pje"] == {"cpf": "11144477735", "tem_senha": True, "tem_totp": True}  # senha nunca vai para a página
            cfg.update(nome="Escritório Teste", cor="#106040", prefixo_anexo="Anexo", rotulos_docx={"nome do autor": "nome completo"})
            corpo = {k: v for k, v in cfg.items() if k not in ("campos", "pessoas", "pje")}
            r = web.post("/api/escritorio", json={**corpo, "pje": {"cpf": "111.444.777-00"}})
            assert r.status_code == 400 and "CPF" in r.get_json()["erro"]
            r = web.post("/api/escritorio", json={**corpo, "dicionario": [{"frases": [], "assunto": "10433"}], "pje": {}})
            assert r.status_code == 400 and "dicionário" in r.get_json()["erro"]
            corpo.update(dicionario=[{"frases": ["dano moral"], "assunto": "10433 - Dano Moral", "materia": "DIREITO CIVIL"}],
                         padrao={"jurisdição": "CUIABÁ CÍVEL"})
            r = web.post("/api/escritorio", json={**corpo, "pje": {"cpf": "", "senha": "", "totp_key": ""}})
            assert r.status_code == 200, r.get_json()
            with open(config, encoding="utf-8") as f:
                assert json.load(f) == {"cpf": "11144477735", "senha": "segredo", "totp_key": "ABC", "headless": False}
            assert robo.PREFIXO_ANEXO == "Anexo" and app_web.escritorio["nome"] == "Escritório Teste"
            assert robo.DICIONARIO[0]["assunto"] == "10433 - Dano Moral" and robo.PADRAO == {"jurisdição": "CUIABÁ CÍVEL"}

            # o modelo do escritório ("Nome do Autor") é lido; falta o resto: o cliente aparece com problema
            lista = web.get("/api/clientes").get_json()["clientes"]
            assert [c["nome"] for c in lista] == ["Fulana"] and lista[0]["situacao"] == "problema"
            ficha = web.get("/api/clientes/Fulana").get_json()
            assert {c["chave"]: c["atual"] for c in ficha["campos"]}["nome completo"] == "FULANA DE TAL"
            assert ficha["motivos"] == {}  # o PDF de teste é vazio: a ficha aparece mesmo assim, sem os motivos
            r = web.post("/api/clientes/Fulana/corrigir", json={"cpf": "111.444.777-00"})
            assert r.status_code == 400 and "CPF inválido" in r.get_json()["erro"]
            form = {c["chave"]: c["atual"] for c in ficha["campos"]} | {
                "matéria": "DIREITO CIVIL", "jurisdição": "CUIABÁ CÍVEL", "classe judicial": "PROCEDIMENTO COMUM CÍVEL (7)",
                "assunto principal": "10433", "tipo ativo": "AUTOR", "pessoa ativo": "FÍSICA", "tipo passivo": "RÉU",
                "pessoa passivo": "JURÍDICA", "entidade": "BANCO X", "cnpj": "11222333000181", "justiça gratuita": "SIM",
                "pedido de liminar ou antecipação de tutela": "NÃO", "segredo de justiça": "NÃO"}
            r = web.post("/api/clientes/Fulana/corrigir", json=form)
            assert r.status_code == 200, r.get_json()
            assert robo.ler_correcoes(pasta)["cnpj"] == "11.222.333/0001-81"  # formatado ao salvar
            assert web.get("/api/clientes").get_json()["clientes"][0]["situacao"] == "pronto"

            # nada de caminho vindo da página
            assert web.get("/api/clientes/..%2Fconfig.json").status_code == 404
            assert web.get("/api/foto/..%2F..%2Fconfig.json").status_code == 404
    finally:
        for n, v in antes.items():
            setattr(robo, n, v)


if __name__ == "__main__":
    test_configuracao_e_correcao()
    print("ok  test_configuracao_e_correcao")
