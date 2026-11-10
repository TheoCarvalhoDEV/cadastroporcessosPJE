"""Interface web do robô de cadastro no PJe (substitui a janela interface_cadastro.py).
Abre no navegador em http://127.0.0.1:8765 e só aceita conexões deste computador (a senha do PJe fica aqui).
Cada escritório se configura na aba Escritório: grava escritorio.json (regras, modelo, anexos, identidade)
e config.json (login do PJe). As opções do PJe vêm do mapa_pje.json (aba Opções do PJe > Ler do PJe).
Uso: python app_web.py
"""
import datetime
import gzip
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import time
import unicodedata
import webbrowser
from collections import Counter
from urllib.parse import urlparse
from flask import Flask, jsonify, request, send_file, send_from_directory, abort, Response
import novo_processo as robo
import mapear_pje

PORTA = int(os.environ.get("ROBO_PORTA", 8765))  # no servidor, cada escritório tem a sua (servidor/robo@.service)
os.makedirs(robo.DADOS, exist_ok=True)  # cópia nova (GitHub) vem sem dados_do_escritorio
PAGINA = os.path.join(robo.SISTEMA, "web", "index.html")
COR_PADRAO = "#2454B4"

CAMPOS = [  # (chave do robô, nome na tela) - mesma ordem do Corrigir dados da janela antiga
    ("matéria", "Matéria"), ("jurisdição", "Jurisdição"), ("competência", "Competência"),
    ("classe judicial", "Classe judicial"), ("assunto principal", "Assunto"),
    ("tipo ativo", "Polo ativo: tipo da parte"), ("pessoa ativo", "Polo ativo: tipo de pessoa"),
    ("nome completo", "Polo ativo: nome"), ("cpf", "Polo ativo: CPF/CNPJ"), ("endereço", "Polo ativo: endereço"), ("raça", "Polo ativo: raça/cor"),
    ("tipo passivo", "Polo passivo: tipo da parte"), ("pessoa passivo", "Polo passivo: tipo de pessoa"),
    ("entidade", "Polo passivo: nome"), ("cnpj", "Polo passivo: CPF/CNPJ"),
    ("justiça gratuita", "Justiça gratuita"), ("pedido de liminar ou antecipação de tutela", "Liminar ou tutela"),
    ("valor da causa", "Valor da causa (R$)"), ("segredo de justiça", "Segredo de justiça"),
    ("prioridade de processo", "Prioridade"),
]
NOMES = dict(CAMPOS)

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # envio de pastas de clientes (servidor)
app.config["MAX_FORM_PARTS"] = 20000  # cada arquivo enviado são 2 partes (arquivo e caminho); o padrão, 1000, é pouco
# ponytail: estado em memória de um único usuário local; servidor central pediria sessão por escritório
estado = {"tarefa": "", "atual": "", "log": [], "pausa": None, "resultados": {}, "autenticador": None, "teste_login": None}
respostas = queue.Queue()
trava = threading.Lock()
escritorio = robo.carregar_escritorio()

robo.saida = estado["log"].append  # o Console da página lê daqui


def pedir_ajuda(etapa, nome, problema, foto):
    """(thread do robô) Mostra a pausa na página e espera a resposta: 'repetir', 'continuar' ou 'pular'."""
    # o rascunho do cliente, para a pessoa resolver no PJe dela (no servidor ninguém mexe no Chrome do robô)
    id_rascunho = robo.rascunho_salvo(os.path.join(raiz(), estado["atual"])) if estado["atual"] else None
    estado["pausa"] = {"etapa": etapa, "nome": nome, "problema": problema, "foto": os.path.basename(foto or ""),
                       "rascunho": robo.URL_RASCUNHO.format(id_rascunho) if id_rascunho else ""}
    try:
        return respostas.get()
    finally:
        estado["pausa"] = None


robo.pedir_ajuda = pedir_ajuda


def raiz():
    """Pasta onde ficam as pastas de clientes. No servidor é fixa, dentro dos dados do escritório (a página não
    escolhe: senão um escritório apontaria para a pasta de outro). No modo local, a escolhida em Configurações
    (padrão: a que contém a pasta do robô)."""
    if robo.SERVIDOR:
        pasta = os.path.join(robo.DADOS, "clientes")
        os.makedirs(pasta, exist_ok=True)
        return pasta
    return escritorio.get("pasta_clientes") or os.path.dirname(robo.PASTA)


def pasta_do_cliente(nome):
    """Só aceita nome de pasta de cliente que está na lista (nada de caminho vindo da página)."""
    pasta = os.path.join(raiz(), nome)
    if pasta not in robo.pastas_de_clientes(raiz()):
        abort(404, "Pasta de cliente não encontrada. Clique em Atualizar a lista.")
    return pasta


def nome_livre(pasta_mae, nome):
    """O nome, ou 'nome (2)', 'nome (3)'... se já existir em pasta_mae."""
    livre, n = nome, 2
    while os.path.exists(os.path.join(pasta_mae, livre)):
        livre, n = f"{nome} ({n})", n + 1
    return livre


def mover_para(pasta, subpasta):
    """Move a pasta do cliente para 'Concluído' ou 'Lixeira' ao lado dela; nome repetido ganha ' (2)', ' (3)'...
    A data da pasta passa a ser a da mudança: é dela que contam os 30 dias da limpeza no servidor."""
    mae = os.path.join(os.path.dirname(pasta), subpasta)
    os.makedirs(mae, exist_ok=True)
    destino = os.path.join(mae, nome_livre(mae, os.path.basename(pasta)))
    shutil.move(pasta, destino)
    os.utime(destino)
    return destino


def ler_json(caminho):
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def gravar_json(caminho, dados):
    with open(caminho + ".tmp", "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(caminho + ".tmp", caminho)


tarefas = queue.Queue()


def trabalhador():
    """A única linha de trabalho do robô: o Playwright só aceita o Chrome na linha que o abriu, então cadastrar e
    ler o PJe rodam sempre aqui, uma tarefa por vez (as duas usam o mesmo Chrome)."""
    while True:
        alvo, args = tarefas.get()
        try:
            alvo(*args)
        except SystemExit as e:
            robo.log(f"Parou: {e}")
            anotar_parada(str(e))
        except Exception as e:
            robo.log(f"ERRO: {robo.resumo(e)}")
            anotar_parada(robo.resumo(e))
        finally:
            estado["tarefa"], estado["atual"], robo.parar = "", "", False
            if robo.SERVIDOR and tarefas.empty():  # libera a memória do Chrome para os outros escritórios
                robo.fechar_navegador()


threading.Thread(target=trabalhador, daemon=True).start()


def em_tarefa(tipo, alvo, *args):
    """Manda a tarefa para a linha de trabalho do robô."""
    with trava:
        if estado["tarefa"]:
            abort(409, "O robô já está trabalhando. Espere terminar ou clique em Parar.")
        estado["tarefa"] = tipo
    robo.parar = False
    tarefas.put((alvo, args))
    return jsonify(ok=True)


def abrir_pje():
    try:
        return robo.entrar(robo.navegador())
    except SystemExit:
        raise
    except Exception as e:
        raise SystemExit(f"Não consegui abrir o Chrome/PJe ({robo.resumo(e)}). Se houver um Chrome do robô aberto de "
                         "antes, feche-o e tente de novo.")


def anotar_parada(msg):
    """O robô parou fora de um cliente (login, Chrome, autenticador, leitura do PJe): vai para o histórico, com a
    foto da tela se o Chrome estiver aberto, para a aba Erros do administrador. Clicar em Parar não é erro."""
    if robo.parar:
        return
    foto = robo.guardar_tela(estado["atual"] or "robô", estado["tarefa"] or "tarefa")
    robo.anotar("parada", tarefa=estado["tarefa"], cliente=estado["atual"], problema=msg, foto=os.path.basename(foto or ""))


def anotar_cadastro(nome, inicio, resultado, msg=""):
    """Para o relatório: resultado = pronto, pulado, erro ou parado (clicaram em Parar)."""
    robo.anotar("cadastro", cliente=nome, inicio=time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(inicio)),
                segundos=round(time.time() - inicio), resultado=resultado, msg=msg)


def cadastrar(nomes):
    """(thread) Login uma vez e cadastra os clientes, um depois do outro. Sem erro: pasta vai para Concluído."""
    abrir_pje()
    for nome in nomes:
        if robo.parar:
            break
        estado["atual"] = nome
        robo.log(f"===== Cliente: {nome} =====")
        inicio = time.time()
        try:
            pendencias = robo.cadastrar(robo.preparar(os.path.join(raiz(), nome)))
        except SystemExit as e:
            estado["resultados"][nome] = {"situacao": "erro", "msg": str(e)}
            anotar_cadastro(nome, inicio, "parado" if robo.parar else "pulado" if isinstance(e, robo.Pulado) else "erro", str(e))
            continue
        except Exception as e:
            estado["resultados"][nome] = {"situacao": "erro", "msg": robo.resumo(e)}
            anotar_cadastro(nome, inicio, "erro", robo.resumo(e))
            continue
        try:
            destino = mover_para(os.path.join(raiz(), nome), "Concluído")
            msg = f"Pasta movida para Concluído\\{os.path.basename(destino)}"
        except OSError as e:
            msg = f"NÃO consegui mover a pasta: {e}"
        estado["resultados"][nome] = {"situacao": "concluido", "msg": msg, "pendencias": pendencias}
        anotar_cadastro(nome, inicio, "pronto")
    robo.log("Fila terminada.")


links = queue.Queue()  # o link do e-mail do PJe, colado em Configurações > Login do PJe


def pedir_link(pediu):
    """(thread do robô) Mostra o campo do link na página e espera colarem. Vazio = cancelado (Parar)."""
    estado["autenticador"] = {"etapa": "link", "pediu": pediu}
    link = links.get()
    if not link:
        raise SystemExit("Configuração do código cancelada.")
    estado["autenticador"] = {"etapa": "entrando"}
    return link


def configurar_codigo():
    """(thread) O robô se cadastra como autenticador do PJe e guarda a chave no config.json."""
    while not links.empty():  # link de uma tentativa anterior
        links.get_nowait()
    estado["autenticador"] = {"etapa": "entrando"}
    pronto = {}

    def guardar(chave, qr):  # antes de concluir no PJe: se algo cair depois, a chave já está aqui
        config = robo.ler_config()
        config["totp_key"], config["totp_qr"] = chave, qr  # o QR fica para outros celulares lerem depois
        robo.gravar_config(config)
        pronto.update(chave=" ".join(re.findall(".{1,4}", chave)), qr=qr)
    try:
        robo.configurar_codigo(robo.navegador(logar=False), pedir_link, guardar)
    except BaseException as e:
        estado["autenticador"] = {"etapa": "erro", "msg": str(e) if isinstance(e, SystemExit) else robo.resumo(e)}
        raise
    estado["autenticador"] = {"etapa": "pronto", **pronto}


def mapear(rascunho, do_zero):
    """(thread) Lê as opções do PJe para o mapa_pje.json, sem a pausa de acompanhamento."""
    pausa, robo.PAUSA_SEGUNDOS = robo.PAUSA_SEGUNDOS, 0
    try:
        d = abrir_pje()
        if rascunho:
            mapear_pje.mapear_rascunho(d, rascunho)
        mapear_pje.mapear(d, do_zero=do_zero)
    finally:
        robo.PAUSA_SEGUNDOS = pausa
        robo._mapa = None  # a leitura das iniciais passa a usar as opções novas


# ---------- páginas e API ----------

@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(409)
def erro(e):
    return jsonify(erro=e.description), e.code


@app.errorhandler(413)
def grande_demais(e):
    return jsonify(erro="Envio grande demais: o limite é 200 MB por vez. Envie menos pastas de cada vez."), 413


@app.get("/")
def pagina():
    r = send_file(PAGINA)
    r.headers["Cache-Control"] = "no-cache"  # a tela muda quando o programa é atualizado
    return r


# fontes da tela guardadas no programa: funciona sem internet e não espera o Google Fonts
@app.get("/fontes/<path:nome>")
def fonte(nome):
    if not nome.endswith(".woff2"):
        abort(404)
    return send_from_directory(os.path.join(os.path.dirname(PAGINA), "fontes"), nome, max_age=31536000)


@app.get("/api/estado")
def ver_estado():
    desde = request.args.get("desde", 0, type=int)
    return jsonify(tarefa=estado["tarefa"], atual=estado["atual"], pausa=estado["pausa"],
                   resultados=estado["resultados"], log=estado["log"][desde:], total=len(estado["log"]),
                   etapas=[n for n, _ in robo.ETAPAS], autenticador=estado["autenticador"],
                   teste_login=estado["teste_login"])


@app.get("/api/escritorio")
def ver_escritorio():
    pje = robo.ler_config()
    return jsonify(servidor=robo.SERVIDOR,
        nome=escritorio.get("nome", ""), cor=escritorio.get("cor", COR_PADRAO), pasta_clientes=raiz(),
        assistente_feito=bool(escritorio.get("assistente_feito")),  # o primeiro acesso (assistente da página) terminou
        pausa_segundos=robo.PAUSA_SEGUNDOS, raca_padrao=robo.RACA_PADRAO, prefixo_anexo=robo.PREFIXO_ANEXO,
        anexo_primeiro=robo.TIPOS_ANEXO[0], anexo_demais=robo.TIPOS_ANEXO[1], rotulos_docx=robo.ROTULOS_DOCX,
        partes_conhecidas=robo.CNPJ_CONHECIDO, dicionario=robo.DICIONARIO, padrao=robo.PADRAO,
        campos=CAMPOS, pessoas=list(robo.PESSOAS),
        pje={"cpf": pje.get("cpf", ""), "tem_senha": bool(pje.get("senha")), "tem_totp": bool(pje.get("totp_key"))})


@app.post("/api/escritorio")
def salvar_escritorio():
    global escritorio
    if estado["tarefa"]:
        abort(409, "Espere o robô terminar para mudar a configuração.")
    novo = request.get_json()
    pje = novo.pop("pje", {})
    erros = []
    if pje.get("totp_key"):
        pje["totp_key"] = robo.limpar_chave(pje["totp_key"])
        if not pje["totp_key"]:
            erros.append(robo.CHAVE_INVALIDA)
    if robo.SERVIDOR:
        novo.pop("pasta_clientes", None)  # no servidor a pasta é fixa (raiz())
    elif not os.path.isdir(novo.get("pasta_clientes", "")):
        erros.append("a pasta de clientes não existe neste computador")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", novo.get("cor", "")):
        erros.append("cor inválida")
    if pje.get("cpf") and not robo.cpf_valido(re.sub(r"\D", "", pje["cpf"])):
        erros.append("CPF do login do PJe inválido")
    for c, v in novo.get("partes_conhecidas", {}).items():
        if not robo.cnpj_valido(re.sub(r"\D", "", v)):
            erros.append(f"CNPJ de '{c}' inválido")
    for t in novo.get("dicionario", []):
        if not t.get("frases"):
            erros.append(f"a linha do dicionário com o assunto '{t.get('assunto') or 'vazio'}' precisa de pelo menos uma palavra")
    if erros:
        abort(400, "Confira: " + "; ".join(erros) + ".")
    novo["pausa_segundos"] = float(novo.get("pausa_segundos") or 0)
    gravar_json(robo.ARQUIVO_ESCRITORIO, novo)
    antigo = robo.ler_config()
    config = dict(antigo)  # o config.json tem outras chaves (folder_path, headless): mantém
    for chave in ("cpf", "senha", "totp_key"):
        if pje.get(chave):  # senha e chave em branco = não mudar (a página nunca recebe as atuais)
            config[chave] = re.sub(r"\D", "", pje[chave]) if chave == "cpf" else pje[chave].strip()
    if pje.get("totp_key"):
        config.pop("totp_qr", None)  # chave colada à mão: o QR guardado era de outra chave
    if config != antigo:  # no servidor, cada gravação é uma versão nova do segredo
        robo.gravar_config(config)
    escritorio = robo.carregar_escritorio()
    return jsonify(ok=True)


# o mapa do PJe tem uns 2 MB: vai comprimido (gzip) e o navegador guarda; só baixa de novo quando o arquivo muda
_mapa_cache = {"chave": None, "etag": "", "bruto": b"", "gzip": b""}


@app.get("/api/mapa")
def ver_mapa():
    try:
        st = os.stat(mapear_pje.ARQUIVO)
        chave = (st.st_mtime_ns, st.st_size)
    except OSError:
        return jsonify({})
    if _mapa_cache["chave"] != chave:
        bruto = json.dumps(ler_json(mapear_pje.ARQUIVO), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        _mapa_cache.update(chave=chave, bruto=bruto, gzip=gzip.compress(bruto, 6),
                           etag='"' + hashlib.sha1(bruto).hexdigest()[:16] + '"')
    cab = {"ETag": _mapa_cache["etag"], "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    if request.headers.get("If-None-Match") == _mapa_cache["etag"]:
        return Response(status=304, headers=cab)
    if "gzip" in request.headers.get("Accept-Encoding", ""):
        return Response(_mapa_cache["gzip"], mimetype="application/json", headers={**cab, "Content-Encoding": "gzip"})
    return Response(_mapa_cache["bruto"], mimetype="application/json", headers=cab)


@app.post("/api/mapa/ler")
def ler_mapa():
    if robo.SERVIDOR:  # o mapa_pje.json é um só para todos os escritórios do servidor
        abort(403, "No servidor, as opções do PJe são atualizadas pelo administrador.")
    corpo = request.get_json() or {}
    rascunho = re.sub(r"\D", "", str(corpo.get("rascunho", "")))
    return em_tarefa("mapear", mapear, rascunho, bool(corpo.get("do_zero")))


@app.get("/api/clientes")
def clientes():
    """Pastas de clientes, cada uma conferida por preparar() (sem abrir o PJe)."""
    # ponytail: confere todas a cada pedido (lê os PDFs); com centenas de pastas, guardar por data de modificação
    lista = []
    for pasta in robo.pastas_de_clientes(raiz()):
        item = {"nome": os.path.basename(pasta), "situacao": "pronto", "problema": "", "anexos": None}
        try:  # para a lista: "Ação de Cobrança · 3 anexos"
            item["anexos"] = len(robo.arquivos_da_pasta(pasta)[2])
        except SystemExit:
            pass
        try:
            robo.preparar(pasta)
        except SystemExit as e:
            item.update(situacao="problema", problema=str(e))
        except Exception as e:
            item.update(situacao="problema", problema=f"Não consegui ler a pasta: {robo.resumo(e)}")
        lista.append(item)
    return jsonify(clientes=lista, raiz=raiz())


def campos_para_corrigir(valores):
    """Cada campo com o valor do .docx, da inicial e a correção, e se falta ou se .docx e inicial divergem."""
    lista = []
    for chave, nome in CAMPOS:
        docx_v, inicial_v, corrigido = valores.get(chave, ("", "", ""))
        diverge = bool(docx_v and inicial_v and robo.comparavel(chave, docx_v) != robo.comparavel(chave, inicial_v))
        atual = corrigido or docx_v or inicial_v
        lista.append({"chave": chave, "nome": nome, "docx": docx_v, "inicial": inicial_v, "corrigido": corrigido,
                      "atual": atual, "diverge": diverge, "falta": chave in robo.CAMPOS_OBRIGATORIOS and not atual})
    return lista


@app.get("/api/clientes/<path:nome>")
def cliente(nome):
    pasta = pasta_do_cliente(nome)
    try:
        campos = campos_para_corrigir(robo.valores_para_corrigir(pasta))
    except SystemExit as e:
        return jsonify(problema=str(e), campos=[], dados=None, motivos={})
    try:
        motivos = robo.motivos_da_inicial(robo.arquivos_da_pasta(pasta)[1])
    except Exception:  # PDF que não abre: a ficha aparece sem dizer de onde veio cada valor
        motivos = {}
    try:
        c = robo.preparar(pasta)
        dados = {"dados": c["dados"], "origem": c["origem"], "inicial": os.path.basename(c["inicial"]),
                 "anexos": [{"nome": os.path.basename(a), "tipo": robo.tipo_do_anexo(i)} for i, a in enumerate(c["anexos"])]}
        problema = ""
    except SystemExit as e:
        dados, problema = None, str(e)
    return jsonify(problema=problema, campos=campos, dados=dados, motivos=motivos, rascunho=robo.rascunho_salvo(pasta),
                   docx=os.path.basename(robo.arquivos_da_pasta(pasta)[0] or ""))


@app.post("/api/clientes/<path:nome>/corrigir")
def corrigir(nome):
    """Grava os campos alterados, os que tinham problema (o usuário confirmou) e os já corrigidos antes.
    Mesmas regras do Corrigir dados da janela antiga."""
    pasta = pasta_do_cliente(nome)
    form = request.get_json()
    novas, erros = {}, []
    for campo in campos_para_corrigir(robo.valores_para_corrigir(pasta)):
        chave = campo["chave"]
        valor = str(form.get(chave, "")).strip()
        if chave in ("cpf", "cnpj"):
            valor = robo.formatar_documento(valor)
        elif chave == "valor da causa":
            valor = robo.formatar_valor(valor)
        problema = (campo["diverge"] or campo["falta"]) and not campo["corrigido"]
        if valor and (campo["corrigido"] or problema or valor != (campo["docx"] or campo["inicial"])):
            novas[chave] = valor
        erro = robo.erro_no_valor(chave, valor) if valor else ("obrigatório" if chave in robo.CAMPOS_OBRIGATORIOS else "")
        if erro:
            erros.append(f"{NOMES[chave]}: {erro}")
    for _, _, c_pessoa, c_nome, c_doc in robo.POLOS.values():  # o documento combina com o tipo de pessoa?
        pessoa, nome_parte, doc = (str(form.get(c, "")).strip() for c in (c_pessoa, c_nome, c_doc))
        if pessoa in robo.PESSOAS and not any(e.startswith(NOMES[c_doc]) for e in erros):
            doc = doc or (robo.CNPJ_CONHECIDO.get(nome_parte.upper(), "") if pessoa == "ÓRGÃO PÚBLICO" else "")
            if erro := robo.erro_documento(pessoa, doc):
                erros.append(f"{NOMES[c_doc]}: {erro}")
    if erros:
        abort(400, "Confira: " + "; ".join(erros))
    robo.salvar_correcoes(pasta, novas)
    mudados = [c for c in form.get("_mudados", []) if c in NOMES]  # a página diz o que foi mexido
    if mudados:
        robo.anotar("correcao", cliente=nome, campos=mudados)
    return jsonify(ok=True)


# ---------- envio de pastas (servidor) ----------

PROIBIDO_NO_NOME = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def nome_seguro(nome):
    """Nome de pasta ou arquivo que não sai da pasta dos clientes nem confunde o sistema de arquivos."""
    return (bool(nome) and nome not in (".", "..") and not PROIBIDO_NO_NOME.search(nome)
            and not nome.endswith((".", " ")) and len(nome) <= 150)


ARQUIVOS_DO_ROBO = (robo.ARQUIVO_RASCUNHO, robo.ARQUIVO_CORRECOES)  # vêm junto: cliente com rascunho não ganha outro


def destino_do_envio(caminho):
    """(pasta do cliente, arquivo) para um caminho relativo vindo da página, ou None se não serve.
    Aceita 'Cliente/arquivo' e 'Lote/Cliente/arquivo' (pasta com várias pastas de clientes dentro).
    Nada de dentro de 'Concluído' ou 'Lixeira', em nível nenhum: voltaria como cliente pendente."""
    partes = [p for p in unicodedata.normalize("NFC", caminho).replace("\\", "/").split("/") if p]  # Mac manda NFD
    if not partes or not all(nome_seguro(p) for p in partes) or {"Concluído", "Lixeira"} & set(partes):
        return None
    if len(partes) == 3:
        partes = partes[1:]
    if len(partes) != 2:
        return None
    if not (partes[1].lower().endswith((".pdf", ".docx")) or partes[1] in ARQUIVOS_DO_ROBO):
        return None
    return partes[0], partes[1]


def json_do_robo(arquivo):
    """Conteúdo de rascunho.json/correcoes.json enviado, se for um objeto JSON pequeno; senão None."""
    dados = arquivo.read(100_001)
    try:
        return dados if len(dados) <= 100_000 and isinstance(json.loads(dados), dict) else None
    except ValueError:
        return None


@app.post("/api/clientes/enviar")
def enviar_pastas():
    """Pastas de clientes arrastadas para a página (só no servidor: no modo local elas já estão no computador)."""
    if not robo.SERVIDOR:
        abort(403, "No modo local, as pastas dos clientes ficam na pasta escolhida em Configurações.")
    arquivos, caminhos = request.files.getlist("arquivos"), request.form.getlist("caminhos")
    nomes, ignorados = {}, []  # pasta enviada -> pasta criada (nome repetido ganha ' (2)')
    # ponytail: envio que cai no meio deixa a pasta pela metade; o escritório apaga e envia de novo
    for arquivo, caminho in zip(arquivos, caminhos):
        destino = destino_do_envio(caminho)
        if not destino:
            ignorados.append(caminho)
            continue
        pasta, nome = destino
        conteudo = json_do_robo(arquivo) if nome in ARQUIVOS_DO_ROBO else None
        if nome in ARQUIVOS_DO_ROBO and conteudo is None:
            ignorados.append(caminho)
            continue
        if pasta not in nomes:
            nomes[pasta] = nome_livre(raiz(), pasta)
        os.makedirs(os.path.join(raiz(), nomes[pasta]), exist_ok=True)
        destino_arquivo = os.path.join(raiz(), nomes[pasta], nome)
        if conteudo is None:
            arquivo.save(destino_arquivo)
        else:
            with open(destino_arquivo, "wb") as f:
                f.write(conteudo)
    ignorados += caminhos[len(arquivos):]
    if not nomes:
        abort(400, "Nenhum arquivo .pdf ou .docx dentro de pastas de clientes.")
    return jsonify(clientes=list(nomes.values()), ignorados=ignorados)


@app.post("/api/clientes/<path:nome>/apagar")
def apagar_cliente(nome):
    """Move a pasta do cliente para a Lixeira do escritório (no servidor ela se esvazia sozinha em 30 dias)."""
    if not robo.SERVIDOR:
        abort(403, "No modo local, apague a pasta no computador.")
    if estado["tarefa"] == "cadastrar":
        abort(409, "Espere o robô terminar para apagar.")
    mover_para(pasta_do_cliente(nome), "Lixeira")
    return jsonify(ok=True)


@app.post("/api/cadastrar")
def iniciar():
    nomes = [os.path.basename(pasta_do_cliente(n)) for n in request.get_json().get("clientes", [])]
    if not nomes:
        abort(400, "Marque pelo menos um cliente pronto.")
    for n in nomes:
        estado["resultados"].pop(n, None)
    return em_tarefa("cadastrar", cadastrar, nomes)


@app.post("/api/parar")
def parar():
    robo.parar = True
    if estado["pausa"]:
        respostas.put("pular")  # solta o robô pausado (e a fila para)
    if (estado["autenticador"] or {}).get("etapa") == "link":
        links.put("")  # solta o robô que esperava o link do e-mail
    return jsonify(ok=True)


def testar_login():
    """(thread) "Testar e continuar" do primeiro acesso: o robô entra no PJe uma vez e diz se deu certo."""
    estado["teste_login"] = {"rodando": True}
    try:
        abrir_pje()
    except SystemExit as e:
        estado["teste_login"] = {"ok": False, "msg": str(e)}
        raise
    estado["teste_login"] = {"ok": True}
    robo.log("Login no PJe testado: entrou sem problemas.")


@app.post("/api/testar-login")
def iniciar_teste_login():
    config = robo.ler_config()
    if not (config.get("cpf") and config.get("senha")):
        abort(400, "Salve o CPF e a senha do PJe antes de testar.")
    if estado["tarefa"]:
        abort(409, "O robô já está trabalhando. Espere terminar ou clique em Parar.")
    estado["teste_login"] = {"rodando": True}  # antes de começar: a página não pode ver o resultado do teste anterior
    return em_tarefa("testar", testar_login)


@app.post("/api/autenticador")
def iniciar_autenticador():
    config = robo.ler_config()
    if not (config.get("cpf") and config.get("senha")):
        abort(400, "Salve o CPF e a senha do PJe antes de configurar o código.")
    return em_tarefa("autenticador", configurar_codigo)


@app.post("/api/autenticador/link")
def colar_link():
    """O link do e-mail do PJe. Só aceita endereço https da Justiça: o robô abre o link no Chrome logado."""
    if (estado["autenticador"] or {}).get("etapa") != "link":
        abort(400, "O robô não está esperando o link.")
    link = str((request.get_json() or {}).get("link", "")).strip()
    url = urlparse(link)
    if url.scheme != "https" or not (url.hostname or "").endswith(".jus.br"):
        abort(400, "Cole o link inteiro do e-mail do PJe: ele começa com https:// e é do endereço do PJe (jus.br).")
    links.put(link)
    return jsonify(ok=True)


@app.get("/api/autenticador/qr")
def ver_qr():
    """Para pôr o código em outro celular sem pedir 'Configurar novo dispositivo' (que derruba todos os outros)."""
    config = robo.ler_config()
    if not config.get("totp_key"):
        abort(404, "O robô ainda não tem a chave do autenticador.")
    return jsonify(qr=config.get("totp_qr", ""), chave=" ".join(re.findall(".{1,4}", config["totp_key"].replace(" ", ""))))


@app.post("/api/autenticador/ler-qr")
def ler_qr():
    """Print ou foto do QR do autenticador -> a chave, para a página pôr no campo (só grava ao Salvar alterações).
    A imagem fica só na memória. Devolve o código de agora para conferir com o celular."""
    arquivo = request.files.get("imagem")
    if not arquivo:
        abort(400, "Escolha a imagem do QR.")
    try:
        conta = robo.conta_do_pje_no_qr(arquivo.read(10 * 1024 * 1024))
    except ValueError as e:
        abort(400, str(e))
    except ImportError:
        abort(409, "Falta o leitor de QR neste computador: rode  pip install zxing-cpp pillow  e abra o programa de novo.")
    return jsonify(chave=conta["chave"], emissor=conta["emissor"], nome=conta["nome"],
                   codigo=robo.pyotp.TOTP(conta["chave"]).now())


@app.post("/api/autenticador/fechar")
def fechar_autenticador():
    """Tira da página o QR e a chave (só ficam na memória até fecharem)."""
    if estado["tarefa"] != "autenticador":
        estado["autenticador"] = None
    return jsonify(ok=True)


@app.post("/api/responder")
def responder():
    resposta = request.get_json().get("resposta")
    if not estado["pausa"] or resposta not in ("repetir", "continuar", "pular"):
        abort(400, "O robô não está esperando resposta.")
    if robo.SERVIDOR and resposta == "continuar" and not estado["pausa"].get("rascunho"):
        abort(400, "Ainda não existe rascunho no PJe: use Tentar de novo ou Pular.")
    robo.log(f"Resposta: {dict(repetir='Tentar de novo', continuar='Continuar (fiz à mão)', pular='Pular cliente')[resposta]}")
    respostas.put(resposta)
    return jsonify(ok=True)


# ---------- relatório ----------

TIPOS_DE_PROBLEMA = [  # (nome no relatório, trecho da mensagem) - vale o 1º que aparecer; nenhum = "Outros"
    ("Chrome do robô não abriu", r"não consegui abrir o chrome"),
    ("Código do autenticador", r"autenticador|base32|código .*recus"),
    ("Login do PJe", r"login"),
    ("Rascunho não criado", r"número do rascunho"),
    ("CPF não confere com a Receita", r"o cpf .* é de '"),
    ("Opção não existe no PJe", r"não existe no pje|o pje não oferece"),
    ("Competência não identificada", r"não sabe a competência"),
    ("Parte para escolher à mão", r"pesquisad[ao] pel[ao]"),
    ("Não vinculou a parte", r"não consegui vincular"),
    ("Endereço da parte", r"endereço|\bcep\b"),
    ("Anexo recusado ou faltando", r"anexo|documentos|recusou"),
    ("Chrome do robô fechado", r"closed|fechad"),
    ("PJe lento ou fora do ar", r"timeout|tempo esgotado|net::|errorunexpected|não respondeu|gateway"),
    ("Dados da pasta do cliente", r"pasta|\.docx|inicial|diverge|correção|ficha"),
]


def tipo_do_problema(texto):
    return next((nome for nome, trecho in TIPOS_DE_PROBLEMA if re.search(trecho, texto or "", re.I)), "Outros")


def ler_historico():
    try:
        with open(robo.ARQUIVO_HISTORICO, encoding="utf-8") as f:
            linhas = f.readlines()
    except FileNotFoundError:
        return []
    eventos = []
    for linha in linhas:
        try:
            eventos.append(json.loads(linha))
        except ValueError:  # linha cortada (programa fechado no meio da gravação)
            pass
    return [e for e in eventos if isinstance(e, dict) and e.get("quando")]


def no_periodo(quando, periodo, hoje):
    if periodo == "mes":
        return quando[:7] == hoje.strftime("%Y-%m")
    if periodo == "mes_passado":
        return quando[:7] == (hoje.replace(day=1) - datetime.timedelta(days=1)).strftime("%Y-%m")
    if periodo == "30":
        return quando >= (hoje - datetime.timedelta(days=29)).isoformat()
    return True


def relatorio(eventos, periodo, hoje):
    """Soma o histórico do período para a tela Relatório."""
    ev = sorted((e for e in eventos if no_periodo(e["quando"], periodo, hoje)), key=lambda e: e["quando"])
    de = lambda tipo: [e for e in ev if e.get("tipo") == tipo]
    cadastros, pausas, sozinho = de("cadastro"), de("pausa"), de("sozinho")
    # ponytail: liga pausa e cadastro pelo cliente e horário (varre tudo); com milhares por mês, gravar um id da rodada
    for c in cadastros:
        dele = [p for p in pausas if p.get("cliente") == c.get("cliente") and c.get("inicio", "") <= p["quando"] <= c["quando"]]
        c["pausas"] = len(dele)
        c["etapa"] = dele[-1].get("etapa", "") if dele and c.get("resultado") != "pronto" else ""
    prontos = [c for c in cadastros if c.get("resultado") == "pronto"]

    grupos = {}
    for e in sorted(pausas + sozinho + [c for c in cadastros if c.get("resultado") == "erro"], key=lambda e: e["quando"]):
        texto = e.get("problema") or e.get("msg", "")
        g = grupos.setdefault(tipo_do_problema(texto), {"pausas": 0, "sozinho": 0, "erros": 0, "clientes": [], "foto": ""})
        g[{"pausa": "pausas", "sozinho": "sozinho"}.get(e["tipo"], "erros")] += 1
        g["clientes"].append(e.get("cliente", ""))
        g["ultima"] = {"quando": e["quando"], "cliente": e.get("cliente", ""), "etapa": e.get("etapa", ""), "texto": texto}
        g["foto"] = e.get("foto") or g["foto"]
    problemas = sorted(({"tipo": t, **g, "vezes": g["pausas"] + g["sozinho"] + g["erros"],
                         "clientes": list(dict.fromkeys(reversed(g["clientes"])))} for t, g in grupos.items()),
                       key=lambda g: -g["vezes"])

    campos = Counter(c for e in de("correcao") for c in e.get("campos", []))
    return {
        "tem_historico": bool(eventos), "eventos": len(ev),
        "prontos": len(prontos), "nao_terminaram": len(cadastros) - len(prontos),
        "sem_pausa": round(100 * sum(c["pausas"] == 0 for c in prontos) / len(prontos)) if prontos else None,
        "minutos": round(sum(c.get("segundos", 0) for c in prontos) / len(prontos) / 60, 1) if prontos else None,
        "pausas": len(pausas), "sozinho": len(sozinho),
        "etapas": [{"etapa": n, "pausas": sum(p.get("etapa") == n for p in pausas),
                    "sozinho": sum(s.get("etapa") == n for s in sozinho)} for n, _ in robo.ETAPAS],
        "problemas": problemas,
        "campos": [{"campo": NOMES.get(c, c), "vezes": n} for c, n in campos.most_common()],
        "ultimos": cadastros[::-1][:20],
    }


@app.get("/api/relatorio")
def ver_relatorio():
    return jsonify(relatorio(ler_historico(), request.args.get("periodo", "mes"), datetime.date.today()))


@app.get("/api/foto/<nome>")
def foto(nome):
    caminho = os.path.join(robo.DADOS, "erros", os.path.basename(nome))  # só fotos de erros/
    if not nome.endswith(".png") or not os.path.exists(caminho):
        abort(404, "Foto não encontrada.")
    return send_file(caminho)


@app.get("/api/pagina-salva/<nome>")
def pagina_salva(nome):
    """A página do PJe salva junto com a foto da pausa (para investigar o erro). Vai como arquivo para baixar, nunca
    aberta aqui: é HTML do PJe."""
    caminho = os.path.join(robo.DADOS, "erros", os.path.basename(nome))
    if not nome.endswith(".html") or not os.path.exists(caminho):
        abort(404, "Página salva não encontrada.")
    return send_file(caminho, mimetype="application/octet-stream", as_attachment=True, download_name=os.path.basename(nome))


# o que conta como problema na aba Erros do administrador (o "sozinho" é engasgo que o robô resolveu)
DESFECHO = {"parada": "parou com erro", "pausa": "pausou", "sozinho": "resolveu sozinho"}


@app.get("/api/erros")
def erros_recentes():
    """Os problemas dos últimos dias (máx. 90), do mais novo ao mais antigo, para a aba Erros do administrador (o
    porteiro junta os de todos os escritórios)."""
    dias = min(max(int(request.args.get("dias", "7")) if request.args.get("dias", "7").isdigit() else 7, 1), 90)
    desde = (datetime.datetime.now() - datetime.timedelta(days=dias)).strftime("%Y-%m-%dT%H:%M:%S")
    lista = []
    for e in ler_historico():
        if e["quando"] < desde:
            continue
        if e.get("tipo") in DESFECHO:
            desfecho = DESFECHO[e["tipo"]]
        elif e.get("tipo") == "cadastro" and e.get("resultado") in ("erro", "pulado"):
            desfecho = "parou com erro" if e["resultado"] == "erro" else "pulado"
        else:
            continue
        problema = e.get("problema") or e.get("msg") or ""
        foto_ = e.get("foto") or ""
        html = foto_[:-4] + ".html" if foto_.endswith(".png") else ""
        lista.append({"quando": e["quando"], "desfecho": desfecho, "cliente": e.get("cliente") or "",
                      "etapa": e.get("etapa") or e.get("tarefa") or "", "problema": problema,
                      "grupo": tipo_do_problema(problema), "foto": foto_, "resposta": e.get("resposta") or "",
                      "pagina": html if html and os.path.exists(os.path.join(robo.DADOS, "erros", html)) else ""})
    lista.sort(key=lambda x: x["quando"], reverse=True)
    return jsonify(erros=lista[:500])


if __name__ == "__main__":
    if not robo.SERVIDOR:
        threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{PORTA}")).start()
    # ponytail: servidor de desenvolvimento do Flask, só o porteiro fala com ele (127.0.0.1); trocar por waitress se travar com vários usuários
    app.run(host="127.0.0.1", port=PORTA, threaded=True)
