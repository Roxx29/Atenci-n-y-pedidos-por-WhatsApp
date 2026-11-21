"""Prueba de carga con pedidos REALES por la web (misma ruta que un cliente: /entrar y /chat). Solo stdlib.
Uso: python despliegue/carga.py <usuarios_por_sitio> dominio1 dominio2 ...
Cada usuario: entra como «PRUEBA-CARGA n» (teléfono falso), pide para recoger y confirma. Cada pedido pasa por el modelo de IA del restaurante.
Los pedidos quedan en su panel: cancélalos allí después. Respeta los topes del servidor (10 /entrar por minuto por IP): si salen 429 es el límite, no un fallo."""
import http.cookiejar
import json
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request

def llamar(op, base, ruta, d=None):
    r = urllib.request.Request(base + ruta, data=json.dumps(d).encode() if d is not None else None, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with op.open(r, timeout=120) as x:
            return x.status, json.loads(x.read() or b"null"), time.time() - t
    except urllib.error.HTTPError as e:
        try:
            cuerpo = json.loads(e.read() or b"null")
        except ValueError:
            cuerpo = None
        return e.code, cuerpo, time.time() - t
    except Exception as e:  # red, tiempo agotado…
        return 0, {"error": str(e)[:80]}, time.time() - t

def usuario(base, n, platos, res, lock, puerta):
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def anota(paso, cod, seg, ok=None):
        with lock:
            res.append((base, paso, cod, seg, ok))
    with puerta:  # /entrar: tope por IP; se espacia
        time.sleep(random.uniform(0, 1))
    c, a, s = llamar(op, base, "/aviso"); anota("aviso", c, s)
    c, d, s = llamar(op, base, "/entrar", {"nombre": f"PRUEBA-CARGA {n}", "telefono": f"5076000{n:04d}", "acepta_privacidad": True, "version_aviso": (a or {}).get("version", "v1")}); anota("entrar", c, s)
    if c != 200:
        return
    p = random.choice(platos)
    texto, hecho = f"Quiero 1 {p['nombre']} para recoger lo antes posible, a nombre de PRUEBA-CARGA {n}", False
    for turno in range(4):  # el asistente puede pedir confirmar nombre, hora o el resumen: se le contesta que sí
        c, d, s = llamar(op, base, "/chat", {"texto": texto}); ped = d.get("pedido") if c == 200 and isinstance(d, dict) else None
        anota("chat-pedir" if turno == 0 else "chat-sigue", c, s, bool(ped) if turno == 0 else None)
        if c != 200:
            return
        if ped and ped.get("estado") != "por_confirmar":
            hecho = True
            break
        texto = "Sí, confirmo"
    anota("pedido-confirmado", 200, 0, hecho)
    c, d, s = llamar(op, base, "/pedidos"); anota("pedidos", c, s)

def main():
    n, sitios = int(sys.argv[1]), sys.argv[2:]
    res, lock, hilos = [], threading.Lock(), []
    for dom in sitios:
        base = "https://" + dom
        c, platos, _ = llamar(urllib.request.build_opener(), base, "/menu")
        platos = [p for p in (platos or []) if isinstance(p, dict) and p.get("disponible")] if c == 200 else []
        if not platos:
            print(f"{dom}: sin menú disponible (HTTP {c}); se salta"); continue
        puerta = threading.Semaphore(4)
        hilos += [threading.Thread(target=usuario, args=(base, i + 1, platos, res, lock, puerta)) for i in range(n)]
    t0 = time.time()
    for h in hilos: h.start()
    for h in hilos: h.join()
    print(f"\n{len(hilos)} usuarios simultáneos en {time.time() - t0:.1f} s")
    for dom in sitios:
        for paso in ("entrar", "chat-pedir", "chat-sigue", "pedido-confirmado", "pedidos"):
            r = [x for x in res if x[0] == "https://" + dom and x[1] == paso]
            if not r: continue
            ok = sum(1 for x in r if x[2] == 200 and x[4] is not False)
            ts = sorted(x[3] for x in r)
            codigos = sorted({x[2] for x in r if x[2] != 200})
            print(f"{dom:28} {paso:15} ok {ok}/{len(r)}  mediana {statistics.median(ts):5.1f}s  máx {ts[-1]:5.1f}s" + (f"  códigos {codigos}" if codigos else ""))

main()
