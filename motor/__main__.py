"""Uso:
  python -m motor validar <carpeta>        revisa servicio.yaml sin llamar a la API
  python -m motor chat <carpeta>           conversa en la terminal como si fueras un cliente
  python -m motor probar <carpeta>         corre los mensajes de prueba.yaml (base de datos temporal)
  python -m motor servir <carpeta> [puerto] webhook de WhatsApp + envío de recordatorios
  python -m motor web <carpeta> [puerto]   canal web de pedidos con chat (por defecto 8080)
  python -m motor pedidos <carpeta>        lista los pedidos
  python -m motor pedidos <carpeta> marcar <id> pagado|en_preparacion|listo|en_camino|entregado|pago_rechazado
  python -m motor panel [carpeta]          panel gráfico del restaurante (tkinter)
  python -m motor migrar <carpeta> [--forzar]  copia datos.sqlite a Turso (TURSO_DATABASE_URL y TURSO_AUTH_TOKEN)
  python -m motor recordatorios <carpeta>  envía una vez los mensajes programados vencidos
"""
import sys
import tempfile
from pathlib import Path

import yaml

from . import herramientas, whatsapp
from .entorno import cargar_env
from .nucleo import Servicio


def main(argv, cliente=None):  # cliente: inyectable en pruebas (solo "probar")
    cargar_env()
    if argv[:1] == ["panel"]:
        from .panel import main as panel  # import diferido: no carga tkinter en los demás comandos
        return panel(argv[1:])
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
            sv = Servicio(carpeta, db=Path(tmp) / "prueba.sqlite", cliente=cliente)
            try:
                prueba = yaml.safe_load((Path(carpeta) / "prueba.yaml").read_text(encoding="utf-8"))
                for caso in prueba["casos"]:
                    print(f"\n=== {caso['nombre']} ===")
                    for t in caso["mensajes"]:
                        print(f"cliente> {t}\nbot> {sv.responder(caso.get('telefono', '50760000001'), t)}")
                for tipo in ("cita", "pedido", "aviso", "mensaje_programado", "escalado"):
                    print(f"\n[{tipo}] {sv.listar(tipo)}")
            finally:
                sv.cerrar()  # Windows no borra el directorio con la base abierta
    elif cmd == "pedidos":
        sv = Servicio(carpeta)
        if argv[2:3] == ["marcar"]:
            if len(argv) != 5:
                raise SystemExit("Uso: python -m motor pedidos <carpeta> marcar <id> pagado|en_preparacion|listo|en_camino|entregado|pago_rechazado")
            try:
                herramientas.marcar_pedido(sv, int(argv[3]), argv[4])
            except ValueError as e:
                raise SystemExit(f"Error: {e}")
        for p in sv.listar("pedido"):
            print(f"#{p['id']} {p['estado']} · {p['nombre']} · {p['modalidad']} · {herramientas.dinero(p['total_c'])}" + (f" · pago {p.get('metodo', '')} ref {p['referencia']}" if p["estado"] == "pago_reportado" else ""))
    elif cmd == "servir":
        whatsapp.servir(Servicio(carpeta), int(argv[2]) if len(argv) > 2 else 8080)
    elif cmd == "web":
        from . import web  # import diferido
        web.servir(Servicio(carpeta), int(argv[2]) if len(argv) > 2 else 8080)
    elif cmd == "migrar":
        from .migrar import migrar
        migrar(carpeta, forzar="--forzar" in argv)
    elif cmd == "recordatorios":
        whatsapp.enviar_pendientes(Servicio(carpeta))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
