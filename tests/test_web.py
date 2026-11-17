"""Canal web. Ejecutar: python -m unittest tests.test_web"""
import datetime as dt
import http.client
import json
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

from motor import web
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso, herramienta, texto

SECRETO = "secreto-de-prueba"
TEL = "50760001111"
AVISO = "Texto del aviso"
PEDIDO = {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "recogida", "nombre": "Ana"}


class Base(unittest.TestCase):
    def arrancar(self, respuestas=()):
        """Levanta un servidor nuevo (puerto 0); se puede llamar otra vez dentro de una prueba."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS + 'web: {aviso_version: "v1", aviso: "aviso.md"}\n', encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        (d / "aviso.md").write_text(AVISO, encoding="utf-8")
        self.sv = sv = Servicio(d, db=":memory:", cliente=ClienteFalso(respuestas))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        self.llamadas = []
        orig = sv.responder
        sv.responder = lambda cid, t, **k: (self.llamadas.append((cid, t)), orig(cid, t, **k))[1]
        srv = web.crear_servidor(sv, 0, SECRETO)
        self.puerto = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(sv.cerrar)  # corre después de cerrar el servidor
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))

    def req(self, metodo, ruta, cuerpo=None, cookie=None, crudo=None, tipo="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        h = {"Content-Type": tipo}
        if cookie:
            h["Cookie"] = cookie
        data = crudo if crudo is not None else (json.dumps(cuerpo) if cuerpo is not None else None)
        c.request(metodo, ruta, data, h)
        r = c.getresponse()
        raw = r.read()
        c.close()
        return r, raw

    def js(self, *a, **k):
        r, raw = self.req(*a, **k)
        return r.status, json.loads(raw), r

    def entrar(self, tel=TEL, nombre="Ana", **extra):
        cuerpo = {"nombre": nombre, "telefono": tel, "consentimiento": True, "version_aviso": "v1", **extra}
        st, d, r = self.js("POST", "/entrar", cuerpo)
        return st, d, (r.getheader("Set-Cookie") or "").split(";")[0]


class Acceso(Base):
    def setUp(self):
        self.arrancar()

    def test_chat_sin_cookie_o_invalida(self):
        self.assertEqual(self.req("POST", "/chat", {"texto": "hola"})[0].status, 401)
        _, _, ck = self.entrar()
        _, exp, firma = ck.split(".")
        self.assertEqual(self.req("POST", "/chat", {"texto": "hola"}, cookie=ck[:-3] + "000")[0].status, 401)  # manipulada
        self.assertEqual(self.req("POST", "/chat", {"texto": "hola"}, cookie=f"sesion=50760002222.{exp}.{firma}")[0].status, 401)  # otro cid
        vieja = "sesion=" + web.firmar(SECRETO, TEL, int(time.time()) - 1)
        self.assertEqual(self.req("POST", "/chat", {"texto": "hola"}, cookie=vieja)[0].status, 401)
        self.assertEqual(self.req("GET", "/avisos")[0].status, 401)
        self.assertEqual(self.req("GET", "/pedidos")[0].status, 401)

    def test_entrar_invalido(self):
        for extra in ({"telefono": "123"}, {"telefono": "abc12345678"}, {"nombre": ""}, {"nombre": "x" * 61}, {"consentimiento": False}, {"consentimiento": "true"}):
            cuerpo = {"nombre": "Ana", "telefono": TEL, "consentimiento": True, "version_aviso": "v1", **extra}
            self.assertEqual(self.req("POST", "/entrar", cuerpo)[0].status, 400, extra)
        self.assertEqual(self.sv.listar("consentimiento"), [])

    def test_version_aviso_equivocada(self):
        st, d, _ = self.entrar(version_aviso="vieja")
        self.assertEqual((st, d["error"]), (400, "El aviso cambió; vuelva a aceptarlo"))
        self.assertEqual(self.sv.listar("consentimiento"), [])

    def test_entrar_guarda_consentimiento_y_aviso(self):
        st, d, _ = self.entrar(tel="+507 6000-1111")
        self.assertEqual((st, d), (200, {"ok": True, "nombre": "Ana"}))
        c = self.sv.listar("consentimiento")
        self.assertEqual(len(c), 1)
        self.assertEqual((c[0]["telefono"], c[0]["version_aviso"], c[0]["nombre"]), (TEL, "v1", "Ana"))
        self.assertTrue(c[0]["consentimiento_at"].startswith("2026-11-02T12:00"))
        self.assertEqual(self.js("GET", "/aviso")[1], {"version": "v1", "texto": AVISO})

    def test_cookie_atributos(self):
        _, _, r = self.js("POST", "/entrar", {"nombre": "Ana", "telefono": TEL, "consentimiento": True, "version_aviso": "v1"})
        sc = r.getheader("Set-Cookie")
        for x in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=86400"):
            self.assertIn(x, sc)

    def test_flujo_chat_cid_y_nombre(self):
        self.arrancar([texto("Hola Ana"), texto("ok")])
        _, _, ck = self.entrar(tel="+507-6000-1111")
        st, d, _ = self.js("POST", "/chat", {"texto": "hola"}, cookie=ck)
        self.assertEqual((st, d), (200, {"respuesta": "Hola Ana", "pedido": None}))
        self.js("POST", "/chat", {"texto": "otra"}, cookie=ck)
        self.assertEqual([c[0] for c in self.llamadas], [TEL, TEL])
        self.assertEqual(self.llamadas[0][1], "[Nombre del cliente: Ana] hola")
        self.assertEqual(self.llamadas[1][1], "otra")  # el nombre solo va en el primero

    def test_chat_texto_invalido_y_tipo(self):
        _, _, ck = self.entrar()
        self.assertEqual(self.req("POST", "/chat", {"texto": ""}, cookie=ck)[0].status, 400)
        self.assertEqual(self.req("POST", "/chat", {"texto": "x" * 1001}, cookie=ck)[0].status, 400)
        self.assertEqual(self.req("POST", "/chat", crudo="texto=hola", tipo="text/plain", cookie=ck)[0].status, 415)

    def test_cuerpo_grande_413(self):
        _, _, ck = self.entrar()
        self.assertEqual(self.req("POST", "/chat", crudo=json.dumps({"texto": "x" * 9000}), cookie=ck)[0].status, 413)

    def test_error_del_modelo_respuesta_generica(self):
        _, _, ck = self.entrar()  # ClienteFalso sin respuestas: falla al pedir la respuesta
        with unittest.mock.patch("traceback.print_exc"), unittest.mock.patch("builtins.print") as pr:
            st, d, _ = self.js("POST", "/chat", {"texto": "hola"}, cookie=ck)
        self.assertEqual((st, d["respuesta"]), (200, web.GENERICA))
        self.assertNotIn(TEL, str(pr.call_args_list))  # teléfono enmascarado en consola
        self.assertEqual(web.enmascarar(TEL), "+507 6***-**11")

    def test_limite_entrar_429(self):
        for _ in range(10):
            self.req("POST", "/entrar", {})
        self.assertEqual(self.req("POST", "/entrar", {})[0].status, 429)

    def test_limite_por_ip_429(self):
        for _ in range(60):
            self.req("GET", "/menu")
        self.assertEqual(self.req("GET", "/menu")[0].status, 429)

    def test_limite_por_cid_429(self):
        self.arrancar([texto("a")] * 40)
        _, _, ck = self.entrar()
        codigos = [self.req("POST", "/chat", {"texto": "a"}, cookie=ck)[0].status for _ in range(31)]
        self.assertEqual((codigos[0], codigos[-1]), (200, 429))

    def test_raiz_html_csp(self):
        r, raw = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.getheader("Content-Type"))
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertIn(b"Ver aviso de privacidad", raw)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_pagina_segura_y_casilla_no_premarcada(self):
        h = Path(web.__file__).with_name("web_index.html").read_text(encoding="utf-8")
        self.assertEqual(h.count("innerHTML"), 1)  # solo la plantilla fija de los pasos; el menú se arma con nodos y el texto del servidor va con textContent
        self.assertNotIn("${p.nombre", h)
        self.assertNotRegex(h, r'<input type="checkbox"[^>]*checked')
        self.assertRegex(h, r'<input type="checkbox" name="privacidad" required')
        h = h.replace("https://tile.openstreetmap.org/", "")  # único origen externo permitido: los mosaicos del mapa (la CSP también lo limita)
        for malo in ("<script src", "http://", "https://", "eval("):
            self.assertNotIn(malo, h)

    def test_entrar_acepta_acepta_privacidad(self):
        st, d, _ = self.entrar(consentimiento=None, acepta_privacidad=True)
        self.assertEqual(st, 200)

    def test_nombre_json_tal_cual(self):
        _, d, _ = self.entrar(nombre="<img src=x onerror=alert(1)>")
        self.assertEqual(d["nombre"], "<img src=x onerror=alert(1)>")  # el escape es del cliente (textContent)


class Avisos(Base):
    def test_avisos_una_vez_y_aislado(self):
        self.arrancar()

        def prog(tel, en="2026-11-02 11:00"):
            self.sv.insertar("mensaje_programado", {"telefono": tel, "enviar_en": en, "texto": f"para {tel}", "estado": "pendiente", "cita_id": None})
        prog(TEL)
        prog("50760002222")
        prog(TEL, en="2026-11-03 11:00")  # futuro
        _, _, ck = self.entrar()
        self.assertEqual(self.js("GET", "/avisos", cookie=ck)[1], {"mensajes": [f"para {TEL}"]})
        self.assertEqual(self.js("GET", "/avisos", cookie=ck)[1], {"mensajes": []})
        self.assertEqual(len([m for m in self.sv.listar("mensaje_programado") if m["estado"] == "pendiente"]), 2)


class MenuYPedidos(Base):
    def test_menu_sin_cookie(self):
        self.arrancar()
        st, d, _ = self.js("GET", "/menu")
        self.assertEqual(st, 200)
        s = next(i for i in d if i["id"] == "sancocho")
        self.assertEqual((s["nombre"], s["precio"], s["disponible"]), ("Sancocho", 5.0, True))
        self.assertFalse(next(i for i in d if i["id"] == "tres-leches")["disponible"])

    def test_pedidos_aislados_y_formato(self):
        self.arrancar([herramienta("crear_pedido", PEDIDO), texto("Listo")])
        _, _, a = self.entrar()
        _, _, b = self.entrar(tel="50760002222", nombre="Luis")
        _, d, _ = self.js("POST", "/chat", {"texto": "quiero 2 sancochos"}, cookie=a)
        self.assertEqual(d["respuesta"], "Listo")
        p = d["pedido"]
        self.assertEqual((p["estado"], p["modalidad"], p["subtotal"], p["total"]), ("por_confirmar", "recogida", "B/. 10.00", "B/. 10.70"))
        self.assertEqual((p["impuesto"], p["envio"]), ("B/. 0.70", "B/. 0.00"))
        self.assertEqual(p["items"], [{"nombre": "Sancocho", "cantidad": 2, "precio": "B/. 5.00"}])
        self.assertEqual(self.js("GET", "/pedidos", cookie=a)[1]["pedidos"], [p])
        self.assertEqual(self.js("GET", "/pedidos", cookie=b)[1], {"pedidos": []})
        # un turno sin cambios en pedidos -> null
        self.sv._cliente.respuestas.append(texto("nada"))
        self.assertIsNone(self.js("POST", "/chat", {"texto": "hola"}, cookie=a)[1]["pedido"])

    def _historial_filas(self):
        return self.sv._q("SELECT id FROM conversaciones")

    def test_visita_nueva_tras_pedido_cerrado(self):
        self.arrancar([herramienta("crear_pedido", PEDIDO), texto("Listo")])
        _, _, a = self.entrar()
        _, d, _ = self.js("POST", "/chat", {"texto": "2 sancochos"}, cookie=a)
        pid = d["pedido"]["id"]
        self.entrar()  # pedido en curso: se conserva historial y pedido
        self.assertEqual(len(self._historial_filas()), 1)
        self.assertEqual(len(self.js("GET", "/pedidos", cookie=a)[1]["pedidos"]), 1)
        self.sv.actualizar(pid, {"estado": "entregado"}, tipo="pedido")
        self.entrar()  # cerrado: chat limpio, pedido ya no se muestra, sigue en BD
        self.assertEqual(self._historial_filas(), [])
        self.assertEqual(self.js("GET", "/pedidos", cookie=a)[1], {"pedidos": []})
        self.assertEqual(len(self.sv.listar("pedido")), 1)

    def test_visita_nueva_sin_reentrar_en_pedidos(self):
        self.arrancar([herramienta("crear_pedido", PEDIDO), texto("Listo")])
        _, _, a = self.entrar()
        _, d, _ = self.js("POST", "/chat", {"texto": "2 sancochos"}, cookie=a)
        self.sv.actualizar(d["pedido"]["id"], {"estado": "entregado"}, tipo="pedido")
        self.assertEqual(self.js("GET", "/pedidos", cookie=a)[1], {"pedidos": []})  # sin /entrar
        self.assertEqual(self._historial_filas(), [])

    def test_visita_nueva_sin_reentrar_en_chat(self):
        self.arrancar([herramienta("crear_pedido", PEDIDO), texto("Listo"), texto("hola de nuevo"), texto("otra")])
        _, _, a = self.entrar()
        _, d, _ = self.js("POST", "/chat", {"texto": "2 sancochos"}, cookie=a)
        self.sv.actualizar(d["pedido"]["id"], {"estado": "entregado"}, tipo="pedido")
        self.js("POST", "/chat", {"texto": "hola"}, cookie=a)
        m = lambda: self.sv._q("SELECT mensajes FROM conversaciones")[0][0]
        self.assertNotIn("sancochos", m())  # historial anterior descartado
        self.js("POST", "/chat", {"texto": "otra"}, cookie=a)
        self.assertIn("hola", m())  # no vuelve a limpiar en cada mensaje

    def test_confirmado_con_pago_pendiente_no_cuenta_como_terminado(self):
        self.arrancar([herramienta("crear_pedido", PEDIDO), texto("Listo")])
        _, _, a = self.entrar()
        _, d, _ = self.js("POST", "/chat", {"texto": "2 sancochos"}, cookie=a)
        self.sv.actualizar(d["pedido"]["id"], {"estado": "confirmado"}, tipo="pedido")
        self.entrar()
        self.assertEqual(len(self._historial_filas()), 1)
        self.assertEqual(len(self.js("GET", "/pedidos", cookie=a)[1]["pedidos"]), 1)

    def test_visita_nueva_por_inactividad(self):
        self.arrancar([texto("hola")])
        _, _, a = self.entrar()
        self.js("POST", "/chat", {"texto": "hola"}, cookie=a)
        self.entrar()
        self.assertEqual(len(self._historial_filas()), 1)  # reciente: se conserva
        self.sv._q("UPDATE conversaciones SET actualizado=?", (time.time() - web.VISITA_S - 60,))
        self.entrar()
        self.assertEqual(self._historial_filas(), [])


class FormatoBot(unittest.TestCase):
    """formato() de la web: **x** y *x* → <strong>, _x_ → <em>, solo con nodos DOM. Se prueba con node y un DOM de mentira (se omite sin node)."""

    JS = r"""
const document = {createDocumentFragment: () => mk("#f"), createTextNode: t => ({t}), createElement: n => mk(n)};
function mk(n) { return {n, c: [], append(...x) { this.c.push(...x); }, set textContent(v) { this.c = [{t: v}]; }}; }
const ser = x => x.t !== undefined ? x.t : (x.n === "#f" ? "" : `<${x.n}>`) + x.c.map(ser).join("") + (x.n === "#f" ? "" : `</${x.n}>`);
%s
const casos = JSON.parse(process.argv[1]);
console.log(JSON.stringify(casos.map(c => ser(formato(c)))));
"""

    def test_formato(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("sin node")
        h = Path(web.__file__).with_name("web_index.html").read_text(encoding="utf-8")
        fn = h[h.index("function formato("):h.index("function burbuja(")]
        casos = {
            "Hola **Ana**, quieres *tasajo*": "Hola <strong>Ana</strong>, quieres <strong>tasajo</strong>",
            "Total: *B/. 7.00* (con ITBMS)": "Total: <strong>B/. 7.00</strong> (con ITBMS)",
            "_nota_ final": "<em>nota</em> final",
            "2 * 3 * 4": "2 * 3 * 4",
            "2*3*4": "2*3*4",
            "snake_case_name": "snake_case_name",
            "*sin cerrar": "*sin cerrar",
            "- uno\n\u2022 dos": "- uno\n\u2022 dos",
            "<b>x</b> *y*": "<b>x</b> <strong>y</strong>",
            "": "",
        }
        r = subprocess.run([node, "-e", self.JS % fn, json.dumps(list(casos))], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), list(casos.values()))


class PlatosEnTexto(unittest.TestCase):
    """platosEn() de la web: qué platos con foto nombra el bot (se prueba con node; se omite sin él)."""

    def test_deteccion(self):
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            self.skipTest("sin node")
        h = Path(web.__file__).with_name("web_index.html").read_text(encoding="utf-8")
        fn = h[h.index("const plano ="):h.index("function pedirPlato(")]
        js = fn + """
const M = [{id:1,nombre:"Tasajo Asado",foto:"a",disponible:true},{id:2,nombre:"Café",foto:"b",disponible:true},{id:3,nombre:"Sancocho",disponible:true},
 {id:4,nombre:"Hojaldre",foto:"d",disponible:false},{id:5,nombre:"Combo",foto:"e",disponible:true},{id:6,nombre:"Arroz",foto:"f",disponible:true},{id:7,nombre:"Ceviche",foto:"g",disponible:true},{id:8,nombre:"Empanada",foto:"h",disponible:true},{id:9,nombre:"Pastel",foto:"i",disponible:true}];
const ids = (t, v = []) => platosEn(t, M, v).map(m => m.id);
console.log(JSON.stringify([ids("Te recomiendo el TASAJO asado!"), ids("tasajo asado"), ids("Sancocho y hojaldre"), ids("un combo"), ids("arroz con pollo"),
 ids("tasajo asado y ceviche", [1]), ids("ceviche, empanada, pastel y tasajo asado"), ids("pasteles"), ids("")]));
"""
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        # tildes/mayúsculas ok; sin foto, agotado y genérico/corto fuera; ya mostrado fuera; máximo 3; palabra completa
        self.assertEqual(json.loads(r.stdout), [[1], [1], [], [], [6], [7], [1, 7, 8], [], []])


if __name__ == "__main__":
    unittest.main()
