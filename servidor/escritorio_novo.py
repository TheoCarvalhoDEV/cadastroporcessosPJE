"""Entra um escritório no servidor, ou mais e-mails num escritório que já existe. Rodar como root no servidor:
  sudo /srv/robo/venv/bin/python /srv/robo/codigo/servidor/escritorio_novo.py <id> "<nome>" <email> [<email>...]
Escritório novo: cria a pasta, o segredo do login do PJe (Secret Manager, guardado em São Paulo) e liga o robô
(systemd robo@<id>). Para cada e-mail: cria o usuário no Firebase e manda o link de criar senha.
Depois, o escritório entra na página e preenche Ajustes > Login do PJe.
Administrador do Autus (vê todos os escritórios na página; a lista só muda por aqui, nunca pela página):
  sudo /srv/robo/venv/bin/python /srv/robo/codigo/servidor/escritorio_novo.py --admin <email>
O administrador também cria escritórios e libera e-mails pela página (porteiro.py + ligar_escritorio.sh)."""
import json
import os
import re
import shutil
import subprocess
import sys

RAIZ = os.environ.get("ROBO_RAIZ", "/srv/robo")


EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def ler(arquivo):
    try:
        with open(arquivo, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"escritorios": {}}


def gravar(arquivo, dados):
    with open(arquivo + ".tmp", "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.replace(arquivo + ".tmp", arquivo)


def limpar_emails(emails):
    emails = list(dict.fromkeys(e.strip().lower() for e in emails if e.strip()))
    ruins = [e for e in emails if not EMAIL.fullmatch(e)]
    if ruins:
        sys.exit(f"E-mail inválido: {', '.join(ruins)}.")
    return emails


def adicionar(arquivo, id_, nome, emails):
    """Grava o escritório (ou os e-mails a mais) no escritorios.json e devolve a porta local do robô dele."""
    if not re.fullmatch(r"[a-z0-9-]{2,30}", id_):
        sys.exit(f"Identificador '{id_}' inválido: use de 2 a 30 letras minúsculas, números ou hífen.")
    dados = ler(arquivo)
    escritorios = dados["escritorios"]
    emails = limpar_emails(emails)
    if id_ not in escritorios and not (nome.strip() and len(nome) <= 80):
        sys.exit("Escreva o nome do escritório (até 80 letras).")
    for outro, e in escritorios.items():
        repetidos = set(emails) & set(e["emails"])
        if outro != id_ and repetidos:  # o porteiro acha o escritório pelo e-mail: tem que ser de um só
            sys.exit(f"E-mail já é do escritório '{outro}': {', '.join(sorted(repetidos))}.")
    if id_ in escritorios:
        escritorios[id_]["emails"] += [e for e in emails if e not in escritorios[id_]["emails"]]
    else:
        porta = max([8800] + [e["porta"] for e in escritorios.values()]) + 1  # 8800 é do porteiro
        escritorios[id_] = {"nome": nome.strip(), "porta": porta, "emails": emails}
    gravar(arquivo, dados)
    return escritorios[id_]["porta"]


def retirar(arquivo, id_, emails):
    """Tira e-mails de um escritório: o acesso cai na hora (o porteiro relê o arquivo a cada pedido)."""
    dados = ler(arquivo)
    if id_ not in dados["escritorios"]:
        sys.exit(f"Escritório '{id_}' não existe.")
    fora = {e.strip().lower() for e in emails}
    dados["escritorios"][id_]["emails"] = [e for e in dados["escritorios"][id_]["emails"] if e not in fora]
    gravar(arquivo, dados)


def adicionar_admin(arquivo, email):
    """Administrador do Autus. Só pela linha de comando do servidor (a página não promove ninguém)."""
    email, = limpar_emails([email])
    dados = ler(arquivo)
    dados["admins"] = list(dict.fromkeys(dados.get("admins", []) + [email]))
    gravar(arquivo, dados)
    return email


def carregar_env(caminho):
    """As variáveis do porteiro (chave do Firebase), para criar os usuários."""
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            chave, _, valor = linha.strip().partition("=")
            if chave:
                os.environ.setdefault(chave, valor)


if __name__ == "__main__":
    arquivo = os.path.join(RAIZ, "escritorios.json")
    if len(sys.argv) == 3 and sys.argv[1] == "--admin":
        email = adicionar_admin(arquivo, sys.argv[2])
        shutil.chown(arquivo, "robo", "robo")
        carregar_env(os.path.join(RAIZ, "porteiro.env"))
        import porteiro
        porteiro.criar_usuario(email)
        porteiro.pedir_nova_senha(email)
        print(f"Administrador {email} pronto. O link de criar senha foi para o e-mail.")
        sys.exit(0)
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    id_, nome, emails = sys.argv[1], sys.argv[2], sys.argv[3:]
    novo = id_ not in ler(arquivo)["escritorios"]
    porta = adicionar(arquivo, id_, nome, emails)
    shutil.chown(arquivo, "robo", "robo")
    if novo:
        pasta = os.path.join(RAIZ, "escritorios", id_)
        os.makedirs(os.path.join(pasta, "clientes"), exist_ok=True)
        with open(os.path.join(pasta, "robo.env"), "w", encoding="utf-8") as f:
            f.write(f"ROBO_PORTA={porta}\nROBO_ESCRITORIO={id_}\n")
        for raiz, pastas, arquivos in os.walk(pasta):
            for n in [raiz] + [os.path.join(raiz, a) for a in pastas + arquivos]:
                shutil.chown(n, "robo", "robo")
        subprocess.run(["gcloud", "secrets", "create", f"pje-login-{id_}", "--replication-policy=user-managed",
                        "--locations=southamerica-east1"], check=True)
        subprocess.run(["systemctl", "enable", "--now", f"robo@{id_}"], check=True)
    carregar_env(os.path.join(RAIZ, "porteiro.env"))
    import porteiro
    for email in emails:
        porteiro.criar_usuario(email.strip().lower())
        porteiro.pedir_nova_senha(email.strip().lower())
    print(f"Escritório '{id_}' pronto (porta {porta}). E-mails com o link de criar senha: {', '.join(emails)}.")
