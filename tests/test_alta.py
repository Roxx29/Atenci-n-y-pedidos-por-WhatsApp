"""«Nuevo cliente» de la consola deja un restaurante listo (carpeta, usuario Dueno, tema, prueba). Ejecutar: python -m unittest tests.test_alta"""
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

from motor import clientes, operador, panel_web, web

CLAVE = "clave-larga-1234"
PIN = "482913"
REPO = Path(__file__).resolve().parent.parent
PLANTILLA = REPO / "servicios" / "_plantilla-restaurante"
BUENO = {"nombre": "Fonda La Ñapa", "slug": "napa", "dominio": "napa.example.com"}


def pedir(puerto, metodo, ruta, cuerpo=None, cookie="", host=None):
    c = http.client.HTTPConnection("127.0.0.1", puerto, timeout=10)
    h = {"Content-Type": "application/json", "Cookie": cookie, **({"Host": host} if host else {})}
    c.request(metodo, ruta, json.dumps(cuerpo) if cuerpo is not None else None, h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw), (r.getheader("Set-Cookie") or "").split(";")[0]


class Alta(unittest.TestCase):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"WEB_SECRET": "maestro"}, clear=True)
        p.start(); self.addCleanup(p.stop)
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.raiz = Path(t.name)
        self.ruta = self.raiz / "clientes.yaml"  # aún no existe: la primera alta lo crea
        self.op = operador.Operador(self.ruta, CLAVE)  # plantilla por defecto: la del repo
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), operador.handler(self.op))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.ck = pedir(self.srv.server_port, "POST", "/api/entrar", {"clave": CLAVE})[2]

    def alta(self, ck=None, **d):
        return pedir(self.srv.server_port, "POST", "/api/clientes", {**BUENO, **d}, self.ck if ck is None else ck)[:2]

    def carpeta(self, slug="napa"):
        return self.raiz / "servicios" / slug

    def dueno(self, slug="napa"):
        con = sqlite3.connect(self.carpeta(slug) / "datos.sqlite")
        try:
            return con.execute("SELECT nombre, rol, pin_hash, activo FROM usuarios").fetchall()
        finally:
            con.close()

    def nada(self):
        """No quedó ni carpeta ni registro."""
        self.assertEqual(list((self.raiz / "servicios").glob("*")) if (self.raiz / "servicios").exists() else [], [])
        self.assertFalse(self.ruta.exists())

    def test_la_plantilla_viene_del_repo_no_de_los_datos(self):
        self.assertEqual(self.op.plantilla, PLANTILLA)

    def test_alta_completa(self):
        st, j = self.alta(pin=PIN, color="#1A73E8", prueba_dias=15, dominio=" Napa.Example.com ")
        hasta = (dt.date.today() + dt.timedelta(days=15)).isoformat()
        self.assertEqual((st, j), (200, {"ok": True, "slug": "napa", "url_web": "https://napa.example.com",
                                         "url_panel": "https://napa.example.com/panel", "prueba_hasta": hasta, "pin": PIN}))
        cfg = yaml.safe_load((self.carpeta() / "servicio.yaml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["marca"], cfg["modulos"]), ("Fonda La Ñapa", []))
        self.assertIn("Fonda La Ñapa", cfg["nombre"]); self.assertIn("Fonda La Ñapa", cfg["negocio"])
        self.assertIsInstance(cfg["reparto"], dict)
        self.assertIn("(ejemplo)", cfg["pago"]["yappy"]); self.assertIn("(ejemplo)", cfg["pago"]["transferencia"])
        self.assertEqual(len(yaml.safe_load((self.carpeta() / "menu.yaml").read_text(encoding="utf-8"))), 3)
        self.assertIn("--primary:#1a73e8", (self.carpeta() / "tema.css").read_text(encoding="utf-8"))
        aviso = (self.carpeta() / "aviso.md").read_text(encoding="utf-8")
        self.assertIn("PENDIENTE DE COMPLETAR", aviso); self.assertIn("Fonda La Ñapa", aviso); self.assertIn("[RUC]", aviso)
        (nombre, rol, h, activo), = self.dueno()
        self.assertEqual((nombre, rol, activo), ("Dueno", "dueno", 1))
        self.assertTrue(h.startswith("pbkdf2$") and panel_web._coincide(PIN, None, h))
        self.assertFalse(panel_web._coincide("482914", None, h))
        c, = clientes.cargar(self.ruta)
        self.assertEqual((c.slug, c.dominio, c.activo, c.prueba_hasta), ("napa", "napa.example.com", True, dt.date.fromisoformat(hasta)))
        for f in self.carpeta().iterdir():  # nada de la plantilla sin rellenar ni del primer cliente
            if f.suffix in (".yaml", ".md"):
                t = f.read_text(encoding="utf-8")
                for malo in ("DeliFood", "delifood", "Chitré", "{nombre}", "{slug}", "{marca}"):
                    self.assertNotIn(malo, t, f.name)

    def test_pin_generado_sin_color_ni_prueba(self):
        st, j = self.alta()
        self.assertEqual((st, j["prueba_hasta"]), (200, None))
        self.assertRegex(j["pin"], r"^\d{6}$")
        self.assertNotIn(j["pin"], panel_web.PIN_DEBILES)
        self.assertTrue(panel_web._coincide(j["pin"], None, self.dueno()[0][2]))
        self.assertFalse((self.carpeta() / "tema.css").exists())  # sin color: el tema del motor
        self.assertNotIn("prueba_hasta", yaml.safe_load(self.ruta.read_text(encoding="utf-8"))["clientes"]["napa"])
        self.assertNotEqual(self.alta(slug="otra", dominio="otra.example.com")[1]["pin"], None)

    def test_el_pin_no_queda_en_claro_en_disco(self):
        self.assertEqual(self.alta(pin=PIN)[0], 200)
        for f in self.raiz.rglob("*"):
            if f.is_file():
                self.assertNotIn(PIN.encode(), f.read_bytes(), f.name)
        self.assertNotIn("pin", self.ruta.read_text(encoding="utf-8").lower())

    def test_slug_y_dominio_repetidos_dan_409(self):
        self.assertEqual(self.alta(pin=PIN)[0], 200)
        antes = self.ruta.read_bytes()
        st, j = self.alta(dominio="otro.example.com", pin="739155")
        self.assertEqual(st, 409); self.assertIsInstance(j["error"], str)
        self.assertEqual(self.alta(slug="otra")[0], 409)
        self.assertEqual(self.ruta.read_bytes(), antes)
        self.assertFalse(self.carpeta("otra").exists())
        self.assertTrue(panel_web._coincide(PIN, None, self.dueno()[0][2]))  # el intento repetido no pisó al dueño

    def test_entradas_hostiles_dan_400_sin_tocar_disco(self):
        malas = [{"slug": s} for s in ("../x", "a/b", "A", "", "a" * 41, "..", "a b", 5, None, "a\\b", "ñ")]
        malas += [{"dominio": x} for x in ("", "https://x.com", "x.com/ruta", "x.com:80", "x.localhost", "sinpunto", "a..b", "x.com\nb.com", 7, "a" * 99 + ".com")]
        malas += [{"nombre": x} for x in ("", "   ", "a" * 61, "a\nmodulos: [estadisticas]", "a\x00b", "a‮b", "a\tb", 9, None, ["x"])]
        malas += [{"color": x} for x in ("red", "#12345", "#1234567", "#12345g", "#fff;}body{display:none", "#ffffff}", 1, "1a73e8", ["#ffffff"])]
        malas += [{"prueba_dias": x} for x in (1, 10, -7, True, "7", 7.5, [7])]
        malas += [{"pin": x} for x in ("12345", "1234567", "123456", "abcdef", 482913, "４８２９１３", "48291 ", ["482913"])]
        for d in malas:
            st, j = self.alta(**{"pin": PIN, **d})
            self.assertEqual(st, 400, d)
            self.assertNotIn(PIN, j["error"], d)
            self.nada()

    def test_el_nombre_no_inyecta_yaml(self):
        nombre = "\": {a: b} # ' \\ - [x] %s {nombre} {slug}"
        self.assertEqual(self.alta(nombre=nombre)[0], 200)
        cfg = yaml.safe_load((self.carpeta() / "servicio.yaml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["marca"], cfg["modulos"]), (nombre, []))
        self.assertIn("@napa", cfg["pago"]["yappy"])  # el {slug} de la plantilla sí; el del nombre quedó literal

    def test_fallo_a_mitad_lo_deshace_todo_y_no_filtra_el_pin(self):
        with unittest.mock.patch.object(self.op, "_guardar", side_effect=RuntimeError(f"disco lleno {PIN}")):
            st, j = self.alta(pin=PIN)
        self.assertEqual(st, 500)
        self.assertNotIn(PIN, json.dumps(j)); self.assertNotIn("disco", j["error"])
        self.nada()
        self.assertEqual(self.alta(pin=PIN)[0], 200)  # no quedó nada que estorbe al reintento

    def test_rechazo_de_clientes_cargar_deshace_la_carpeta(self):
        st, j = self.alta(slug="personal", pin=PIN)  # PANEL_PIN_PERSONAL ya significa otra cosa: lo rechaza clientes.cargar
        self.assertEqual(st, 400); self.assertNotIn(PIN, j["error"])
        self.nada()

    def test_sin_sesion_no_crea_nada(self):
        self.assertEqual(self.alta(ck="operador=9999999999.falsa")[0], 401)
        self.nada()

    def test_el_cliente_nuevo_arranca_en_alojar_y_su_panel_acepta_el_pin(self):
        self.assertEqual(self.alta(pin=PIN, color="#1a73e8")[0], 200)
        srv = web.crear_alojamiento(clientes.cargar(self.ruta), 0, ruta=self.ruta)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [sv.cerrar() for sv in srv.servicios.values()])
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        puerto, host = srv.server_address[1], "napa.example.com"
        self.assertEqual(pedir(puerto, "GET", "/config", host=host)[1]["marca"], "Fonda La Ñapa")
        self.assertEqual(len(pedir(puerto, "GET", "/menu", host=host)[1]), 3)
        self.assertIn(b"--primary:#1a73e8", pedir(puerto, "GET", "/", host=host)[1])
        self.assertEqual(pedir(puerto, "POST", "/panel/entrar", {"pin": "739155"}, host=host)[0], 401)
        st, _, ck = pedir(puerto, "POST", "/panel/entrar", {"pin": PIN}, host=host)
        self.assertEqual(st, 200)
        self.assertEqual(pedir(puerto, "GET", "/panel/dueno/usuarios", cookie=ck, host=host)[0], 200)  # entra como dueño

    def test_restaurante_pedidos_no_cambia(self):
        orig = REPO / "servicios" / "restaurante-pedidos"
        foto = lambda: {f.name: f.read_bytes() for f in orig.iterdir() if f.suffix in (".yaml", ".md")}
        antes = foto()
        self.assertEqual(self.alta(pin=PIN, color="#1a73e8")[0], 200)
        self.assertEqual(foto(), antes)
        self.assertIn("DeliFood", antes["servicio.yaml"].decode("utf-8"))


class Plantilla(unittest.TestCase):
    def test_generica_sin_datos_del_primer_cliente(self):
        for f in PLANTILLA.iterdir():
            t = f.read_text(encoding="utf-8")
            for malo in ("DeliFood", "delifood", "Chitré", "Herrera", "Los Santos"):
                self.assertNotIn(malo, t, f.name)


if __name__ == "__main__":
    unittest.main()
