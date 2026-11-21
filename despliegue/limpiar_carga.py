"""Cancela los pedidos «PRUEBA-CARGA n» (los de despliegue/carga.py) en el panel de cada restaurante. Solo stdlib.
Uso: python despliegue/limpiar_carga.py dominio1=PIN1 dominio2=PIN2 ...
Solo toca pedidos activos cuyo nombre empieza por «PRUEBA-CARGA»; los reales no se tocan. Los PIN no se guardan."""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request

ACTIVOS_NO = {"cancelado", "entregado"}

def llamar(op, base, ruta, d=None):
    r = urllib.request.Request(base + ruta, data=json.dumps(d).encode() if d is not None else None, headers={"Content-Type": "application/json"})
    try:
        with op.open(r, timeout=60) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

for arg in sys.argv[1:]:
    dom, _, pin = arg.partition("=")
    base, op = "https://" + dom, urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    c, _ = llamar(op, base, "/panel/entrar", {"pin": pin})
    if c != 200:
        print(f"{dom}: no entra (HTTP {c})"); continue
    c, est = llamar(op, base, "/panel/estado")
    if c != 200:
        print(f"{dom}: no lee pedidos (HTTP {c})"); continue
    mios = [p for p in est["pedidos"] if (p.get("nombre") or "").startswith("PRUEBA-CARGA") and p["estado"] not in ACTIVOS_NO]
    ok = sum(llamar(op, base, "/panel/marcar", {"pedido_id": p["id"], "estado": "cancelado"})[0] == 200 for p in mios)
    print(f"{dom}: canceladas {ok}/{len(mios)}")
