#!/bin/bash
# Liga o robô de um escritório novo: pasta, segredo do login do PJe (Secret Manager, São Paulo) e serviço robo@<id>.
# É a ÚNICA parte de criar escritório que precisa de root. O atualizar.sh instala uma cópia com dono root em
# /usr/local/sbin/autus-ligar-escritorio, e o porteiro (usuário robo) só pode rodar ela com sudo (/etc/sudoers.d/autus).
# Por isso confere tudo o que recebe e não segue atalhos (symlink) dentro das pastas do usuário robo.
#   sudo /usr/local/sbin/autus-ligar-escritorio <id> <porta>
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
ID=${1:-}
PORTA=${2:-}
[[ "$ID" =~ ^[a-z0-9-]{2,30}$ ]] || { echo "Identificador inválido." >&2; exit 2; }
[[ "$PORTA" =~ ^8[0-9]{3}$ ]] && (( PORTA > 8800 )) || { echo "Porta inválida." >&2; exit 2; }
BASE=/srv/robo/escritorios
PASTA=$BASE/$ID
[ ! -L "$BASE" ] && [ ! -L "$PASTA" ] && [ ! -L "$PASTA/clientes" ] && [ ! -L "$PASTA/robo.env" ] \
  || { echo "Pasta do escritório com atalho: recusado." >&2; exit 2; }
mkdir -p "$PASTA/clientes"
[ -e "$PASTA/robo.env" ] || printf 'ROBO_PORTA=%s\nROBO_ESCRITORIO=%s\n' "$PORTA" "$ID" > "$PASTA/robo.env"
chown -R --no-dereference robo:robo "$PASTA"
gcloud secrets describe "pje-login-$ID" --quiet >/dev/null 2>&1 \
  || gcloud secrets create "pje-login-$ID" --replication-policy=user-managed --locations=southamerica-east1 --quiet
systemctl enable --now "robo@$ID"
echo "Robô do escritório $ID ligado (porta $PORTA)."
