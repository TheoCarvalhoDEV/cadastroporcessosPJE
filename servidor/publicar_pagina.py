"""Publica a página (sistema/web) no Firebase Hosting, em https://<ROBO_SITE>.web.app, apontando para o servidor
(ROBO_API, ex.: https://34-39-255-214.sslip.io). Assim o endereço do Firebase fica na barra do navegador e o trabalho
pesado (login, pastas, robô) continua no servidor. Roda no atualizar.sh, com a conta da máquina do Google.
Uso: ROBO_SITE=autus ROBO_API=https://... python publicar_pagina.py   (fora da máquina: --token <token do gcloud>)
"""
import gzip
import hashlib
import json
import os
import re
import sys
import urllib.request

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "sistema", "web")
HOSTING = "https://firebasehosting.googleapis.com/v1beta1/"
META_VAZIA = '<meta name="api" content="">'
META_GOOGLE = '<meta name="google-client" content="">'  # ID do cliente OAuth do botão do Google (vazio: sem botão)


def configuracao(api, google=False):
    """Regras do site: toda rota mostra a página (ex.: /nova-senha do e-mail), cabeçalhos de segurança e cache.
    Com o login do Google, libera só o que o botão oficial do Google usa (accounts.google.com/gsi)."""
    gsi = " https://accounts.google.com/gsi/" if google else ""
    politica = "; ".join([
        "default-src 'self'", f"connect-src 'self' {api}{gsi}", "img-src 'self' data: blob:" + (" https://*.googleusercontent.com" if google else ""),
        f"style-src 'self' 'unsafe-inline'{gsi}", f"script-src 'self' 'unsafe-inline'{' https://accounts.google.com/gsi/client' if google else ''}",
        "font-src 'self'", f"frame-src{gsi}" if google else "frame-src 'none'",
        "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'", "object-src 'none'"])
    seguranca = {"Content-Security-Policy": politica, "X-Content-Type-Options": "nosniff",
                 "Referrer-Policy": "no-referrer", "Strict-Transport-Security": "max-age=31536000",
                 "Permissions-Policy": "camera=(), microphone=(), geolocation=()"}
    return {"cleanUrls": True,  # /privacidade e /termos (as páginas públicas que o Google pede) sem o .html
            "rewrites": [{"glob": "**", "path": "/index.html"}],
            "headers": [{"glob": "**", "headers": {**seguranca, "Cache-Control": "no-cache"}},
                        {"glob": "/fontes/**", "headers": {"Cache-Control": "public, max-age=31536000, immutable"}}]}


def arquivos(api, google_client=""):
    """{caminho no site: bytes}: a página com o endereço do servidor (e o ID do botão do Google) e as fontes."""
    with open(os.path.join(WEB, "index.html"), encoding="utf-8") as f:
        pagina = f.read()
    for meta in (META_VAZIA, META_GOOGLE):
        if meta not in pagina:
            raise SystemExit("index.html sem a linha " + meta)
    pagina = pagina.replace(META_VAZIA, f'<meta name="api" content="{api}">')
    pagina = pagina.replace(META_GOOGLE, f'<meta name="google-client" content="{google_client}">')
    saida = {"/index.html": pagina.encode("utf-8")}
    for nome in ("privacidade.html", "termos.html"):  # Política de Privacidade e Termos de Serviço
        with open(os.path.join(WEB, nome), "rb") as f:
            saida["/" + nome] = f.read()
    for nome in sorted(os.listdir(os.path.join(WEB, "fontes"))):
        if nome.endswith(".woff2"):
            with open(os.path.join(WEB, "fontes", nome), "rb") as f:
                saida["/fontes/" + nome] = f.read()
    return saida


def token_da_maquina():
    pedido = urllib.request.Request("http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/"
                                    "default/token", headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(pedido, timeout=5) as r:
        return json.load(r)["access_token"]


def chamar(token, metodo, url, corpo=None, tipo="application/json"):
    dados = corpo if isinstance(corpo, bytes) or corpo is None else json.dumps(corpo).encode("utf-8")
    pedido = urllib.request.Request(url if url.startswith("https://") else HOSTING + url, data=dados, method=metodo,
                                    headers={"Authorization": "Bearer " + token, "Content-Type": tipo})
    with urllib.request.urlopen(pedido, timeout=60) as r:
        texto = r.read()
        return json.loads(texto) if texto else {}


def publicar(site, api, token, google_client=""):
    comprimidos = {caminho: gzip.compress(conteudo, mtime=0) for caminho, conteudo in arquivos(api, google_client).items()}
    resumos = {caminho: hashlib.sha256(c).hexdigest() for caminho, c in comprimidos.items()}
    versao = chamar(token, "POST", f"sites/{site}/versions", {"config": configuracao(api, bool(google_client))})["name"]
    pedido = chamar(token, "POST", f"{versao}:populateFiles", {"files": resumos})
    faltam = set(pedido.get("uploadRequiredHashes", []))
    for caminho, resumo in resumos.items():
        if resumo in faltam:  # o Firebase só pede o que mudou desde a última publicação
            chamar(token, "POST", f"{pedido['uploadUrl']}/{resumo}", comprimidos[caminho], "application/octet-stream")
    chamar(token, "PATCH", f"{versao}?update_mask=status", {"status": "FINALIZED"})
    chamar(token, "POST", f"sites/{site}/releases?versionName={versao}", {"message": "atualizar.sh"})
    return len(faltam)


if __name__ == "__main__":
    site, api = os.environ.get("ROBO_SITE", ""), os.environ.get("ROBO_API", "").rstrip("/")
    if not site or not api.startswith("https://"):
        sys.exit("Faltam ROBO_SITE e ROBO_API (https://...) para publicar a página no Firebase Hosting.")
    token = sys.argv[sys.argv.index("--token") + 1] if "--token" in sys.argv else token_da_maquina()
    google_client = os.environ.get("ROBO_GOOGLE_CLIENT", "").strip()
    if google_client and not re.fullmatch(r"[0-9a-z-]+\.apps\.googleusercontent\.com", google_client):
        sys.exit("ROBO_GOOGLE_CLIENT não parece um ID de cliente OAuth do Google (termina em .apps.googleusercontent.com).")
    enviados = publicar(site, api, token, google_client)
    print(f"Página publicada em https://{site}.web.app ({enviados} arquivo(s) novo(s)), falando com {api}")
