#!/bin/bash
# Atualiza o código do servidor (git pull) sem derrubar cadastro no meio: cada robô só reinicia quando está parado.
#   sudo bash /srv/robo/codigo/servidor/atualizar.sh
set -euo pipefail
RAIZ=/srv/robo
cd "$RAIZ"  # sudo mantém a pasta de quem rodou; o usuário robo não lê a pasta pessoal de ninguém
if [ -z "${ATUALIZAR_JA_BAIXOU:-}" ]; then
  sudo -u robo git -C $RAIZ/codigo pull --ff-only
  # o bash segue lendo a cópia antiga deste arquivo: roda de novo a versão que acabou de chegar
  ATUALIZAR_JA_BAIXOU=1 exec bash $RAIZ/codigo/servidor/atualizar.sh
fi
sudo -u robo env UV_PYTHON_INSTALL_DIR=/opt/uv-python uv pip install --no-config --python $RAIZ/venv/bin/python -q \
  -r $RAIZ/codigo/servidor/requisitos.txt
cp $RAIZ/codigo/servidor/robo@.service $RAIZ/codigo/servidor/porteiro.service $RAIZ/codigo/servidor/limpar.service \
   $RAIZ/codigo/servidor/limpar.timer /etc/systemd/system/
systemctl daemon-reload
# administrador cria escritório pela página: o porteiro (robo) só pode rodar esta cópia com dono root, via sudo
install -o root -g root -m 755 $RAIZ/codigo/servidor/ligar_escritorio.sh /usr/local/sbin/autus-ligar-escritorio
echo 'robo ALL=(root) NOPASSWD: /usr/local/sbin/autus-ligar-escritorio' > /etc/sudoers.d/autus.novo
chmod 440 /etc/sudoers.d/autus.novo
visudo -cqf /etc/sudoers.d/autus.novo && mv /etc/sudoers.d/autus.novo /etc/sudoers.d/autus

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
# a página no Firebase Hosting (ROBO_SITE e ROBO_API no porteiro.env) sai ANTES do porteiro reiniciar: o porteiro
# novo manda quem chega pelo endereço do servidor para a página do Firebase
if grep -q '^ROBO_SITE=.' $RAIZ/porteiro.env; then
  sudo -u robo bash -c "set -a; . $RAIZ/porteiro.env; exec $RAIZ/venv/bin/python $RAIZ/codigo/servidor/publicar_pagina.py"
fi
systemctl restart porteiro
echo "Atualizado."
