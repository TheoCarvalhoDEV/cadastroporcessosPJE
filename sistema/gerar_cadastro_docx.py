"""Cria o "Cadastro de Ação Judicial - <nome> - <classe>.docx" nas pastas de clientes que ainda não têm .docx,
com os dados tirados da petição inicial (as mesmas regras do robô). Pastas que já têm .docx não são tocadas.
O .docx sai no formato que o novo_processo.py lê (tabela "Campo | Valor", "Polo Ativo (Requerente)", "Campo: valor").

Uso: python gerar_cadastro_docx.py
"""
import os
import re
import docx
import PyPDF2
import novo_processo as robo

PESSOA_TEXTO = {"FÍSICA": "Física", "JURÍDICA": "Jurídica", "ÓRGÃO PÚBLICO": "Jurídica (Órgão Público)",
                "ENTE OU AUTORIDADE": "Ente ou Autoridade", "MINISTÉRIO PÚBLICO": "Ministério Público"}
ORGAOS = {"EDUCACAO": "Secretaria de Estado de Educação (SEDUC-MT)", "SAUDE": "Secretaria de Estado de Saúde (SES-MT)"}


def juntar_nome(nome, pasta):
    """O PDF quebra nomes ('WESL EY'): junta pedaços vizinhos que, juntos, formam uma palavra do nome da pasta."""
    da_pasta = set(robo.comparavel("nome", os.path.basename(pasta)).split())
    partes = nome.split()
    i = 0
    while i < len(partes) - 1:
        if robo.comparavel("nome", partes[i] + partes[i + 1]).split()[0] in da_pasta:
            partes[i:i + 2] = [partes[i] + partes[i + 1]]
        else:
            i += 1
    return " ".join(partes)


def extras(inicial):
    """O que o robô não usa mas o cadastro mostra: função, regime e órgão vinculado (vazio se a inicial não diz)."""
    texto = " ".join(" ".join((p.extract_text() or "") for p in PyPDF2.PdfReader(inicial).pages).split())
    funcao = re.search(r"função de\s+([A-Za-zÀ-ú]+)", texto)
    orgao = re.search(r"vincula\s?d\s?[oa]\s+à\s+(Secretaria[^.,;]+)", texto)
    orgao = orgao and next((v for k, v in ORGAOS.items() if k in robo.comparavel("orgao", orgao[1])), orgao[1].strip())
    temporario = "contrato temporário" in texto.lower() or "contratos temporários" in texto.lower()
    return {
        "qualificação": f"{funcao[1]} - Vínculo Temporário" if funcao and temporario else funcao[1] if funcao else "",
        "regime": "Regime Especial / Contrato Temporário" if temporario else "",
        "órgão": orgao or "",
        "prioridade": "Requerida na inicial" if re.search(r"prioridade de tramita", texto, re.I) else "Não aplicável",
    }


def criar_docx(pasta):
    r = robo.preparar(pasta)
    d, x = r["dados"], extras(r["inicial"])
    d["nome completo"] = juntar_nome(d["nome completo"], pasta)
    cnpj = d.get("cnpj") or robo.CNPJ_CONHECIDO.get(d["entidade"].upper(), "")
    classe = re.sub(r"\s*\(\d+\)$", "", d["classe judicial"]).title().replace(" Da ", " da ").replace(" Do ", " do ")
    codigo = re.search(r"\((\d+)\)", d["classe judicial"])

    doc = docx.Document()
    doc.add_heading("CADASTRO DE AÇÃO JUDICIAL", 0)
    doc.add_heading(f"PJe - {classe}" + (f" (Classe {codigo[1]})" if codigo else ""), 1)

    doc.add_heading("1. Dados Iniciais", 0)
    tabela = doc.add_table(rows=1, cols=2, style="Table Grid")
    tabela.rows[0].cells[0].text, tabela.rows[0].cells[1].text = "Campo", "Informação Cadastral"
    for campo in ("Matéria", "Jurisdição", "Classe Judicial", "Assunto Principal"):
        c = tabela.add_row().cells
        c[0].text, c[1].text = campo, d[campo.lower()]

    doc.add_heading("2. Partes do Processo", 0)
    polos = [(f"Polo Ativo ({d['tipo ativo'].title()})",
              [("Tipo da Parte", d["tipo ativo"]), ("Tipo de Pessoa", PESSOA_TEXTO[d["pessoa ativo"]]),
               ("Nome Completo", d["nome completo"]), ("CPF", robo.formatar_documento(d.get("cpf", ""))),
               ("Endereço", d.get("endereço") or ""),
               ("Qualificação Profissional", x["qualificação"]), ("Regime Jurídico", x["regime"])]),
             (f"Polo Passivo ({d['tipo passivo'].title()})",
              [("Tipo da Parte", d["tipo passivo"]), ("Tipo de Pessoa", PESSOA_TEXTO[d["pessoa passivo"]]),
               ("Entidade", d["entidade"]), ("CNPJ", robo.formatar_documento(cnpj)),
               ("Órgão Vinculado", x["órgão"])])]
    for titulo, linhas in polos:
        doc.add_heading(titulo, 2)
        for campo, valor in linhas:
            if valor:
                doc.add_paragraph(f"{campo}: {valor}")

    doc.add_heading("3. Características da Causa", 0)
    for campo, valor in [("Justiça Gratuita", d["justiça gratuita"]),
                         ("Pedido de Liminar ou Antecipação de Tutela", d["pedido de liminar ou antecipação de tutela"]),
                         ("Valor da Causa", f"R$ {d['valor da causa']}"),
                         ("Segredo de Justiça", d["segredo de justiça"]),
                         ("Prioridade de Processo", x["prioridade"])]:
        doc.add_paragraph(f"{campo}: {valor}")

    nome_curto = re.sub(r"\s*\[.*$", "", os.path.basename(pasta)).strip()
    caminho = os.path.join(pasta, f"Cadastro de Ação Judicial - {nome_curto} - {classe}.docx")
    doc.save(caminho)
    return caminho, d


def conferir(caminho, dados):
    """O robô lê do .docx novo os mesmos valores que vieram da inicial? Devolve os campos que não batem."""
    lido = robo.ler_docx(caminho)
    return [c for c in robo.CAMPOS_OBRIGATORIOS + ["cpf"] if not c.startswith("pessoa ")
            and robo.comparavel(c, lido.get(c, "")) != robo.comparavel(c, dados.get(c) or "")]


if __name__ == "__main__":
    raiz = os.path.dirname(robo.PASTA)
    for pasta in robo.pastas_de_clientes(raiz):
        if robo.arquivos_da_pasta(pasta)[0]:
            print(f"já tem .docx: {os.path.basename(pasta)}")
            continue
        try:
            caminho, dados = criar_docx(pasta)
            robo.preparar(pasta)  # com o .docx novo, o robô continua sem divergência com a inicial
        except SystemExit as e:
            print(f"ERRO {os.path.basename(pasta)}: {e}")
            continue
        diferentes = conferir(caminho, dados)
        print(f"{'CONFERIR ' + str(diferentes) if diferentes else 'criado'}: {caminho}")
