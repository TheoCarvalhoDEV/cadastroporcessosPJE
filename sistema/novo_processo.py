"""Cadastra um processo novo no PJe (rascunho, NÃO assina e NÃO protocola) a partir da pasta da cliente:
  - "Cadastro de Ação Judicial - <nome> - <classe>.docx"  -> dados do cadastro
  - "DOC 01] ....pdf", "DOC 02] ....pdf" ...              -> anexos, nessa ordem
  - o outro .pdf                                           -> petição inicial

Uso: python novo_processo.py ["pasta da cliente"]
Sem argumento, usa a pasta mais recente (com .docx) ao lado de "PROTOCOLAMENTO -10.04".
"""
import os
import re
import sys
import json
import time
import base64
import functools
from urllib.parse import urljoin
import unicodedata
import docx
import pyotp
import PyPDF2
from playwright.sync_api import sync_playwright, Error as ErroDoNavegador, TimeoutError as TempoEsgotado

SISTEMA = os.path.dirname(os.path.abspath(__file__))  # o código (vai igual para todo escritório)
PASTA = os.path.dirname(SISTEMA)                       # a pasta do robô; as pastas de clientes ficam ao lado dela
# No servidor (servidor/robo@.service) cada escritório roda um processo com ROBO_SERVIDOR=1 e a própria ROBO_DADOS;
# sem essas variáveis é o modo local de sempre.
SERVIDOR = os.environ.get("ROBO_SERVIDOR") == "1"
DADOS = os.environ.get("ROBO_DADOS") or os.path.join(PASTA, "dados_do_escritorio")  # login, configuração, Chrome, fotos
ARQUIVO_CONFIG = os.path.join(DADOS, "config.json")    # login do PJe no modo local
# login do PJe no servidor: um segredo por escritório no Secret Manager (nunca no disco nem nos snapshots)
SEGREDO = f"projects/{os.environ.get('ROBO_PROJETO', '')}/secrets/pje-login-{os.environ.get('ROBO_ESCRITORIO', '')}"
URL_LOGIN = "https://pje.tjmt.jus.br/pje/login.seam"
# Mesmo link do menu "Processo > Novo processo"
URL_NOVO_PROCESSO = "https://pje.tjmt.jus.br/pje/Processo/cadastrar.seam?newInstance=true"
# Reabre um rascunho que existe (se foi excluído, o PJe responde "Erro inesperado")
URL_RASCUNHO = "https://pje.tjmt.jus.br/pje/Processo/update.seam?idProcesso={}"

# O documento não traz o CNPJ do executado; estes são os que o PJe tem cadastrados
CNPJ_CONHECIDO = {"ESTADO DE MATO GROSSO": "03507415000144"}

# Como o robô identifica o processo pela inicial (sem .docx): jurisdição, competência e classe saem do endereçamento
# e da ação, escolhidas entre as opções reais do PJe (mapa_pje.json); ver identificar(). Assunto e matéria a inicial
# não diz: saem do DICIONARIO do escritório; o que nada identificar sai do PADRAO. Os dois são editados na interface
# web (Configurações), que grava escritorio.json; ver carregar_escritorio(). Abaixo, os padrões do escritório JVO.
# Dicionário: quando TODAS as frases aparecem na inicial -> assunto e matéria. Vale a 1ª entrada que servir.
DICIONARIO = [
    {"frases": ["fgts"], "assunto": "10158 - FGTS/Fundo de Garantia por Tempo de Serviço",
     "materia": "DIREITO PROCESSUAL CIVIL E DO TRABALHO"},
    {"frases": ["cumprimento de sentença", "fazenda"],
     "assunto": "9517 - Título Executivo Judicial / Liquidação / Cumprimento / Execução",
     "materia": "DIREITO PROCESSUAL CIVIL E DO TRABALHO"},
]
PADRAO = {}  # {campo: valor} usado quando a inicial e o dicionário não dizem (ex.: {"matéria": "DIREITO CIVIL"})
# campos deduzidos (palpite, não leitura): se o .docx disser outra coisa, vale o .docx sem parar
REGRAS = ("jurisdição", "classe judicial", "assunto principal", "matéria", "competência", "tipo ativo", "tipo passivo")
ARQUIVO_MAPA = os.path.join(SISTEMA, "mapa_pje.json")
# Anexos: nome começa com PREFIXO + número ("DOC 01] ...", "Doc. 2 - ..."); tipo do 1º e dos demais no PJe
PREFIXO_ANEXO = "DOC"
TIPOS_ANEXO = ("Documento de Identificação", "Documento de comprovação")
# Modelo de .docx do escritório: rótulo dele (minúsculo) -> campo do robô (ex.: "nome do autor" -> "nome completo")
ROTULOS_DOCX = {}

# Tipo de pessoa da parte -> rótulo da opção no modal "Associar parte" (a posição da opção muda conforme o
# tipo da parte, então o robô escolhe pelo rótulo). Órgão público é "Jurídica" + "Órgão público? Sim".
PESSOAS = {"FÍSICA": "Física", "JURÍDICA": "Jurídica", "ÓRGÃO PÚBLICO": "Jurídica",
           "ENTE OU AUTORIDADE": "Ente ou autoridade", "MINISTÉRIO PÚBLICO": "Ministério Público"}
# polo: (grade de partes, campo do tipo da parte, campo do tipo de pessoa, campo do nome, campo do CPF/CNPJ)
POLOS = {"A": ("gridPartesPoloAtivoList", "tipo ativo", "pessoa ativo", "nome completo", "cpf"),
         "P": ("gridPartesPoloPassivoList", "tipo passivo", "pessoa passivo", "entidade", "cnpj")}
RACA_PADRAO = "ND - NÃO DECLARADO"  # Raça/Cor é obrigatória e o documento não informa
LIMITE_INICIAL_MB, LIMITE_ANEXO_MB = 5, 15  # limites do PJe para PDF
ARQUIVO_CORRECOES = "correcoes.json"  # correções feitas na interface, dentro da pasta do cliente

# Pausa (segundos) depois de cada ação, para acompanhar na tela etapa por etapa.
# O elemento que o robô vai mexer fica contornado de vermelho. 0 = velocidade normal.
PAUSA_SEGUNDOS = 3

TENTATIVAS_AUTOMATICAS = 2  # por etapa, antes de pausar e pedir ajuda
ESPERA_PJE_FORA = 30        # segundos, quando o PJe responde com erro ou não carrega
SINAIS_PJE_FORA = ("erro inesperado", "bad gateway", "service unavailable", "gateway time", "temporariamente indispon")

CAMPOS_OBRIGATORIOS = [
    "matéria", "jurisdição", "classe judicial", "assunto principal",
    "tipo ativo", "pessoa ativo", "nome completo", "tipo passivo", "pessoa passivo", "entidade",
    "justiça gratuita", "pedido de liminar ou antecipação de tutela", "valor da causa", "segredo de justiça",
]

avisos = []

ARQUIVO_ESCRITORIO = os.path.join(DADOS, "escritorio.json")
ARQUIVO_HISTORICO = os.path.join(DADOS, "historico.jsonl")  # um acontecimento por linha: a tela Relatório soma daqui


def carregar_escritorio():
    """Personalização do escritório (escritorio.json, gravado pela interface web) por cima dos padrões acima.
    Sem o arquivo, valem os padrões. Devolve o que foi lido ({} sem arquivo)."""
    global DICIONARIO, PADRAO, CNPJ_CONHECIDO, RACA_PADRAO, PAUSA_SEGUNDOS, PREFIXO_ANEXO, TIPOS_ANEXO, ROTULOS_DOCX
    if not os.path.exists(ARQUIVO_ESCRITORIO):
        return {}
    with open(ARQUIVO_ESCRITORIO, encoding="utf-8") as f:
        e = json.load(f)
    if "dicionario" in e:
        DICIONARIO = [t for t in e["dicionario"] if t.get("frases")]
    elif "acoes" in e:  # configuração de antes de 04/11/2026 (tipos de ação): as frases continuam dando assunto e matéria
        DICIONARIO = [{"frases": a["frases"], "assunto": a.get("assunto", ""), "materia": a.get("materia", "")}
                      for a in e["acoes"] if a.get("frases")]
    PADRAO = {k: v for k, v in e.get("padrao", PADRAO).items() if v}
    CNPJ_CONHECIDO = {k.upper().strip(): re.sub(r"\D", "", v) for k, v in e.get("partes_conhecidas", CNPJ_CONHECIDO).items()}
    RACA_PADRAO = e.get("raca_padrao") or RACA_PADRAO
    PAUSA_SEGUNDOS = e.get("pausa_segundos", PAUSA_SEGUNDOS)
    PREFIXO_ANEXO = e.get("prefixo_anexo", PREFIXO_ANEXO)
    TIPOS_ANEXO = (e.get("anexo_primeiro") or TIPOS_ANEXO[0], e.get("anexo_demais") or TIPOS_ANEXO[1])
    ROTULOS_DOCX = {k.lower().strip(): v for k, v in e.get("rotulos_docx", ROTULOS_DOCX).items() if k.strip()}
    return e


def numero_do_anexo(nome):
    """'DOC 01] x.pdf' -> 1. Prefixo do escritório, sem diferença de maiúscula, aceita 'Doc. 01'. None se não for anexo."""
    m = re.match(rf"{re.escape(PREFIXO_ANEXO)}\.?\s*(\d+)", nome, re.I)
    return int(m[1]) if m else None


carregar_escritorio()


class Problema(Exception):
    """O que o robô não resolve sozinho (dado errado, PJe recusou...): pausa e pergunta o que fazer."""


class Pulado(SystemExit):
    """Responderam 'Pular este cliente' numa pausa (para o relatório separar de erro)."""


class PJeInstavel(Exception):
    """O PJe não respondeu como esperado ('Erro inesperado', campo que não aceitou o valor): dá para refazer a etapa."""


def arquivos_da_pasta(pasta):
    """Separa o .docx (opcional), a petição inicial e os anexos 'DOC NN]' (ordenados pelo número). Confere os tamanhos.
    Sem .docx, devolve None no lugar dele: os dados saem só da inicial."""
    nomes = [n for n in os.listdir(pasta) if not n.startswith("~$")]  # listdir: nomes com [ ] quebram o glob
    docxs = [n for n in nomes if n.lower().endswith(".docx")]
    pdfs = [n for n in nomes if n.lower().endswith(".pdf")]
    anexos = sorted((n for n in pdfs if numero_do_anexo(n) is not None), key=numero_do_anexo)
    iniciais = [n for n in pdfs if n not in anexos]
    if len(docxs) > 1 or len(iniciais) != 1:
        sys.exit(f"A pasta precisa ter 1 PDF de inicial (fora os 'DOC NN]') e no máximo 1 .docx. Achei: {docxs + iniciais}")

    grandes = [f"{n} ({os.path.getsize(os.path.join(pasta, n)) / 1048576:.1f} MB)"
               for n, limite in [(iniciais[0], LIMITE_INICIAL_MB)] + [(a, LIMITE_ANEXO_MB) for a in anexos]
               if os.path.getsize(os.path.join(pasta, n)) > limite * 1048576]
    if grandes:
        sys.exit(f"Acima do limite do PJe (inicial {LIMITE_INICIAL_MB} MB, anexo {LIMITE_ANEXO_MB} MB): {grandes}")
    caminho = lambda n: os.path.join(pasta, n)
    return (caminho(docxs[0]) if docxs else None), caminho(iniciais[0]), [caminho(a) for a in anexos]


def ler_docx(caminho):
    """Junta as linhas 'Campo | Valor' das tabelas e os parágrafos 'Campo: valor' num dict (chaves em minúsculas).
    Sem .docx (caminho None), devolve vazio."""
    if not caminho:
        return {}
    doc = docx.Document(caminho)
    dados = {}
    for tabela in doc.tables:
        for linha in tabela.rows:
            celulas = [c.text.strip() for c in linha.cells]
            if len(celulas) >= 2 and celulas[0]:
                chave = celulas[0].lower()
                dados.setdefault(ROTULOS_DOCX.get(chave, chave), celulas[1])
    for p in doc.paragraphs:
        texto = p.text.strip()
        polo = re.match(r"Polo (Ativo|Passivo) \((.+)\)", texto)  # ex.: "Polo Ativo (Exequente)"
        if polo:
            dados[f"tipo {polo[1].lower()}"] = polo[2].upper()
        elif ":" in texto:
            chave, valor = texto.split(":", 1)
            chave = chave.strip().lower()
            dados.setdefault(ROTULOS_DOCX.get(chave, chave), valor.strip())
    # "Valor da Causa: A apurar ..." conta como faltando: só vale se tiver o número
    m = re.search(r"\d[\d.]*,\d{2}", dados.get("valor da causa", ""))
    dados["valor da causa"] = m[0] if m else ""
    return dados


@functools.lru_cache(maxsize=32)
def _texto_do_pdf(caminho, modificado):
    return " ".join(" ".join((p.extract_text() or "") for p in PyPDF2.PdfReader(caminho).pages).split())


def texto_da_inicial(caminho):
    """Texto corrido da inicial (guardado enquanto o arquivo não mudar: a interface lê a mesma inicial várias vezes)."""
    return _texto_do_pdf(caminho, os.path.getmtime(caminho))


def dados_da_inicial(caminho):
    """O que dá para tirar da petição inicial. O PDF quebra palavras e números com espaços
    ('013.975.611 -65'), por isso os padrões aceitam espaços e os números são limpos depois."""
    return identificar(texto_da_inicial(caminho), os.path.basename(caminho))[0]


def motivos_da_inicial(caminho):
    """{campo: de onde o robô tirou o valor}, para mostrar na interface."""
    return identificar(texto_da_inicial(caminho), os.path.basename(caminho))[1]


def dados_do_texto(texto, nome_arquivo):
    return identificar(texto, nome_arquivo)[0]


_mapa = None


def mapa_pje():
    """Opções da tela Novo processo lidas do PJe por mapear_pje.py: {matéria: {"jurisdicoes": {jurisdição:
    [competências]}, "classes": {competência: [classes]}}}. Vazio se não foram lidas (aí jurisdição, competência
    e classe ficam para escolher na interface). Quem relê o mapa zera _mapa."""
    global _mapa
    if _mapa is None:
        try:
            with open(ARQUIVO_MAPA, encoding="utf-8") as f:
                _mapa = json.load(f).get("materias", {})
        except (OSError, ValueError):
            _mapa = {}
    return _mapa


# palavras que não ajudam a escolher: artigos e o vocabulário de qualquer endereçamento
SEM_PESO = {"DE", "DA", "DO", "DAS", "DOS", "E", "A", "O", "AS", "OS", "EM", "NO", "NA", "AO", "OU", "POR", "PARA",
            "COM", "CONTRA", "LEI", "ART", "VARA", "VARAS", "JUIZ", "JUIZA", "JUIZO", "DIREITO", "COMARCA", "ESTADO",
            "MATO", "GROSSO", "ACAO", "EXCELENTISSIMO", "EXCELENTISSIMA", "SENHOR", "SENHORA", "DOUTOR", "DOUTORA"}
# sem pista na inicial, vale o caminho cível comum (pesa menos que uma pista de verdade)
PISTAS_PADRAO = {"PROCED", "COMUM", "CIVEL", "FEITO", "GERAI"}
SUFIXOS_JURISDICAO = ("JUIZADOS", "CÍVEL", "CRIMINAL", "SAÚDE")


def radicais(texto):
    """Palavras que importam, sem acento e sem plural, cortadas no começo ('JUIZADOS' e 'JUIZADO' -> 'JUIZAD')."""
    return {p.rstrip("S")[:6] for p in re.findall(r"[A-Z]{2,}", comparavel("texto", texto)) if p not in SEM_PESO}


def mais_parecida(opcoes, pistas):
    """A opção do PJe que mais combina com as pistas: palavra dela que está nas pistas soma 1 (0,4 se só está nas
    PISTAS_PADRAO), palavra que não está desconta 0,6. Empate: a de nome mais curto. None se nenhuma tem palavra."""
    def nota(opcao):
        r = radicais(re.sub(r"\(\d+\)", "", opcao))
        achou = r & (pistas | PISTAS_PADRAO)
        return sum(1 if p in pistas else 0.4 for p in achou) - 0.6 * len(r - achou) if achou else None
    notas = [(n, -len(o), o) for o in opcoes if (n := nota(o)) is not None]
    return max(notas)[2] if notas else None


def escolher_jurisdicao(enderecamento, jurisdicoes):
    """'...JUIZADO ESPECIAL ... DA COMARCA DE CÁCERES' -> 'CÁCERES JUIZADOS'. Acha a comarca (o nome da jurisdição
    sem JUIZADOS/CÍVEL/...) no endereçamento e, na família dela, a que combina: juizado, criminal, saúde ou cível."""
    junto = comparavel("texto", enderecamento).replace(" ", "")  # o PDF quebra palavras ("C UIABÁ")
    busca = junto.split("COMARCADE", 1)[1][:40] if "COMARCADE" in junto else junto
    familias = {}
    for j in jurisdicoes:
        p = j.split()
        familias.setdefault(" ".join(p[:-1]) if len(p) > 1 and p[-1] in SUFIXOS_JURISDICAO else j, []).append(j)
    achadas = [b for b in familias if comparavel("texto", b).replace(" ", "") in busca]
    if not achadas:
        return None
    base = max(achadas, key=len)  # "PRIMAVERA DO LESTE" e não "VERA"
    familia = familias[base]
    for pista, sufixo in (("JUIZAD", "JUIZADOS"), ("CRIMIN", "CRIMINAL"), ("SAUDE", "SAÚDE")):
        if pista in junto and f"{base} {sufixo}" in familia:
            return f"{base} {sufixo}"
    return next((j for j in (f"{base} CÍVEL", base) if j in familia), familia[0])


def primeiro_documento(trecho, digitos):
    """Primeiro CPF (11) ou CNPJ (14) com dígito verificador certo no trecho, formatado. None se não houver."""
    for m in re.finditer(r"\d[\d.\s/-]{9,22}\d", trecho):
        d = re.sub(r"\D", "", m[0])
        if len(d) == digitos and (cpf_valido(d) if digitos == 11 else cnpj_valido(d)):
            return formatar_documento(d)
    return None


def identificar(texto, nome_arquivo):
    """Lê a inicial inteira: devolve ({campo: valor ou None}, {campo: de onde veio}). Jurisdição, competência e
    classe são escolhidas entre as opções do PJe pelo endereçamento e pela ação; assunto e matéria pelo DICIONARIO;
    o que faltar, pelo PADRAO do escritório."""
    minusculo, motivos = texto.lower(), {}

    def achar(padrao, limpar=lambda s: re.sub(r"\s", "", s), onde=texto):
        m = re.search(padrao, onde, re.I)
        return limpar(m[1]) if m else None

    # a quem a inicial se dirige: "EXCELENTÍSSIMO ... JUIZADO ESPECIAL ... DA COMARCA DE CÁCERES [ESTADO DE MATO GROSSO]"
    inicio = re.search(r"EXCELENT|\bAO\s+(?:DOUTO\s+)?JU[IÍ]Z|\bAO\s+N[UÚ]CLEO|\bAO\s+JUIZADO", texto, re.I)
    enderecamento, depois = "", texto
    if inicio:
        trecho = texto[inicio.start(): inicio.start() + 300]
        fim = re.search(r"ESTADO\s+DE\s+MATO\s+GROSSO|,", trecho[30:])
        enderecamento = trecho[:30 + fim.start()] if fim else trecho[:220]
        depois = re.sub(r"^\s*ESTADO\s+DE\s+MATO\s+GROSSO", "", texto[inicio.start() + len(enderecamento):])
    # o réu e a ação logo antes dele: "...vem propor a presente AÇÃO DE COBRANÇA – FGTS em desfavor de ESTADO DE MATO GROSSO,"
    reu = re.search(r"\s(?:em\s+desfavor|em\s+face)\s+d[eoa]s?\s+([^,]+?)\s*,", depois, re.I)
    acao = re.split(r"\b(?:propor|ajuizar|promover|interpor|requerer|apresentar)\b",
                    depois[max(0, reu.start() - 250): reu.start()], flags=re.I)[-1] if reu else ""

    # partes. Nome como está na qualificação ("...ESTADO DE MATO GROSSO CICRANO DA SILVA PEREIRA , brasileiro"); o nome
    # do arquivo costuma vir sem "da/de/dos" e não bate com a Receita, por isso só serve de reserva
    qualificado = (re.search(r"MATO GROSSO\s+([A-ZÀ-Ú][A-ZÀ-Ú .'-]+?)\s*,\s*(?:brasileir|portador)", texto)
                   or re.match(r"\s*([A-ZÀ-Ú][A-ZÀ-Ú .'-]{4,}?)\s*,", depois))
    nome = qualificado[1].strip() if qualificado else re.split(r"\s*\[", nome_arquivo)[0]
    motivos["nome completo"] = "qualificação na inicial" if qualificado else "nome do arquivo da inicial"
    qualificacao_autor = depois[:reu.start()] if reu else depois[:1500]  # o CPF depois do réu é dele, não do autor
    cpf = achar(r"\[CPF\]\s*sob o n\S*\s*(\d[\d.\s-]{12,16}\d)") or primeiro_documento(qualificacao_autor, 11)
    entidade = reu[1].strip() if reu else None
    if entidade:
        motivos["entidade"] = f'"em desfavor de {entidade}"'
    cnpj = None
    if entidade and entidade.upper() in CNPJ_CONHECIDO:
        motivos["cnpj"] = f"réu conhecido: o robô usa o CNPJ cadastrado no PJe ({formatar_documento(CNPJ_CONHECIDO[entidade.upper()])})"
    elif entidade:
        resto = depois[reu.end():][:600]
        cnpj = (achar(r"\[CNPJ\s*\]\s*sob o n\S*\s*(\d[\d./\s-]{16,20}\d)", onde=resto)
                or primeiro_documento(resto, 14) or primeiro_documento(resto, 11))
    reu_publico = identificar_pessoa(entidade, cnpj) == "ÓRGÃO PÚBLICO" if entidade else False

    # jurisdição -> competência -> classe, entre as opções do PJe; o que não der para identificar, o padrão do escritório
    def ou_padrao(campo, valor, motivo):
        if valor:
            motivos[campo] = motivo
            return valor
        if PADRAO.get(campo):
            motivos[campo] = "padrão do escritório"
        return PADRAO.get(campo)

    mapa = mapa_pje()
    pistas = radicais(enderecamento) | radicais(acao) | ({"FAZEND", "PUBLIC"} if reu_publico else set())
    jurisdicao = ou_padrao("jurisdição", escolher_jurisdicao(enderecamento, {j for m in mapa.values() for j in m["jurisdicoes"]}),
                           f'endereçamento: "...{" ".join(enderecamento.split()[-12:])}"')
    competencias = sorted({c for m in mapa.values() for c in m["jurisdicoes"].get(jurisdicao, [])})
    competencia = ou_padrao("competência", mais_parecida(competencias, pistas),
                            "a que mais combina com o endereçamento" + (" e o réu público" if reu_publico else ""))
    classes = sorted({k for m in mapa.values() if competencia in m["jurisdicoes"].get(jurisdicao, [])
                      for k in m["classes"].get(competencia, [])})
    classe = ou_padrao("classe judicial", mais_parecida(classes, pistas),
                       f'a que mais combina com a ação: "{" ".join(acao.split()[-12:])}"' if acao.strip()
                       else "a que mais combina com o endereçamento")
    # assunto e matéria: dicionário do escritório. Matéria sem dicionário: entre as do PJe que têm essa jurisdição,
    # competência e classe, a única ou a que mais combina com a competência (Família e Sucessões -> DIREITO CIVIL > FAMÍLIA)
    texto_parecido = " " + parecido(texto) + " "
    termo = next((t for t in DICIONARIO if all(f" {parecido(f)} " in texto_parecido for f in t["frases"])), {})
    possiveis = [m for m, v in mapa.items() if competencia in v["jurisdicoes"].get(jurisdicao, [])
                 and classe in v["classes"].get(competencia, [])]
    palpite = possiveis[0] if len(possiveis) == 1 else mais_parecida(possiveis, pistas | radicais(competencia or ""))
    if termo:
        motivos["assunto principal"] = motivos["matéria"] = f'dicionário: "{"; ".join(termo["frases"])}"'
    materia = termo.get("materia") or ou_padrao("matéria", palpite, "a única do PJe com essa jurisdição, competência e classe"
                                                if len(possiveis) == 1 else "palpite pela competência: confira")
    assunto = termo.get("assunto") or ou_padrao("assunto principal", None, "")

    # tipo da parte: pela palavra que a inicial usa; senão pela classe (juizado: requerente; cumprimento: exequente)
    papeis = (("exequente", "EXEQUENTE", "EXECUTADO"), ("requerente", "REQUERENTE", "REQUERIDO"))
    ativo, passivo = next(((a, p) for palavra, a, p in papeis if palavra in minusculo), (None, None))
    if not ativo and classe:
        ativo, passivo = (("EXEQUENTE", "EXECUTADO") if re.search(r"CUMPRIMENTO|EXECUÇÃO", classe) else
                          ("REQUERENTE", "REQUERIDO") if "JUIZADO" in classe else (None, None))
    pede_tutela = re.search(r"tutela de urgência|tutela antecipada|antecipação de tutela|medida liminar|pedido liminar", minusculo)
    # "residente e domiciliado na Rua X [Quadra 15] , nº 6 05, Bairro Y , Cáceres/Mato Grosso, Código ... [CEP]: 782 05-515,"
    morada = re.search(r"residente e domiciliad[oa] n[ao]\s+(.+?)\s*,\s*C\s?ó\s?d\s?i\s?g\s?o.{0,40}?\[CEP\]\s*:?\s*([\d\s.-]+\d)", texto)
    cep = re.sub(r"\D", "", morada[2]) if morada else ""
    dados = {
        "endereço": (re.sub(r"\s+([,.\]/])|(\[)\s+", r"\1\2", morada[1]) + f", CEP {cep[:5]}-{cep[5:]}"
                     if len(cep) == 8 else None),
        "nome completo": nome,
        "jurisdição": jurisdicao,
        "classe judicial": classe,
        "assunto principal": assunto or None,
        "matéria": materia,
        "competência": competencia,
        "cpf": cpf,
        "entidade": entidade,
        "cnpj": cnpj,
        "valor da causa": (achar(r"Do Valor da Causa.*?R\$\s*(\d[\d.\s]*,\s*\d{2})")
                           or achar(r"(?:valor\s+da\s+causa|d[áa]-se\s+(?:à|a)\s+causa|atribui-se\s+(?:à|a)\s+causa)"
                                    r".{0,200}?R\$\s*(\d[\d.\s]*,\s*\d{2})")),
        "justiça gratuita": "SIM" if "justiça gratuita" in minusculo else None,
        "tipo ativo": ativo,
        "tipo passivo": passivo,
        "pedido de liminar ou antecipação de tutela": "SIM" if pede_tutela else "NÃO",
        "segredo de justiça": "SIM" if "segredo de justiça" in minusculo or "sigilo" in minusculo else "NÃO",
    }
    return dados, motivos


def partes_do_endereco(endereco):
    """'Rua X [Quadra 15], nº 605, Bairro Y, Cáceres/Mato Grosso, CEP 78205-515' -> campos da aba ENDEREÇOS.
    O que vem entre [ ] vira complemento; sem número = 'S/N'; sem bairro (zona rural) = o logradouro. None se não der."""
    m = re.fullmatch(r"(.+?),?\s*CEP\s*(\d{5})-?(\d{3})", (endereco or "").strip())
    if not m:
        return None
    complemento = " ".join(re.findall(r"\[([^\]]*)\]", m[1]))
    partes = [p.strip() for p in re.sub(r"\s*\[[^\]]*\]", "", m[1]).split(",") if p.strip()]
    if len(partes) < 2:
        return None
    numero = next((p for p in partes[1:-1] if re.match(r"(s/\s*n|n\s?[º°o]\b|n\.)", p, re.I)), "")
    bairro = next((p for p in partes[1:-1] if p != numero), partes[0])
    return {"cep": f"{m[2]}-{m[3]}", "logradouro": partes[0], "bairro": re.sub(r"^Bairro\s+", "", bairro),
            "numero": re.sub(r"\D", "", numero) or "S/N", "complemento": complemento[:50],
            "cidade": partes[-1].split("/")[0].strip()}


def comparavel(campo, valor):
    """Forma de comparar o .docx com a inicial: só os dígitos nos números; sem acento e maiúsculo no resto."""
    if campo in ("cpf", "cnpj", "valor da causa"):
        return re.sub(r"\D", "", valor)
    sem_acento = unicodedata.normalize("NFKD", valor).encode("ascii", "ignore").decode()
    if campo in ("nome completo", "entidade"):  # o PDF quebra nomes com espaço ("WESL EY"): compara sem espaços
        return "".join(sem_acento.upper().split())
    return " ".join(sem_acento.upper().split())


def identificar_pessoa(nome, documento):
    """Tipo de pessoa pelo nome e pelo CPF/CNPJ. '' quando não dá para saber (fica pendente para escolher na tela)."""
    nome_simples = comparavel("nome", nome or "")
    digitos = re.sub(r"\D", "", documento or "")
    if "MINISTERIO PUBLICO" in nome_simples:
        return "MINISTÉRIO PÚBLICO"
    if (nome or "").upper() in CNPJ_CONHECIDO or re.match(r"(ESTADO|MUNICIPIO|UNIAO|FAZENDA PUBLICA)\b", nome_simples):
        return "ÓRGÃO PÚBLICO"
    return {11: "FÍSICA", 14: "JURÍDICA"}.get(len(digitos), "")


def completar_com_inicial(dados, inicial, fixos=()):
    """Preenche o que faltar no .docx com a petição inicial e confere o que estiver nos dois.
    Se divergirem, para (melhor parar do que cadastrar com dado errado). Os campos em `fixos` (corrigidos
    na interface) valem mais que a inicial. No fim, exige os obrigatórios."""
    for campo, da_inicial in dados_da_inicial(inicial).items():
        no_docx = dados.get(campo, "")
        if campo in fixos or not da_inicial:
            continue
        if not no_docx:
            dados[campo] = da_inicial
        elif campo not in REGRAS and comparavel(campo, no_docx) != comparavel(campo, da_inicial):
            sys.exit(f"'{campo}' diverge: o .docx diz '{no_docx}' e a inicial diz '{da_inicial}'. Corrija antes de rodar.")
    for _, _, c_pessoa, c_nome, c_doc in POLOS.values():  # tipo de pessoa: se ninguém disse, deduz
        if not dados.get(c_pessoa):
            dados[c_pessoa] = identificar_pessoa(dados.get(c_nome), dados.get(c_doc))

    faltando = [c for c in CAMPOS_OBRIGATORIOS if not dados.get(c)]
    if faltando:
        sys.exit(f"Não achei na inicial (nem no .docx): {', '.join(faltando)}. Preencha na ficha do cliente.")
    if not re.sub(r"\D", "", dados["valor da causa"]).strip("0"):
        sys.exit("Valor da causa zerado no .docx e na inicial.")
    return dados


def pastas_de_clientes(raiz):
    """Pastas de clientes em 'Cadastro de processo' (com um PDF de inicial ou um .docx), fora 'Concluído' e a
    pasta do robô."""
    def eh_cliente(pasta):
        return any((f.lower().endswith(".docx") and not f.startswith("~$"))
                   or (f.lower().endswith(".pdf") and numero_do_anexo(f) is None) for f in os.listdir(pasta))
    return sorted(os.path.join(raiz, n) for n in os.listdir(raiz)
                  if n not in ("Concluído", "Lixeira") and os.path.join(raiz, n) != PASTA and os.path.isdir(os.path.join(raiz, n))
                  and eh_cliente(os.path.join(raiz, n)))


def cpf_valido(digitos):
    """Confere os dois dígitos verificadores do CPF (pega erro de digitação)."""
    if len(digitos) != 11 or digitos == digitos[0] * 11:
        return False
    for n in (9, 10):
        resto = sum(int(digitos[i]) * (n + 1 - i) for i in range(n)) * 10 % 11
        if resto % 10 != int(digitos[n]):
            return False
    return True


def cnpj_valido(digitos):
    """Confere os dois dígitos verificadores do CNPJ."""
    if len(digitos) != 14 or digitos == digitos[0] * 14:
        return False
    for n in (12, 13):
        pesos = list(range(n - 7, 1, -1)) + list(range(9, 1, -1))
        resto = sum(int(d) * p for d, p in zip(digitos, pesos)) % 11
        if (0 if resto < 2 else 11 - resto) != int(digitos[n]):
            return False
    return True


def erro_documento(pessoa, documento):
    """O documento combina com o tipo de pessoa? Física só com CPF; jurídica e órgão público só com CNPJ;
    Ministério Público e ente/autoridade não precisam (o PJe busca pelo nome). '' se estiver certo."""
    digitos = re.sub(r"\D", "", documento or "")
    if pessoa == "FÍSICA":
        if len(digitos) == 14:
            return "é um CNPJ, mas pessoa física precisa de CPF"
        return "" if cpf_valido(digitos) else ("falta o CPF" if not digitos else "CPF inválido (confira os números)")
    if pessoa in ("JURÍDICA", "ÓRGÃO PÚBLICO"):
        if len(digitos) == 11:
            return "é um CPF, mas pessoa jurídica precisa de CNPJ"
        return "" if cnpj_valido(digitos) else ("falta o CNPJ" if not digitos else "CNPJ inválido (confira os números)")
    return ""


def formatar_documento(valor):
    """11 dígitos -> 000.000.000-00; 14 -> 00.000.000/0000-00; o resto fica como está."""
    d = re.sub(r"\D", "", valor)
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return valor


def formatar_valor(valor):
    """'9493,48', '9.493' ou 'R$ 9493' -> '9.493,48' / '9.493,00'. Ponto como decimal ('9493.48') fica como está
    (é ambíguo) e o erro_no_valor avisa."""
    v = valor.replace("R$", "").strip()
    m = re.fullmatch(r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{1,2}))?", v)
    if not m:
        return valor
    return f"{int(m[1].replace('.', '')):,}".replace(",", ".") + "," + (m[2] or "00").ljust(2, "0")


def erro_no_valor(campo, valor):
    """Confere o formato dos campos que o robô digita em campo com máscara. '' se estiver certo."""
    digitos = re.sub(r"\D", "", valor)
    if campo in ("cpf", "cnpj") and valor.strip():
        if len(digitos) not in (11, 14):
            return "precisa ter 11 dígitos (CPF) ou 14 (CNPJ)"
        if not (cpf_valido(digitos) if len(digitos) == 11 else cnpj_valido(digitos)):
            return f"{'CPF' if len(digitos) == 11 else 'CNPJ'} inválido (confira os números)"
    if campo.startswith("pessoa ") and valor not in PESSOAS:
        return f"escolha um destes: {', '.join(PESSOAS)}"
    if campo == "valor da causa" and not (re.fullmatch(r"\d[\d.]*,\d{2}", valor.strip()) and digitos.strip("0")):
        return "o valor da causa precisa estar no formato 1.234,56 e ser maior que zero"
    return ""


def ler_correcoes(pasta):
    """Correções feitas na interface ({campo: valor}), gravadas na pasta do cliente."""
    caminho = os.path.join(pasta, ARQUIVO_CORRECOES)
    if not os.path.exists(caminho):
        return {}
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except ValueError:
        sys.exit(f"O arquivo {ARQUIVO_CORRECOES} da pasta está corrompido: apague-o e corrija os dados de novo.")


def salvar_correcoes(pasta, correcoes):
    """Grava as correções; sem nenhuma, apaga o arquivo."""
    caminho = os.path.join(pasta, ARQUIVO_CORRECOES)
    if not correcoes:
        if os.path.exists(caminho):
            os.remove(caminho)
        return
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(correcoes, f, ensure_ascii=False, indent=2)


def valores_para_corrigir(pasta):
    """Para o formulário da interface: {campo: (valor no .docx, valor na inicial, correção salva)}.
    Para (SystemExit) se o problema for de arquivo, que não se corrige no formulário."""
    caminho_docx, inicial, _ = arquivos_da_pasta(pasta)
    do_docx, da_inicial, correcoes = ler_docx(caminho_docx), dados_da_inicial(inicial), ler_correcoes(pasta)
    base = {**{k: v for k, v in da_inicial.items() if v}, **{k: v for k, v in do_docx.items() if v}}
    for _, _, c_pessoa, c_nome, c_doc in POLOS.values():  # sugestão do tipo de pessoa (vai na coluna "inicial")
        da_inicial.setdefault(c_pessoa, identificar_pessoa(base.get(c_nome), base.get(c_doc)))
    return {c: (do_docx.get(c, ""), da_inicial.get(c) or "", correcoes.get(c, ""))
            for c in set(do_docx) | set(da_inicial) | set(correcoes)}


def preparar(pasta):
    """Lê e confere a pasta da cliente ANTES de abrir o PJe. Para (SystemExit com a mensagem) se houver problema.
    'origem' diz de onde veio cada campo: '.docx', 'inicial' ou 'corrigido' (na interface)."""
    caminho_docx, inicial, anexos = arquivos_da_pasta(pasta)
    do_docx = ler_docx(caminho_docx)
    correcoes = ler_correcoes(pasta)
    errados = [f"{c}: {m}" for c, v in correcoes.items() if (m := erro_no_valor(c, v))]
    if errados:
        sys.exit(f"Correção inválida: {'; '.join(errados)}")
    dados = completar_com_inicial({**do_docx, **correcoes}, inicial, fixos=correcoes)
    for polo, (_, _, c_pessoa, c_nome, c_doc) in POLOS.items():  # cada tipo de pessoa exige um documento
        pessoa, nome = dados[c_pessoa], dados[c_nome]
        if pessoa not in PESSOAS:
            sys.exit(f"Tipo de pessoa inválido para '{nome}': '{pessoa}'. Escolha na ficha do cliente.")
        doc = dados.get(c_doc, "") or (CNPJ_CONHECIDO.get(nome.upper(), "") if pessoa == "ÓRGÃO PÚBLICO" else "")
        erro = erro_documento(pessoa, doc)
        if erro:
            sys.exit(f"'{nome}' ({pessoa.lower()}): {erro}. Corrija na ficha do cliente.")
    origem = {c: ("corrigido" if c in correcoes else
                  "regra" if (c.startswith("pessoa ") or c in REGRAS) and not do_docx.get(c) else
                  "inicial" if dados[c] and not do_docx.get(c) else ".docx") for c in dados}
    return {"pasta": pasta, "dados": dados, "inicial": inicial, "anexos": anexos, "origem": origem}


# ---------- o navegador: Playwright com o Google Chrome instalado ----------
# Tudo que mexe no navegador roda na mesma linha de execução (o Playwright exige): a interface web tem uma só
# linha de trabalho para o robô (app_web.trabalhador).

_playwright = None


def abrir_navegador():
    """O Chrome instalado no computador, com o perfil do robô (dados_do_escritorio/Perfil_PJe guarda a sessão do
    PJe). Alertas do PJe são aceitos e vão para o Console."""
    global _playwright
    if _playwright is None:
        _playwright = sync_playwright().start()
    args = ["--disable-blink-features=AutomationControlled", "--disable-notifications"]
    # no servidor não há tela: Chrome sem janela num tamanho fixo (as fotos das pausas saem iguais às do escritório)
    # em português e no horário de Cuiabá: o serviço do servidor não tem idioma, e o robô reconhece as mensagens do
    # PJe pelo texto ("Código inválido"); em inglês ele não reconheceria e tentaria o código de novo
    tela = dict(headless=True, viewport={"width": 1366, "height": 768}, locale="pt-BR",
                timezone_id="America/Cuiaba") if SERVIDOR else \
        dict(headless=False, no_viewport=True, args=["--start-maximized"] + args)
    try:
        contexto = _playwright.chromium.launch_persistent_context(
            os.path.join(DADOS, "Perfil_PJe"), channel="chrome", **{"args": args, **tela},
            ignore_default_args=["--enable-automation"])
    except ErroDoNavegador as e:
        if "is not found" in str(e):
            sys.exit("O robô usa o Google Chrome deste computador e não o encontrou. Instale o Chrome e tente de novo.")
        raise
    contexto.set_default_timeout(30000)
    pagina = contexto.pages[0] if contexto.pages else contexto.new_page()

    def alerta(dialogo):
        log(f"  Alerta do navegador: {dialogo.message}")
        dialogo.accept()
    pagina.on("dialog", alerta)
    return pagina


def esperar_ate(condicao, timeout=30, intervalo=0.25):
    """Repete condicao() até dar algo verdadeiro (e devolve) ou passar o tempo (TempoEsgotado). A página
    redesenhando no meio (elemento sumiu, página trocou) conta como 'ainda não'."""
    limite = time.time() + timeout
    while True:
        try:
            valor_ = condicao()
        except ErroDoNavegador as e:
            if "closed" in str(e).lower():  # Chrome fechado: não adianta esperar
                raise
            valor_ = None
        if valor_:
            return valor_
        if time.time() > limite:
            raise TempoEsgotado(f"Tempo esgotado ({timeout} s) esperando a tela do PJe.")
        time.sleep(intervalo)


def logado(d):
    """Qualquer página do PJe com sessão aberta (painel, Quadro de avisos, aviso novo...) tem o link de Sair.
    Não depender da página que o PJe abre depois do login: ela muda (ex.: Quadro de avisos de 20/10/2026)."""
    return bool(d.query_selector("[id='tabConsultaProcesso_lbl']") or d.query_selector("a[href*='logout.seam']"))


@functools.cache
def cliente_segredos():
    from google.cloud import secretmanager  # só o servidor precisa da biblioteca
    return secretmanager.SecretManagerServiceClient()


def ler_config():
    """Login do PJe deste escritório: cpf, senha, totp_key (e totp_qr). Modo local: config.json; servidor: o
    segredo do escritório no Secret Manager. Escritório novo, que ainda não salvou o login: {}."""
    if SERVIDOR:
        from google.api_core.exceptions import NotFound
        try:
            versao = cliente_segredos().access_secret_version(request={"name": SEGREDO + "/versions/latest"})
        except NotFound:
            return {}
        return json.loads(versao.payload.data)
    try:
        with open(ARQUIVO_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def gravar_config(config):
    """Grava o login do PJe. No servidor, cria a versão nova do segredo e destrói as anteriores (a senha velha
    não fica guardada)."""
    if SERVIDOR:
        cliente = cliente_segredos()
        nova = cliente.add_secret_version(request={"parent": SEGREDO, "payload": {
            "data": json.dumps(config, ensure_ascii=False).encode("utf-8")}})
        for v in cliente.list_secret_versions(request={"parent": SEGREDO, "filter": "state:ENABLED"}):
            if v.name != nova.name:
                cliente.destroy_secret_version(request={"name": v.name})
        return
    with open(ARQUIVO_CONFIG + ".tmp", "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(ARQUIVO_CONFIG + ".tmp", ARQUIVO_CONFIG)


ERRO_LOGIN = "#input-error, #input-error-otp-code, .kc-feedback-text, .alert-error, #mensagemErroLogin, .rich-messages"
NOVO_AUTENTICADOR = "#kc-totp-settings-form"  # página do QR: o PJe quer um autenticador novo (pediram 'Configurar novo dispositivo')
CONFIGURE_NO_ROBO = "Em Configurações > Login do PJe, clique em 'Configurar o código pelo robô'."


def mensagens_da_tela(d):
    return " ".join(e.inner_text().strip() for e in d.query_selector_all(ERRO_LOGIN) if e.inner_text().strip())


def cpf_e_senha(d, config):
    """Tela de login: CPF, senha e Entrar. Devolve a tela seguinte: 'codigo', 'novo autenticador' ou 'logado'."""
    d.goto(URL_LOGIN)
    # O perfil pode já ter sessão ativa: espera o formulário de login OU o painel
    esperar_ate(lambda: d.query_selector("#username") or logado(d))
    if logado(d):
        return "logado"
    log("Fazendo login (CPF e senha)...")
    for tentativa in range(3):  # a tela de login às vezes se redesenha enquanto o robô digita
        try:
            visivel(d, "#username").fill(re.sub(r"\D", "", config["cpf"]))
            visivel(d, "#password").fill(config["senha"])
            break
        except ErroDoNavegador:
            if tentativa == 2:
                raise
            d.wait_for_timeout(2000)
    # O PJe alterna entre duas telas de login: a nova (botão kc-login) e a antiga loginOld.seam (botão btnEntrar,
    # visto em 20/10/2026). Clica no botão que existir.
    botao = next((e for e in d.query_selector_all("#kc-login, #btnEntrar, input[type='submit'][value='Entrar']")
                  if e.is_visible()), None)
    if not botao:
        sys.exit("A tela de login do PJe mudou: não achei o botão Entrar. Entre à mão no Chrome do robô e tente de novo.")
    botao.click()
    # depois da senha: o código de 2 fatores, a página do QR (dispositivo novo pedido) ou, às vezes, direto o painel
    try:
        esperar_ate(lambda: d.query_selector("#otp") or d.query_selector(NOVO_AUTENTICADOR) or logado(d)
                    or mensagens_da_tela(d), 60)
    except TempoEsgotado:
        sys.exit("Depois do CPF e senha o PJe mostrou uma tela que o robô não conhece. Termine o login à mão no "
                 "Chrome do robô e tente de novo.")
    if d.query_selector("#otp"):
        return "codigo"
    if d.query_selector(NOVO_AUTENTICADOR):
        return "novo autenticador"
    if logado(d):
        return "logado"
    sys.exit(f"O PJe recusou o login: {mensagens_da_tela(d)}")


def entrar(d):
    """Login por CPF + senha + código TOTP (sem certificado). Os três vêm do config.json.
    Se a sessão ainda estiver ativa, só confere o painel."""
    config = ler_config()
    if not (config.get("cpf") and config.get("senha")):
        sys.exit("Falta o login do PJe (CPF e senha). Preencha em Configurações > Login do PJe e clique em Salvar alterações.")
    try:
        if logado(d):  # recuperando no meio do cadastro com a sessão viva: não precisa logar de novo
            return d
    except ErroDoNavegador:
        pass
    tela = cpf_e_senha(d, config)
    if tela == "novo autenticador":  # não conclui sozinho no meio do cadastro: trocaria o código do celular do advogado
        sys.exit("O PJe está pedindo para cadastrar um autenticador novo (alguém clicou em 'Configurar novo dispositivo'). "
                 + CONFIGURE_NO_ROBO)
    if tela == "codigo":
        if not config.get("totp_key"):
            sys.exit("Falta a chave do autenticador do PJe. " + CONFIGURE_NO_ROBO)
        visivel(d, "#otp").fill(pyotp.TOTP(config["totp_key"]).now())
        visivel(d, "xpath=//input[@type='submit' or @value='VALIDAR' or @value='Validar']").click()
        try:
            esperar_ate(lambda: logado(d) or mensagens_da_tela(d), 40)
        except TempoEsgotado:
            pass  # a espera abaixo dá o erro de tempo (e o cadastro tenta de novo)
        if "inválido" in mensagens_da_tela(d).lower():
            sys.exit("O PJe recusou o código do autenticador: a chave guardada não vale mais (alguém configurou um "
                     "dispositivo novo?). " + CONFIGURE_NO_ROBO)
    esperar_ate(lambda: logado(d), 40)
    if "QuadroAviso" in d.url:  # aviso novo do tribunal: o robô segue, mas mostra no Console
        titulos = [e.inner_text().strip() for e in d.query_selector_all(
            "[id*='quadroAvisoPapelMensagem'] h1, [id*='quadroAvisoPapelMensagem'] h2, [id*='quadroAvisoPapelMensagem'] h3")
            if e.inner_text().strip()]
        log(f"  O PJe abriu o Quadro de avisos{': ' + ' / '.join(titulos[:3]) if titulos else ''}. Seguindo.")
    log("Login OK.")
    return d


def chave_do_texto(textos):
    """A chave que o PJe mostra em 'Não foi possível ler o código QR?': 32 letras/números em grupos de 4."""
    return next((t.replace(" ", "") for t in textos if re.fullmatch(r"[A-Z2-7]{4}( [A-Z2-7]{4}){5,}", t.strip())), None)


def configurar_codigo(d, pedir_link, guardar):
    """Primeiro acesso ou chave perdida: cadastra o robô como autenticador do PJe (2 fatores).
    Depois do CPF e senha, se o PJe pedir o código, clica em 'Configurar novo dispositivo' e espera o link do e-mail
    (pedir_link(pediu) devolve o link colado na interface); se já mostrar a página do QR, segue direto. Na página do
    QR lê a chave e o QR da MESMA abertura (cada abertura gera outra chave), chama guardar(chave, qr) ANTES de concluir
    (se algo cair depois, a chave não se perde) e conclui com o código gerado da chave. O dispositivo novo substitui o
    antigo: o celular precisa escanear o mesmo QR."""
    config = ler_config()
    if not (config.get("cpf") and config.get("senha")):
        sys.exit("Salve o CPF e a senha do PJe antes de configurar o código.")
    sair = d.query_selector("a[href*='logout.seam']") if logado(d) else None
    if sair:  # com a sessão aberta o PJe não mostra a tela do código: sai antes
        log("Saindo do PJe para configurar o código...")
        d.goto(urljoin(d.url, sair.get_attribute("href")))
    tela = cpf_e_senha(d, config)
    if tela == "logado":
        sys.exit("O PJe entrou sem pedir o código de 2 fatores: não há o que configurar.")
    if tela == "codigo":
        pedir = next((e for e in d.query_selector_all("a, button") if e.is_visible()
                      and "novo dispositivo" in (e.inner_text() or "").lower()), None)
        if pedir:
            log("Pedindo ao PJe um dispositivo novo (o PJe manda um e-mail com o link)...")
            pedir.click()
            d.wait_for_timeout(3000)
            campo = d.query_selector("#username")  # se o PJe perguntar de quem é a conta
            if campo and campo.is_visible():
                campo.fill(re.sub(r"\D", "", config["cpf"]))
                next(e for e in d.query_selector_all("input[type='submit'], button[type='submit']") if e.is_visible()).click()
                d.wait_for_timeout(3000)
            aviso = mensagens_da_tela(d)
            if aviso:
                log(f"  O PJe disse: {aviso}")
        else:
            log("Não achei no PJe o 'Configurar novo dispositivo': peça pelo PJe e cole o link do e-mail.")
        if not d.query_selector(NOVO_AUTENTICADOR):
            d.goto(pedir_link(bool(pedir)))
            try:
                esperar_ate(lambda: d.query_selector(NOVO_AUTENTICADOR), 60)
            except TempoEsgotado:
                sys.exit(f"O link não abriu a página do QR (pode ter expirado ou já ter sido usado). {mensagens_da_tela(d)} "
                         "Clique em 'Configurar o código pelo robô' de novo.")
    log("Lendo a chave na página do QR...")
    imagem = d.query_selector("#kc-totp-secret-qr-code, img[alt*='Barcode']")  # o QR (alt 'Figure: Barcode' no PJe)
    qr = (imagem.get_attribute("src") or "") if imagem else ""
    qr = qr if qr.startswith("data:image") else ""
    segredo = d.query_selector("input[name='totpSecret']")  # a mesma chave do QR, escondida no formulário
    if segredo and segredo.get_attribute("value"):
        chave = base64.b32encode(segredo.get_attribute("value").encode()).decode().rstrip("=")
    else:  # sem o campo escondido: a chave em texto; o QR desta abertura já não vale
        clicar(d, "#mode-manual")
        chave = esperar_ate(lambda: chave_do_texto(d.evaluate(
            "() => [...document.querySelectorAll('p, span, code, strong, div')].map(e => e.innerText.trim())")), 30)
        qr = ""
    guardar(chave, qr)
    log("Cadastrando o robô como autenticador...")
    visivel(d, "#totp").fill(pyotp.TOTP(chave).now())
    visivel(d, "#saveTOTPBtn").click()
    try:
        esperar_ate(lambda: logado(d) or mensagens_da_tela(d) or not d.query_selector(NOVO_AUTENTICADOR), 60)
    except TempoEsgotado:
        pass
    if d.query_selector(NOVO_AUTENTICADOR):
        sys.exit(f"O PJe não aceitou o cadastro do autenticador: {mensagens_da_tela(d) or 'sem mensagem'}.")
    log("Autenticador cadastrado: o robô já gera o código sozinho.")
    return chave, qr


def navegador(logar=True):
    """O Chrome do robô, logado. Se a janela foi fechada, abre outro e faz o login (logar=False: só abre)."""
    global navegador_atual
    if navegador_atual is not None:
        try:
            if not navegador_atual.is_closed():
                navegador_atual.title()  # dá erro se o Chrome do robô foi fechado
                return navegador_atual
        except Exception:
            pass
        try:
            navegador_atual.context.close()
        except Exception:
            pass
    log("Abrindo o Chrome do robô...")
    navegador_atual = entrar(abrir_navegador()) if logar else abrir_navegador()
    return navegador_atual


def fechar_navegador():
    """Fecha o Chrome do robô (no servidor, quando a fila do escritório acaba: libera memória para os outros)."""
    global navegador_atual
    if navegador_atual is not None:
        try:
            navegador_atual.context.close()
        except Exception:  # já fechado
            pass
    navegador_atual = None


# ---------- utilitários de tela ----------

saida = lambda linha: print(linha, flush=True)  # a interface troca por uma função que escreve na tela
parar = False  # a interface liga quando clicam em Parar; o robô para na próxima ação
navegador_atual = None  # a página do Chrome do robô; navegador() abre outro se a janela for fechada


def pedir_ajuda(etapa, nome_etapa, problema, foto):
    """Chamada quando o robô não consegue seguir. A interface troca por uma função que mostra o painel de
    pausa e devolve 'repetir', 'continuar' ou 'pular'. Na linha de comando, para no primeiro problema."""
    return "pular"


def log(msg):
    saida(f"[{time.strftime('%H:%M:%S')}] {msg}")


def anotar(tipo, **campos):
    """Guarda um acontecimento (cadastro, pausa, resolvido sozinho, correção) no histórico deste escritório.
    Se não der para gravar, o cadastro segue."""
    linha = json.dumps({"tipo": tipo, "quando": time.strftime("%Y-%m-%dT%H:%M:%S"), **campos}, ensure_ascii=False)
    try:
        os.makedirs(os.path.dirname(ARQUIVO_HISTORICO), exist_ok=True)
        with open(ARQUIVO_HISTORICO, "a", encoding="utf-8") as f:
            f.write(linha + "\n")
    except OSError as e:
        log(f"  Não consegui anotar no histórico do relatório ({e}).")


def resumo(erro):
    """Mensagem curta de uma exceção (as do navegador vêm com o registro inteiro da tentativa)."""
    msg = str(erro).split("\n")[0].replace("Message:", "").strip()
    return f"{type(erro).__name__}: {msg}" if msg else type(erro).__name__


def valor(e):
    return e.evaluate("e => e.value")  # o valor de agora (o atributo value guarda só o inicial)


def destacar(d, e):
    """Rola até o elemento e contorna de vermelho: mostra na tela o que o robô vai mexer."""
    e.evaluate("e => { e.scrollIntoView({block: 'center'}); e.style.outline = '3px solid red'; }")


def descrever(d, e, seletor):
    """Nome legível do botão/campo para o log (texto, rótulo do rádio ou valor); senão o seletor."""
    if e.get_attribute("type") == "radio":
        rotulo = d.query_selector(f"label[for='{e.get_attribute('id')}']")
        return rotulo.inner_text().strip() if rotulo else seletor
    return (e.inner_text() or e.get_attribute("value") or e.get_attribute("title") or "").strip()[:60] or seletor


SEM_AJAX = """() => ['_viewRoot:status.start', 'modalStatus', 'modalStatusContainer'].every(id => {
    const e = document.getElementById(id); return !e || e.style.display == 'none'; })"""


def aguardar(d):
    """Espera o AJAX do PJe (RichFaces) terminar e faz a pausa de acompanhamento."""
    d.wait_for_timeout(500)
    esperar_ate(lambda: d.evaluate(SEM_AJAX), 60)
    if "errorUnexpected" in d.url:
        raise PJeInstavel("O PJe respondeu 'Erro inesperado'.")
    if parar:
        sys.exit("Parado pelo usuário.")
    if PAUSA_SEGUNDOS:
        d.wait_for_timeout(PAUSA_SEGUNDOS * 1000)


def visivel(d, seletor, timeout=30):
    return esperar_ate(lambda: next((e for e in d.query_selector_all(seletor) if e.is_visible()), None), timeout)


def clicar(d, seletor):
    e = visivel(d, seletor)
    log(f"    clicar: {descrever(d, e, seletor)}")
    for tentativa in range(3):
        try:
            destacar(d, e)
            d.wait_for_timeout(PAUSA_SEGUNDOS * 500)
            e.click(timeout=10000)
            break
        except ErroDoNavegador:
            if tentativa == 2:
                raise
            aguardar(d)  # tela de "carregando" na frente, ou o PJe redesenhou o botão: espera e acha de novo
            e = visivel(d, seletor)
    aguardar(d)


def parecido(texto):
    """Forma de comparar com as opções do PJe sem depender de acento, maiúscula, pontuação e 'e/de/da/do'
    ('DIREITO ADMINISTRATIVO OUTRAS MATÉRIAS DIREITO PÚBLICO' = 'Direito Administrativo e Outras Matérias de Direito Público')."""
    palavras = re.sub(r"[^A-Z0-9 ]", " ", comparavel("opção", texto)).split()
    return " ".join(p for p in palavras if p not in ("E", "DE", "DA", "DO", "DAS", "DOS"))


def com_busca(e):
    """Lista com busca (select2, ex.: Tipo da Parte desde 23/10/2026): o <select> de verdade fica escondido."""
    return "select2-hidden-accessible" in (e.get_attribute("class") or "")


def lista(d, seletor, timeout=30):
    """O <select> do seletor que está na tela: visível, ou escondido por uma lista com busca."""
    return esperar_ate(lambda: next((e for e in d.query_selector_all(seletor) if com_busca(e) or e.is_visible()), None),
                       timeout)


def opcoes_da_lista(campo):
    """[(valor, texto)] das opções do <select> (lidas na página: funciona com o <select> escondido)."""
    return [tuple(o) for o in campo.evaluate("s => Array.from(s.options).map(o => [o.value, o.textContent.trim()])")]


def escolher(d, seletor, texto):
    """Seleciona a opção de texto igual a `texto` (ou que termine com ele, ex.: '(12078)'). Se não houver igual,
    aceita a escrita parecida (sem acento, 'e/de/do'...). Sem nenhuma, pausa mostrando as opções que o PJe tem."""
    def opcao():
        opcoes = opcoes_da_lista(lista(d, seletor))
        achou = ([o for o in opcoes if o[1] == texto] or [o for o in opcoes if o[1].endswith(texto)]
                 or [o for o in opcoes if parecido(o[1]) == parecido(texto)])
        return achou[0] if achou else None
    log(f"    escolher: {texto}")
    destacar(d, lista(d, seletor))
    try:
        escolhida, rotulo = esperar_ate(opcao, 30)
    except TempoEsgotado:
        opcoes = [t for _, t in opcoes_da_lista(lista(d, seletor)) if t and "selecione" not in t.lower()]
        raise Problema(f"Opção '{texto}' não existe no PJe. Opções da lista: {' | '.join(opcoes[:15])}. "
                       "Escolha a certa na ficha do cliente e clique em Tentar de novo, ou escolha à mão no Chrome e clique em Continuar.")
    if rotulo != texto:
        log(f"    (no PJe está escrito '{rotulo}')")
    campo = lista(d, seletor)
    if com_busca(campo):  # a lista com busca só avisa o PJe pela troca via jQuery (a tela muda e o AJAX dispara)
        campo.evaluate("(s, v) => jQuery(s).val(v).trigger('change')", escolhida)
    else:
        campo.select_option(value=escolhida)
    aguardar(d)


def digitar(d, seletor, digitos):
    """Campos com máscara (CPF, CNPJ, valor): a máscara posiciona o cursor logo depois do foco e alguns
    campos disparam AJAX no foco. Então foca, espera, limpa, volta ao início e digita tecla a tecla,
    conferindo os dígitos (até 3 tentativas)."""
    log(f"    digitar: {digitos}")
    for _ in range(3):
        visivel(d, seletor).evaluate("e => { e.scrollIntoView({block: 'center'}); e.focus(); }")
        d.wait_for_timeout(500)
        aguardar(d)
        campo = visivel(d, seletor)  # o AJAX do foco pode redesenhar o campo
        destacar(d, campo)
        campo.focus()
        for tecla in ("Control+A", "Delete", "Home"):
            d.keyboard.press(tecla)
        d.keyboard.type(digitos, delay=50)
        if re.sub(r"\D", "", valor(campo)) == digitos:
            return campo
    raise PJeInstavel(f"O campo {seletor} ficou '{valor(campo)}', esperado {digitos}.")


# ---------- abas do cadastro ----------

def escolher_dados_iniciais(d, dados):
    """Tela Novo processo (vista em 04/11/2026): Matéria -> Jurisdição -> Competência -> Classe, nessa ordem, porque
    cada lista só se enche depois da escolha anterior. Não clica em Incluir (não cria rascunho)."""
    d.goto(URL_NOVO_PROCESSO)
    esperar_ate(lambda: d.query_selector("[id='form_lbl']"))  # aba "Dados iniciais"
    escolher(d, "select[id$=':areaDireitoCombo']", dados["matéria"])
    escolher(d, "select[id$=':jurisdicaoCombo']", dados["jurisdição"])
    competencia(d, dados)
    codigo = re.search(r"\((\d+)\)", dados["classe judicial"])[1]
    escolher(d, "select[id$=':classeJudicialCombo']", f"({codigo})")


def dados_iniciais(d, dados):
    escolher_dados_iniciais(d, dados)
    clicar(d, "input[id$=':incluiProcessoButton']")  # cria o rascunho
    esperar_ate(lambda: d.query_selector("[id='assunto_lbl']"), 40)


def competencia(d, dados):
    """Lista Competência (desde 23/10/2026, logo depois da jurisdição). O valor vem da leitura da inicial, do .docx
    ou da ficha; sem ele, pausa mostrando as opções do PJe. Sem a lista na tela (PJe antigo), não faz nada."""
    seletor = "select[id$=':comboConflitoCompetencia']"
    if not d.query_selector(seletor):
        return
    if dados.get("competência"):
        escolher(d, seletor, dados["competência"])
        return
    try:  # as opções chegam pelo AJAX da jurisdição
        esperar_ate(lambda: len(opcoes_da_lista(lista(d, seletor))) > 1)
    except TempoEsgotado:
        pass
    opcoes = [t for _, t in opcoes_da_lista(lista(d, seletor)) if t and "selecione" not in t.lower()]
    raise Problema(f"O robô não sabe a COMPETÊNCIA deste processo. Opções do PJe: {' | '.join(opcoes[:20])}. Escolha na "
                   "ficha do cliente e clique em Tentar de novo, ou escolha no Chrome a competência e a classe, clique em "
                   "Incluir e depois em Continuar.")


def assunto(d, dados):
    """Digita o código, pesquisa e clica em Adicionar no resultado de baixo."""
    codigo = re.match(r"\d+", dados["assunto principal"])[0]
    no_processo = f"xpath=//table[starts-with(@id,'l_processoAssuntoListList')]//td[normalize-space()='{codigo}']"
    clicar(d, "[id='assunto_lbl']")
    if d.query_selector_all(no_processo):
        log(f"  O assunto {codigo} já está no processo.")
        return
    visivel(d, "input[id$=':codAssuntoTrf']").fill(codigo)
    clicar(d, "[id='r_processoAssuntoListSearchForm:search']")
    visivel(d, f"xpath=//table[starts-with(@id,'r_processoAssuntoListList')]//tr[td[normalize-space()='{codigo}']]"
               "//a[@title='Adicionar']").click()
    aguardar(d)
    esperar_ate(lambda: d.query_selector_all(no_processo))


def linhas_da_grade(d, grid):
    return [tr.inner_text() for tr in d.query_selector_all(f"[id^='{grid}'] tr")]


def ja_vinculado(d, grid, nome):
    """Aba Partes: a parte já está na grade do polo? (para refazer a etapa sem duplicar)"""
    clicar(d, "[id='tabPartes_lbl']")
    return any(nome.upper() in t.upper() for t in linhas_da_grade(d, grid))


def abrir_modal_parte(d, polo, tipo):
    """polo 'A' (ativo) ou 'P' (passivo), com a aba Partes aberta. O clique do '+' é no td pai do botão."""
    visivel(d, f"#addParte{polo}").query_selector("xpath=..").click()
    aguardar(d)
    escolher(d, "[id^='mpAssociarParteProcesso'] select", tipo)
    try:  # o 2º passo (Física, Jurídica...) chega pelo AJAX da escolha; escolher_pessoa diz o que fazer se não vier
        esperar_ate(lambda: d.query_selector_all("input[id*=':tipoPessoa:']"))
    except TempoEsgotado:
        pass


def vincular(d, botao, grid, nome):
    clicar(d, botao)
    try:
        esperar_ate(lambda: any(nome.upper() in t.upper() for t in linhas_da_grade(d, grid)))
    except TempoEsgotado:
        msgs = " | ".join(e.inner_text() for e in d.query_selector_all(".rich-messages, .alert-danger") if e.inner_text().strip())
        raise Problema(f"Não consegui vincular {nome}: {msgs or 'o PJe não mostrou mensagem'}")


def escolher_pessoa(d, pessoa):
    """Marca o tipo de pessoa pelo rótulo: a posição da opção muda conforme o tipo da parte
    (ex.: com EXEQUENTE não aparece 'Ente ou autoridade')."""
    rotulo = PESSOAS[pessoa]
    for radio in d.query_selector_all("input[id*=':tipoPessoa:']"):
        texto = d.query_selector(f"label[for='{radio.get_attribute('id')}']")
        if texto and texto.inner_text().strip().startswith(rotulo):
            clicar(d, f"input[id='{radio.get_attribute('id')}']")
            return
    raise Problema(f"Para esse tipo da parte, o PJe não oferece '{rotulo}'. Troque o tipo de pessoa "
                   "na ficha do cliente, ou vincule à mão e clique em Continuar.")


def parte(d, dados, polo):
    """Vincula a parte do polo ('A' ativo, 'P' passivo) pelo caminho do tipo de pessoa dela."""
    grid, c_papel, c_pessoa, c_nome, c_doc = POLOS[polo]
    pessoa, nome = dados[c_pessoa], dados[c_nome]
    doc = re.sub(r"\D", "", dados.get(c_doc, "")) or CNPJ_CONHECIDO.get(nome.upper(), "")  # conferido em preparar()
    # a grade mostra "NOME - CPF: 000.000.000-00": procurar pelo documento não depende de como o nome foi escrito
    procurar = formatar_documento(doc) if doc else "MINISTÉRIO PÚBLICO" if pessoa == "MINISTÉRIO PÚBLICO" else nome
    if ja_vinculado(d, grid, procurar):
        log(f"  {nome} já está no polo {'ativo' if polo == 'A' else 'passivo'}.")
        return
    abrir_modal_parte(d, polo, dados[c_papel])
    escolher_pessoa(d, pessoa)
    if pessoa == "FÍSICA":
        pessoa_fisica(d, nome, doc, grid, *((dados.get("endereço", ""), dados.get("raça", "")) if polo == "A" else ("", "")))
    elif pessoa == "ÓRGÃO PÚBLICO":  # Jurídica + órgão público, buscado pelo CNPJ
        clicar(d, "input[id$='isOrgaoPublico1SelectOneRadio:0']")    # Órgão público? Sim
        digitar(d, "input[id$=':preCadastroPessoaFisica_cnpjPessoaJuridica']", doc)
        clicar(d, "input[id$=':btnPesquisarOrgaoPublico2']")
        clicar(d, "a[id*=':preCadastroPessoaFisica_orgaoPublicoList:0:']")
        vincular(d, "input[id$=':btnInserirOrgPub']", grid, formatar_documento(doc))
    elif pessoa == "MINISTÉRIO PÚBLICO":  # o PJe já mostra o MP-MT na lista
        clicar(d, "a[id*=':preCadastroPessoaFisica_orgaoPublicoList2:0:']")
        vincular(d, "input[id$=':btnInserirOrgPub2']", grid, "MINISTÉRIO PÚBLICO")
    elif pessoa == "JURÍDICA":
        # ponytail: pesquisa e para; o resto do caminho (empresa que não é órgão público) ainda não foi visto no PJe
        clicar(d, "input[id$='isOrgaoPublico1SelectOneRadio:1']")    # Órgão público? Não
        digitar(d, "input[id$=':preCadastroPessoaFisica_nrCPJ']", doc)
        clicar(d, "input[id$=':pesquisarDocumentoPrincipal']")
        raise Problema(f"Empresa pesquisada pelo CNPJ {doc}. Confira os dados e termine o vínculo de '{nome}' à mão "
                       "(pessoa jurídica que não é órgão público ainda não foi testada até o fim). Depois clique em Continuar.")
    else:  # ENTE OU AUTORIDADE: a busca pelo nome pode trazer muitas páginas; quem escolhe é a pessoa
        visivel(d, "input[id$=':preCadastroPessoaFisica_nomeEnte']").fill(nome)
        clicar(d, "input[id$=':btnPesquisarEnte']")
        raise Problema(f"Ente ou autoridade pesquisado pelo nome '{nome}'. Escolha o certo na lista, clique em "
                       "Inserir e depois em Continuar.")


def pessoa_fisica(d, nome, cpf, grid, endereco="", raca=""):
    """Pessoa física pelo CPF: confere o nome da Receita, endereço e Raça/Cor (a da ficha; vazia, a padrão do
    escritório), e vincula."""
    digitar(d, "input[id$=':preCadastroPessoaFisica_nrCPF']", cpf)
    clicar(d, "input[id$=':pesquisarDocumentoPrincipal']")
    nome_receita = valor(visivel(d, "input[id$=':dsNomeCivil']"))
    if comparavel("nome completo", nome_receita) != comparavel("nome completo", nome):  # sem acento e sem espaços
        raise Problema(f"O CPF {cpf} é de '{nome_receita}', mas o documento diz '{nome}'. Parei sem vincular.")
    nome = nome_receita.strip()  # daqui em diante, o nome como a Receita escreve
    clicar(d, "input[id$=':btnConfirmarCadastro']")
    # 2º passo: o tipo aparece preenchido, mas o servidor só registra com um 'change'. Isso zera a Raça/Cor,
    # então tem que vir antes dela.
    lista(d, "select[id$=':tpParteAlteracao']").evaluate("e => e.dispatchEvent(new Event('change'))")
    aguardar(d)
    conferir_endereco(d, nome, endereco)
    clicar(d, "[id='formInserirParteProcesso:informacoesPessoaisRichTab_lbl']")
    escolher(d, "select[id$=':raca']", raca or RACA_PADRAO)
    avisos.append(f"Raça/Cor de {nome} marcada como '{raca or RACA_PADRAO}'"
                  f"{'' if raca else ' (padrão do escritório)'}; profissão não preenchida.")
    vincular(d, "input[id$=':btnInserirParteProcesso']", grid, formatar_documento(cpf))


def enderecos_da_parte(d):
    return [" ".join(tr.inner_text().split()) for tr in
            d.query_selector_all("table[id*='enderecoGridTabList'] tbody tr") if tr.inner_text().strip()]


def conferir_endereco(d, nome, endereco=""):
    """Aba ENDEREÇOS da parte: precisa ter endereço cadastrado. Usa o que já está lá
    (não clicar em 'Usar no processo': já vem marcado e clicar desmarca/dá erro). Sem nenhum, cadastra o da inicial."""
    clicar(d, "[id='formInserirParteProcesso:enderecoUsuario_lbl']")
    enderecos = enderecos_da_parte(d)
    if not enderecos:
        cadastrar_endereco(d, nome, endereco)
        enderecos = enderecos_da_parte(d)
    log(f"  Endereço(s) de {nome}: {' / '.join(enderecos)}")
    avisos.append(f"Endereço usado para {nome}: {enderecos[0]} (confira se está atualizado).")


def cadastrar_endereco(d, nome, endereco):
    """Aba ENDEREÇOS > Adicionar endereço: digita o CEP, escolhe a sugestão (o PJe preenche Estado/Cidade e,
    em CEP de rua, logradouro e bairro), completa o que ficou vazio com a inicial e clica Incluir."""
    partes = partes_do_endereco(endereco)
    if not partes:
        raise Problema(f"{nome} não tem endereço cadastrado no PJe e não achei o endereço na inicial. Cadastre o "
                       "endereço da parte no Chrome (ou preencha na ficha do cliente) e clique em Tentar de novo.")
    log(f"  {nome} sem endereço no PJe: cadastrando o da inicial ({endereco}).")
    cep = visivel(d, "input[id$=':cadastroPartePessoaEnderecoCEP']")
    destacar(d, cep)
    cep.fill("")
    cep.focus()
    d.keyboard.type(partes["cep"], delay=100)  # a sugestão só abre com os 9 caracteres (00000-000), tecla a tecla
    try:
        sugestao = esperar_ate(lambda: next((tr for tr in d.query_selector_all("table[id$=':suggest'] tr")
                                             if tr.is_visible() and tr.inner_text().strip()), None), 20)
    except TempoEsgotado:
        raise Problema(f"O PJe não achou o CEP {partes['cep']} de {nome}. Confira o CEP, cadastre o endereço no Chrome "
                       "e clique em Tentar de novo.")
    log(f"    escolher: {' '.join(sugestao.inner_text().split())}")
    sugestao.click()
    aguardar(d)
    # o Número só libera depois que o PJe processa o CEP: ele é o sinal de que dá para preencher
    esperar_ate(lambda: any(x.is_enabled() for x in d.query_selector_all(
        "input[id$=':cadastroPartePessoaEndereconumeroEndereco']")), 20)
    for campo in ("Bairro", "Logradouro", "numeroEndereco", "complemento"):
        seletor = f"input[id$=':cadastroPartePessoaEndereco{'' if campo[0].islower() else 'nome'}{campo}']"
        texto = partes[{"numeroEndereco": "numero"}.get(campo, campo.lower())]
        e = visivel(d, seletor)
        # CEP de rua: o PJe preenche e TRAVA bairro e logradouro (cinza). Campo travado ou já preenchido fica como está.
        if texto and e.is_enabled() and not valor(e).strip():
            destacar(d, e)
            log(f"    digitar: {texto}")
            e.fill(texto)
        elif valor(e).strip():
            log(f"    {campo}: '{valor(e).strip()}' (preenchido pelo PJe)")
    clicar(d, "input[id$=':cadastroPartePessoaEnderecobtnGravarEndereco']")
    try:
        esperar_ate(lambda: enderecos_da_parte(d))
    except TempoEsgotado:
        msgs = " | ".join(e.inner_text() for e in d.query_selector_all(".rich-messages, .alert-danger") if e.inner_text().strip())
        raise Problema(f"O PJe não incluiu o endereço de {nome}: {msgs or 'sem mensagem'}. Cadastre no Chrome e "
                       "clique em Tentar de novo.")
    avisos.append(f"Endereço de {nome} cadastrado no PJe a partir da inicial: {endereco}.")


def caracteristicas(d, dados):
    sim = lambda chave: dados[chave].strip().upper().startswith("S")
    clicar(d, "[id='processoPrioridadeProcesso_lbl']")
    clicar(d, f"input[id$='justicaGratuitaSelectOneRadio:{0 if sim('justiça gratuita') else 1}']")
    clicar(d, f"input[id$='tutelaLiminarSelectOneRadio:{0 if sim('pedido de liminar ou antecipação de tutela') else 1}']")

    digitar(d, "input[id$=':valorCausa']", re.sub(r"\D", "", dados["valor da causa"]))  # já conferido em completar_com_inicial()
    clicar(d, "input[id$=':salvaCaracteristicaProcessoButton']")  # sem Salvar a seção não grava

    if sim("segredo de justiça"):
        clicar(d, "input[id='frmSegredoSig:selectOneRadio:0']")
        clicar(d, "input[id='frmSegredoSig:grvSegredo']")

    prioridade = dados.get("prioridade de processo", "")
    opcoes = [t for _, t in opcoes_da_lista(lista(d, "select[id$=':prioridadeProcesso']"))]
    match = next((o for o in opcoes if o.lower() == prioridade.lower()), None)
    if match:
        ja = " ".join(tr.inner_text() for tr in d.query_selector_all("[id^='processoPrioridadeProcessoAbaList'] tbody tr"))
        if match.lower() in ja.lower():
            log(f"  Prioridade '{match}' já está no processo.")
        else:
            escolher(d, "select[id$=':prioridadeProcesso']", match)
            clicar(d, "input[id='formAddPrioridadeProcesso:save']")
    else:
        avisos.append(f"Prioridade não definida (documento diz: '{prioridade}').")


def tipo_do_anexo(i):
    """Tipo no PJe: o 1º anexo (DOC 01, documentos pessoais) e os demais, conforme TIPOS_ANEXO do escritório."""
    return TIPOS_ANEXO[0] if i == 0 else TIPOS_ANEXO[1]


def documentos_no_rascunho(d):
    """Quantos documentos o rascunho já tem. Lê a aba 'Protocolar inicial', que só mostra (o robô não clica em nada nela)."""
    clicar(d, "[id='informativo_lbl']")
    esperar_ate(lambda: d.query_selector_all("xpath=//*[normalize-space()='Detalhes do processo']"))
    total = d.query_selector_all("xpath=//table[@id='processoDocumentoGridList']"
                                 "/following-sibling::span[contains(., 'resultados encontrados')]")
    return int(re.match(r"\d+", total[0].inner_text().strip())[0]) if total else 0


def assinar_liberado(d):
    """Enquanto falta classificar, o botão diz 'Aguardando a classificação dos documentos'."""
    return valor(visivel(d, "#btn-assinador")) == "Assinar documento(s)"


def documentos(d, inicial, anexos, refazendo=False):
    """Sobe a inicial (tipo 'Petição Inicial', já vem marcado) e depois os anexos, um de cada vez.
    Ao refazer a etapa, confere antes quantos documentos o rascunho já tem (não reenvia)."""
    if refazendo:
        ja, total = documentos_no_rascunho(d), 1 + len(anexos)
        if ja == total:
            log("  A inicial e os anexos já estão no rascunho.")
            return
        if ja:
            continuar_documentos(d, anexos, ja, total)
            return
    descricao = os.path.splitext(os.path.basename(inicial))[0]  # exatamente como a inicial está nomeada na pasta

    def preencher_descricao():
        campo = visivel(d, "input[id='ipDescDecoration:ipDesc']")
        if valor(campo) != descricao:
            campo.fill(descricao)
            campo.evaluate("e => e.blur()")
            aguardar(d)

    clicar(d, "[id='novoAnexo_lbl']")
    preencher_descricao()
    d.query_selector("input[id$=':uploadDocumentoPrincipal:file']").set_input_files(inicial)
    # o campo de anexos só aparece depois que a inicial sobe
    esperar_ate(lambda: d.query_selector_all("input.dz-hidden-input"), 120)
    aguardar(d)
    preencher_descricao()  # o envio pode voltar o campo para "Petição Inicial"
    log(f"Inicial enviada ({descricao}). Enviando anexos...")
    for i, anexo in enumerate(anexos):
        adicionar_anexo(d, i, anexo)
        log(f"  ({i + 1}/{len(anexos)}) {os.path.basename(anexo)} -> {tipo_do_anexo(i)}")
    esperar_ate(lambda: assinar_liberado(d))


def continuar_documentos(d, anexos, ja, total):
    """Retoma o envio de onde parou. Os documentos sobem na ordem (inicial, DOC 01, DOC 02...), então com `ja`
    no rascunho faltam os anexos a partir do DOC `ja`. Volta à aba Incluir petições e manda só esses."""
    faltam = anexos[ja - 1:]
    nomes = "; ".join(os.path.basename(a) for a in faltam)
    log(f"  O rascunho já tem {ja} de {total} documentos. Continuando do {os.path.basename(faltam[0])}...")
    clicar(d, "[id='novoAnexo_lbl']")
    try:
        esperar_ate(lambda: d.query_selector_all("input.dz-hidden-input"))
    except TempoEsgotado:
        # ponytail: a aba depois da inicial salva ainda não foi vista pelo robô; a foto/HTML da pausa mostra como ela é
        raise Problema(f"Não achei onde adicionar anexos na aba Incluir petições. Faltam {len(faltam)}: {nomes}. "
                       "Envie à mão (tipo 'Documento de comprovação') e clique em Continuar.")
    base = len(d.query_selector_all(".dz-preview"))  # o que a lista já mostra fica antes dos novos
    for k, anexo in enumerate(faltam):
        adicionar_anexo(d, ja - 1 + k, anexo, linha=base + k)
        log(f"  ({ja + k}/{len(anexos)}) {os.path.basename(anexo)} -> {tipo_do_anexo(ja - 1 + k)}")
    try:
        esperar_ate(lambda: assinar_liberado(d))
    except TempoEsgotado:
        pass  # quem confirma é a contagem abaixo
    agora = documentos_no_rascunho(d)
    if agora != total:
        raise Problema(f"Depois de continuar o envio, o rascunho tem {agora} de {total} documentos. Confira na aba "
                       "Protocolar inicial, envie o que faltar à mão e clique em Continuar.")


CLASSIFICAR_ANEXO = """([linha, tipo]) => {
    const l = document.querySelectorAll('.dz-preview')[linha];
    const s = l && l.querySelector("select[id$=':tipoDoc']");
    if (!s) return false;
    const o = Array.from(s.options).find(o => o.textContent.trim() == tipo);
    if (!o) return 'sem opção';
    l.scrollIntoView({block: 'center'}); l.style.outline = '3px solid red';
    jQuery(s).val(o.value).trigger('change');
    return true;
}"""


def adicionar_anexo(d, i, anexo, linha=None):
    """Um anexo: escolhe o arquivo, classifica e espera o PJe receber. O PJe só sobe o arquivo
    DEPOIS de classificado, então esperar o upload antes de escolher o tipo trava.
    `i` = posição do anexo (define o tipo); `linha` = posição dele na lista da tela (muda ao continuar um envio)."""
    linha = i if linha is None else linha
    # O arquivo vai direto no campo escondido do Dropzone (o botão ADICIONAR abre a janela do Windows, que o robô
    # não opera). Rebusca: o Dropzone troca o campo a cada arquivo.
    d.query_selector_all("input.dz-hidden-input")[0].set_input_files(anexo)
    esperar_ate(lambda: len(d.query_selector_all(".dz-preview")) > linha, 60)

    log(f"    classificar: {os.path.basename(anexo)} -> {tipo_do_anexo(i)}")
    d.wait_for_timeout(PAUSA_SEGUNDOS * 500)
    # O Dropzone redesenha a lista enquanto sobe/classifica: a linha é achada pelo número DENTRO da página, na hora.
    # O select é select2 (escondido): troca pelo jQuery para disparar o AJAX do PJe.
    achou = esperar_ate(lambda: d.evaluate(CLASSIFICAR_ANEXO, [linha, tipo_do_anexo(i)]))
    if achou == "sem opção":
        raise Problema(f"O PJe não oferece o tipo '{tipo_do_anexo(i)}' para {os.path.basename(anexo)}.")

    def enviado():
        classe = d.evaluate("linha => { const l = document.querySelectorAll('.dz-preview')[linha]; return l ? l.className : ''; }", linha)
        return "erro" if "dz-error" in classe else "dz-success" in classe
    if esperar_ate(enviado, 300) == "erro":
        raise Problema(f"O PJe recusou {os.path.basename(anexo)}: {d.query_selector_all('.dz-preview')[linha].inner_text()}")
    aguardar(d)


# ---------- imprevistos: recuperar, guardar a tela, refazer a etapa ----------

def esperar(segundos):
    """time.sleep que atende o Parar."""
    for _ in range(segundos):
        if parar:
            sys.exit("Parado pelo usuário.")
        time.sleep(1)


def pje_fora(d):
    """A página é de erro do PJe ou do servidor (fora do ar, gateway)?"""
    try:
        texto = d.inner_text("body", timeout=5000)[:3000].lower()
        return "errorUnexpected" in d.url or any(s in texto for s in SINAIS_PJE_FORA)
    except Exception:
        return True


def recuperar(id_rascunho):
    """Antes de refazer uma etapa: garante o Chrome aberto e logado e reabre o rascunho (alertas do PJe já são
    aceitos sozinhos, ver abrir_navegador)."""
    d = navegador()
    if pje_fora(d):
        log(f"  O PJe não respondeu direito. Esperando {ESPERA_PJE_FORA} s...")
        esperar(ESPERA_PJE_FORA)
    entrar(d)  # se a sessão caiu, faz o login; se não, só confere o painel
    if id_rascunho:
        d.goto(URL_RASCUNHO.format(id_rascunho))
        esperar_ate(lambda: d.query_selector("[id='tabPartes_lbl']"))
        aguardar(d)


def guardar_tela(nome_cliente, etapa):
    """Foto e HTML da página em erros/, para ver depois o que o PJe mostrou. Devolve o caminho da foto ('' se não deu)."""
    base = os.path.join(DADOS, "erros", f"{time.strftime('%Y-%m-%d %H%M%S')} - {nome_cliente} - etapa {etapa}")
    try:
        os.makedirs(os.path.dirname(base), exist_ok=True)
        navegador_atual.screenshot(path=base + ".png")
        with open(base + ".html", "w", encoding="utf-8") as f:
            f.write(navegador_atual.content())
        return base + ".png"
    except Exception:  # Chrome fechado: fica sem foto
        return ""


def id_do_rascunho(d):
    try:
        m = re.search(r"idProcesso=(\d+)", d.url)
    except Exception:
        return None
    return m[1] if m else None


ETAPAS = [  # (nome, função(driver, cliente, refazendo))
    ("Dados iniciais", lambda d, c, r: dados_iniciais(d, c["dados"])),
    ("Assuntos", lambda d, c, r: assunto(d, c["dados"])),
    ("Partes - polo ativo", lambda d, c, r: parte(d, c["dados"], "A")),
    ("Partes - polo passivo", lambda d, c, r: parte(d, c["dados"], "P")),
    ("Características", lambda d, c, r: caracteristicas(d, c["dados"])),
    ("Petições e documentos", lambda d, c, r: documentos(d, c["inicial"], c["anexos"], r)),
]


ARQUIVO_RASCUNHO = "rascunho.json"  # na pasta do cliente: o rascunho que o robô já criou no PJe


def rascunho_salvo(pasta):
    """idProcesso do rascunho que o robô já criou para o cliente (None se ainda não criou)."""
    try:
        with open(os.path.join(pasta, ARQUIVO_RASCUNHO), encoding="utf-8") as f:
            return str(json.load(f)["idProcesso"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def salvar_rascunho(pasta, id_rascunho):
    """Guarda o número do rascunho na pasta: se o cadastro parar (Pular, Chrome fechado, programa reiniciado),
    o próximo continua dele em vez de criar outro."""
    try:
        with open(os.path.join(pasta, ARQUIVO_RASCUNHO), "w", encoding="utf-8") as f:
            json.dump({"idProcesso": id_rascunho, "criado_em": time.strftime("%Y-%m-%d %H:%M")}, f)
    except OSError as e:
        log(f"  Não consegui guardar o número do rascunho na pasta ({e}). Se o cadastro parar, o próximo cria outro.")


def continuar_rascunho(pasta):
    """Abre o rascunho salvo do cliente. Devolve o id, ou None se não há ou se o PJe não abre mais (excluído ou
    protocolado: aí cria outro). Outra falha (PJe fora...) para o cadastro, para não criar rascunho repetido."""
    id_rascunho = rascunho_salvo(pasta)
    if not id_rascunho:
        return None
    try:
        recuperar(id_rascunho)
    except Exception as e:
        try:
            sumiu = "errorUnexpected" in navegador_atual.url
        except Exception:
            sumiu = False
        if not sumiu:
            raise
        log(f"  O rascunho {id_rascunho} não abre mais no PJe (excluído ou protocolado): vou criar outro.")
        return None
    log(f"Continuando o rascunho que o robô já tinha criado: {URL_RASCUNHO.format(id_rascunho)}")
    return id_rascunho


def cadastrar(cliente):
    """Cadastra um cliente já conferido por preparar(), etapa por etapa. Falha técnica: recupera e refaz a
    etapa (até TENTATIVAS_AUTOMATICAS vezes). O que não resolve: pedir_ajuda(). Devolve as pendências.
    Se o cliente já tem rascunho (rascunho.json), continua dele: as etapas 2 a 6 conferem o que já está feito."""
    avisos.clear()
    dados_, origem_ = cliente["dados"], cliente["origem"]
    da_inicial = [f"{c} ({dados_[c]})" for c, o in origem_.items() if o == "inicial"]
    por_regra = [f"{c} ({dados_[c]})" for c, o in origem_.items() if o == "regra"]
    if da_inicial:
        avisos.append(f"Tirado da inicial, confira: {'; '.join(da_inicial)}.")
    if por_regra:
        avisos.append(f"Identificado por regra (não está escrito na inicial), confira: {'; '.join(por_regra)}.")
    pasta_nome = os.path.basename(cliente["pasta"])
    id_rascunho = continuar_rascunho(cliente["pasta"])
    continuando = bool(id_rascunho)  # rascunho de antes: toda etapa confere o que já existe (documentos não sobem 2x)
    recarregar = False
    for n, (nome, etapa) in enumerate(ETAPAS, 1):
        log(f"== Etapa {n}/{len(ETAPAS)}: {nome} ==")
        if n == 1 and id_rascunho:
            log("Etapa 1/6 já feita: o rascunho existe.")
            continue
        falhas = 0
        while True:
            try:
                if recarregar:
                    recuperar(id_rascunho)
                etapa(navegador(), cliente, recarregar or continuando)
                recarregar = False
                if falhas:  # falhou, refez e passou: engasgo que o robô resolveu sozinho
                    anotar("sozinho", cliente=pasta_nome, etapa=nome, problema=problema, vezes=falhas)
                break
            except Problema as e:
                problema = str(e)
            except Exception as e:  # técnico: tempo esgotado, elemento sumiu, PJe fora, Chrome fechado...
                id_rascunho = id_rascunho or id_do_rascunho(navegador_atual)
                recarregar = True
                if n == 1 and id_rascunho:  # caiu depois de criar o rascunho: a etapa 1 já está feita
                    break
                falhas += 1
                problema = resumo(e)
                if falhas <= TENTATIVAS_AUTOMATICAS:
                    log(f"  Falhou ({problema}). Tentando de novo ({falhas}/{TENTATIVAS_AUTOMATICAS})...")
                    continue
            log(f"  PAUSADO: {problema}")
            foto = guardar_tela(pasta_nome, n)
            resposta = pedir_ajuda(n, nome, problema, foto)
            anotar("pausa", cliente=pasta_nome, etapa=nome, problema=problema, foto=os.path.basename(foto or ""),
                   resposta="parar" if parar else resposta)
            recarregar = True
            if resposta == "pular":
                raise Pulado(problema)
            if resposta == "continuar":
                avisos.append(f"Etapa {n} ({nome}) feita à mão: confira.")
                break
            falhas = 0  # "repetir"
        if n == 1:
            id_rascunho = id_rascunho or id_do_rascunho(navegador_atual)
            if not id_rascunho:
                sys.exit("Não achei o número do rascunho (idProcesso) na página depois dos Dados iniciais.")
            log(f"Rascunho criado: {URL_RASCUNHO.format(id_rascunho)}")
            salvar_rascunho(cliente["pasta"], id_rascunho)
        log(f"Etapa {n}/{len(ETAPAS)} concluída.")

    avisos.append("Documentos enviados mas NÃO assinados: confira e clique em 'Assinar documento(s)' antes de protocolar.")
    pendencias = list(dict.fromkeys(avisos))  # etapa refeita pode repetir aviso
    log("Pronto. O processo NÃO foi assinado nem protocolado. Pendências:")
    for a in pendencias:
        log(f"  - {a}")
    return pendencias


if __name__ == "__main__":
    if len(sys.argv) > 1:
        pasta = sys.argv[1]
    else:
        pastas = pastas_de_clientes(os.path.dirname(PASTA))
        if not pastas:
            sys.exit("Nenhuma pasta de cliente encontrada. Uso: python novo_processo.py \"pasta da cliente\"")
        pasta = max(pastas, key=os.path.getmtime)

    cliente = preparar(pasta)
    log(f"Pasta: {os.path.basename(pasta)}")
    for chave in CAMPOS_OBRIGATORIOS:
        log(f"  {chave}: {cliente['dados'][chave]}  ({cliente['origem'][chave]})")
    log(f"  inicial: {os.path.basename(cliente['inicial'])}")
    for k, a in enumerate(cliente["anexos"]):
        log(f"  anexo: {os.path.basename(a)} -> {tipo_do_anexo(k)}")

    navegador()
    cadastrar(cliente)
