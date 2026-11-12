"""Testes da interface web sem PJe e sem mexer nos arquivos reais. Rodar: python test_app_web.py"""
import datetime
import json
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema"))  # o código
import tempfile
from unittest import mock
import docx
import app_web
import novo_processo as robo

robo.ARQUIVO_HISTORICO = os.path.join(tempfile.mkdtemp(), "historico.jsonl")  # o relatório de verdade não recebe testes

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
             mock.patch.object(robo, "ARQUIVO_CONFIG", config), mock.patch.object(app_web, "escritorio", {"pasta_clientes": raiz}), \
             mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
            cfg = web.get("/api/escritorio").get_json()
            assert cfg["pje"] == {"cpf": "11144477735", "tem_senha": True, "tem_totp": True}  # senha nunca vai para a página
            cfg.update(nome="Escritório Teste", cor="#106040", prefixo_anexo="Anexo", rotulos_docx={"nome do autor": "nome completo"})
            corpo = {k: v for k, v in cfg.items() if k not in ("campos", "pessoas", "pje")}
            r = web.post("/api/escritorio", json={**corpo, "pje": {"cpf": "111.444.777-00"}})
            assert r.status_code == 400 and "CPF" in r.get_json()["erro"]
            r = web.post("/api/escritorio", json={**corpo, "pje": {"totp_key": "ABC1 DEFG"}})  # '1' não é base32
            assert r.status_code == 400 and "I por 1" in r.get_json()["erro"]
            r = web.post("/api/escritorio", json={**corpo, "dicionario": [{"frases": [], "assunto": "10433"}], "pje": {}})
            assert r.status_code == 400 and "dicionário" in r.get_json()["erro"]
            corpo.update(dicionario=[{"frases": ["dano moral"], "assunto": "10433 - Dano Moral", "materia": "DIREITO CIVIL"}],
                         padrao={"jurisdição": "CUIABÁ CÍVEL"})
            r = web.post("/api/escritorio", json={**corpo, "pje": {"cpf": "", "senha": "", "totp_key": ""}})
            assert r.status_code == 200, r.get_json()
            with open(config, encoding="utf-8") as f:
                assert json.load(f) == {"cpf": "11144477735", "senha": "segredo", "totp_key": "ABC", "headless": False}
            r = web.post("/api/escritorio", json={**corpo, "logo": "data:image/svg+xml;base64,AAAA", "pje": {}})  # só PNG
            assert r.status_code == 400 and "logo" in r.get_json()["erro"]
            assert web.post("/api/escritorio", json={**corpo, "logo": "data:image/png;base64,iVBORw0KGgo=", "pje": {}}).status_code == 200
            assert web.get("/api/escritorio").get_json()["logo"] == "data:image/png;base64,iVBORw0KGgo="
            assert cfg["assistente_feito"] is False  # escritório que nunca passou pelo primeiro acesso
            assert web.post("/api/escritorio", json={**corpo, "assistente_feito": True, "pje": {}}).status_code == 200
            assert web.get("/api/escritorio").get_json()["assistente_feito"] is True
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
            r = web.post("/api/clientes/Fulana/corrigir", json={**form, "_mudados": ["cnpj", "../outra"]})
            assert r.status_code == 200, r.get_json()
            correcao = app_web.ler_historico()[-1]  # para o relatório: só os campos que a página diz que mudaram
            assert (correcao["tipo"], correcao["cliente"], correcao["campos"]) == ("correcao", "Fulana", ["cnpj"])
            assert robo.ler_correcoes(pasta)["cnpj"] == "11.222.333/0001-81"  # formatado ao salvar
            assert web.get("/api/clientes").get_json()["clientes"][0]["situacao"] == "pronto"

            # nada de caminho vindo da página
            assert web.get("/api/clientes/..%2Fconfig.json").status_code == 404
            assert web.get("/api/foto/..%2F..%2Fconfig.json").status_code == 404
    finally:
        for n, v in antes.items():
            setattr(robo, n, v)


def test_relatorio():
    ev = [
        {"tipo": "correcao", "quando": "2026-11-02T09:00:00", "cliente": "A", "campos": ["matéria", "jurisdição"]},
        {"tipo": "correcao", "quando": "2026-11-02T09:05:00", "cliente": "B", "campos": ["matéria"]},
        {"tipo": "sozinho", "quando": "2026-11-03T10:01:00", "cliente": "A", "etapa": "Assuntos",
         "problema": "TimeoutError: Timeout 30000ms exceeded.", "vezes": 1},
        {"tipo": "cadastro", "quando": "2026-11-03T10:06:00", "inicio": "2026-11-03T10:00:00", "cliente": "A",
         "segundos": 360, "resultado": "pronto", "msg": ""},
        {"tipo": "pausa", "quando": "2026-11-03T10:08:00", "cliente": "B", "etapa": "Partes - polo ativo",
         "problema": "O CPF 1 é de 'X', mas o documento diz 'B'. Parei sem vincular.", "foto": "b.png", "resposta": "pular"},
        {"tipo": "cadastro", "quando": "2026-11-03T10:09:00", "inicio": "2026-11-03T10:06:00", "cliente": "B",
         "segundos": 180, "resultado": "pulado", "msg": "O CPF 1 é de 'X'"},
        {"tipo": "pausa", "quando": "2026-11-03T10:12:00", "cliente": "C", "etapa": "Partes - polo ativo",
         "problema": "O CPF 2 é de 'Y', mas o documento diz 'C'. Parei sem vincular.", "foto": "c.png", "resposta": "continuar"},
        {"tipo": "cadastro", "quando": "2026-11-03T10:20:00", "inicio": "2026-11-03T10:09:00", "cliente": "C",
         "segundos": 660, "resultado": "pronto", "msg": ""},
        {"tipo": "cadastro", "quando": "2026-10-20T10:00:00", "inicio": "2026-10-20T09:55:00", "cliente": "D",
         "segundos": 300, "resultado": "erro", "msg": "O PJe recusou o login: senha"},
    ]
    hoje = datetime.date(2026, 11, 4)
    r = app_web.relatorio(ev, "mes", hoje)
    assert (r["prontos"], r["nao_terminaram"], r["sem_pausa"], r["minutos"], r["pausas"], r["sozinho"]) == (2, 1, 50, 8.5, 2, 1)
    ativo = next(e for e in r["etapas"] if e["etapa"] == "Partes - polo ativo")
    assert (ativo["pausas"], ativo["sozinho"]) == (2, 0)
    cpf = r["problemas"][0]  # o pulado não conta de novo como erro
    assert (cpf["tipo"], cpf["vezes"], cpf["clientes"], cpf["foto"]) == ("CPF não confere com a Receita", 2, ["C", "B"], "c.png")
    assert r["campos"][0] == {"campo": "Matéria", "vezes": 2}
    assert r["ultimos"][0]["cliente"] == "C" and r["ultimos"][1]["etapa"] == "Partes - polo ativo"
    passado = app_web.relatorio(ev, "mes_passado", hoje)
    assert passado["nao_terminaram"] == 1 and passado["problemas"][0]["tipo"] == "Login do PJe"
    assert app_web.relatorio(ev, "30", hoje)["eventos"] == 9 and app_web.relatorio(ev, "30", datetime.date(2026, 11, 30))["eventos"] == 8
    assert app_web.relatorio([], "tudo", hoje)["tem_historico"] is False

    for texto, tipo in [  # mensagens que o robô escreve de verdade
            ("Opção 'Juizado Especial de Fazenda Pública' não existe no PJe. Opções da lista: X", "Opção não existe no PJe"),
            ("Para esse tipo da parte, o PJe não oferece 'Jurídica'. Troque o tipo", "Opção não existe no PJe"),
            ("O robô não sabe a COMPETÊNCIA deste processo. Opções do PJe: X", "Competência não identificada"),
            ("TimeoutError: Tempo esgotado (30 s) esperando a tela do PJe.", "PJe lento ou fora do ar"),
            ("TargetClosedError: Target page, context or browser has been closed", "Chrome do robô fechado"),
            ("O PJe não achou o CEP 78000-000 de FULANA. Confira o CEP", "Endereço da parte"),
            ("O PJe recusou DOC 03].pdf: arquivo inválido", "Anexo recusado ou faltando"),
            ("Empresa pesquisada pelo CNPJ 11222333000181. Confira os dados", "Parte para escolher à mão"),
            ("Não consegui vincular FULANA: o PJe não mostrou mensagem", "Não vinculou a parte"),
            ("Não achei na inicial (nem no .docx): cpf. Preencha na ficha do cliente.", "Dados da pasta do cliente"),
            ("Não achei o número do rascunho (idProcesso) na página depois dos Dados iniciais.", "Rascunho não criado"),
            ("qualquer outra coisa", "Outros")]:
        assert app_web.tipo_do_problema(texto) == tipo, texto


def test_link_do_autenticador():
    """O robô abre o link colado no Chrome logado: só aceita https da Justiça, e só quando está esperando."""
    web = app_web.app.test_client()
    tmp = tempfile.mkdtemp()
    with mock.patch.object(robo, "ARQUIVO_CONFIG", os.path.join(tmp, "config.json")):
        assert web.post("/api/autenticador").status_code == 400  # sem CPF e senha salvos
        assert web.get("/api/autenticador/qr").status_code == 404  # sem chave ainda
        robo.gravar_config({"totp_key": "MFRGGZDFMZTWQ2LKGAYTEMZUGQ2TMNZY", "totp_qr": "data:image/png;base64,QR"})
        r = web.get("/api/autenticador/qr").get_json()  # o QR guardado, para outro celular ler sem derrubar ninguém
        assert r == {"qr": "data:image/png;base64,QR", "chave": "MFRG GZDF MZTW Q2LK GAYT EMZU GQ2T MNZY"}
    assert web.post("/api/autenticador/link", json={"link": "https://sso.cloud.pje.jus.br/x"}).status_code == 400  # não espera
    with mock.patch.dict(app_web.estado, {"autenticador": {"etapa": "link"}}):
        for ruim in ("http://sso.cloud.pje.jus.br/x", "https://pje.jus.br.golpe.com/x", "javascript:alert(1)", ""):
            assert web.post("/api/autenticador/link", json={"link": ruim}).status_code == 400, ruim
        assert web.post("/api/autenticador/link", json={"link": " https://sso.cloud.pje.jus.br/a?key=1 "}).status_code == 200
        assert app_web.links.get_nowait() == "https://sso.cloud.pje.jus.br/a?key=1"
        web.post("/api/parar")  # Cancelar solta o robô que espera o link
        assert app_web.links.get_nowait() == ""
    app_web.robo.parar = False


def test_testar_login_do_primeiro_acesso():
    """'Testar e continuar' do assistente: o robô entra no PJe uma vez; a página lê o resultado em /api/estado."""
    import time as t
    web = app_web.app.test_client()
    tmp = tempfile.mkdtemp()
    with mock.patch.object(robo, "ARQUIVO_CONFIG", os.path.join(tmp, "config.json")):
        assert web.post("/api/testar-login").status_code == 400  # sem CPF e senha
        robo.gravar_config({"cpf": "11144477735", "senha": "s"})
        for resultado, esperado in ((None, {"ok": True}), (SystemExit("O PJe recusou a senha."), {"ok": False, "msg": "O PJe recusou a senha."})):
            def abrir():
                if resultado:
                    raise resultado
            with mock.patch.object(app_web, "abrir_pje", abrir), mock.patch.object(app_web, "anotar_parada", lambda m: None):
                assert web.post("/api/testar-login").status_code == 200
                assert web.get("/api/estado").get_json()["teste_login"] in ({"rodando": True}, esperado)
                for _ in range(50):
                    if not app_web.estado["tarefa"]:
                        break
                    t.sleep(0.05)
                assert web.get("/api/estado").get_json()["teste_login"] == esperado
        with mock.patch.dict(app_web.estado, {"tarefa": "cadastrar"}):
            assert web.post("/api/testar-login").status_code == 409


def test_erros_para_o_administrador():
    """/api/erros: paradas, pausas, pulados e o que o robô resolveu sozinho, do mais novo ao mais antigo; cadastro
    pronto e Parar não entram. Parada fora de um cliente (login) vai para o histórico com a foto."""
    import datetime as dt
    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "erros"))
    open(os.path.join(tmp, "erros", "f1.html"), "w").close()
    agora = dt.datetime.now()
    q = lambda horas: (agora - dt.timedelta(hours=horas)).strftime("%Y-%m-%dT%H:%M:%S")
    eventos = [{"tipo": "cadastro", "quando": q(1), "cliente": "Ana [X]", "resultado": "pronto"},
               {"tipo": "cadastro", "quando": q(2), "cliente": "Bia", "resultado": "erro", "msg": "Tempo esgotado (30 s)"},
               {"tipo": "cadastro", "quando": q(3), "cliente": "Caio", "resultado": "pulado", "msg": "anexo recusado"},
               {"tipo": "cadastro", "quando": q(3), "cliente": "Dani", "resultado": "parado"},
               {"tipo": "pausa", "quando": q(4), "cliente": "Caio", "etapa": "Partes", "problema": "não consegui vincular",
                "foto": "f1.png", "resposta": "pular"},
               {"tipo": "sozinho", "quando": q(5), "cliente": "Eva", "etapa": "Assuntos", "problema": "net::ERR", "vezes": 1},
               {"tipo": "pausa", "quando": q(24 * 10), "cliente": "Velho", "problema": "antigo"}]
    with open(os.path.join(tmp, "historico.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(e) for e in eventos) + "\n")
    web = app_web.app.test_client()
    with mock.patch.multiple(robo, DADOS=tmp, ARQUIVO_HISTORICO=os.path.join(tmp, "historico.jsonl")):
        r = web.get("/api/erros?dias=7").get_json()["erros"]
        assert [(x["cliente"], x["desfecho"]) for x in r] == [("Bia", "parou com erro"), ("Caio", "pulado"),
                                                              ("Caio", "pausou"), ("Eva", "resolveu sozinho")]
        assert r[2]["foto"] == "f1.png" and r[2]["pagina"] == "f1.html" and r[2]["grupo"] == "Não vinculou a parte"
        assert r[2]["resposta"] == "pular" and r[0]["grupo"] == "PJe lento ou fora do ar"
        assert len(web.get("/api/erros?dias=30").get_json()["erros"]) == 5 and web.get("/api/erros?dias=x").status_code == 200
        assert web.get("/api/pagina-salva/f1.html").headers["Content-Disposition"].startswith("attachment")
        assert web.get("/api/pagina-salva/..%2Fhistorico.jsonl").status_code == 404
        # parada no login: entra no histórico (com a foto, se der); Parar não entra
        with mock.patch.object(robo, "guardar_tela", lambda n, e: os.path.join(tmp, "erros", "login.png")), \
             mock.patch.dict(app_web.estado, {"tarefa": "cadastro", "atual": ""}):
            app_web.anotar_parada("Não consegui abrir o Chrome/PJe (TargetClosedError)")
            robo.parar = True
            app_web.anotar_parada("parado pela pessoa")
            robo.parar = False
        r = web.get("/api/erros?dias=1").get_json()["erros"][0]
        assert r["desfecho"] == "parou com erro" and r["foto"] == "login.png" and r["etapa"] == "cadastro"
        assert r["grupo"] == "Chrome do robô não abriu"
        assert not any("parado pela pessoa" in x["problema"] for x in web.get("/api/erros").get_json()["erros"])


def test_ler_qr_do_autenticador():
    """A imagem do QR vira a chave para o campo (não grava nada) e o código de agora, para conferir no celular."""
    import io
    web = app_web.app.test_client()
    chave = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
    assert web.post("/api/autenticador/ler-qr").status_code == 400  # sem imagem
    with mock.patch.object(robo, "conta_do_pje_no_qr", lambda b: {"chave": chave, "emissor": "PDPJ", "nome": "Escritório"}), \
         mock.patch.object(robo, "gravar_config", lambda c: (_ for _ in ()).throw(AssertionError("não grava"))):
        r = web.post("/api/autenticador/ler-qr", data={"imagem": (io.BytesIO(b"png"), "qr.png")}).get_json()
    assert r["chave"] == chave and r["emissor"] == "PDPJ" and r["codigo"] == robo.pyotp.TOTP(chave).now()
    with mock.patch.object(robo, "conta_do_pje_no_qr", lambda b: (_ for _ in ()).throw(ValueError("Não achei um QR"))):
        r = web.post("/api/autenticador/ler-qr", data={"imagem": (io.BytesIO(b"x"), "x.jpg")})
    assert r.status_code == 400 and "Não achei" in r.get_json()["erro"]


# ---------- modo servidor ----------

import threading
import time


def pausar(resultado):
    """Põe o robô numa pausa (thread) e espera ela aparecer no estado."""
    t = threading.Thread(target=lambda: resultado.append(app_web.pedir_ajuda(2, "Assuntos", "problema", "")))
    t.start()
    while not app_web.estado["pausa"]:
        time.sleep(0.01)
    return t


def test_modo_servidor():
    tmp = tempfile.mkdtemp()
    gravacoes = []
    antes = {n: getattr(robo, n) for n in GLOBAIS}
    web = app_web.app.test_client()
    try:
        with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp), \
             mock.patch.object(robo, "ARQUIVO_ESCRITORIO", os.path.join(tmp, "escritorio.json")), \
             mock.patch.object(robo, "ler_config", lambda: {"cpf": "11144477735", "senha": "s"}), \
             mock.patch.object(robo, "gravar_config", gravacoes.append):
            cfg = web.get("/api/escritorio").get_json()
            assert cfg["servidor"] is True and cfg["pasta_clientes"] == os.path.join(tmp, "clientes")
            corpo = {k: v for k, v in cfg.items() if k not in ("campos", "pessoas", "pje", "servidor")}
            corpo.update(pasta_clientes="C:/outro escritorio", pje={"cpf": "", "senha": "", "totp_key": ""})
            assert web.post("/api/escritorio", json=corpo).status_code == 200
            assert app_web.raiz() == os.path.join(tmp, "clientes")  # no servidor a página não escolhe pasta
            assert gravacoes == []  # login não mudou: não cria versão nova do segredo
            assert web.post("/api/mapa/ler", json={}).status_code == 403
    finally:
        for n, v in antes.items():
            setattr(robo, n, v)
        app_web.escritorio = robo.carregar_escritorio()


def test_pausa_no_servidor_mostra_o_rascunho():
    tmp = tempfile.mkdtemp()
    pasta = os.path.join(tmp, "clientes", "Fulana")
    os.makedirs(pasta)
    web, resultado = app_web.app.test_client(), []
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        app_web.estado["atual"] = "Fulana"
        try:
            t = pausar(resultado)
            assert app_web.estado["pausa"]["rascunho"] == ""
            assert web.post("/api/responder", json={"resposta": "continuar"}).status_code == 400  # sem rascunho
            web.post("/api/responder", json={"resposta": "repetir"})
            t.join()
            robo.salvar_rascunho(pasta, "123")
            t = pausar(resultado)
            assert app_web.estado["pausa"]["rascunho"] == robo.URL_RASCUNHO.format("123")
            assert web.post("/api/responder", json={"resposta": "continuar"}).status_code == 200
            t.join()
            assert resultado == ["repetir", "continuar"]
        finally:
            app_web.estado["atual"] = ""


def test_concluido_conta_os_30_dias_da_mudanca():
    raiz = tempfile.mkdtemp()
    pasta = os.path.join(raiz, "Fulana")
    os.makedirs(pasta)
    os.utime(pasta, (0, 0))  # pasta enviada há muito tempo
    destino = app_web.mover_para(pasta, "Concluído")
    assert destino == os.path.join(raiz, "Concluído", "Fulana") and time.time() - os.path.getmtime(destino) < 60
    os.makedirs(pasta)
    assert app_web.mover_para(pasta, "Concluído") == os.path.join(raiz, "Concluído", "Fulana (2)")


import io


def enviar(web, arquivos):  # [(caminho relativo, bytes)]
    return web.post("/api/clientes/enviar", content_type="multipart/form-data",
                    data={"arquivos": [(io.BytesIO(b), os.path.basename(c.replace("\\", "/"))) for c, b in arquivos],
                          "caminhos": [c for c, _ in arquivos]})


def test_envio_de_pastas():
    tmp = tempfile.mkdtemp()
    raiz = os.path.join(tmp, "clientes")
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        r = enviar(web, [("Fulana/Fulana [Ação].pdf", b"%PDF"), ("Fulana/DOC 01] RG.pdf", b"%PDF"),
                         ("Lote/Beltrano/Cadastro.docx", b"PK"), ("Ciclano/INICIAL.PDF", b"%PDF"),
                         ("../fora.pdf", b"x"), ("Fulana/../../x.pdf", b"x"), ("solto.pdf", b"x"),
                         ("Fulana/foto.jpg", b"x"), ("Concluído/a.pdf", b"x"), ("A/B/C/d.pdf", b"x"),
                         ("Fulana/con:.pdf", b"x"), ("C:\\Windows\\a.pdf", b"x")])
        assert r.status_code == 200
        assert sorted(r.get_json()["clientes"]) == ["Beltrano", "Ciclano", "Fulana"]
        assert len(r.get_json()["ignorados"]) == 8
        assert sorted(os.listdir(tmp)) == ["clientes"]  # nada fora da pasta dos clientes
        assert sorted(os.listdir(raiz)) == ["Beltrano", "Ciclano", "Fulana"]
        assert sorted(os.listdir(os.path.join(raiz, "Fulana"))) == ["DOC 01] RG.pdf", "Fulana [Ação].pdf"]
        assert enviar(web, [("Fulana/Outra.pdf", b"%PDF")]).get_json()["clientes"] == ["Fulana (2)"]
        assert enviar(web, [("solto.pdf", b"x")]).status_code == 400
        limite = app_web.app.config["MAX_CONTENT_LENGTH"]
        app_web.app.config["MAX_CONTENT_LENGTH"] = 10
        try:
            r = enviar(web, [("Fulana/grande.pdf", b"x" * 100)])
            assert r.status_code == 413 and "200 MB" in r.get_json()["erro"]
        finally:
            app_web.app.config["MAX_CONTENT_LENGTH"] = limite
    assert enviar(web, [("Fulana/a.pdf", b"x")]).status_code == 403  # modo local: o escritório usa a pasta do computador


def test_envio_recusa_concluido_em_qualquer_nivel():
    """Pasta 'Concluído' arrastada junto não volta como cliente pendente (criaria rascunho repetido no PJe)."""
    tmp = tempfile.mkdtemp()
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        r = enviar(web, [("Fulana/Fulana.pdf", b"%PDF"), ("Concluído/Beltrano/b.pdf", b"%PDF"),
                         ("Concluído/Ciclano/c.pdf", b"%PDF"), ("Lote/Lixeira/d.pdf", b"%PDF")])  # 2ª: nome do Mac (NFD)
        assert r.get_json()["clientes"] == ["Fulana"] and len(r.get_json()["ignorados"]) == 3, r.get_json()


def test_envio_aceita_arquivos_do_robo():
    """rascunho.json e correcoes.json vêm junto (cliente que já tinha rascunho não ganha outro); JSON quebrado não."""
    tmp = tempfile.mkdtemp()
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        r = enviar(web, [("Fulana/Fulana.pdf", b"%PDF"), ("Fulana/rascunho.json", b'{"idProcesso": 123}'),
                         ("Fulana/correcoes.json", b"{quebrado"), ("Fulana/outro.json", b"{}")])
        assert sorted(os.listdir(os.path.join(tmp, "clientes", "Fulana"))) == ["Fulana.pdf", "rascunho.json"]
        assert r.get_json()["ignorados"] == ["Fulana/correcoes.json", "Fulana/outro.json"]
        assert robo.rascunho_salvo(os.path.join(tmp, "clientes", "Fulana")) == "123"


def test_envio_com_muitos_arquivos():
    """Várias pastas de uma vez (cada arquivo são 2 partes do formulário: o arquivo e o caminho)."""
    tmp = tempfile.mkdtemp()
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        r = enviar(web, [(f"Cliente {i // 10}/DOC {i % 10:02d}] a.pdf", b"%PDF") for i in range(600)])
        assert r.status_code == 200 and len(r.get_json()["clientes"]) == 60, r.status_code


def test_apagar_cliente():
    tmp = tempfile.mkdtemp()
    raiz = os.path.join(tmp, "clientes")
    os.makedirs(os.path.join(raiz, "Fulana"))
    open(os.path.join(raiz, "Fulana", "Fulana [Ação].pdf"), "wb").close()
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        app_web.estado["tarefa"] = "cadastrar"
        try:
            assert web.post("/api/clientes/Fulana/apagar").status_code == 409
        finally:
            app_web.estado["tarefa"] = ""
        r = web.post("/api/clientes/Fulana/apagar")
        assert r.status_code == 200 and r.get_json()["lixeira"] == "Fulana"
        assert os.listdir(os.path.join(raiz, "Lixeira")) == ["Fulana"] and "Fulana" not in os.listdir(raiz)
        # Desfazer: volta para a lista; nome que não está na Lixeira (ou caminho) não passa
        assert web.post("/api/lixeira/..%2Fx/restaurar").status_code == 404
        r = web.post("/api/lixeira/Fulana/restaurar")
        assert r.status_code == 200 and r.get_json()["cliente"] == "Fulana" and "Fulana" in os.listdir(raiz)
        assert web.post("/api/lixeira/Fulana/restaurar").status_code == 404
    assert web.post("/api/clientes/Fulana/apagar").status_code == 403
    assert web.post("/api/lixeira/Fulana/restaurar").status_code == 403


def test_texto_da_peticao():
    """O texto da inicial, página por página, para o painel ao lado da ficha; só de pasta de cliente da lista."""
    tmp = tempfile.mkdtemp()
    pasta = os.path.join(tmp, "clientes", "Fulana")
    os.makedirs(pasta)
    open(os.path.join(pasta, "Fulana [Ação].pdf"), "wb").close()
    open(os.path.join(pasta, "DOC 01] RG.pdf"), "wb").close()  # anexo não é a inicial
    paginas = [mock.Mock(extract_text=lambda: "EXCELENTÍSSIMO SENHOR JUIZ"), mock.Mock(extract_text=lambda: None)]
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp):
        with mock.patch.object(robo.PyPDF2, "PdfReader", lambda c: mock.Mock(pages=paginas) if c.endswith("[Ação].pdf") else 1 / 0):
            r = web.get("/api/clientes/Fulana/texto")
            assert r.status_code == 200 and r.get_json() == {"arquivo": "Fulana [Ação].pdf", "paginas": ["EXCELENTÍSSIMO SENHOR JUIZ", ""]}
        assert web.get("/api/clientes/Fulana/texto").get_json()["paginas"] == []  # PDF que não abre
        assert web.get("/api/clientes/..%2Fconfig.json/texto").status_code == 404  # nada de caminho vindo da página
        assert web.get("/api/clientes/Ninguém/texto").status_code == 404
        open(os.path.join(pasta, "Outra inicial.pdf"), "wb").close()  # pasta com problema: sem texto, com o motivo
        r = web.get("/api/clientes/Fulana/texto").get_json()
        assert r["paginas"] == [] and "1 PDF de inicial" in r["problema"]


def test_leitor_ia_le_as_pastas_pendentes():
    tmp = tempfile.mkdtemp()
    pasta = os.path.join(tmp, "clientes", "Fulana")
    os.makedirs(pasta)
    with open(os.path.join(pasta, "Fulana [Ação].pdf"), "wb") as f:
        f.write(b"%PDF-1.4 teste")
    chamadas = []
    web = app_web.app.test_client()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp), \
         mock.patch.object(robo, "ler_com_ia", lambda c: chamadas.append(c) or {"autor_nome": "FULANA DE TAL"}):
        lidas = app_web.estado["ia_lidas"]
        app_web.ler_pendentes_ia()
        assert len(chamadas) == 1 and robo.estado_ia(pasta) == "lida" and app_web.estado["ia_lidas"] == lidas + 1
        app_web.ler_pendentes_ia()
        assert len(chamadas) == 1  # já lida: não paga a leitura de novo
        assert web.get("/api/clientes").get_json()["clientes"][0]["ia"] == "lida"
        e = web.get("/api/estado").get_json()
        assert e["ia_lidas"] == lidas + 1 and e["ia_ultima"] == "Fulana" and e["ia_lendo"] == "" and e["ia_segundos"] == 0
        with mock.patch.object(robo, "identificar", lambda t, n: ({}, {})), mock.patch.object(robo, "texto_da_inicial", lambda c: ""):
            ia = web.get("/api/clientes/Fulana").get_json()["ia"]  # a ficha mostra o que a IA leu e quanto tempo levou
        assert ia["estado"] == "lida" and ia["campos"] == ["nome completo"] and isinstance(ia["segundos"], (int, float))
    os.remove(os.path.join(pasta, robo.ARQUIVO_LEITURA_IA))

    def falha(caminho):
        raise OSError("cota esgotada")
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "DADOS", tmp), \
         mock.patch.object(robo, "ler_com_ia", falha):
        app_web.ler_pendentes_ia()
        assert robo.estado_ia(pasta) == "erro"  # o cadastro segue com as regras
        parada = app_web.ler_historico()[-1]  # aparece em Erros, no painel do administrador
        assert parada["tipo"] == "parada" and parada["cliente"] == "Fulana"
        assert app_web.tipo_do_problema(parada["problema"]) == "Leitura pela IA"
        app_web.ler_pendentes_ia()  # falhou agora há pouco: não tenta de novo na mesma hora
        assert app_web.ler_historico()[-1] == parada
    with mock.patch.object(robo, "SERVIDOR", False), mock.patch.object(robo, "DADOS", tmp), \
         mock.patch.object(robo, "ler_com_ia", lambda c: 1 / 0):
        app_web.ler_pendentes_ia()  # modo local: não lê


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
