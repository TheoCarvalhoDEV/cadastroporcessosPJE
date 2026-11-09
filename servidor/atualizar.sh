#!/bin/bash
# Atualiza o código do servidor (git pull) sem derrubar cadastro no meio: cada robô só reinicia quando está parado.
#   sudo bash /srv/robo/codigo/servidor/atualizar.sh
set -euo pipefail
RAIZ=/srv/robo
cd "$RAIZ"  # sudo mantém a pasta de quem rodou; o usuário robo não lê a pasta pessoal de ninguém
sudo -u robo git -C $RAIZ/codigo pull --ff-only
sudo -u robo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv pip install --no-config --python $RAIZ/venv/bin/python -q \
  -r $RAIZ/codigo/servidor/requisitos.txt
cp $RAIZ/codigo/servidor/robo@.service $RAIZ/codigo/servidor/porteiro.service $RAIZ/codigo/servidor/limpar.service \
   $RAIZ/codigo/servidor/limpar.timer /etc/systemd/system/
systemctl daemon-reload

# ponytail: uma tarefa pode começar entre a conferência e o reinício; aceitável com poucos escritórios
for env in $RAIZ/escritorios/*/robo.env; do
  [ -e "$env" ] || continue
  id=$(basename "$(dirname "$env")")
  porta=$(grep '^ROBO_PORTA=' "$env" | cut -d= -f2)
  while tarefa=$(curl -s "127.0.0.1:$porta/api/estado" | $RAIZ/venv/bin/python -c \
      'import json, sys; print(json.load(sys.stdin)["tarefa"])' 2>/dev/null) && [ -n "$tarefa" ]; do
    echo "Esperando o escritório $id terminar ($tarefa)..."
    sleep 10
  done
  systemctl restart "robo@$id"
  echo "robo@$id reiniciado"
done
systemctl restart porteiro
echo "Atualizado."
