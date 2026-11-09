"""Limpeza diária do servidor (servidor/limpar.timer): apaga o que passou de 30 dias em Concluído, na Lixeira e nas
fotos das pausas de cada escritório. Cliente ainda não cadastrado nunca é apagado (o escritório tem os originais
de tudo, porque foi ele que enviou). Uso: python limpar.py"""
import glob
import os
import shutil
import time


def limpar(raiz_escritorios, dias=30, agora=None):
    """Apaga e devolve o que é mais velho que `dias` (pela data da pasta, que mover_para() acerta ao mudar)."""
    limite = (agora or time.time()) - dias * 86400
    removidos = []
    for padrao in ("*/clientes/Concluído/*", "*/clientes/Lixeira/*", "*/erros/*"):
        for caminho in glob.glob(os.path.join(raiz_escritorios, padrao)):
            if os.path.getmtime(caminho) < limite:
                if os.path.isdir(caminho):
                    shutil.rmtree(caminho)
                else:
                    os.remove(caminho)
                removidos.append(caminho)
    return removidos


if __name__ == "__main__":
    removidos = limpar(os.path.join(os.environ.get("ROBO_RAIZ", "/srv/robo"), "escritorios"))
    print(f"Limpeza: {len(removidos)} itens com mais de 30 dias apagados.")
