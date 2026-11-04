"""Interface web do robô de cadastro no PJe (substitui a janela interface_cadastro.py).
Abre no navegador em http://127.0.0.1:8765 e só aceita conexões deste computador (a senha do PJe fica aqui).
Cada escritório se configura na aba Escritório: grava escritorio.json (regras, modelo, anexos, identidade)
e config.json (login do PJe). As opções do PJe vêm do mapa_pje.json (aba Opções do PJe > Ler do PJe).
Uso: python app_web.py
"""
import json
import os
import queue
import re
import shutil
import threading
import webbrowser
from flask import Flask, jsonify, request, send_file, abort
import novo_processo as robo
import mapear_pje

PORTA = 8765
os.makedirs(robo.DADOS, exist_ok=True)  # cópia nova (GitHub) vem sem dados_do_escritorio
CONFIG = os.path.join(robo.DADOS, "config.json")
PAGINA = os.path.join(robo.SISTEMA, "web", "index.html")
COR_PADRAO = "#2454B4"

CAMPOS = [  # (chave do robô, nome na tela) - mesma ordem do Corrigir dados da janela antiga
    ("matéria", "Matéria"), ("jurisdição", "Jurisdição"), ("competência", "Competência"),
    ("classe judicial", "Classe judicial"), ("assunto principal", "Assunto"),
    ("tipo ativo", "Polo ativo: tipo da parte"), ("pessoa ativo", "Polo ativo: tipo de pessoa"),
    ("nome completo", "Polo ativo: nome"), ("cpf", "Polo ativo: CPF/CNPJ"), ("endereço", "Polo ativo: endereço"),
    ("tipo passivo", "Polo passivo: tipo da parte"), ("pessoa passivo", "Polo passivo: tipo de pessoa"),
    ("entidade", "Polo passivo: nome"), ("cnpj", "Polo passivo: CPF/CNPJ"),
    ("justiça gratuita", "Justiça gratuita"), ("pedido de liminar ou antecipação de tutela", "Liminar ou tutela"),
    ("valor da causa", "Valor da causa (R$)"), ("segredo de justiça", "Segredo de justiça"),
    ("prioridade de processo", "Prioridade"),
]
NOMES = dict(CAMPOS)

app = Flask(__name__, static_folder=None)
# ponytail: estado em memória de um único usuário local; servidor central pediria sessão por escritório
estado = {"tarefa": "", "atual": "", "log": [], "pausa": None, "resultados": {}}
respostas = queue.Queue()
trava = threading.Lock()
escritorio = robo.carregar_escritorio()

robo.saida = estado["log"].append  # o Console da página lê daqui


def pedir_ajuda(etapa, nome, problema, foto):
    """(thread do robô) Mostra a pausa na página e espera a resposta: 'repetir', 'continuar' ou 'pular'."""
    estado["pausa"] = {"etapa": etapa, "nome": nome, "problema": problema, "foto": os.path.basename(foto or "")}
    try:
        return respostas.get()
    finally:
        estado["pausa"] = None


robo.pedir_ajuda = pedir_ajuda


def raiz():
    """Pasta onde ficam as pastas de clientes (padrão: a que contém a pasta do robô)."""
    return escritorio.get("pasta_clientes") or os.path.dirname(robo.PASTA)


def pasta_do_cliente(nome):
    """Só aceita nome de pasta de cliente que está na lista (nada de caminho vindo da página)."""
    pasta = os.path.join(raiz(), nome)
    if pasta not in robo.pastas_de_clientes(raiz()):
        abort(404, "Pasta de cliente não encontrada. Clique em Atualizar a lista.")
    return pasta


def mover_para_concluido(pasta):
    """Move para 'Concluído' ao lado das pastas de clientes; nome repetido ganha ' (2)', ' (3)'..."""
    concluido = os.path.join(os.path.dirname(pasta), "Concluído")
    os.makedirs(concluido, exist_ok=True)
    destino, n = os.path.join(concluido, os.path.basename(pasta)), 2
    while os.path.exists(destino):
        destino, n = os.path.join(concluido, f"{os.path.basename(pasta)} ({n})"), n + 1
    shutil.move(pasta, destino)
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
        except Exception as e:
            robo.log(f"ERRO: {robo.resumo(e)}")
        finally:
            estado["tarefa"], estado["atual"], robo.parar = "", "", False


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


def cadastrar(nomes):
    """(thread) Login uma vez e cadastra os clientes, um depois do outro. Sem erro: pasta vai para Concluído."""
    abrir_pje()
    for nome in nomes:
        if robo.parar:
            break
        estado["atual"] = nome
        robo.log(f"===== Cliente: {nome} =====")
        try:
            pendencias = robo.cadastrar(robo.preparar(os.path.join(raiz(), nome)))
        except SystemExit as e:
            estado["resultados"][nome] = {"situacao": "erro", "msg": str(e)}
            continue
        except Exception as e:
            estado["resultados"][nome] = {"situacao": "erro", "msg": robo.resumo(e)}
            continue
        try:
            destino = mover_para_concluido(os.path.join(raiz(), nome))
            msg = f"Pasta movida para Concluído\\{os.path.basename(destino)}"
        except OSError as e:
            msg = f"NÃO consegui mover a pasta: {e}"
        estado["resultados"][nome] = {"situacao": "concluido", "msg": msg, "pendencias": pendencias}
    robo.log("Fila terminada.")


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
@app.errorhandler(404)
@app.errorhandler(409)
def erro(e):
    return jsonify(erro=e.description), e.code


@app.get("/")
def pagina():
    return send_file(PAGINA)


@app.get("/api/estado")
def ver_estado():
    desde = request.args.get("desde", 0, type=int)
    return jsonify(tarefa=estado["tarefa"], atual=estado["atual"], pausa=estado["pausa"],
                   resultados=estado["resultados"], log=estado["log"][desde:], total=len(estado["log"]),
                   etapas=[n for n, _ in robo.ETAPAS])


@app.get("/api/escritorio")
def ver_escritorio():
    pje = ler_json(CONFIG)
    return jsonify(
        nome=escritorio.get("nome", ""), cor=escritorio.get("cor", COR_PADRAO), pasta_clientes=raiz(),
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
    if not os.path.isdir(novo.get("pasta_clientes", "")):
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
    config = ler_json(CONFIG)  # o config.json tem outras chaves (folder_path, headless): mantém
    for chave in ("cpf", "senha", "totp_key"):
        if pje.get(chave):  # senha e chave em branco = não mudar (a página nunca recebe as atuais)
            config[chave] = re.sub(r"\D", "", pje[chave]) if chave == "cpf" else pje[chave].strip()
    gravar_json(CONFIG, config)
    escritorio = robo.carregar_escritorio()
    return jsonify(ok=True)


@app.get("/api/mapa")
def ver_mapa():
    return jsonify(ler_json(mapear_pje.ARQUIVO))


@app.post("/api/mapa/ler")
def ler_mapa():
    corpo = request.get_json() or {}
    rascunho = re.sub(r"\D", "", str(corpo.get("rascunho", "")))
    return em_tarefa("mapear", mapear, rascunho, bool(corpo.get("do_zero")))


@app.get("/api/clientes")
def clientes():
    """Pastas de clientes, cada uma conferida por preparar() (sem abrir o PJe)."""
    # ponytail: confere todas a cada pedido (lê os PDFs); com centenas de pastas, guardar por data de modificação
    lista = []
    for pasta in robo.pastas_de_clientes(raiz()):
        item = {"nome": os.path.basename(pasta), "situacao": "pronto", "problema": ""}
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
    return jsonify(problema=problema, campos=campos, dados=dados, motivos=motivos, rascunho=robo.rascunho_salvo(pasta))


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
    return jsonify(ok=True)


@app.post("/api/responder")
def responder():
    resposta = request.get_json().get("resposta")
    if not estado["pausa"] or resposta not in ("repetir", "continuar", "pular"):
        abort(400, "O robô não está esperando resposta.")
    robo.log(f"Resposta: {dict(repetir='Tentar de novo', continuar='Continuar (fiz à mão)', pular='Pular cliente')[resposta]}")
    respostas.put(resposta)
    return jsonify(ok=True)


@app.get("/api/foto/<nome>")
def foto(nome):
    caminho = os.path.join(robo.DADOS, "erros", os.path.basename(nome))  # só fotos de erros/
    if not nome.endswith(".png") or not os.path.exists(caminho):
        abort(404, "Foto não encontrada.")
    return send_file(caminho)


if __name__ == "__main__":
    threading.Timer(1, lambda: webbrowser.open(f"http://127.0.0.1:{PORTA}")).start()
    app.run(host="127.0.0.1", port=PORTA, threaded=True)
