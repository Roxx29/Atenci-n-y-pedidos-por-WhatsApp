"""Sube a la consola del operador las paginas propias de la carpeta Videos/<carpeta> (web de pedidos + panel) de cada restaurante. Solo stdlib.
Uso: python despliegue/subir_paginas.py [https://operador.nubiksoft.com]
La clave del operador se pide por teclado (o OPERADOR_CLAVE en el entorno); no se guarda. Cada restaurante se reconoce por su dominio."""
import base64
import getpass
import http.cookiejar
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VIDEOS = Path.home() / "Videos"
CARPETAS = {"mamaelia.nubiksoft.com": "casa-de-mama-elia", "urbansazon.nubiksoft.com": "urban-sazon", "delifood.nubiksoft.com": "chitre-delifood"}  # dominio -> carpeta
base = (sys.argv[1] if len(sys.argv) > 1 else "https://operador.nubiksoft.com").rstrip("/")
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

def api(metodo, ruta, d=None):
    r = urllib.request.Request(base + ruta, data=json.dumps(d).encode() if d is not None else None, method=metodo, headers={"Content-Type": "application/json"})
    try:
        with op.open(r, timeout=120) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"null")
        except ValueError:
            return e.code, None

def zip_de(carpeta):  # todos los archivos de la carpeta en la raíz; sin copias .bak
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(carpeta.iterdir()):
            if f.is_file() and ".bak" not in f.name and f.suffix != ".zip":
                z.write(f, f.name)
    return mem.getvalue()

c, j = api("POST", "/api/entrar", {"clave": os.environ.get("OPERADOR_CLAVE") or getpass.getpass("Clave del operador: ")})
if c != 200:
    sys.exit(f"No entra a la consola (HTTP {c}). Si es 403, tu IP no está en OPERADOR_IPS.")
c, cl = api("GET", "/api/clientes")
por_dominio = {x["dominio"]: x["slug"] for x in (cl["clientes"] if isinstance(cl, dict) else cl or [])}
for dom, carpeta in CARPETAS.items():
    slug = por_dominio.get(dom)
    if not slug:
        print(f"{dom}: no está en la consola; se salta"); continue
    c, j = api("POST", f"/api/clientes/{slug}/pagina", {"zip": base64.b64encode(zip_de(VIDEOS / carpeta)).decode()})
    print(f"{dom} ({slug}): " + ("subida y activa" if c == 200 else f"falló HTTP {c}: {(j or {}).get('error')}"))
