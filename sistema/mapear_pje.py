"""Lê do PJe todas as opções da tela Novo processo e grava em mapa_pje.json. A interface usa o mapa para cada
escritório montar as regras dele escolhendo de listas reais.
  - Dados iniciais: Matéria -> Jurisdição -> Competência -> Classe judicial (cascata, tela de 23/10/2026).
    Só escolhe nas listas: NÃO clica em Incluir, não cria rascunho.
  - Com o número de um rascunho (opcional): prioridades, tipos de documento e tipos de parte/pessoa.
    Só abre as abas e o modal de parte; não salva nada.
Demora (cerca de 40 min: são milhares de consultas). Grava a cada matéria; se parar, roda de novo e continua.
Uso: python mapear_pje.py [idProcesso de um rascunho]
"""
import json
import os
import sys
import time
import novo_processo as robo

ARQUIVO = os.path.join(robo.SISTEMA, "mapa_pje.json")
MATERIA, JURISDICAO, COMPETENCIA, CLASSE = "areaDireitoCombo", "jurisdicaoCombo", "comboConflitoCompetencia", "classeJudicialCombo"


def carregar():
    if os.path.exists(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            return json.load(f)
    return {"materias": {}}


def gravar(mapa):
    mapa["atualizado_em"] = time.strftime("%Y-%m-%d %H:%M")
    with open(ARQUIVO + ".tmp", "w", encoding="utf-8") as f:
        json.dump(mapa, f, ensure_ascii=False, indent=1)
    os.replace(ARQUIVO + ".tmp", ARQUIVO)  # a interface lê o arquivo: nunca deixar pela metade


def opcoes(d, campo):
    return d.evaluate("""campo => { const s = document.querySelector("select[id$=':" + campo + "']");
        return s ? Array.from(s.options).map(o => o.textContent.trim()).filter(t => t && t != 'Selecione') : []; }""", campo)


def escolher(d, campo, texto):
    """Troca a lista direto na página (milhares de trocas: sem o log e a pausa do robô.escolher)."""
    d.evaluate("""([campo, texto]) => { const s = document.querySelector("select[id$=':" + campo + "']");
        s.value = Array.from(s.options).find(o => o.textContent.trim() == texto).value;
        s.dispatchEvent(new Event('change')); }""", [campo, texto])
    robo.aguardar(d)


def mapear(d, progresso=robo.log, do_zero=False):
    """Percorre a cascata da tela Novo processo. Por matéria grava {jurisdição: [competências]} e
    {competência: [classes]}. Continua de onde parou; do_zero=True lê tudo de novo (o tribunal mudou a tela)."""
    # ponytail: as classes de uma competência são lidas na 1ª jurisdição que a oferece (amostras de 04/11/2026
    # bateram). Se algum dia variarem por jurisdição, guardar por (jurisdição, competência): ~10x mais consultas.
    mapa = carregar()
    if do_zero:
        mapa["materias"] = {}
    d.goto(robo.URL_NOVO_PROCESSO)
    robo.esperar_ate(lambda: d.query_selector("[id='form_lbl']"))
    robo.aguardar(d)
    materias = opcoes(d, MATERIA)
    for i, m in enumerate(materias, 1):
        if mapa["materias"].get(m, {}).get("completo"):
            continue
        escolher(d, MATERIA, m)
        item = {"jurisdicoes": {}, "classes": {}}
        jurisdicoes = opcoes(d, JURISDICAO)
        progresso(f"({i}/{len(materias)}) {m}: {len(jurisdicoes)} jurisdições")
        for j in jurisdicoes:
            escolher(d, JURISDICAO, j)
            competencias = opcoes(d, COMPETENCIA)
            item["jurisdicoes"][j] = competencias
            for c in competencias or [""]:  # sem competência na tela (PJe antigo): classes direto da jurisdição
                if c not in item["classes"]:
                    if c:
                        escolher(d, COMPETENCIA, c)
                    item["classes"][c] = opcoes(d, CLASSE)
        item["completo"] = True
        mapa["materias"][m] = item
        gravar(mapa)
    progresso(f"Mapa da tela Novo processo pronto: {len(materias)} matérias.")
    return mapa


def mapear_rascunho(d, id_processo, progresso=robo.log):
    """Listas que só aparecem dentro de um rascunho. Só lê: abre abas e o modal de parte, não salva."""
    def abrir():
        d.goto(robo.URL_RASCUNHO.format(id_processo))
        robo.esperar_ate(lambda: d.query_selector("[id='tabPartes_lbl']"))
        robo.aguardar(d)

    mapa = carregar()
    abrir()
    robo.clicar(d, "[id='processoPrioridadeProcesso_lbl']")
    prioridades = opcoes(d, "prioridadeProcesso")
    robo.clicar(d, "[id='novoAnexo_lbl']")
    tipos_documento = opcoes(d, "cbTD")
    pessoas_js = """() => Array.from(document.querySelectorAll("input[id*=':tipoPessoa:']")).filter(r => r.offsetParent)
        .map(r => { const l = document.querySelector("label[for='" + r.id + "']"); return l ? l.textContent.trim() : ''; })"""
    tipos_parte = {}
    for polo, nome in (("A", "ativo"), ("P", "passivo")):
        abrir()
        robo.clicar(d, "[id='tabPartes_lbl']")
        robo.visivel(d, f"#addParte{polo}").query_selector("xpath=..").click()
        modal = "[id^='mpAssociarParteProcesso'] select"
        tipos_parte[nome] = {}
        for _, tipo in robo.opcoes_da_lista(robo.lista(d, modal)):
            if tipo == "Selecione":
                continue
            robo.escolher(d, modal, tipo)  # lista com busca: o robô sabe trocar
            try:  # alguns tipos não têm 2º passo
                robo.esperar_ate(lambda: d.evaluate(pessoas_js), 5)
            except robo.TempoEsgotado:
                pass
            tipos_parte[nome][tipo] = d.evaluate(pessoas_js)
    mapa["rascunho"] = {"id": id_processo, "prioridades": prioridades, "tipos_documento": tipos_documento,
                        "tipos_parte": tipos_parte}
    gravar(mapa)
    progresso(f"Rascunho {id_processo}: {len(prioridades)} prioridades, {len(tipos_documento)} tipos de documento.")
    d.goto(robo.URL_NOVO_PROCESSO)  # sai do rascunho sem salvar (o modal de parte fica para trás)
    return mapa


if __name__ == "__main__":
    robo.PAUSA_SEGUNDOS = 0
    navegador = robo.navegador()
    if len(sys.argv) > 1:
        mapear_rascunho(navegador, sys.argv[1])
    mapear(navegador)
