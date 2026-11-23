"""Vista previa de una página propia con DATOS FALSOS (sin PINs, sin base de datos, sin internet).
Uso:  python diseno/editable/vista.py diseno/editable/<tu-carpeta> [puerto]      -> abre http://localhost:8090/
Imita la API del motor (diseno/editable/API.md) con los datos de vista-previa/datos.json. Cualquier PIN de 6 números entra al panel
y al repartidor. Al guardar un archivo de la carpeta, la página se recarga sola. Solo necesita Python (nada que instalar)."""
import copy
import datetime as dt
import html
import json
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

KIT = Path(__file__).resolve().parent
RAIZ = KIT.parent.parent
sys.path.insert(0, str(RAIZ))
from motor import paginas  # noqa: E402  (solo stdlib: no hace falta instalar el motor)

ESTATICO = RAIZ / "motor" / "estatico"
DATOS = KIT / "vista-previa" / "datos.json"
PREVIOS = {"pagado": {"pago_reportado", "confirmado"}, "en_preparacion": {"pagado"}, "listo": {"en_preparacion"}, "en_camino": {"listo"},
           "entregado": {"listo", "en_camino"}}  # = motor/herramientas.PREVIOS
TERMINADOS = {"entregado", "cancelado", "caducado"}
RECARGA = (b"<script>(()=>{let v;setInterval(async()=>{try{const r=await(await fetch('/_vista/cambio')).text();if(v&&r!==v)location.reload();v=r}catch(e){}},1000)})()"
           b"</script>")  # solo en la vista previa: recarga al guardar


def armar(html_bytes, marca, carpeta=None):
    """Igual que el motor (web.armar_pagina): tema (el tema.css de la carpeta si lo hay; si no, el del motor) y marca."""
    t = Path(carpeta) / "tema.css" if carpeta else None
    css = (t if t and t.is_file() else ESTATICO / "tema.css").read_bytes()
    return paginas.sustituir(html_bytes, css, html.escape(marca).encode())


def dinero(x):
    return f"B/. {x:.2f}"


def num(s):
    return float(str(s).replace("B/.", "").strip() or 0)


class Estado:
    """Lo que el servidor real guarda en su base de datos, aquí en memoria y con datos falsos."""

    def __init__(self):
        self.d = json.loads(DATOS.read_text(encoding="utf-8"))
        self.menu, self.pedidos, self.avisos = copy.deepcopy(self.d["menu"]), copy.deepcopy(self.d["pedidos"]), copy.deepcopy(self.d["avisos"])
        self.cerrado, self.pos, self.aviso_ok, self.lock = self.d["config"]["cerrado"], None, False, threading.Lock()
        ahora = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        for p in self.pedidos:
            p.update(creado_at=ahora, estado_at=ahora, cliente=False, cobrado_por=None, fuera_de_zona=False)

    def totales(self, p):
        sub = sum(i["precio"] * i["cantidad"] for i in p["items"])
        imp = round(sub * self.d["config"]["impuesto_pct"] / 100, 2)
        env = self.d["config"]["envio"].get(p["zona"], 0) if p["modalidad"] == "entrega" else 0
        return {"subtotal": dinero(sub), "impuesto": dinero(imp), "envio": dinero(env), "total": dinero(sub + imp + env)}

    def items(self, p, precio=True):
        return [{"nombre": i["nombre"], "cantidad": i["cantidad"], **({"precio": dinero(i["precio"])} if precio else {})} for i in p["items"]]

    @staticmethod
    def acciones(p):
        a = [e for e, previos in PREVIOS.items() if p["estado"] in previos]
        return a + (["pago_rechazado"] if p["estado"] == "pago_reportado" else []) + (["cancelado"] if p["estado"] in ("por_confirmar", "confirmado") else [])

    def para_cliente(self, p):  # = web.formato_pedido (+ seguimiento si va en camino)
        d = {"id": p["id"], "estado": p["estado"], "modalidad": p["modalidad"], "items": self.items(p), "ubicacion": p["ubicacion"], **self.totales(p),
             "por_cobrar": p["por_cobrar"]}
        if p["modalidad"] == "mesa":
            d.update(mesa=p["mesa"], espera_personal=False)
        if p["estado"] == "en_camino":
            d.update(repartidor=p["repartidor"], repartidor_pos=self.vista_pos(), mensaje=None, parada=1, eta_min=12)
        return d

    def para_panel(self, p, detalle=False):  # = panel_web.pedido_json
        abierto = detalle and p["estado"] not in TERMINADOS
        tel = p["telefono"]
        d = {"id": p["id"], "estado": p["estado"], "nombre": p["nombre"], "telefono": tel if abierto else tel[3] + "***-**" + tel[-2:],
             "modalidad": p["modalidad"], "zona": p["zona"], "hora_deseada": p["hora_deseada"], "notas": p["notas"], "items": self.items(p), **self.totales(p),
             "metodo": p["metodo"], "referencia": p["referencia"], "comprobante": p["comprobante"], "creado_at": p["creado_at"], "estado_at": p["estado_at"],
             "acciones": self.acciones(p), "repartidor": p["repartidor"], "tiene_ubicacion": p["ubicacion"], "por_cobrar": p["por_cobrar"],
             "cobrado_por": p["cobrado_por"], "fuera_de_zona": p["fuera_de_zona"]}
        if abierto:
            d.update(direccion=p["direccion"], mapa=self.mapa() if p["ubicacion"] else None)
        return d

    def para_repartidor(self, p):  # = panel_web.pedido_repartidor
        return {"id": p["id"], "estado": p["estado"], "nombre": p["nombre"], "telefono": p["telefono"], "direccion": p["direccion"], "zona": p["zona"],
                "notas": p["notas"], "hora_deseada": p["hora_deseada"], "items": self.items(p, False), "total": self.totales(p)["total"],
                "pagado": not p["por_cobrar"], "cobrar": self.totales(p)["total"] if p["por_cobrar"] else None, "mapa": self.mapa() if p["ubicacion"] else None,
                "tiene_ubicacion": p["ubicacion"], "fuera_de_zona": p["fuera_de_zona"], "parada": self.parada(p),
                "acciones": {"listo": ["en_camino"], "en_camino": ["entregado"]}.get(p["estado"], [])}

    def mapa(self):
        r = self.d["restaurante"]
        return f"https://www.google.com/maps/dir/?api=1&destination={r['lat'] + 0.004},{r['lon'] - 0.003}"

    def de_juan(self):
        return [p for p in self.pedidos if p["repartidor"] == "Juan" and p["estado"] in ("en_preparacion", "listo", "en_camino")]

    def parada(self, p):
        ps = [x for x in self.de_juan() if x["estado"] != "en_preparacion"]
        return next((i + 1 for i, x in enumerate(ps) if x is p), None)

    def ruta(self):
        r = self.d["restaurante"]
        ps = [x for x in self.de_juan() if x["estado"] != "en_preparacion"]
        paradas = [{"id": p["id"], "lat": r["lat"] + 0.004 * (i + 1), "lon": r["lon"] - 0.003 * (i + 1), "eta_min": 8 * (i + 1), "dist_m": 600 * (i + 1),
                    "n": i + 1, "fuera_de_viaje": False} for i, p in enumerate(ps)]
        return {"origen": {"lat": r["lat"], "lon": r["lon"]} if paradas else None, "total_min": 8 * len(paradas) or None, "paradas": paradas}

    def vista_pos(self):
        return {**self.pos, "edad_s": 3, "viejo": False} if self.pos else None

    def aviso(self, texto, pid):
        self.avisos.insert(0, {"id": max([a["id"] for a in self.avisos] + [0]) + 1, "tipo": "pedido", "texto": texto, "pedido_id": pid})

    def nuevo(self, texto, nombre):
        """Lo que haría el asistente: arma el pedido con los platos que el texto nombra (2 del primero si no nombra ninguno)."""
        t = texto.lower()
        items = []
        for m in self.menu:
            i = t.find(m["nombre"].lower().split()[0])  # ponytail: por la primera palabra del plato; el asistente real entiende mucho más
            if i >= 0 and m["disponible"]:
                antes = t[:i].split()[-1:] or ["1"]
                items.append({"nombre": m["nombre"], "cantidad": int(antes[0]) if antes[0].isdigit() else 1, "precio": m["precio"]})
        items = items or [{"nombre": self.menu[0]["nombre"], "cantidad": 2, "precio": self.menu[0]["precio"]}]
        mesa = getattr(self, "mesa", None)  # sesión de mesa (QR): sin entrega ni recogida
        entrega = not mesa and "recog" not in t
        p = {"id": max(p["id"] for p in self.pedidos) + 1 if self.pedidos else 1, "estado": "por_confirmar", "nombre": nombre or "Cliente",
             "telefono": "50760001111", "modalidad": "mesa" if mesa else "entrega" if entrega else "recogida", **({"mesa": mesa} if mesa else {}), "zona": next(iter(self.d["config"]["envio"]), "") if entrega else "",
             "direccion": "Calle de ejemplo 1" if entrega else "", "hora_deseada": "", "notas": "", "items": items, "metodo": None, "referencia": None,
             "comprobante": False, "repartidor": None, "ubicacion": False, "por_cobrar": False, "cliente": True, "cobrado_por": None, "fuera_de_zona": False,
             "creado_at": dt.datetime.now().astimezone().isoformat(timespec="seconds")}
        p["estado_at"] = p["creado_at"]
        self.pedidos.append(p)
        return p


def crear(carpeta, puerto=8090):
    carpeta = Path(carpeta).resolve()
    if not carpeta.is_dir():
        raise SystemExit(f"No existe la carpeta {carpeta}")
    est = Estado()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, codigo, cuerpo, tipo="application/json", extra=()):
            b = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo, ensure_ascii=False).encode()
            self.send_response(codigo)
            for k, v in (("Content-Type", tipo + "; charset=utf-8"), ("Content-Length", str(len(b))), ("Cache-Control", "no-store"),
                         ("X-Content-Type-Options", "nosniff"), *extra):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _err(self, codigo, msg):
            self._enviar(codigo, {"error": msg})

        def _cookie(self, nombre):
            return any(p.strip().startswith(nombre + "=vista") for p in (self.headers.get("Cookie") or "").split(";"))

        def _cuerpo(self):
            n = min(int(self.headers.get("Content-Length") or 0), 6_000_000)
            crudo = self.rfile.read(n) if n else b""
            if (self.headers.get("Content-Type") or "").startswith("application/json"):
                try:
                    d = json.loads(crudo or b"{}")
                    return d if isinstance(d, dict) else {}
                except ValueError:
                    return {}
            return crudo

        def _pagina(self, nombre):
            f = carpeta / nombre if (carpeta / nombre).is_file() else KIT / "base" / nombre  # sin la suya, la de base/
            cuerpo = armar(f.read_bytes(), est.d["config"]["marca"], carpeta).replace(b"</body>", RECARGA + b"</body>")
            self._enviar(200, cuerpo, "text/html", [("Content-Security-Policy", paginas.csp_propia())])

        def do_GET(self):
            u = urlsplit(self.path)
            r, q = u.path, parse_qs(u.query)
            with est.lock:
                return self._get(r, q)

        def do_POST(self):
            r = urlsplit(self.path).path
            d = self._cuerpo()
            with est.lock:
                return self._post(r, d, parse_qs(urlsplit(self.path).query))

        def _get(self, r, q):
            if r == "/":
                return self._pagina("index.html")
            if r == "/panel":
                return self._pagina("panel.html")
            if r in ("/repartidor", "/repartidor/"):
                return self._pagina("repartidor.html")
            if r.startswith("/web/"):
                return paginas.servir(self, carpeta, r, "no-store")  # al editar, siempre lo último
            if r.startswith("/estatico/"):
                return paginas.servir(self, ESTATICO, "/web/" + r[len("/estatico/"):])
            if r == "/_vista/cambio":
                return self._enviar(200, str(max((f.stat().st_mtime_ns for f in carpeta.rglob("*") if f.is_file()), default=0)).encode(), "text/plain")
            if r == "/repartidor/icono.svg":
                return self._enviar(200, (RAIZ / "motor" / "repartidor_icono.svg").read_bytes(), "image/svg+xml")
            if r == "/repartidor/manifest.webmanifest":
                return self._enviar(200, {"name": "Repartidor", "start_url": "/repartidor/", "display": "standalone"}, "application/manifest+json")
            if r == "/repartidor/sw.js":
                return self._enviar(200, b"", "text/javascript")
            if r == "/salud":
                return self._enviar(200, {"ok": True})
            if r == "/config":
                m = self._mesa((q.get("m") or [""])[0])
                return self._enviar(200, {**est.d["config"], "cerrado": est.cerrado, **({"mesa": m, "etiqueta": f"Mesa {m}"} if m else {}),
                                          **({"pago": {k: v for k, v in est.d["pago"].items() if v}} if self._cookie("sesion") else {})})
            if r == "/menu":
                return self._enviar(200, est.menu)
            if r == "/aviso":
                return self._enviar(200, est.d["aviso"])
            if r in ("/pedidos", "/avisos"):
                if not self._cookie("sesion"):
                    return self._err(401, "Sesión inválida")
                if r == "/avisos":
                    return self._enviar(200, {"mensajes": []})
                return self._enviar(200, {"pedidos": [est.para_cliente(p) for p in reversed(est.pedidos) if p["cliente"]]})
            if r.startswith("/panel/"):
                if not self._cookie("panel"):
                    return self._err(401, "Sesión inválida")
                if r == "/panel/estado":
                    desde = int((q.get("desde") or ["0"])[0] or 0)
                    return self._enviar(200, {"pedidos": [est.para_panel(p) for p in est.pedidos if p["estado"] not in TERMINADOS],
                                              "avisos": [a for a in est.avisos if a["id"] > desde], "ultimo": max([a["id"] for a in est.avisos] + [0]),
                                              "escalados": [], "repartidores": est.d["repartidores"], "cerrado": est.cerrado, "cerrado_hasta": None, "modulos": [], "rutas": {}})
                if r.startswith("/panel/pedido/"):
                    p = next((p for p in est.pedidos if str(p["id"]) == r.rsplit("/", 1)[1]), None)
                    return self._enviar(200, {"pedido": est.para_panel(p, True)}) if p else self._err(404, "No existe ese pedido")
                if r.startswith("/panel/comprobante/"):
                    return self._err(404, "Sin comprobante (vista previa)")
                if r in ("/panel/menu", "/panel/dueno/menu"):
                    return self._enviar(200, {"menu": est.menu})
                if r == "/panel/repartidores_mapa":
                    rest = {**est.d["restaurante"], "nombre": est.d["config"]["marca"]}
                    camino = [p for p in est.pedidos if p["estado"] == "en_camino"]
                    return self._enviar(200, {"restaurante": rest, "repartidores": [{"repartidor": p["repartidor"], "pedido_id": p["id"], **est.vista_pos()}
                                                                                     for p in camino if est.pos]})
                if r == "/panel/dueno/ajustes":
                    return self._enviar(200, {"ajustes": {**est.d["ajustes"], "cerrado": est.cerrado, "cerrado_hasta": None, "excepciones": {}, "factura": {"razon_social": "", "ruc": "", "dv": "", "sucursal": ""}, "envio": [{"nombre": z, "precio": pr, "incluye": []} for z, pr in est.d["config"]["envio"].items()]}})
                if r == "/panel/dueno/usuarios":
                    return self._enviar(200, {"usuarios": est.d["usuarios"]})
            if r == "/repartidor/pedidos":
                if not self._cookie("repartidor"):
                    return self._err(401, "Sesión inválida")
                return self._enviar(200, {"nombre": "Juan", "pedidos": [est.para_repartidor(p) for p in est.de_juan()], "ruta": est.ruta(),
                                          "aviso_ubicacion": self._aviso_rep()})
            self._err(404, "No encontrado")

        @staticmethod
        def _mesa(codigo):
            """Vista previa: cualquier código de 8 letras/números es una mesa (la 1 a la 20 según el código); el motor real lo busca en la base."""
            return int(codigo, 36) % 20 + 1 if re.fullmatch(r"[a-z0-9]{8}", codigo or "") else None

        def _aviso_rep(self):
            return {"version": "ejemplo", "texto": est.d["aviso_ubicacion"], "aceptado": est.aviso_ok}

        def _pedido(self, pid, de_cliente=False):
            return next((p for p in est.pedidos if str(p["id"]) == str(pid) and (p["cliente"] or not de_cliente)), None)

        def _post(self, r, d, q):
            ck = lambda: [("Set-Cookie", f"sesion=vista; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400")]
            if r == "/entrar":
                m = self._mesa(d.get("codigo")) if not d.get("telefono") else None  # QR de mesa: sin teléfono
                if not (isinstance(d.get("nombre"), str) and (d["nombre"].strip() if not m else 1 <= len(d["nombre"].strip()) <= 60)):
                    return self._err(400, "Nombre inválido")
                if not (d.get("consentimiento") is True or d.get("acepta_privacidad") is True):
                    return self._err(400, "Se requiere el consentimiento")
                if d.get("version_aviso") != est.d["aviso"]["version"]:
                    return self._err(400, "El aviso cambió; vuelva a aceptarlo")
                est.nombre, est.mesa = d["nombre"].strip(), m
                return self._enviar(200, {"ok": True, "nombre": est.nombre, **({"mesa": m} if m else {})}, extra=ck())
            if r in ("/chat", "/ubicacion", "/efectivo", "/comprobante"):
                if not self._cookie("sesion"):
                    return self._err(401, "Sesión inválida")
                mio = next((p for p in reversed(est.pedidos) if p["cliente"] and p["estado"] not in TERMINADOS), None)
                if r == "/chat":
                    t = d.get("texto") if isinstance(d, dict) else None
                    if not (isinstance(t, str) and 1 <= len(t) <= 1000):
                        return self._err(400, "Texto inválido")
                    if mio and mio["estado"] == "por_confirmar" and any(w in t.lower() for w in ("sí", "si", "confirm", "dale", "ok")):
                        mio.update(estado="confirmado")
                        est.aviso(f"Pedido #{mio['id']} confirmado de {mio['nombre']}.", mio["id"])
                        return self._enviar(200, {"respuesta": "¡Listo! Tu pedido quedó **confirmado**. Ahora puedes pagar. (Respuesta de EJEMPLO)", "pedido": est.para_cliente(mio)})
                    if not mio:
                        p = est.nuevo(t, getattr(est, "nombre", ""))
                        return self._enviar(200, {"respuesta": f"Te resumo tu pedido (EJEMPLO): total {est.totales(p)['total']}. ¿Lo confirmas?", "pedido": est.para_cliente(p)})
                    return self._enviar(200, {"respuesta": "Esto es la vista previa: aquí respondería el asistente del restaurante.", "pedido": None})
                if r == "/ubicacion":
                    if mio and mio["modalidad"] == "entrega":
                        mio["ubicacion"] = True
                    return self._enviar(200, {"ok": True, "pedido_id": mio and mio["id"], "ubicacion": True})
                if r == "/efectivo":
                    p = self._pedido(d.get("pedido"), True)
                    if not p:
                        return self._err(404, "No existe ese pedido")
                    if p["modalidad"] != "entrega" or p["estado"] != "confirmado":
                        return self._err(409, "El efectivo es solo para entregas confirmadas")
                    p.update(estado="pagado", metodo="efectivo", por_cobrar=True)
                    return self._enviar(200, {"ok": True, "pedido": est.para_cliente(p)})
                p = self._pedido((q.get("pedido") or [""])[0], True)
                if not p:
                    return self._err(404, "No existe ese pedido")
                if p["estado"] not in ("confirmado", "pago_reportado"):
                    return self._err(409, "El pedido no está esperando pago")
                p.update(estado="pago_reportado", comprobante=True, metodo="yappy")
                return self._enviar(200, {"ok": True, "pedido": est.para_cliente(p)})
            if r in ("/panel/entrar", "/repartidor/entrar"):
                pin = d.get("pin")
                if not (isinstance(pin, str) and pin.isdigit() and len(pin) == 6):
                    return self._err(401, "PIN incorrecto")
                if r == "/panel/entrar":
                    return self._enviar(200, {"ok": True, "nombre": "Dueno", "rol": "dueno"},
                                        extra=[("Set-Cookie", "panel=vista; HttpOnly; SameSite=Strict; Path=/panel")])
                return self._enviar(200, {"ok": True, "nombre": "Juan", "aviso_ubicacion": self._aviso_rep()},
                                    extra=[("Set-Cookie", "repartidor=vista; HttpOnly; SameSite=Strict; Path=/repartidor")])
            if r.startswith("/panel/"):
                return self._panel(r, d)
            if r.startswith("/repartidor/"):
                return self._repartidor(r, d)
            self._err(404, "No encontrado")

        def _panel(self, r, d):
            if not self._cookie("panel"):
                return self._err(401, "Sesión inválida")
            if r == "/panel/salir":
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", "panel=; HttpOnly; SameSite=Strict; Path=/panel; Max-Age=0")])
            if r in ("/panel/marcar", "/panel/asignar"):
                p = self._pedido(d.get("pedido_id"))
                if not p:
                    return self._err(409, "No existe ese pedido")
                if r == "/panel/asignar":
                    if p["modalidad"] != "entrega":
                        return self._err(409, "Solo se asigna repartidor a pedidos de entrega")
                    p["repartidor"] = d.get("repartidor") or None
                else:
                    e = d.get("estado")
                    if e not in est.acciones(p):
                        return self._err(409, f"El pedido está '{p['estado']}'; esa acción no aplica ahora.")
                    if e == "pago_rechazado":
                        e = "confirmado"
                    p.update(estado=e, estado_at=dt.datetime.now().astimezone().isoformat(timespec="seconds"))
                    if e == "entregado" and p["por_cobrar"]:
                        p.update(por_cobrar=False, cobrado_por="Dueno")
                return self._enviar(200, {"ok": True, "pedido": est.para_panel(p)})
            if r in ("/panel/agotado", "/panel/dueno/plato"):
                m = next((m for m in est.menu if m["id"] == (d.get("plato") or d.get("id"))), None)
                if not m:
                    return self._err(404, "No existe ese plato")
                if r == "/panel/agotado":
                    m["disponible"] = d.get("disponible") is True
                else:
                    m.update({k: d[k] for k in ("nombre", "precio", "categoria", "disponible") if k in d})
                return self._enviar(200, {"ok": True, "plato": m})
            if r == "/panel/tienda":
                est.cerrado = d.get("cerrado") is True
                return self._enviar(200, {"ok": True, "cerrado": est.cerrado, "cerrado_hasta": None})
            if r == "/panel/dueno/usuario":
                return self._enviar(200, {"ok": True, "usuario": {"nombre": d.get("nombre"), "rol": d.get("rol", "personal"), "activo": d.get("activo", True)}})
            if r == "/panel/dueno/ajustes":
                return self._enviar(200, {"ok": True, "ajustes": {**est.d["ajustes"], **{k: v for k, v in d.items() if k in est.d["ajustes"]}}})
            if r == "/panel/dueno/foto":
                return self._err(400, "En la vista previa no se suben fotos")
            if r.startswith("/panel/escalado/") or r.startswith("/panel/dueno/"):
                return self._enviar(200, {"ok": True})  # vista previa: se acepta y no se guarda (estadísticas: módulo apagado)
            self._err(404, "No encontrado")

        def _repartidor(self, r, d):
            if not self._cookie("repartidor"):
                return self._err(401, "Sesión inválida")
            if r == "/repartidor/salir":
                est.pos = None
                return self._enviar(200, {"ok": True}, extra=[("Set-Cookie", "repartidor=; HttpOnly; SameSite=Strict; Path=/repartidor; Max-Age=0")])
            if r == "/repartidor/aviso_ubicacion":
                est.aviso_ok = True
                return self._enviar(200, {"ok": True})
            if r == "/repartidor/posicion":
                if not any(p["estado"] == "en_camino" for p in est.de_juan()):
                    return self._err(409, "No tiene pedidos en camino")
                est.pos = {"lat": d.get("lat"), "lon": d.get("lon"), "precision": float(d.get("precision") or 0)}
                return self._enviar(200, {"ok": True})
            if r == "/repartidor/marcar":
                p = next((p for p in est.de_juan() if p["id"] == d.get("pedido_id")), None)
                if not p:
                    return self._err(409, "No tiene ese pedido asignado")
                if d.get("estado") not in est.para_repartidor(p)["acciones"]:
                    return self._err(409, "Ese cambio no aplica ahora")
                if d["estado"] == "entregado" and p["por_cobrar"] and d.get("cobrado") is not True:
                    return self._err(409, "Confirme que cobró el efectivo")
                p.update(estado=d["estado"], por_cobrar=False if d["estado"] == "entregado" else p["por_cobrar"])
                return self._enviar(200, {"ok": True, "pedido": est.para_repartidor(p) if p["estado"] != "entregado" else None})
            self._err(404, "No encontrado")

    return ThreadingHTTPServer(("127.0.0.1", puerto), H)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Uso: python diseno/editable/vista.py <carpeta-de-tu-pagina> [puerto]")
    srv = crear(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8090)
    p = srv.server_address[1]
    print(f"Vista previa (datos falsos) de {sys.argv[1]}:\n  Web de pedidos: http://localhost:{p}/\n  Panel:          http://localhost:{p}/panel"
          f"   (cualquier PIN de 6 números)\n  Repartidor:     http://localhost:{p}/repartidor/\nCtrl+C para parar.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
