"""Testes sem PJe. Rodar: python test_novo_processo.py"""
import json
import os
import tempfile
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema"))  # o código
from unittest import mock
import docx
from playwright.sync_api import TimeoutError as TimeoutException
import novo_processo as robo

robo.ARQUIVO_HISTORICO = os.path.join(tempfile.mkdtemp(), "historico.jsonl")  # o relatório de verdade não recebe testes

# os testes não dependem da configuração deste computador (escritorio.json): valem o dicionário e o padrão da JVO
robo.DICIONARIO = [
    {"frases": ["fgts"], "assunto": "10158 - FGTS/Fundo de Garantia por Tempo de Serviço", "materia": "DIREITO PROCESSUAL CIVIL E DO TRABALHO"},
    {"frases": ["cumprimento de sentença", "fazenda"], "assunto": "9517 - Título Executivo Judicial / Liquidação / Cumprimento / Execução",
     "materia": "DIREITO PROCESSUAL CIVIL E DO TRABALHO"}]
robo.PADRAO = {}

OBRIGATORIOS = {c: "X" for c in robo.CAMPOS_OBRIGATORIOS if not c.startswith("pessoa ")} | {
    "cpf": "111.444.777-35", "valor da causa": "1.000,00", "entidade": "ESTADO DE MATO GROSSO"}


def pasta_de_cliente(campos):
    """Pasta temporária com um .docx (tabela Campo|Valor) e uma inicial (o PDF não é lido: dados_da_inicial é trocado)."""
    pasta = tempfile.mkdtemp()
    doc = docx.Document()
    tabela = doc.add_table(rows=0, cols=2)
    for campo, valor in campos.items():
        linha = tabela.add_row().cells
        linha[0].text, linha[1].text = campo, valor
    doc.save(os.path.join(pasta, "Cadastro de Ação Judicial - Fulana - Classe.docx"))
    open(os.path.join(pasta, "Fulana [Ação].pdf"), "wb").close()
    return pasta


def test_erro_no_valor():
    assert robo.erro_no_valor("cpf", "111.444.777-35") == ""
    assert robo.erro_no_valor("cpf", "111.444.777-3")
    assert robo.erro_no_valor("cnpj", "") == ""
    assert robo.erro_no_valor("cnpj", "03.507.415/0001-4")
    assert robo.erro_no_valor("valor da causa", "9.493,48") == ""
    assert robo.erro_no_valor("valor da causa", "9493")      # sem centavos: viraria 94,93
    assert robo.erro_no_valor("valor da causa", "0,00")
    assert robo.erro_no_valor("matéria", "qualquer") == ""


def test_logado_em_qualquer_pagina_do_pje():
    pagina = lambda ids=(), links=(): mock.Mock(query_selector=lambda s: (
        1 if any(f"'{i}'" in s for i in ids) or (s == "a[href*='logout.seam']" and links) else None))
    assert robo.logado(pagina(ids=("tabConsultaProcesso_lbl",)))   # painel
    assert robo.logado(pagina(links=True))                          # Quadro de avisos (20/10/2026)
    assert not robo.logado(pagina(ids=("username",)))               # tela de login


def test_opcao_parecida():
    pje = "DIREITO ADMINISTRATIVO E OUTRAS MATÉRIAS DE DIREITO PÚBLICO"
    assert robo.parecido("DIREITO ADMINISTRATIVO OUTRAS MATÉRIAS DIREITO PÚBLICO") == robo.parecido(pje)
    assert robo.parecido("Direito Administrativo e Outras Materias de Direito Publico") == robo.parecido(pje)
    assert robo.parecido("DIREITO PROCESSUAL CIVIL E DO TRABALHO") != robo.parecido(pje)


def test_endereco_da_inicial():
    texto = ("residente e domiciliado na Rua Santa Maria [Quadra nº 15] , nº 6 05, Bairro Marajoara , Cáceres /Mato "
             "Grosso, Código Endereçame nto Postal [CEP]: 782 05-515, por seu patrono")
    endereco = robo.dados_do_texto(texto, "x.pdf")["endereço"]
    assert endereco == "Rua Santa Maria [Quadra nº 15], nº 6 05, Bairro Marajoara, Cáceres/Mato Grosso, CEP 78205-515"
    assert robo.partes_do_endereco(endereco) == {"cep": "78205-515", "logradouro": "Rua Santa Maria",
        "bairro": "Marajoara", "numero": "605", "complemento": "Quadra nº 15", "cidade": "Cáceres"}
    rural = robo.partes_do_endereco("Zona Rural [Lote 263], Mirassol D’Oeste/Mato Grosso, CEP 78280-000")
    assert (rural["logradouro"], rural["bairro"], rural["numero"]) == ("Zona Rural", "Zona Rural", "S/N")
    assert robo.partes_do_endereco("Rua X, s/nº, Centro, Indiavaí/MT, CEP 78295000")["numero"] == "S/N"
    assert robo.partes_do_endereco("sem CEP nenhum") is None


def test_correcao_vale_mais_que_docx_e_inicial():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {"valor da causa": "9.493,48"}):
        try:
            robo.preparar(pasta)
            assert False, "devia parar: .docx e inicial divergem"
        except SystemExit as e:
            assert "diverge" in str(e)
        robo.salvar_correcoes(pasta, {"valor da causa": "9.493,48"})
        cliente = robo.preparar(pasta)
    assert cliente["dados"]["valor da causa"] == "9.493,48"
    assert cliente["origem"]["valor da causa"] == "corrigido"
    assert cliente["origem"]["cpf"] == ".docx"


def test_correcao_invalida_para():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    robo.salvar_correcoes(pasta, {"cpf": "123"})
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        try:
            robo.preparar(pasta)
            assert False, "devia parar: CPF corrigido inválido"
        except SystemExit as e:
            assert "cpf" in str(e)


def test_salvar_correcoes_vazio_apaga():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    robo.salvar_correcoes(pasta, {"cpf": "111.444.777-35"})
    assert os.path.exists(os.path.join(pasta, robo.ARQUIVO_CORRECOES))
    robo.salvar_correcoes(pasta, {})
    assert not os.path.exists(os.path.join(pasta, robo.ARQUIVO_CORRECOES))


def test_correcoes_corrompidas():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    with open(os.path.join(pasta, robo.ARQUIVO_CORRECOES), "w", encoding="utf-8") as f:
        f.write("{ isso não é json")
    try:
        robo.ler_correcoes(pasta)
        assert False, "devia parar com mensagem"
    except SystemExit as e:
        assert robo.ARQUIVO_CORRECOES in str(e)


def test_sem_cnpj_para_antes_do_pje():
    pasta = pasta_de_cliente(OBRIGATORIOS | {"entidade": "MUNICÍPIO DE CUIABÁ"})
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        try:
            robo.preparar(pasta)
            assert False, "devia parar: executado sem CNPJ"
        except SystemExit as e:
            assert "CNPJ" in str(e)


def test_valores_para_corrigir():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    robo.salvar_correcoes(pasta, {"matéria": "Y"})
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {"valor da causa": "9.493,48", "cnpj": None}):
        valores = robo.valores_para_corrigir(pasta)
    assert valores["valor da causa"] == ("1.000,00", "9.493,48", "")
    assert valores["matéria"] == ("X", "", "Y")
    assert valores["cnpj"] == ("", "", "")


# ---------- inicial sem .docx e documento x tipo de pessoa ----------

TEXTO_INICIAL = ("EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DE DIREITO DA VARA ESPECIALIZADA DA FAZENDA PÚBLICA DA COMARCA DE "
                 "CUIABÁ ESTADO DE MATO GROSSO FULANA DE TAL SOUZA, brasileira, inscrita no Cadastro Pessoas Físicas [CPF] "
                 "sob o n° 529.982.247 -25, ... AÇÃO COLETIVA EM FASE DE CUMPRIMENTO DE SENTENÇA ... em desfavor do "
                 "ESTADO DE MATO GROSSO, pessoa jurídica ... exequente ... justiça gratuita ... Do Valor da Causa ... "
                 "R$ 8.333,31")


def test_regras_tiram_da_inicial_o_que_o_docx_dava():
    d = robo.dados_do_texto(TEXTO_INICIAL, "Fulana de Tal Souza [Ação Execução].pdf")
    assert d["jurisdição"] == "CUIABÁ CÍVEL"
    assert d["classe judicial"].endswith("(12078)") and d["assunto principal"].startswith("9517")
    assert d["matéria"] == "DIREITO PROCESSUAL CIVIL E DO TRABALHO"
    assert (d["cpf"], d["entidade"], d["valor da causa"]) == ("529.982.247-25", "ESTADO DE MATO GROSSO", "8.333,31")
    assert (d["tipo ativo"], d["tipo passivo"]) == ("EXEQUENTE", "EXECUTADO")
    outra = robo.dados_do_texto("COMARCA DE SINOP ESTADO DE MATO GROSSO ação de cobrança", "X [Ação].pdf")
    assert outra["jurisdição"] is None and outra["classe judicial"] is None  # não chuta: fica para escolher


def test_regra_fgts_juizado():
    texto = ("EXCELENTÍSSIMA SENHORA DOUTORA JUÍZA DE DIREITO DO NÚCLEO DE JUSTIÇA DIGITAL DOS JUIZADOS ESPECIAIS DO "
             "ESTADO DE MATO GROSSO CICRANO DA SILVA PEREIRA , brasileiro, ... [CPF] sob o nº 390.533.447 -05, ... "
             "propor a presente AÇÃO DE COBRANÇA – FUNDO DE GARANTI A DO TEMPO DE SERVIÇO [FGTS] em desfavor de "
             "ESTADO DE MATO GROSSO , pessoa jurídica ... Juizado Especial da Fazenda Pública ... REQUERENTE ... "
             "Do Valor da Causa Dá-se à presente o valor de R$ 1.621,00")
    d = robo.dados_do_texto(texto, "Cicrano Silva Pereira [Ação Cobrança Indenizatória Substitutiva [FGTS].pdf")
    assert d["nome completo"] == "CICRANO DA SILVA PEREIRA"  # da qualificação, não do nome do arquivo (sem o "DA")
    assert robo.comparavel("nome completo", "BELTR ANO RODRIGUES DOS SANTOS") == robo.comparavel("nome completo", "Beltrano Rodrigues dos Santos")
    assert d["jurisdição"] == "NÚCLEO DE JUSTIÇA DIGITAL DOS JUIZADOS ESPECIAIS"
    assert d["classe judicial"] == "PROCEDIMENTO DO JUIZADO ESPECIAL DA FAZENDA PÚBLICA (14695)"
    assert d["assunto principal"].startswith("10158")
    assert (d["tipo ativo"], d["tipo passivo"]) == ("REQUERENTE", "REQUERIDO")
    # Juizado da Fazenda de comarca: vai para os JUIZADOS da comarca (não para a vara cível), mesmo com o PDF
    # quebrando a palavra ("C UIABÁ"); comarca fora da tabela fica pendente
    juizado = lambda comarca: robo.dados_do_texto(texto.replace(
        "NÚCLEO DE JUSTIÇA DIGITAL DOS JUIZADOS ESPECIAIS DO", f"JUIZADO ESPECIAL DA FAZENDA PÚBLICA DA COMARCA DE {comarca}"),
        "X [Ação].pdf")["jurisdição"]
    assert juizado("C UIABÁ") == "CUIABÁ JUIZADOS"
    assert juizado("CÁCERES") == "CÁCERES JUIZADOS"
    assert juizado("VÁRZEA GRANDE") == "VÁRZEA GRANDE JUIZADOS"
    assert juizado("SINOP") == "SINOP JUIZADOS"  # qualquer comarca do PJe, sem tabela
    assert juizado("GOTHAM") is None  # comarca que o PJe não tem: fica para escolher


def test_identifica_processos_de_outros_tipos():
    """Jurisdição, competência e classe saem do endereçamento, da ação e do réu, entre as opções lidas do PJe."""
    casos = [
        ("EXCELENTÍSSIMO SENHOR JUIZ DE DIREITO DO JUIZADO ESPECIAL CÍVEL DA COMARCA DE SINOP ESTADO DE MATO GROSSO "
         "MARIA DA SILVA, brasileira, inscrita no CPF sob o nº 111.444.777-35, vem propor a presente AÇÃO DE INDENIZAÇÃO "
         "POR DANOS MORAIS em face de BANCO EXEMPLO S.A., pessoa jurídica de direito privado, inscrita no CNPJ sob o nº "
         "11.222.333/0001-81. Dá-se à causa o valor de R$ 10.000,00.",
         ("SINOP JUIZADOS", "Juizado Especial Cível", "PROCEDIMENTO DO JUIZADO ESPECIAL CÍVEL (436)", "BANCO EXEMPLO S.A.",
          "11.222.333/0001-81", "111.444.777-35", "10.000,00")),
        ("EXCELENTÍSSIMO SENHOR DOUTOR JUIZ DE DIREITO DA 2ª VARA CÍVEL DA COMARCA DE RONDONÓPOLIS - MT JOÃO PEREIRA, "
         "brasileiro, CPF 111.444.777-35, vem ajuizar AÇÃO DE COBRANÇA em desfavor de EMPRESA X LTDA, CNPJ 11.222.333/0001-81",
         ("RONDONÓPOLIS", "Cível de Feitos Gerais", "PROCEDIMENTO COMUM CÍVEL (7)", "EMPRESA X LTDA", "11.222.333/0001-81",
          "111.444.777-35", None)),
        ("EXCELENTÍSSIMO SENHOR JUIZ DE DIREITO DA VARA ESPECIALIZADA DE FAMÍLIA E SUCESSÕES DA COMARCA DE CUIABÁ ANA LIMA, "
         "brasileira, vem propor AÇÃO DE ALIMENTOS em face de PEDRO LIMA, brasileiro, CPF 111.444.777-35",
         ("CUIABÁ CÍVEL", "Família e Sucessões", "ALIMENTOS - LEI ESPECIAL Nº 5.478/68 (69)", "PEDRO LIMA",
          "111.444.777-35", None, None)),  # o CPF depois do réu é do réu, não da autora
        ("EXCELENTÍSSIMA SENHORA JUÍZA DE DIREITO DO NÚCLEO DE JUSTIÇA DIGITAL DOS JUIZADOS ESPECIAIS DO ESTADO DE MATO "
         "GROSSO CARLA SOUZA, brasileira, vem propor AÇÃO DE COBRANÇA em desfavor do MUNICÍPIO DE SINOP, pessoa jurídica "
         "de direito público, CNPJ 11.222.333/0001-81",
         ("NÚCLEO DE JUSTIÇA DIGITAL DOS JUIZADOS ESPECIAIS", "Juizado Especial de Fazenda Pública",
          "PROCEDIMENTO DO JUIZADO ESPECIAL DA FAZENDA PÚBLICA (14695)", "MUNICÍPIO DE SINOP", "11.222.333/0001-81", None, None)),
    ]
    campos = ("jurisdição", "competência", "classe judicial", "entidade", "cnpj", "cpf", "valor da causa")
    for texto, esperado in casos:
        d, motivos = robo.identificar(texto, "X.pdf")
        assert tuple(d[c] for c in campos) == esperado, (tuple(d[c] for c in campos), esperado)
        assert "endereçamento" in motivos["jurisdição"]
    d, _ = robo.identificar(casos[2][0], "X.pdf")
    assert d["matéria"] == "DIREITO CIVIL > FAMíLIA"  # palpite pela competência (sem dicionário); o PJe escreve "FAMíLIA"
    assert robo.identificar(casos[0][0], "X.pdf")[0]["tipo ativo"] == "REQUERENTE"  # juizado: pela classe


def test_pasta_sem_docx_funciona_so_com_a_inicial():
    raiz = tempfile.mkdtemp()
    pasta = os.path.join(raiz, "Fulana")
    os.makedirs(pasta)
    open(os.path.join(pasta, "Fulana de Tal Souza [Ação Execução].pdf"), "wb").close()
    open(os.path.join(pasta, "DOC 01] Documentos.pdf"), "wb").close()
    with mock.patch.object(robo, "dados_da_inicial", lambda c: robo.dados_do_texto(TEXTO_INICIAL, os.path.basename(c))):
        cliente = robo.preparar(pasta)
    assert cliente["dados"]["classe judicial"].endswith("(12078)")
    assert cliente["origem"]["classe judicial"] == "regra" and cliente["origem"]["cpf"] == "inicial"
    assert len(cliente["anexos"]) == 1
    assert robo.pastas_de_clientes(raiz) == [pasta]  # aparece na lista mesmo sem .docx


def test_personalizacao_do_escritorio():
    """escritorio.json (gravado pela interface web) troca dicionário, padrão, prefixo/tipo dos anexos e rótulos do .docx."""
    nomes = ("DICIONARIO", "PADRAO", "CNPJ_CONHECIDO", "RACA_PADRAO", "PAUSA_SEGUNDOS", "PREFIXO_ANEXO", "TIPOS_ANEXO", "ROTULOS_DOCX")
    antes = {n: getattr(robo, n) for n in nomes}
    pasta = tempfile.mkdtemp()
    arquivo = os.path.join(pasta, "escritorio.json")
    with open(arquivo, "w", encoding="utf-8") as f:
        robo.json.dump({"dicionario": [{"frases": ["dano moral"], "assunto": "10433 - Indenização por Dano Moral",
                                        "materia": "DIREITO CIVIL > RESPONSABILIDADE CIVIL"}],
                        "padrao": {"jurisdição": "SINOP", "competência": ""},
                        "prefixo_anexo": "Anexo", "anexo_primeiro": "Procuração ou substabelecimento",
                        "rotulos_docx": {"Nome do Autor": "nome completo"}}, f)
    try:
        with mock.patch.object(robo, "ARQUIVO_ESCRITORIO", arquivo):
            robo.carregar_escritorio()
        d, motivos = robo.identificar("EXCELENTÍSSIMO JUIZ DA VARA ÚNICA ... indenização por DANO MORAL em face de X, ...", "X.pdf")
        assert d["assunto principal"].startswith("10433") and d["matéria"] == "DIREITO CIVIL > RESPONSABILIDADE CIVIL"
        assert motivos["matéria"] == 'dicionário: "dano moral"'
        assert d["jurisdição"] == "SINOP" and motivos["jurisdição"] == "padrão do escritório"  # o endereçamento não diz a comarca
        assert d["competência"] == "Cível de Feitos Gerais"  # escolhida dentro da jurisdição padrão
        # configuração antiga (tipos de ação) continua valendo como dicionário
        with open(arquivo, "w", encoding="utf-8") as f:
            robo.json.dump({"acoes": [{"frases": ["fgts"], "assunto": "10158", "materia": "M", "classe": "X"}]}, f)
        with mock.patch.object(robo, "ARQUIVO_ESCRITORIO", arquivo):
            robo.carregar_escritorio()
        assert robo.DICIONARIO == [{"frases": ["fgts"], "assunto": "10158", "materia": "M"}]
        assert robo.numero_do_anexo("ANEXO 03 - holerites.pdf") == 3 and robo.numero_do_anexo("DOC 01].pdf") is None
        assert robo.tipo_do_anexo(0) == "Procuração ou substabelecimento" and robo.tipo_do_anexo(1) == "Documento de comprovação"
        doc = docx.Document()
        doc.add_paragraph("Nome do Autor: FULANA DE TAL")
        doc.save(os.path.join(pasta, "c.docx"))
        assert robo.ler_docx(os.path.join(pasta, "c.docx"))["nome completo"] == "FULANA DE TAL"
    finally:
        for n, v in antes.items():
            setattr(robo, n, v)


def test_docx_vale_mais_que_a_regra():
    pasta = pasta_de_cliente(OBRIGATORIOS | {"jurisdição": "VÁRZEA GRANDE CÍVEL"})
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {"jurisdição": "CUIABÁ CÍVEL"}):
        assert robo.preparar(pasta)["dados"]["jurisdição"] == "VÁRZEA GRANDE CÍVEL"  # regra não trava o cadastro


def test_digitos_verificadores():
    assert robo.cpf_valido("52998224725") and not robo.cpf_valido("52998224726") and not robo.cpf_valido("11111111111")
    assert robo.cnpj_valido("03507415000144") and not robo.cnpj_valido("03507415000145")


def test_documento_combina_com_o_tipo_de_pessoa():
    assert robo.erro_documento("FÍSICA", "529.982.247-25") == ""
    assert "CPF" in robo.erro_documento("FÍSICA", "03.507.415/0001-44")       # CNPJ em pessoa física
    assert "CNPJ" in robo.erro_documento("JURÍDICA", "529.982.247-25")        # CPF em pessoa jurídica
    assert "inválido" in robo.erro_documento("ÓRGÃO PÚBLICO", "03.507.415/0001-45")
    assert robo.erro_documento("MINISTÉRIO PÚBLICO", "") == ""
    assert robo.erro_documento("ENTE OU AUTORIDADE", "") == ""


def test_preparar_recusa_cpf_em_pessoa_juridica():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    robo.salvar_correcoes(pasta, {"pessoa ativo": "JURÍDICA"})  # ativo tem CPF, mas marcaram jurídica
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        try:
            robo.preparar(pasta)
            assert False, "devia parar: CPF em pessoa jurídica"
        except SystemExit as e:
            assert "CNPJ" in str(e)


def test_formatacao():
    assert robo.formatar_valor("9493,48") == "9.493,48"
    assert robo.formatar_valor("9.493") == "9.493,00"
    assert robo.formatar_valor("R$ 9493,4") == "9.493,40"
    assert robo.formatar_valor("9493.48") == "9493.48"  # ambíguo: não mexe (o erro_no_valor avisa)
    assert robo.formatar_documento("52998224725") == "529.982.247-25"
    assert robo.formatar_documento("03507415000144") == "03.507.415/0001-44"


# ---------- tipo de pessoa das partes ----------

def test_identificar_pessoa():
    assert robo.identificar_pessoa("FULANA DE TAL SOUZA", "529.982.247-25") == "FÍSICA"
    assert robo.identificar_pessoa("ESTADO DE MATO GROSSO", "") == "ÓRGÃO PÚBLICO"
    assert robo.identificar_pessoa("MUNICÍPIO DE CUIABÁ", "03.533.064/0001-46") == "ÓRGÃO PÚBLICO"
    assert robo.identificar_pessoa("Ministério Público do Estado de Mato Grosso", "") == "MINISTÉRIO PÚBLICO"
    assert robo.identificar_pessoa("BANCO AGIBANK S.A", "10.664.513/0001-50") == "JURÍDICA"
    assert robo.identificar_pessoa("FULANO", "") == ""  # sem documento: fica pendente


def test_preparar_deduz_tipo_de_pessoa():
    pasta = pasta_de_cliente(OBRIGATORIOS)
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        cliente = robo.preparar(pasta)
    assert cliente["dados"]["pessoa ativo"] == "FÍSICA" and cliente["origem"]["pessoa ativo"] == "regra"
    assert cliente["dados"]["pessoa passivo"] == "ÓRGÃO PÚBLICO"


def test_pessoa_pendente_para_no_preparar():
    pasta = pasta_de_cliente(OBRIGATORIOS | {"entidade": "FULANO DE TAL"})  # sem CPF/CNPJ do passivo
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        try:
            robo.preparar(pasta)
            assert False, "devia parar: tipo de pessoa do passivo desconhecido"
        except SystemExit as e:
            assert "pessoa passivo" in str(e)
        robo.salvar_correcoes(pasta, {"pessoa passivo": "ENTE OU AUTORIDADE"})  # escolhido na tela
        cliente = robo.preparar(pasta)
    assert cliente["dados"]["pessoa passivo"] == "ENTE OU AUTORIDADE"


def test_ministerio_publico_nao_precisa_de_documento():
    pasta = pasta_de_cliente(OBRIGATORIOS | {"entidade": "MINISTÉRIO PÚBLICO DO ESTADO DE MATO GROSSO"})
    with mock.patch.object(robo, "dados_da_inicial", lambda c: {}):
        assert robo.preparar(pasta)["dados"]["pessoa passivo"] == "MINISTÉRIO PÚBLICO"


def test_erro_no_valor_pessoa():
    assert robo.erro_no_valor("pessoa ativo", "FÍSICA") == ""
    assert robo.erro_no_valor("pessoa ativo", "PESSOA")
    assert robo.erro_no_valor("cpf", "03.507.415/0001-44") == ""  # polo ativo pode ser empresa (CNPJ)


class ElementoFalso:
    def __init__(self, id_="", text=""):
        self.id_, self.text = id_, text

    def get_attribute(self, nome):
        return self.id_

    def inner_text(self):
        return self.text


class ModalFalso:
    """Modal 'Associar parte' com os rótulos de tipo de pessoa de um papel qualquer."""
    def __init__(self, rotulos):
        self.radios = [ElementoFalso(f"f:tipoPessoa:{i}") for i in range(len(rotulos))]
        self.rotulos = {r.id_: ElementoFalso(text=t) for r, t in zip(self.radios, rotulos)}

    def query_selector_all(self, seletor):
        return self.radios

    def query_selector(self, seletor):
        return next((self.rotulos[i] for i in self.rotulos if f"'{i}'" in seletor), None)


def test_escolher_pessoa_pelo_rotulo_e_nao_pela_posicao():
    cliques = []
    # com EXEQUENTE o PJe mostra só 3 opções: o MP fica na posição 2 (com EXECUTADO, fica na 3)
    modal = ModalFalso(["Física", "Jurídica", "Ministério Público do Estado de Mato Grosso"])
    with mock.patch.multiple(robo, clicar=lambda d, s: cliques.append(s)):
        robo.escolher_pessoa(modal, "MINISTÉRIO PÚBLICO")
        assert cliques == ["input[id='f:tipoPessoa:2']"]
        try:
            robo.escolher_pessoa(modal, "ENTE OU AUTORIDADE")
            assert False, "devia pausar: essa opção não existe para esse papel"
        except robo.Problema:
            pass


def rodar_parte(pessoa, nome, doc="", ja=False):
    """parte() com o PJe trocado por gravadores. Devolve (ações, Problema ou None)."""
    acoes = []
    grava = lambda tipo: (lambda d, *a: acoes.append((tipo,) + a))
    campo = mock.Mock()
    dados = {"tipo passivo": "EXECUTADO", "pessoa passivo": pessoa, "entidade": nome, "cnpj": doc}
    with mock.patch.multiple(robo, ja_vinculado=lambda d, g, n: ja, abrir_modal_parte=grava("modal"),
                             escolher_pessoa=grava("pessoa"), clicar=grava("clicar"), digitar=grava("digitar"),
                             vincular=grava("vincular"), visivel=lambda d, s: campo, saida=lambda l: None):
        try:
            robo.parte(None, dados, "P")
            return acoes, None
        except robo.Problema as e:
            return acoes, e


def test_parte_orgao_publico_pelo_cnpj():
    acoes, problema = rodar_parte("ÓRGÃO PÚBLICO", "ESTADO DE MATO GROSSO")
    assert problema is None
    assert ("digitar", "input[id$=':preCadastroPessoaFisica_cnpjPessoaJuridica']", "03507415000144") in acoes
    # confere na grade pelo CNPJ formatado, como o PJe mostra ("ESTADO DE MATO GROSSO - CNPJ: 03.507.415/0001-44")
    assert acoes[-1] == ("vincular", "input[id$=':btnInserirOrgPub']", "gridPartesPoloPassivoList", "03.507.415/0001-44")


def test_parte_ministerio_publico():
    acoes, problema = rodar_parte("MINISTÉRIO PÚBLICO", "MINISTÉRIO PÚBLICO DO ESTADO DE MATO GROSSO")
    assert problema is None and acoes[-1][:2] == ("vincular", "input[id$=':btnInserirOrgPub2']")


def test_parte_juridica_e_ente_pausam_depois_de_pesquisar():
    acoes, problema = rodar_parte("JURÍDICA", "BANCO AGIBANK S.A", "10.664.513/0001-50")
    assert ("digitar", "input[id$=':preCadastroPessoaFisica_nrCPJ']", "10664513000150") in acoes
    assert problema and "Continuar" in str(problema)
    acoes, problema = rodar_parte("ENTE OU AUTORIDADE", "SECRETARIA DE SAÚDE")
    assert ("clicar", "input[id$=':btnPesquisarEnte']") in acoes and problema


def test_parte_ja_vinculada_nao_abre_o_modal():
    acoes, problema = rodar_parte("ÓRGÃO PÚBLICO", "ESTADO DE MATO GROSSO", ja=True)
    assert acoes == [] and problema is None


# ---------- etapas com retomada (sem PJe: etapas e Chrome falsos) ----------

CLIENTE = {"pasta": r"C:\x\Fulana", "dados": {}, "inicial": "", "anexos": [], "origem": {}}
URL_COM_RASCUNHO = "https://pje.tjmt.jus.br/pje/Processo/update.seam?idProcesso=123"


class ChromeFalso:
    url = "https://pje.tjmt.jus.br/pje/Processo/cadastrar.seam"


def etapa(*roteiro, chamadas=None):
    """Etapa falsa: a cada chamada, segue o roteiro (exceção = falha, função = roda, nada = sucesso)."""
    roteiro = list(roteiro)

    def rodar(d, cliente, refazendo):
        if chamadas is not None:
            chamadas.append(refazendo)
        acao = roteiro.pop(0) if roteiro else None
        if isinstance(acao, BaseException):
            raise acao
        if acao:
            acao(d)
    return rodar


def cria_rascunho(d):
    d.url = URL_COM_RASCUNHO


def cria_e_cai(d):
    d.url = URL_COM_RASCUNHO
    raise TimeoutException("caiu depois de criar")


def rodar(etapas, respostas=()):
    """cadastrar() com etapas falsas. Devolve (pendências ou SystemExit, ids passados a recuperar, perguntas)."""
    chrome, recuperou, perguntas, respostas = ChromeFalso(), [], [], list(respostas)

    def ajuda(n, nome, problema, foto):
        perguntas.append((n, problema))
        return respostas.pop(0)
    with mock.patch.multiple(robo, ETAPAS=etapas, navegador=lambda: chrome, navegador_atual=chrome,
                             recuperar=recuperou.append, guardar_tela=lambda *a: "", pedir_ajuda=ajuda,
                             saida=lambda linha: None):
        try:
            resultado = robo.cadastrar(CLIENTE)
        except SystemExit as e:
            resultado = e
    return resultado, recuperou, perguntas


def seis(**trocas):
    """As 6 etapas falsas (a 1ª cria o rascunho); trocas={'e<n>': etapa} substitui a etapa n."""
    etapas = [("E1", etapa(cria_rascunho))] + [(f"E{n}", etapa()) for n in range(2, 7)]
    for n, f in trocas.items():
        etapas[int(n[1:]) - 1] = (f"E{n[1:]}", f)
    return etapas


def test_falha_tecnica_recupera_e_refaz():
    chamadas = []
    pend, recuperou, perguntas = rodar(seis(e2=etapa(TimeoutException("lento"), chamadas=chamadas)))
    assert isinstance(pend, list) and not perguntas
    assert recuperou == ["123"] and chamadas == [False, True]


def test_tres_falhas_pausa_e_continuar():
    pend, recuperou, perguntas = rodar(seis(e2=etapa(*[TimeoutException("x")] * 3)), respostas=["continuar"])
    assert [n for n, _ in perguntas] == [2]
    assert len(recuperou) == 3  # 2 automáticas + antes da etapa 3 (depois do "continuar")
    assert any("Etapa 2 (E2) feita à mão" in p for p in pend)


def test_problema_pausa_direto_e_repetir():
    chamadas = []
    pend, recuperou, perguntas = rodar(seis(e3=etapa(robo.Problema("sem endereço"), chamadas=chamadas)),
                                       respostas=["repetir"])
    assert perguntas == [(3, "sem endereço")]
    assert recuperou == ["123"] and chamadas == [False, True]
    assert isinstance(pend, list)


def test_pular_cliente():
    resultado, _, _ = rodar(seis(e4=etapa(robo.Problema("CPF de outra pessoa"))), respostas=["pular"])
    assert isinstance(resultado, SystemExit) and "CPF de outra pessoa" in str(resultado)


class PJeFalso:
    """Login do PJe (Keycloak) de mentira: tela de login -> código, ou página do QR (dispositivo novo pedido) -> painel."""
    SEGREDO = "abcdefghij0123456789"  # o campo escondido totpSecret: a chave é o base32 disso

    def __init__(self, depois_da_senha):
        self.tela, self.depois_da_senha, self.url, self.clicados, self.preenchido = "painel", depois_da_senha, "", [], {}

    def elementos(self):
        return {"login": ["#username", "#password", "#kc-login"],
                "codigo": ["#otp", "#kc-login", "a:Configurar novo dispositivo"],
                "pedido": [],
                "qr": ["#kc-totp-settings-form", "#totp", "#saveTOTPBtn", "input[name='totpSecret']", "img[alt*='Barcode']"],
                "painel": ["a[href*='logout.seam']"]}[self.tela]

    def query_selector_all(self, seletor):
        pedidos = [s.strip() for s in seletor.split(",")]
        return [ElementoDoLogin(self, n) for n in self.elementos()
                if n in pedidos or (n.startswith("a:") and "a" in pedidos) or (n == "img[alt*='Barcode']" and n in seletor)]

    def query_selector(self, seletor):
        return next(iter(self.query_selector_all(seletor)), None)

    def goto(self, url):
        self.url = url
        if "logout" in url:
            self.tela = "login"
        elif url == robo.URL_LOGIN:
            self.tela = "painel" if self.tela == "painel" else "login"
        elif "action-token" in url:
            self.tela = "qr"

    def clicar(self, nome):
        self.clicados.append(nome)
        if nome == "#kc-login" and self.tela == "login":
            self.tela = self.depois_da_senha
        elif nome.startswith("a:"):
            self.tela = "pedido"  # o PJe manda o e-mail
        elif nome == "#saveTOTPBtn":
            chave = robo.base64.b32encode(self.SEGREDO.encode()).decode()
            assert self.preenchido["#totp"] == robo.pyotp.TOTP(chave).now()
            self.tela = "painel"

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, *args):
        return True


class ElementoDoLogin:
    def __init__(self, pagina, nome):
        self.pagina, self.nome = pagina, nome

    def is_visible(self):
        return True

    def fill(self, valor):
        self.pagina.preenchido[self.nome] = valor

    def click(self, **k):
        self.pagina.clicar(self.nome)

    def inner_text(self):
        return self.nome[2:] if self.nome.startswith("a:") else ""

    def get_attribute(self, atributo):
        return {"value": PJeFalso.SEGREDO, "src": "data:image/png;base64,QR", "href": "/pje/logout.seam"}.get(atributo)


def configurar(pagina, link="https://sso.cloud.pje.jus.br/auth/realms/pje/login-actions/action-token?key=x"):
    """configurar_codigo() na página falsa. Devolve (resultado, guardado antes de concluir, links pedidos)."""
    guardado, pedidos = [], []

    def guardar(chave, qr):
        guardado.append((chave, qr, list(pagina.clicados)))

    def pedir_link(pediu):
        pedidos.append(pediu)
        return link
    with mock.patch.multiple(robo, ler_config=lambda: {"cpf": "111.444.777-35", "senha": "x"}, saida=lambda linha: None):
        return robo.configurar_codigo(pagina, pedir_link, guardar), guardado, pedidos


def test_chave_do_texto():
    assert robo.chave_do_texto(["Abra o aplicativo", "MFRG GZDF MZTW Q2LK GAYT EMZU GQ2T MNZY"]) == "MFRGGZDFMZTWQ2LKGAYTEMZUGQ2TMNZY"
    assert robo.chave_do_texto(["ABCD EFGH", "texto qualquer"]) is None


def test_configurar_codigo_direto_na_pagina_do_qr():
    """Pediram dispositivo novo: o PJe já mostra o QR depois da senha. Guarda a chave ANTES de concluir."""
    pagina = PJeFalso("qr")
    (chave, qr), guardado, pedidos = configurar(pagina)
    assert chave == robo.base64.b32encode(PJeFalso.SEGREDO.encode()).decode() and qr == "data:image/png;base64,QR"
    assert guardado == [(chave, qr, ["#kc-login"])] and not pedidos  # guardou antes do CADASTRAR; sem e-mail
    assert pagina.clicados[-1] == "#saveTOTPBtn" and pagina.tela == "painel"


def test_configurar_codigo_pede_dispositivo_novo_e_espera_o_link():
    pagina = PJeFalso("codigo")
    (chave, _), guardado, pedidos = configurar(pagina)
    assert pedidos == [True] and "a:Configurar novo dispositivo" in pagina.clicados and pagina.tela == "painel"
    assert guardado[0][0] == chave


def test_login_nao_conclui_dispositivo_novo_sozinho():
    """No meio do cadastro o robô NÃO cadastra autenticador (trocaria o código do celular do advogado): para e explica."""
    pagina = PJeFalso("qr")
    pagina.tela = "login"
    with mock.patch.multiple(robo, ler_config=lambda: {"cpf": "111.444.777-35", "senha": "x", "totp_key": "ABC"},
                             saida=lambda linha: None):
        try:
            robo.entrar(pagina)
            assert False, "devia parar"
        except SystemExit as e:
            assert "Configurar o código pelo robô" in str(e)
    assert "#saveTOTPBtn" not in pagina.clicados


def test_historico_do_relatorio():
    """O que vai para a tela Relatório: o engasgo que o robô resolveu sozinho e a pausa com a resposta."""
    open(robo.ARQUIVO_HISTORICO, "w").close()
    rodar(seis(e2=etapa(TimeoutException("lento"))))
    resultado, _, _ = rodar(seis(e4=etapa(robo.Problema("CPF de outra pessoa"))), respostas=["pular"])
    assert isinstance(resultado, robo.Pulado)
    with open(robo.ARQUIVO_HISTORICO, encoding="utf-8") as f:
        ev = [json.loads(linha) for linha in f]
    assert [(e["tipo"], e["etapa"], e["cliente"]) for e in ev] == [("sozinho", "E2", "Fulana"), ("pausa", "E4", "Fulana")]
    assert ev[0]["vezes"] == 1 and "lento" in ev[0]["problema"]
    assert ev[1]["resposta"] == "pular" and ev[1]["problema"] == "CPF de outra pessoa"


def test_etapa1_cai_depois_de_criar_o_rascunho_nao_cria_outro():
    chamadas = []
    pend, recuperou, perguntas = rodar(seis(e1=etapa(cria_e_cai, chamadas=chamadas)))
    assert chamadas == [False] and not perguntas  # não refez a etapa 1
    assert recuperou == ["123"]                    # a etapa 2 começou reabrindo o rascunho


def test_continua_o_rascunho_que_ja_existe():
    """Cadastro que parou (Pular, programa fechado): o próximo continua do rascunho salvo na pasta, sem criar outro,
    e toda etapa confere o que já existe (os documentos não sobem de novo)."""
    pasta = tempfile.mkdtemp()
    cliente, chrome, chamadas, refazendo = dict(CLIENTE, pasta=pasta), ChromeFalso(), [], []
    etapas = seis(e1=etapa(cria_rascunho, chamadas=chamadas), e6=lambda d, c, r: refazendo.append(r))
    with mock.patch.multiple(robo, ETAPAS=etapas, navegador=lambda: chrome, navegador_atual=chrome,
                             recuperar=lambda id_: None, guardar_tela=lambda *a: "", saida=lambda linha: None):
        robo.cadastrar(cliente)  # 1ª vez: cria o rascunho 123 e guarda na pasta
        assert robo.rascunho_salvo(pasta) == "123" and chamadas == [False] and refazendo == [False]
        robo.cadastrar(cliente)  # 2ª vez: não refaz a etapa 1; a etapa 6 confere o que já subiu
        assert chamadas == [False] and refazendo == [False, True]


def test_rascunho_salvo_que_nao_abre_mais_cria_outro():
    pasta = tempfile.mkdtemp()
    robo.salvar_rascunho(pasta, "999")
    chrome = ChromeFalso()

    def nao_abre(id_):
        chrome.url = "https://pje.tjmt.jus.br/pje/errorUnexpected.seam"
        raise TimeoutException("não abriu")
    with mock.patch.multiple(robo, ETAPAS=seis(), navegador=lambda: chrome, navegador_atual=chrome, recuperar=nao_abre,
                             guardar_tela=lambda *a: "", saida=lambda linha: None):
        robo.cadastrar(dict(CLIENTE, pasta=pasta))
    assert robo.rascunho_salvo(pasta) == "123"  # criou outro e guardou


def test_parar_nao_vira_falha():
    resultado, recuperou, perguntas = rodar(seis(e2=etapa(SystemExit("Parado pelo usuário."))))
    assert isinstance(resultado, SystemExit) and not recuperou and not perguntas


def test_navegador_reabre_quando_a_janela_foi_fechada():
    class Fechado:  # a janela do Chrome do robô foi fechada
        context = mock.Mock()

        def is_closed(self):
            return True
    novo = object()
    with mock.patch.multiple(robo, navegador_atual=Fechado(), abrir_navegador=lambda: novo,
                             entrar=lambda d: d, saida=lambda linha: None):
        assert robo.navegador() is novo


def test_limpar_chave():
    assert robo.limpar_chave("jbsw y3dp-ehpk 3pxp") == "JBSWY3DPEHPK3PXP"  # copiada da tela, em grupos de 4
    assert robo.limpar_chave("JBSW1Y3DP") == ""  # '1' não existe em base32 (era um I ou L)
    assert robo.limpar_chave("") == ""


def exportacao_do_authenticator(*contas):
    """Texto do QR de 'Transferir contas' do Google Authenticator com as contas (chave base32, nome, emissor)."""
    import base64 as b64
    def campo(n, valor):
        return bytes([n << 3 | 2, len(valor)]) + valor
    corpo = b"".join(campo(1, campo(1, b64.b32decode(k)) + campo(2, nome.encode()) + campo(3, emissor.encode())
                               + bytes([4 << 3, 1, 5 << 3, 1, 6 << 3, 2])) for k, nome, emissor in contas)
    from urllib.parse import quote
    return "otpauth-migration://offline?data=" + quote(b64.b64encode(corpo).decode())


def test_contas_do_texto_qr():
    pje, gmail = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert robo.contas_do_texto_qr(f"otpauth://totp/PDPJ:Escritorio?secret={pje.lower()}&issuer=PDPJ") == \
        [{"chave": pje, "emissor": "PDPJ", "nome": "Escritorio"}]
    contas = robo.contas_do_texto_qr(exportacao_do_authenticator((pje, "Escritório", "PDPJ"), (gmail, "a@b.com", "Google")))
    assert [(c["chave"], c["emissor"]) for c in contas] == [(pje, "PDPJ"), (gmail, "Google")]
    assert robo.contas_do_texto_qr("https://pje.tjmt.jus.br") == []


def test_conta_do_pje_no_qr():
    import io
    import zxingcpp
    from PIL import Image
    pje, gmail = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def imagem(texto):  # um QR de verdade, em JPG (como o print que chega pelo WhatsApp)
        m = memoryview(zxingcpp.create_barcode(texto, zxingcpp.BarcodeFormat.QRCode).to_image(scale=6))
        saida = io.BytesIO()
        Image.frombytes("L", (m.shape[1], m.shape[0]), m.tobytes()).save(saida, "JPEG", quality=70)
        return saida.getvalue()
    # duas contas: escolhe a do PJe pelo emissor
    assert robo.conta_do_pje_no_qr(imagem(exportacao_do_authenticator((gmail, "a@b.com", "Google"),
                                                                     (pje, "Escritório", "PDPJ"))))["chave"] == pje
    for img, msg in [(imagem("https://exemplo.com"), "não é de um app autenticador"), (b"nada", "abrir a imagem"),
                     (imagem(exportacao_do_authenticator((gmail, "a", "Google"), (pje, "b", "Banco"))), "2 contas")]:
        try:
            robo.conta_do_pje_no_qr(img)
            assert False, msg
        except ValueError as e:
            assert msg in str(e), e


def test_login_com_chave_invalida_explica():
    pagina = mock.Mock()
    with mock.patch.multiple(robo, ler_config=lambda: {"cpf": "1", "senha": "s", "totp_key": "ABC1 DEFG"},
                             logado=lambda d: False, cpf_e_senha=lambda d, c: "codigo", saida=lambda linha: None):
        try:
            robo.entrar(pagina)
            assert False, "devia ter parado"
        except SystemExit as e:
            assert "I por 1" in str(e)


def test_navegador_reaproveita_o_chrome_quando_o_login_parou():
    """Login parou com o Chrome aberto (perfil travado): a próxima tentativa usa o mesmo Chrome, não abre outro."""
    pagina = mock.Mock(is_closed=lambda: False)
    abertos = []

    def abrir():
        abertos.append(pagina)
        return pagina

    def entrar_falha(d):
        sys.exit("O PJe recusou o código do autenticador")
    with mock.patch.multiple(robo, navegador_atual=None, abrir_navegador=abrir, entrar=entrar_falha,
                             saida=lambda linha: None):
        try:
            robo.navegador()
            assert False, "devia ter parado no login"
        except SystemExit:
            pass
        with mock.patch.object(robo, "entrar", lambda d: d):
            assert robo.navegador() is pagina
    assert len(abertos) == 1


def test_documentos_parciais_continua_de_onde_parou():
    anexos = [f"DOC {n:02d}].pdf" for n in range(1, 16)]
    enviados = []
    contagem = iter([5, 16])  # antes: inicial + DOC 01..04; depois: todos
    pagina = mock.Mock(query_selector_all=lambda s: [mock.Mock()] * (3 if s == ".dz-preview" else 1))
    with mock.patch.multiple(robo, documentos_no_rascunho=lambda d: next(contagem), clicar=lambda d, s: None,
                             visivel=lambda d, s: mock.Mock(evaluate=lambda js: "Assinar documento(s)"),
                             adicionar_anexo=lambda d, i, a, linha: enviados.append((i, a, linha)),
                             saida=lambda linha: None):
        robo.documentos(pagina, "inicial.pdf", anexos, refazendo=True)
    # DOC 05 é o anexo nº 4 (tipo 'comprovação'); na tela entra depois das 3 linhas que já estavam lá
    assert enviados[0] == (4, "DOC 05].pdf", 3) and enviados[-1] == (14, "DOC 15].pdf", 13) and len(enviados) == 11


def test_documentos_parciais_pausa():
    def na_hora(condicao, *a, **k):  # sem esperar 30 s: a aba não tem onde anexar
        return condicao() or (_ for _ in ()).throw(TimeoutException("não apareceu"))
    with mock.patch.multiple(robo, documentos_no_rascunho=lambda d: 5, clicar=lambda d, s: None,
                             esperar_ate=na_hora, saida=lambda linha: None):
        try:  # aba sem lugar para anexar: pausa dizendo o que falta
            robo.documentos(mock.Mock(query_selector_all=lambda s: []), "inicial.pdf",
                            [f"DOC {n:02d}].pdf" for n in range(1, 16)], refazendo=True)
            assert False, "devia pausar"
        except (robo.Problema, TimeoutException) as e:
            assert isinstance(e, robo.Problema) and "Faltam 11: DOC 05].pdf;" in str(e) and "DOC 04]" not in str(e)
    with mock.patch.multiple(robo, documentos_no_rascunho=lambda d: 16, saida=lambda linha: None):
        robo.documentos(None, "inicial.pdf", ["a.pdf"] * 15, refazendo=True)  # tudo lá: não faz nada


# ---------- modo servidor ----------

from types import SimpleNamespace
from google.api_core.exceptions import NotFound


class SegredosFalsos:
    """Imita o SecretManagerServiceClient: versões [nome, dados, estado]."""
    def __init__(self):
        self.versoes = []

    def access_secret_version(self, request):
        ativas = [v for v in self.versoes if v[2] == "ENABLED"]
        if not ativas:
            raise NotFound("sem versão")
        return SimpleNamespace(payload=SimpleNamespace(data=ativas[-1][1]))

    def add_secret_version(self, request):
        nome = f"{request['parent']}/versions/{len(self.versoes) + 1}"
        self.versoes.append([nome, request["payload"]["data"], "ENABLED"])
        return SimpleNamespace(name=nome)

    def list_secret_versions(self, request):
        return [SimpleNamespace(name=v[0]) for v in self.versoes if v[2] == "ENABLED"]

    def destroy_secret_version(self, request):
        for v in self.versoes:
            if v[0] == request["name"]:
                v[2] = "DESTROYED"


def test_config_local():
    tmp = tempfile.mkdtemp()
    with mock.patch.object(robo, "SERVIDOR", False), mock.patch.object(robo, "ARQUIVO_CONFIG", os.path.join(tmp, "config.json")):
        assert robo.ler_config() == {}
        robo.gravar_config({"cpf": "11144477735"})
        assert robo.ler_config() == {"cpf": "11144477735"}
        assert os.listdir(tmp) == ["config.json"]  # gravação atômica não deixa .tmp


def test_config_no_servidor():
    falso = SegredosFalsos()
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "cliente_segredos", lambda: falso), \
         mock.patch.object(robo, "SEGREDO", "projects/p/secrets/pje-login-x"):
        assert robo.ler_config() == {}  # escritório novo: ainda sem login
        robo.gravar_config({"cpf": "1", "senha": "a"})
        robo.gravar_config({"cpf": "1", "senha": "b"})
        assert robo.ler_config() == {"cpf": "1", "senha": "b"}
        assert [v[2] for v in falso.versoes] == ["DESTROYED", "ENABLED"]  # a senha velha não fica guardada
        assert falso.versoes[1][0] == "projects/p/secrets/pje-login-x/versions/2"


def test_fechar_navegador():
    fechou = []
    pagina = SimpleNamespace(context=SimpleNamespace(close=lambda: fechou.append(1)))
    with mock.patch.object(robo, "navegador_atual", pagina):
        robo.fechar_navegador()
        assert robo.navegador_atual is None and fechou == [1]
        robo.fechar_navegador()  # já fechado: não dá erro


def test_chrome_do_servidor_sem_janela():
    chamado = {}
    contexto = SimpleNamespace(set_default_timeout=lambda t: None, pages=[SimpleNamespace(on=lambda *a: None)])

    def abrir(perfil, **kw):
        chamado.update(kw)
        return contexto
    pw = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=abrir))
    with mock.patch.object(robo, "SERVIDOR", True), mock.patch.object(robo, "_playwright", pw):
        robo.abrir_navegador()
    assert chamado["headless"] is True and chamado["viewport"] == {"width": 1366, "height": 768}
    assert "no_viewport" not in chamado and "--start-maximized" not in chamado["args"]
    # sem isso o PJe responde em inglês e o robô não reconhece "Código inválido" (tentaria de novo e bloquearia a conta)
    assert chamado["locale"] == "pt-BR" and chamado["timezone_id"] == "America/Cuiaba"


def test_lixeira_e_concluido_fora_da_lista():
    raiz = tempfile.mkdtemp()
    for p in ("Fulana", "Lixeira", "Concluído"):
        os.makedirs(os.path.join(raiz, p))
        open(os.path.join(raiz, p, "Fulana [Ação].pdf"), "wb").close()
    assert [os.path.basename(p) for p in robo.pastas_de_clientes(raiz)] == ["Fulana"]


if __name__ == "__main__":
    testes = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for t in testes:
        t()
        print("ok ", t.__name__)
    print(f"{len(testes)} testes passaram")
