"""Liga a máquina do robô (robo-pje, Compute Engine) quando alguém abre o Autus. A máquina se desliga sozinha depois de
30 minutos sem uso (desligar_ocioso.py); este serviço fica no Cloud Run, que sem uso não cobra nada.
POST /  ->  {"estado": "RUNNING" | "LIGANDO" | "STOPPING" | ...}: liga a máquina se estiver parada; ligada, não faz nada.
A conta autus-ligar só pode ver e ligar esta máquina (papel autusLigar: compute.instances.get e .start).

Sem build (nada de Cloud Build nem repositório de imagens): roda na imagem pública do Python com este arquivo na variável
CODIGO. Publicar de novo depois de mudar:
  python servidor/ligar.py --yaml > ligar.env.yaml
  gcloud run deploy autus-ligar --project=robo-pje-tjmt --region=southamerica-east1 \
    --image=docker.io/library/python:3.12-alpine --command=python3 --args=-c,"exec(__import__('os').environ['CODIGO'])" \
    --env-vars-file=ligar.env.yaml --service-account=autus-ligar@robo-pje-tjmt.iam.gserviceaccount.com \
    --memory=128Mi --cpu=1 --max-instances=1 --concurrency=20 --timeout=30 --quiet
(a liberação para a página chamar sem login, allUsers = roles/run.invoker, foi dada uma vez e o deploy mantém)
"""
import json
import os
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAQUINA = ("https://compute.googleapis.com/compute/v1/projects/robo-pje-tjmt/zones/southamerica-east1-a/"
           "instances/robo-pje")
ORIGENS = {"https://autus.web.app", "https://autus.firebaseapp.com"}  # só a página do Autus lê a resposta


def google(metodo, url):
    pedido = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/"
                                    "default/token", headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(pedido, timeout=5) as r:
        token = json.load(r)["access_token"]
    pedido = urllib.request.Request(url, method=metodo, data=b"" if metodo == "POST" else None,
                                    headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(pedido, timeout=20) as r:
        return json.load(r)


class Ligar(BaseHTTPRequestHandler):
    def cabecalhos(self, codigo):
        self.send_response(codigo)
        if (origem := self.headers.get("Origin", "")) in ORIGENS:
            self.send_header("Access-Control-Allow-Origin", origem)
            self.send_header("Access-Control-Allow-Methods", "POST")
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.cabecalhos(204)
        self.end_headers()

    def do_POST(self):
        try:
            estado = google("GET", MAQUINA)["status"]
            if estado == "TERMINATED":  # parada: liga (STOPPING ainda não aceita; a página pede de novo em 1 min)
                google("POST", MAQUINA + "/start")
                estado = "LIGANDO"
            corpo, codigo = {"estado": estado}, 200
        except Exception as e:  # sem permissão, Google fora do ar...: a página mostra o erro normal do servidor
            corpo, codigo = {"erro": str(e)[:300]}, 502
        self.cabecalhos(codigo)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(corpo).encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__" and "--yaml" in sys.argv:  # a variável CODIGO do Cloud Run (gcloud --env-vars-file)
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        print("CODIGO: " + json.dumps(f.read()))
elif "PORT" in os.environ:  # no Cloud Run
    ThreadingHTTPServer(("", int(os.environ["PORT"])), Ligar).serve_forever()
