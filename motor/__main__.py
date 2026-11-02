"""Uso:
  python -m motor validar <carpeta>        revisa servicio.yaml sin llamar a la API
  python -m motor chat <carpeta>           conversa en la terminal como si fueras un cliente
  python -m motor probar <carpeta>         corre los mensajes de prueba.yaml (base de datos temporal)
  python -m motor servir <carpeta> [puerto] webhook de WhatsApp + envío de recordatorios
  python -m motor recordatorios <carpeta>  envía una vez los mensajes programados vencidos
"""
import sys
import tempfile
from pathlib import Path

import yaml

from . import whatsapp
from .nucleo import Servicio


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd, carpeta = argv[0], argv[1]
    if cmd == "validar":
        sv = Servicio(carpeta, db=":memory:")
        print(f"OK: {sv.cfg.get('nombre')} · {len(sv.tools)} herramientas · prompt de {len(sv.system)} caracteres")
    elif cmd == "chat":
        sv = Servicio(carpeta)
        print(f"{sv.cfg.get('nombre')} — escribe 'salir' para terminar")
        while (t := input("cliente> ").strip()) != "salir":
            print("bot>", sv.responder("50760000000", t))
    elif cmd == "probar":
        with tempfile.TemporaryDirectory() as tmp:
            sv = Servicio(carpeta, db=Path(tmp) / "prueba.sqlite")
            prueba = yaml.safe_load((Path(carpeta) / "prueba.yaml").read_text(encoding="utf-8"))
            for caso in prueba["casos"]:
                print(f"\n=== {caso['nombre']} ===")
                for t in caso["mensajes"]:
                    print(f"cliente> {t}\nbot> {sv.responder(caso.get('telefono', '50760000001'), t)}")
            for tipo in ("cita", "mensaje_programado", "escalado"):
                print(f"\n[{tipo}] {sv.listar(tipo)}")
    elif cmd == "servir":
        whatsapp.servir(Servicio(carpeta), int(argv[2]) if len(argv) > 2 else 8080)
    elif cmd == "recordatorios":
        whatsapp.enviar_pendientes(Servicio(carpeta))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
