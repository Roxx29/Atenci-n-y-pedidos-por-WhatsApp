"""Consola del operador. Ejecutar: python -m unittest tests.test_operador"""
import datetime as dt
import http.client
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import unittest.mock
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from motor import clientes, operador

CLAVE = "clave-larga-1234"
TEL = "50760001111"


class Consola(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.raiz = Path(t.name)
        pl = self.raiz / "servicios" / "_plantilla"; pl.mkdir(parents=True)
        (pl / "servicio.yaml").write_text("nombre: x\n", encoding="utf-8")
        a = self.raiz / "servicios" / "uno"; a.mkdir()
        (a / "servicio.yaml").write_text("modulos: [estadisticas]\n", encoding="utf-8")
        hoy = dt.datetime.now().isoformat(timespec="minutes")
        con = sqlite3.connect(a / "datos.sqlite")
        con.execute("CREATE TABLE registros(id INTEGER PRIMARY KEY, tipo TEXT, datos TEXT, creado TEXT)")
        for tipo, d in [("pedido", {"estado": "entregado", "total_c": 1250, "creado_at": hoy, "estado_at": hoy, "telefono": TEL, "nombre": "Ana Secreta"}),
                        ("pedido", {"estado": "nuevo", "total_c": 500, "creado_at": hoy}),
                        ("escalado", {"estado": "abierto", "telefono": TEL})]:
            con.execute("INSERT INTO registros(tipo,datos,creado) VALUES(?,?,?)", (tipo, json.dumps(d), hoy))
        con.commit(); con.close()
        self.ruta = self.raiz / "clientes.yaml"
        self.ruta.write_text(yaml.safe_dump({"clientes": {"uno": {"carpeta": "servicios/uno", "dominio": "uno.com", "activo": True}}}), encoding="utf-8")
        self.op = operador.Operador(self.ruta, CLAVE, plantilla=pl)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), operador.handler(self.op))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)

    def req(self, m, u, d=None, ck="", xff=None):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        h = {"Content-Type": "application/json", "Cookie": ck}
        if xff:
            h["X-Forwarded-For"] = xff
        c.request(m, u, json.dumps(d) if d is not None else None, h)
        r = c.getresponse(); b = r.read()
        return r.status, (json.loads(b) if b[:1] == b"{" else b), r.getheader("Set-Cookie")

    def sesion(self):
        return self.req("POST", "/api/entrar", {"clave": CLAVE})[2].split(";")[0]

    def test_sin_clave_no_arranca(self):
        with self.assertRaises(SystemExit):
            operador.Operador(self.ruta, "")

    def test_login_cookie_y_bloqueo(self):
        self.assertEqual(self.req("GET", "/api/clientes")[0], 401)
        st, _, ck = self.req("POST", "/api/entrar", {"clave": CLAVE})
        self.assertEqual(st, 200); self.assertIn("HttpOnly", ck)
        self.assertEqual(self.req("GET", "/api/clientes", ck="operador=9999999999.falsa")[0], 401)
        self.assertEqual(self.req("GET", "/api/clientes", ck=ck.split(";")[0])[0], 200)
        for _ in range(5):
            self.assertEqual(self.req("POST", "/api/entrar", {"clave": "mala"})[0], 401)
        self.assertEqual(self.req("POST", "/api/entrar", {"clave": CLAVE})[0], 429)

    def test_bloqueo_por_ip(self):
        os.environ["PROXY"] = "1"; self.addCleanup(os.environ.pop, "PROXY", None)
        for _ in range(5):
            self.assertEqual(self.req("POST", "/api/entrar", {"clave": "mala"}, xff="9.9.9.9, 1.1.1.1")[0], 401)
        self.assertEqual(self.req("POST", "/api/entrar", {"clave": CLAVE}, xff="x, 1.1.1.1")[0], 429)  # mismo IP real (el último)
        self.assertEqual(self.req("POST", "/api/entrar", {"clave": CLAVE}, xff="1.1.1.1, 2.2.2.2")[0], 200)  # otra IP no bloqueada

    def test_clave_corta(self):
        with self.assertRaises(SystemExit):
            operador.Operador(self.ruta, "a" * 15)

    def test_cookie_secure_y_salir(self):
        self.assertNotIn("Secure", self.req("POST", "/api/entrar", {"clave": CLAVE})[2])
        self.op.seguro = True
        ck = self.req("POST", "/api/entrar", {"clave": CLAVE})[2]
        self.assertIn("Secure", ck)
        sesion = ck.split(";")[0]
        st, _, borra = self.req("POST", "/api/salir", {}, sesion)
        self.assertEqual(st, 200); self.assertIn("Max-Age=0", borra)

    def test_lista_solo_agregados(self):
        st, j, _ = self.req("GET", "/api/clientes", ck=self.sesion())
        c = j["clientes"][0]
        self.assertEqual((c["hoy"], c["ventas_mes"], c["avisos"], c["modulos"]), (2, 1250, 1, ["estadisticas"]))
        self.assertGreater(c["bytes"], 0)
        txt = json.dumps(j)
        self.assertNotIn(TEL, txt); self.assertNotIn("Secreta", txt)

    def test_pausa_y_nuevo(self):
        ck = self.sesion()
        self.assertEqual(self.req("POST", "/api/clientes/uno/pausa", {"activo": False}, ck)[0], 200)
        self.assertFalse(self.req("GET", "/api/clientes", ck=ck)[1]["clientes"][0]["activo"])
        self.assertEqual(self.req("POST", "/api/clientes", {"nombre": "Dos", "slug": "dos", "dominio": "dos.com"}, ck)[0], 200)
        self.assertTrue((self.raiz / "servicios" / "dos" / "servicio.yaml").exists())
        self.assertEqual([c.slug for c in clientes.cargar(self.ruta)], ["uno", "dos"])  # el archivo real sigue cargable
        self.assertEqual(self.req("POST", "/api/clientes", {"nombre": "Dos", "slug": "dos", "dominio": "x.com"}, ck)[0], 409)  # slug duplicado

    def test_alta_invalida_deja_todo_igual(self):
        ck, antes = self.sesion(), self.ruta.read_text(encoding="utf-8")
        for d, st in (({"slug": "dos", "dominio": "uno.com"}, 409), ({"slug": "dos"}, 400), ({"slug": "dos", "dominio": "https://x.com"}, 400)):  # dominio repetido / vacío / mal formado
            self.assertEqual(self.req("POST", "/api/clientes", {"nombre": "Dos", **d}, ck)[0], st)
            self.assertEqual(self.ruta.read_text(encoding="utf-8"), antes)
            self.assertFalse((self.raiz / "servicios" / "dos").exists())

    def test_slug_invalido_y_traversal(self):
        ck = self.sesion()
        for s in ("../x", "A", "a/b", "", "a" * 41):
            self.assertEqual(self.req("POST", "/api/clientes", {"slug": s}, ck)[0], 400)
        self.assertEqual(self.req("POST", "/api/clientes/..%2Funo/respaldo", {}, ck)[0], 400)
        self.assertEqual(self.req("POST", "/api/clientes/nadie/respaldo", {}, ck)[0], 400)

    def test_respaldo(self):
        st, j, _ = self.req("POST", "/api/clientes/uno/respaldo", {}, self.sesion())
        self.assertEqual(st, 200)
        copia = self.raiz / "backups" / "uno" / j["archivo"]
        con = sqlite3.connect(copia)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM registros").fetchone()[0], 3)
        con.close()

    def test_config_y_pago(self):
        ck, base = self.sesion(), dt.date.today()
        ruta = "/api/clientes/uno/"
        for d in ({"modulos": ["x"]}, {"precio": -1}, {"precio": "9"}, {"precio": True}, {"notas": "a" * 501}):
            self.assertEqual(self.req("POST", ruta + "config", d, ck)[0], 400)
        self.assertEqual(self.req("POST", "/api/clientes/nadie/config", {"precio": 1}, ck)[0], 400)
        self.assertEqual(self.req("POST", ruta + "config", {"modulos": [], "precio": 30, "notas": "ok"}, ck)[0], 200)
        j = self.req("GET", "/api/clientes", ck=ck)[1]
        c = j["clientes"][0]
        self.assertEqual((c["modulos"], c["precio"], c["notas"], c["estado_pago"]), ([], 30, "ok", "sin_pago"))
        self.assertEqual(j["resumen"], {"activos": 1, "ingresos": 0, "vencidos": 0, "en_prueba": 0, "pruebas_por_vencer": 0})
        self.assertEqual(self.req("POST", ruta + "pago", {"meses": 2}, ck)[0], 400)
        st, r, _ = self.req("POST", ruta + "pago", {"meses": 3}, ck)
        self.assertEqual(r["pagado_hasta"], (base + dt.timedelta(days=90)).isoformat())
        self.req("POST", ruta + "pago", {"meses": 1}, ck)  # suma desde pagado_hasta, no desde hoy
        j = self.req("GET", "/api/clientes", ck=ck)[1]
        c = j["clientes"][0]
        self.assertEqual(c["pagado_hasta"], (base + dt.timedelta(days=120)).isoformat())
        self.assertEqual((c["estado_pago"], c["etiqueta_pago"], len(c["pagos"])), ("al_dia", "Al día", 2))
        self.assertEqual(j["resumen"]["ingresos"], 30)
        self.assertEqual(json.loads((self.raiz / "pagos.jsonl").read_text().splitlines()[0]),
                         {"fecha": base.isoformat(), "slug": "uno", "monto": 90, "meses": 3})
        e = yaml.safe_load(self.ruta.read_text())["clientes"]["uno"]
        self.assertEqual((e["dominio"], e["activo"]), ("uno.com", True))  # conserva el resto
        self.assertEqual([c.slug for c in clientes.cargar(self.ruta)], ["uno"])

    def test_prueba_y_tope_ia(self):
        ck, hoy, ruta = self.sesion(), dt.date.today(), "/api/clientes/uno/"
        # contrato: clientes.py los admite; si aún no, se parchea solo en esta prueba
        cl = lambda: self.req("GET", "/api/clientes", ck=ck)[1]
        for d in ({"accion": "x"}, {"accion": "iniciar", "dias": 10}, {"accion": "iniciar", "dias": True}, {"accion": "pagar", "meses": 2}):
            self.assertEqual(self.req("POST", ruta + "prueba", d, ck)[0], 400)
        for t in (-1, 100001, "5", True, 1.5):
            self.assertEqual(self.req("POST", ruta + "config", {"tope_ia_dia": t}, ck)[0], 400)
        j = cl(); c = j["clientes"][0]
        self.assertEqual((c["prueba_dias"], c["uso_ia"], c["tope_ia"], j["resumen"]["en_prueba"]), (None, 0, 0, 0))  # sin tabla uso_ia: 0
        self.assertEqual(self.req("POST", ruta + "prueba", {"accion": "iniciar", "dias": 7}, ck)[1]["hasta"], (hoy + dt.timedelta(days=7)).isoformat())
        j = cl(); c = j["clientes"][0]
        self.assertEqual((c["prueba_dias"], c["etiqueta_prueba"], c["tope_ia"]), (7, "Prueba: quedan 7 días", 300))  # defecto en prueba
        self.assertEqual((j["resumen"]["en_prueba"], j["resumen"]["pruebas_por_vencer"]), (1, 0))
        self.req("POST", ruta + "prueba", {"accion": "extender", "dias": 15}, ck)
        self.assertEqual(cl()["clientes"][0]["prueba_dias"], 22)
        self.assertEqual(self.req("POST", ruta + "config", {"tope_ia_dia": 0}, ck)[0], 200)  # 0 explícito = sin tope, no el defecto
        self.assertEqual(cl()["clientes"][0]["tope_ia"], 0)
        con = sqlite3.connect(self.raiz / "servicios" / "uno" / "datos.sqlite")
        con.execute("CREATE TABLE uso_ia(dia TEXT PRIMARY KEY, mensajes INTEGER)")
        con.execute("INSERT INTO uso_ia VALUES(?,87)", (hoy.isoformat(),)); con.commit(); con.close()
        self.req("POST", ruta + "config", {"tope_ia_dia": 300}, ck)
        c = cl()["clientes"][0]
        self.assertEqual((c["uso_ia"], c["tope_ia"]), (87, 300))
        y = yaml.safe_load(self.ruta.read_text())["clientes"]["uno"]; y["prueba_hasta"] = (hoy - dt.timedelta(days=2)).isoformat()
        self.ruta.write_text(yaml.safe_dump({"clientes": {"uno": y}}), encoding="utf-8")
        c = cl()["clientes"][0]
        self.assertEqual((c["prueba_dias"], c["etiqueta_prueba"]), (-2, "Prueba terminada hace 2 días (pausado)"))
        self.assertEqual(cl()["resumen"]["pruebas_por_vencer"], 0)
        st, r, _ = self.req("POST", ruta + "prueba", {"accion": "pagar", "meses": 1}, ck)
        self.assertEqual(r["hasta"], (hoy + dt.timedelta(days=30)).isoformat())
        e = yaml.safe_load(self.ruta.read_text())["clientes"]["uno"]
        self.assertNotIn("prueba_hasta", e); self.assertEqual((e["dominio"], e["tope_ia_dia"]), ("uno.com", 300))
        self.assertEqual(cl()["clientes"][0]["prueba_dias"], None)

    def test_estado_pago(self):
        h, f = dt.date(2026, 6, 10), operador.Operador.estado_pago
        self.assertEqual(f(None, h)[0], "sin_pago")
        self.assertEqual(f(h - dt.timedelta(days=3), h)[1:], (-3, "Vencido hace 3 días"))
        self.assertEqual(f(h + dt.timedelta(days=5), h)[::2], ("por_vencer", "Vence en 5 días"))
        self.assertEqual(f(h + dt.timedelta(days=6), h)[0], "al_dia")

    def test_post_exige_json(self):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_port)
        c.request("POST", "/api/entrar", "clave=x", {"Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 415)


if __name__ == "__main__":
    unittest.main()
