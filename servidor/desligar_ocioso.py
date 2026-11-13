"""Desliga a máquina do robô quando ninguém usa o Autus há 30 minutos e nenhum robô está trabalhando. Parada, a máquina
não cobra (só o disco e o endereço fixo); quem abrir o Autus depois liga de novo (servidor/ligar.py, no Cloud Run).
"Uso" = o porteiro anota em ultimo_uso cada pedido da página, menos a consulta automática do estado (/api/estado).
Roda a cada 5 minutos pelo desligar.timer, como root.
"""
import glob
import json
import os
import subprocess
import time
import urllib.request

RAIZ = os.environ.get("ROBO_RAIZ", "/srv/robo")
OCIOSO = 30 * 60


def ocupado(arquivo_env):
    """True se o robô deste escritório está cadastrando, pausado esperando alguém ou com a IA lendo uma petição."""
    with open(arquivo_env, encoding="utf-8") as f:
        porta = next(linha.split("=", 1)[1].strip() for linha in f if linha.startswith("ROBO_PORTA="))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/api/estado?desde=999999999", timeout=10) as r:
            estado = json.load(r)
    except (OSError, ValueError):
        return False  # robô fora do ar não segura a máquina ligada
    # ponytail: entre uma petição e a próxima da fila da IA, ia_lendo fica vazio por instantes; o que sobrar na
    # fila é lido quando a máquina ligar de novo
    return bool(estado.get("tarefa") or estado.get("pausa") or estado.get("ia_lendo"))


def segundos_sem_uso(agora=None):
    agora = agora or time.time()
    try:
        with open("/proc/uptime") as f:
            ligou = agora - float(f.read().split()[0])  # recém-ligada conta como uso: quem ligou vai entrar
    except OSError:  # fora do Linux (testes)
        ligou = 0
    try:
        ultimo = max(ligou, os.path.getmtime(os.path.join(RAIZ, "ultimo_uso")))
    except OSError:
        ultimo = ligou
    return agora - ultimo


def deve_desligar():
    parado = segundos_sem_uso()
    return parado > OCIOSO and not any(ocupado(e) for e in glob.glob(os.path.join(RAIZ, "escritorios", "*", "robo.env")))


if __name__ == "__main__" and deve_desligar():
    print(f"Ninguém usa o Autus há {segundos_sem_uso() / 60:.0f} min e nenhum robô está trabalhando: desligando.", flush=True)
    subprocess.run(["systemctl", "poweroff"], check=False)
