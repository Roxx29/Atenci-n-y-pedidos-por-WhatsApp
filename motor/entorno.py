"""Carga <raíz del repo>/.env en os.environ sin pisar lo ya definido. Nunca imprime valores."""
import os
from pathlib import Path


def cargar_env(ruta=None):
    ruta = Path(ruta) if ruta else Path(__file__).resolve().parent.parent / ".env"
    try:
        lineas = ruta.read_text(encoding="utf-8-sig").splitlines()
    except OSError:  # sin .env (p. ej. empaquetado): se ignora
        return
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("export "):
            linea = linea[7:].lstrip()
        clave, sep, valor = linea.partition("=")
        clave, valor = clave.strip(), valor.strip()
        if not sep or not clave:
            continue
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "'\"":
            valor = valor[1:-1]
        os.environ.setdefault(clave, valor)
