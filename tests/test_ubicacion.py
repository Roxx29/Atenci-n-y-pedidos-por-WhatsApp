"""Ubicación del cliente y rol repartidor. Ejecutar: python -m unittest tests.test_ubicacion"""
import datetime as dt
import os
import unittest
import unittest.mock

from motor import herramientas, panel_web, ubicacion
from tests.test_web import PEDIDO, TEL, Base

LARGO = "https://www.google.com/maps/place/Chitre/@7.9667,-80.4333,17z/data=!3m1"
ENT = {"items": [{"id": "sancocho", "cantidad": 2}], "modalidad": "entrega", "nombre": "Ana", "direccion": "Calle 5", "zona": ""}
PIN_P, PIN_J, PIN_L = "483920", "739104", "264817"


class Funciones(unittest.TestCase):
    def test_extraer(self):
        for u in (LARGO, "https://www.google.com/maps?q=7.9667,-80.4333", "https://www.google.com/maps/place/X/data=!3d7.9667!4d-80.4333", "https://maps.google.com/?ll=7.9667%2C-80.4333"):
            self.assertEqual(ubicacion.extraer(u), (7.9667, -80.4333))
        self.assertIsNone(ubicacion.extraer("https://www.google.com/maps/place/Chitre"))

    def test_enlace_largo_pegado_a_mano_y_corto(self):
        self.assertEqual(ubicacion.desde_enlace(LARGO), (7.9667, -80.4333, LARGO))
        self.assertEqual(ubicacion.desde_enlace("7.9667, -80.4333"), (7.9667, -80.4333, None))
        self.assertEqual(ubicacion.desde_enlace("https://maps.app.goo.gl/abc", resolver=lambda u: LARGO), (7.9667, -80.4333, LARGO))

    def test_solo_dominios_de_google_y_nunca_se_sigue_otro(self):
        resolver = unittest.mock.Mock(return_value=LARGO)
        for malo in ("https://evil.com/maps/@7.9,-80.4", "http://www.google.com/maps/@7.9667,-80.4333", "https://google.com.evil.com/x", "https://goo.gl.evil.com/x", "javascript:alert(1)"):
            with self.assertRaises(ValueError):
                ubicacion.desde_enlace(malo, resolver=resolver)
        resolver.assert_not_called()
        # un corto que redirige a un sitio no-Google: ni sus coordenadas ni su URL se usan; queda el enlace original (de Google)
        corto = "https://maps.app.goo.gl/abc"
        self.assertEqual(ubicacion.desde_enlace(corto, resolver=lambda u: "https://evil.com/@7.9667,-80.4333"), (None, None, corto))
        self.assertTrue(ubicacion.host_ok("https://g.co/kgs/abc"))

    def test_enlace_de_google_sin_coordenadas_se_acepta(self):
        place = "https://www.google.com/maps/place/Parque+Union+Chitre"
        self.assertEqual(ubicacion.desde_enlace(place, resolver=lambda u: u), (None, None, place))
        self.assertEqual(ubicacion.url_mapa({"ubicacion": {"lat": None, "lon": None, "enlace": place}, "direccion": "Calle 5"}), place)
        self.assertIn("Calle%205", ubicacion.url_mapa({"ubicacion": {"lat": None, "lon": None}, "direccion": "Calle 5"}))

    def test_validar_rango_y_zona(self):
        self.assertEqual(ubicacion.validar(7.9667, -80.4333), (7.9667, -80.4333))
        self.assertEqual(ubicacion.validar(8.9824, -79.5199), (8.9824, -79.5199))  # Panamá ciudad: válida
        self.assertTrue(ubicacion.fuera_de_zona(8.9824, -79.5199))
        self.assertFalse(ubicacion.fuera_de_zona(7.9667, -80.4333))
        for lat, lon in ((91, 0), (float("nan"), 0), ("7.9", -80.4), (True, -80.4), (None, None)):
            with self.assertRaises(ValueError):
                ubicacion.validar(lat, lon)

    def test_url_mapa(self):
        self.assertEqual(ubicacion.url_mapa({"ubicacion": {"lat": 7.9, "lon": -80.4}}), "https://www.google.com/maps/dir/?api=1&destination=7.9,-80.4")
        self.assertIn("Calle%205", ubicacion.url_mapa({"direccion": "Calle 5", "zona": "Chitré"}))

    def test_pin_repartidores(self):
        with unittest.mock.patch.dict(os.environ, {"REPARTIDORES": f"Juan:{PIN_J},Pedro:{PIN_J},Luis:123456,Ana.B:{PIN_L},Maria:{panel_web.hash_pin('918273')}", "REPARTIDOR_PIN": ""}):
            r = panel_web.env_repartidores()
        self.assertEqual(sorted(r), ["Juan", "Maria"])  # repetido, trivial y nombre no válido se ignoran


class Flujo(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN_P, "PANEL_PIN_HASH": "", "PANEL_PIN_PERSONAL": "", "REPARTIDORES": f"Juan:{PIN_J},Pedro:{PIN_L}", "REPARTIDOR_PIN": ""})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    def entrega(self, tel=TEL):
        p = herramientas.crear_pedido(self.sv, telefono=tel, **ENT)
        return p["pedido_id"]

    def pedido(self, pid):
        return next(p for p in self.sv.listar("pedido") if p["id"] == pid)

    def cookie(self, ruta, pin):
        st, d, r = self.js("POST", ruta, {"pin": pin})
        return st, (r.getheader("Set-Cookie") or "").split(";")[0]

    # --- cliente ---
    def test_ubicacion_navegador_y_no_a_la_ia(self):
        pid = self.entrega()
        ck = self.entrar()[2]
        st, d, _ = self.js("POST", "/ubicacion", {"lat": 7.9667, "lon": -80.4333, "precision": 20}, cookie=ck)
        self.assertEqual((st, d["pedido_id"]), (200, pid))
        u = self.pedido(pid)["ubicacion"]
        self.assertEqual((u["lat"], u["fuente"]), (7.9667, "navegador"))
        vista = herramientas._pedido_para_modelo(self.pedido(pid))
        self.assertNotIn("7.9667", str(vista))
        self.assertTrue(vista["ubicacion_recibida"])
        self.assertTrue(self.js("GET", "/pedidos", cookie=ck)[1]["pedidos"][0]["ubicacion"])

    def test_ubicacion_enlace_y_errores(self):
        self.entrega()
        ck = self.entrar()[2]
        self.assertEqual(self.js("POST", "/ubicacion", {"enlace": LARGO}, cookie=ck)[0], 200)
        self.assertEqual(self.pedido(1)["ubicacion"]["enlace"], LARGO)
        for cuerpo in ({"enlace": "https://evil.com/@7.9667,-80.4333"}, {"lat": "x", "lon": 1}, {"enlace": 5}):
            st, d, _ = self.js("POST", "/ubicacion", cuerpo, cookie=ck)
            self.assertEqual(st, 400)
            self.assertIn("error", d)
        self.assertEqual(self.js("POST", "/ubicacion", {"enlace": LARGO})[0], 401)

    def test_lejos_y_sin_coordenadas_se_aceptan_con_aviso(self):
        pid = self.entrega()
        ck = self.entrar()[2]
        self.assertEqual(self.js("POST", "/ubicacion", {"lat": 8.9824, "lon": -79.5199}, cookie=ck)[0], 200)
        self.assertTrue(self.pedido(pid)["ubicacion"]["fuera_de_zona"])
        place = "https://www.google.com/maps/place/Parque+Union+Chitre"
        with unittest.mock.patch.object(ubicacion, "_seguir", lambda u, timeout=4: u):
            st, d, _ = self.js("POST", "/ubicacion", {"enlace": place}, cookie=ck)
        self.assertEqual(st, 200)
        u = self.pedido(pid)["ubicacion"]
        self.assertEqual((u["lat"], u["enlace"]), (None, place))
        self.assertEqual(self.js("GET", "/pedidos", cookie=ck)[1]["pedidos"][0]["ubicacion"], True)
        # panel y repartidor funcionan sin coordenadas
        herramientas.confirmar_pedido(self.sv, pid)
        for e in ("pagado", "en_preparacion", "listo"):
            herramientas.marcar_pedido(self.sv, pid, e)
        pan = self.cookie("/panel/entrar", PIN_P)[1]
        self.js("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Juan"}, cookie=pan)
        self.assertEqual(self.js("GET", f"/panel/pedido/{pid}", cookie=pan)[1]["pedido"]["mapa"], place)
        juan = self.cookie("/repartidor/entrar", PIN_J)[1]
        p = self.js("GET", "/repartidor/pedidos", cookie=juan)[1]["pedidos"][0]
        self.assertEqual((p["mapa"], p["tiene_ubicacion"], p["fuera_de_zona"]), (place, True, False))

    def test_ubicacion_antes_del_pedido_se_une_al_siguiente(self):
        ck = self.entrar()[2]
        st, d, _ = self.js("POST", "/ubicacion", {"lat": 7.9667, "lon": -80.4333}, cookie=ck)
        self.assertEqual((st, d["pedido_id"]), (200, None))
        pid = self.entrega()
        self.js("POST", "/chat", {"texto": "hola"}, cookie=ck)  # el turno la une al pedido de entrega abierto
        self.assertEqual(self.pedido(pid)["ubicacion"]["lat"], 7.9667)

    def test_herramienta_enlace_del_cliente(self):
        self.sv.cfg["herramientas"].append("guardar_ubicacion")
        pid = self.entrega()
        turno = {"texto": f"mi ubicación {LARGO}", "creados": set()}
        herramientas.ejecutar(self.sv, "guardar_ubicacion", {"pedido_id": pid, "enlace": LARGO}, TEL, turno)
        self.assertEqual(self.pedido(pid)["ubicacion"]["lon"], -80.4333)
        with self.assertRaises(ValueError):  # un enlace que el cliente no escribió
            herramientas.ejecutar(self.sv, "guardar_ubicacion", {"pedido_id": pid, "enlace": "7.9, -80.4"}, TEL, turno)
        with self.assertRaises(ValueError):  # pedido ajeno
            herramientas.ejecutar(self.sv, "guardar_ubicacion", {"pedido_id": pid, "enlace": LARGO}, "50799990000", turno)

    def test_se_borra_al_entregar_cancelar_y_a_las_24h(self):
        self.sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=self.sv.tz)
        for fin in ("entregado", "cancelado"):
            pid = self.entrega()
            herramientas.poner_ubicacion(self.sv, pid, 7.9667, -80.4333, "navegador")
            if fin == "cancelado":
                herramientas.cancelar_pedido(self.sv, pid)
            else:
                herramientas.confirmar_pedido(self.sv, pid)
                for e in ("pagado", "en_preparacion", "listo", "en_camino", "entregado"):
                    herramientas.marcar_pedido(self.sv, pid, e)
            self.assertIsNone(self.pedido(pid).get("ubicacion"))
        pid = self.entrega()
        herramientas.confirmar_pedido(self.sv, pid)
        herramientas.poner_ubicacion(self.sv, pid, 7.9667, -80.4333, "navegador")
        self.sv.ahora = lambda: dt.datetime(2026, 11, 3, 13, 0, tzinfo=self.sv.tz)
        herramientas.purgar_comprobantes(self.sv)
        self.assertIsNone(self.pedido(pid).get("ubicacion"))

    # --- repartidor ---
    def test_repartidor_sin_configurar_404(self):
        with unittest.mock.patch.dict(os.environ, {"REPARTIDORES": ""}):
            self.arrancar()
        self.assertEqual(self.req("GET", "/repartidor")[0].status, 404)

    def test_pwa_y_login(self):
        self.assertEqual(self.req("GET", "/repartidor")[0].status, 200)
        r, raw = self.req("GET", "/repartidor/manifest.webmanifest")
        self.assertIn(b"standalone", raw)
        self.assertEqual(self.req("GET", "/repartidor/sw.js")[0].status, 200)
        self.assertEqual(self.req("GET", "/repartidor/icono.svg")[0].status, 200)
        self.assertEqual(self.js("GET", "/repartidor/pedidos")[0], 401)
        self.assertEqual(self.cookie("/repartidor/entrar", "000111")[0], 401)
        st, ck = self.cookie("/repartidor/entrar", PIN_J)
        self.assertTrue(ck.startswith("repartidor="))
        self.assertEqual(self.js("GET", "/repartidor/pedidos", cookie=ck)[0], 200)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[0], 401)  # la cookie del repartidor no abre el panel
        panel = self.cookie("/panel/entrar", PIN_P)[1]
        self.assertEqual(self.js("GET", "/repartidor/pedidos", cookie=panel)[0], 401)

    def test_bloqueo_repartidor(self):
        for _ in range(5):
            self.assertEqual(self.cookie("/repartidor/entrar", "000111")[0], 401)
        self.assertEqual(self.cookie("/repartidor/entrar", PIN_J)[0], 429)

    def test_asignar_ver_y_marcar(self):
        pid = self.entrega()
        herramientas.poner_ubicacion(self.sv, pid, 7.9667, -80.4333, "navegador")
        pan = self.cookie("/panel/entrar", PIN_P)[1]
        juan, pedro = self.cookie("/repartidor/entrar", PIN_J)[1], self.cookie("/repartidor/entrar", PIN_L)[1]
        self.assertEqual(self.js("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Juan"}, cookie=pan)[0], 409)  # aún sin pagar
        herramientas.confirmar_pedido(self.sv, pid)
        herramientas.marcar_pedido(self.sv, pid, "pagado")
        self.assertEqual(self.js("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Nadie"}, cookie=pan)[0], 409)
        d = self.js("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Juan"}, cookie=pan)[1]
        self.assertEqual((d["pedido"]["repartidor"], d["pedido"]["tiene_ubicacion"]), ("Juan", True))
        est = self.js("GET", "/panel/estado", cookie=pan)[1]
        self.assertEqual(est["repartidores"], ["Juan", "Pedro"])
        self.assertIn("7.9667", self.js("GET", f"/panel/pedido/{pid}", cookie=pan)[1]["pedido"]["mapa"])
        self.assertEqual(self.js("GET", "/repartidor/pedidos", cookie=juan)[1]["pedidos"], [])  # pagado: aún no está en preparación
        herramientas.marcar_pedido(self.sv, pid, "en_preparacion")
        herramientas.marcar_pedido(self.sv, pid, "listo")
        lista = self.js("GET", "/repartidor/pedidos", cookie=juan)[1]["pedidos"]
        self.assertEqual([(p["id"], p["acciones"], p["pagado"], p["telefono"]) for p in lista], [(pid, ["en_camino"], True, TEL)])
        self.assertIn("destination=7.9667,-80.4333", lista[0]["mapa"])
        self.assertEqual(self.js("GET", "/repartidor/pedidos", cookie=pedro)[1]["pedidos"], [])  # solo los suyos
        self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": pid, "estado": "en_camino"}, cookie=pedro)[0], 409)
        self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": pid, "estado": "entregado"}, cookie=juan)[0], 409)  # antes va en_camino
        self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": pid, "estado": "en_camino"}, cookie=juan)[0], 200)
        self.assertEqual(self.pedido(pid)["en_camino_por"], "Juan")
        self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": pid, "estado": "entregado"}, cookie=juan)[0], 200)
        self.assertEqual(self.js("GET", "/repartidor/pedidos", cookie=juan)[1]["pedidos"], [])  # sin historial
        self.assertIsNone(self.pedido(pid).get("ubicacion"))
        acc = [a["accion"] for a in self.sv.listar("auditoria")]
        for esperado in ("repartidor_login", "asignar", "en_camino", "entregado"):
            self.assertIn(esperado, acc)
        self.assertNotIn("7.9667", str(self.sv.listar("auditoria")))

    def test_whatsapp_location(self):
        from motor import whatsapp
        pid = self.entrega()
        payload = {"entry": [{"changes": [{"value": {"messages": [{"id": "m1", "from": TEL, "type": "location", "location": {"latitude": 7.9667, "longitude": -80.4333}}]}}]}]}
        (tel, lat, lon), = whatsapp.ubicaciones_entrantes(payload)
        self.assertTrue(whatsapp.guardar_ubicacion_wa(self.sv, tel, lat, lon))
        self.assertEqual(self.pedido(pid)["ubicacion"]["fuente"], "whatsapp")
        self.assertTrue(whatsapp.guardar_ubicacion_wa(self.sv, TEL, 9.0, -79.5))  # lejos: se guarda con aviso
        self.assertTrue(self.pedido(pid)["ubicacion"]["fuera_de_zona"])
        self.assertFalse(whatsapp.guardar_ubicacion_wa(self.sv, TEL, 99, 0))

    def test_recogida_no_se_asigna(self):
        pid = herramientas.crear_pedido(self.sv, telefono=TEL, **PEDIDO)["pedido_id"]
        herramientas.confirmar_pedido(self.sv, pid)
        herramientas.marcar_pedido(self.sv, pid, "pagado")
        pan = self.cookie("/panel/entrar", PIN_P)[1]
        self.assertEqual(self.js("POST", "/panel/asignar", {"pedido_id": pid, "repartidor": "Juan"}, cookie=pan)[0], 409)


if __name__ == "__main__":
    unittest.main()
