#!/bin/bash
# Monta (ou atualiza) o servidor do robô numa máquina Ubuntu 24.04 do Google Cloud. Rodar como root:
#   sudo bash montar.sh <dominio> <firebase-api-key> [site-do-firebase-hosting]
# Com o site (ex.: autus), a página fica em https://autus.web.app e o servidor só atende o trabalho (publicar_pagina.py).
# Rodar de novo não estraga nada: não troca a chave das sessões nem mexe nos dados dos escritórios.
# As senhas e chaves ficam em /srv/robo/*.env (chmod 600), nunca no repositório.
set -euo pipefail
DOMINIO=${1:?uso: sudo bash montar.sh <dominio> <firebase-api-key>}
API_KEY=${2:?uso: sudo bash montar.sh <dominio> <firebase-api-key>}
RAIZ=/srv/robo
CODIGO=$RAIZ/codigo
REPO=https://github.com/TheoCarvalhoDEV/cadastroporcessosPJE
export DEBIAN_FRONTEND=noninteractive
export UV_PYTHON_INSTALL_DIR=/opt/uv-python  # o Python fica fora do /root, legível pelo usuário robo

apt-get update -qq
apt-get install -y -qq git curl gnupg openssl unattended-upgrades debian-keyring debian-archive-keyring apt-transport-https
dpkg-reconfigure -f noninteractive unattended-upgrades  # atualizações de segurança automáticas
timedatectl set-timezone America/Cuiaba  # relatório, registro do robô e fotos das pausas na hora do escritório
# depois de atualizar o Ubuntu, o needrestart não reinicia os robôs sozinho (derrubaria um cadastro no meio)
mkdir -p /etc/needrestart/conf.d
echo '$nrconf{override_rc}{qr(^robo@)} = 0;' > /etc/needrestart/conf.d/robo.conf

id robo >/dev/null 2>&1 || useradd --system --home-dir $RAIZ --shell /usr/sbin/nologin robo
mkdir -p $RAIZ/escritorios
git config --global --add safe.directory $CODIGO
if [ -d $CODIGO/.git ]; then git -C $CODIGO pull --ff-only; else git clone $REPO $CODIGO; fi

# Python 3.14 (mesma versão do desenvolvimento) com uv, e o Google Chrome que o robô usa
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
[ -x $RAIZ/venv/bin/python ] || uv venv --no-config --python 3.14 $RAIZ/venv
uv pip install --no-config --python $RAIZ/venv/bin/python -q -r $CODIGO/servidor/requisitos.txt
$RAIZ/venv/bin/playwright install --with-deps chrome

# Caddy: HTTPS automático na frente do porteiro
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi
cp $CODIGO/servidor/Caddyfile /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironment=ROBO_DOMINIO=%s\n' "$DOMINIO" > /etc/systemd/system/caddy.service.d/robo.conf

# variáveis dos serviços (fora do repositório)
PROJETO=$(curl -s -H "Metadata-Flavor: Google" http://metadata.google.internal/computeMetadata/v1/project/project-id)
printf 'ROBO_SERVIDOR=1\nROBO_PROJETO=%s\n' "$PROJETO" > $RAIZ/robo.env
CHAVE=$(grep -s '^ROBO_CHAVE_SESSAO=' $RAIZ/porteiro.env | cut -d= -f2- || true)
[ -n "$CHAVE" ] || CHAVE=$(openssl rand -hex 32)  # gerada uma vez só: trocar derruba todas as sessões
SITE=${3:-$(grep -s '^ROBO_SITE=' $RAIZ/porteiro.env | cut -d= -f2- || true)}  # sem o 3º argumento, mantém o que tinha
printf 'ROBO_RAIZ=%s\nFIREBASE_API_KEY=%s\nROBO_CHAVE_SESSAO=%s\nROBO_SITE=%s\nROBO_API=https://%s\n' \
  "$RAIZ" "$API_KEY" "$CHAVE" "$SITE" "$DOMINIO" > $RAIZ/porteiro.env

cp $CODIGO/servidor/robo@.service $CODIGO/servidor/porteiro.service $CODIGO/servidor/limpar.service \
   $CODIGO/servidor/limpar.timer /etc/systemd/system/
chown -R robo:robo $RAIZ
chmod 600 $RAIZ/*.env
systemctl daemon-reload
systemctl enable --now porteiro limpar.timer
systemctl restart caddy
bash $CODIGO/servidor/atualizar.sh  # reinicia cada robô só quando ele estiver parado, e o porteiro por último
echo "Pronto: https://$DOMINIO"
