"""Mapa en vivo del repartidor (servidor). Ejecutar: python -m unittest tests.test_mapa"""
import datetime as dt
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from motor import herramientas, panel_web, ubicacion, web
from tests.test_ubicacion import ENT, PIN_J, PIN_L, PIN_P
from tests.test_web import CID, TEL, Base, cid_tel

TEL_B = "50760002222"
AQUI = {"lat": 7.9667, "lon": -80.4333, "precision": 15}
REST = {"RESTAURANTE_LAT": "7.96", "RESTAURANTE_LON": "-80.43"}
LEJOS, CERCA = (8.00, -80.43), (7.97, -80.43)  # del restaurante


class Mapa(Base):
    def setUp(self):
        for p in (unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN_P, "PANEL_PIN_HASH": "", "PANEL_PIN_PERSONAL": "", "REPARTIDORES": f"Juan:{PIN_J},Pedro:{PIN_L}", "REPARTIDOR_PIN": ""}),
                  unittest.mock.patch.object(ubicacion, "REP_INTERVALO_S", 0)):
            p.start()
            self.addCleanup(p.stop)
        self.arrancar()
        self.pan = self.cookie("/panel/entrar", PIN_P)
        self.juan = self.cookie("/repartidor/entrar", PIN_J)

    def cookie(self, ruta, pin):
        return (self.js("POST", ruta, {"pin": pin})[2].getheader("Set-Cookie") or "").split(";")[0]

    def aceptar(self, ck=None):
        return self.js("POST", "/repartidor/aviso_ubicacion", {"version": "v1"}, cookie=ck or self.juan)[0]

    def listo(self, tel=TEL, rep="Juan", ubic=None):
        pid = herramientas.crear_pedido(self.sv, telefono=tel, **ENT)["pedido_id"]
        if ubic:
            herramientas.poner_ubicacion(self.sv, pid, *ubic, "navegador")
        herramientas.confirmar_pedido(self.sv, pid)
        for e in ("pagado", "en_preparacion", "listo"):
            herramientas.marcar_pedido(self.sv, pid, e)
        panel_web.asignar(self.sv, pid, rep, ["Juan", "Pedro"])
        return pid

    def camino(self, tel=TEL, ubic=None):
        pid = self.listo(tel, ubic=ubic)
        panel_web.marcar(self.sv, pid, "en_camino", "Juan")
        return pid

    def enviar(self, cuerpo=AQUI, ck=None):
        return self.js("POST", "/repartidor/posicion", cuerpo, cookie=ck or self.juan)

    def mapa(self):
        return self.js("GET", "/panel/repartidores_mapa", cookie=self.pan)[1]["repartidores"]

    def test_auth(self):
        self.assertEqual(self.enviar(ck="x=1")[0], 401)
        self.assertEqual(self.js("GET", "/panel/repartidores_mapa")[0], 401)
        self.assertEqual(self.js("GET", "/panel/repartidores_mapa", cookie=self.juan)[0], 401)  # cookie de repartidor no abre el panel
        self.assertEqual(self.enviar(ck=self.pan)[0], 401)
        self.assertEqual(self.req("POST", "/repartidor/posicion", crudo="lat=1", tipo="text/plain", cookie=self.juan)[0].status, 415)  # CSRF: solo JSON

    def test_aviso_obligatorio(self):
        self.camino()
        d = self.js("GET", "/repartidor/pedidos", cookie=self.juan)[1]["aviso_ubicacion"]
        self.assertEqual((d["version"], d["aceptado"]), ("v1", False))
        self.assertIn("a Fonda", d["texto"])
        self.assertEqual(self.enviar()[0], 403)
        self.assertEqual(self.js("POST", "/repartidor/aviso_ubicacion", {"version": "v0"}, cookie=self.juan)[0], 409)
        self.assertEqual(self.aceptar(), 200)
        self.assertTrue(self.js("GET", "/repartidor/pedidos", cookie=self.juan)[1]["aviso_ubicacion"]["aceptado"])
        self.assertEqual(self.enviar()[0], 200)

    def test_sin_pedido_en_camino_409_y_limite_429(self):
        self.aceptar()
        self.listo()  # listo, aún no en camino
        self.assertEqual(self.enviar()[0], 409)
        with unittest.mock.patch.object(ubicacion, "REP_INTERVALO_S", 5):
            self.assertEqual(self.enviar()[0], 429)  # el intento anterior fue hace < 5 s

    def test_validacion(self):
        self.aceptar()
        self.camino()
        for malo in ({**AQUI, "lat": "7.9"}, {**AQUI, "lat": 91}, {**AQUI, "lon": -181}, {**AQUI, "lat": True}, {"lat": 7.9667, "lon": -80.4333},
                     {**AQUI, "precision": -1}, {**AQUI, "precision": 5001}, {**AQUI, "precision": "10"}, {**AQUI, "lat": 8.9824, "lon": -79.5199}):  # la última: Ciudad de Panamá, fuera de zona
            self.assertEqual(self.enviar(malo)[0], 400, malo)
        self.assertEqual(self.mapa(), [])

    def test_panel_y_cliente_solo_el_suyo_y_siguiente_parada(self):
        self.aceptar()
        a, b = self.camino(CID), self.camino(cid_tel(TEL_B))  # pedidos web: los ve el navegador de cada uno
        ca, cb, cc = self.entrar()[2], self.entrar(tel=TEL_B, nombre="Luis")[2], self.entrar(tel="50760003333", nombre="Eva")[2]
        self.assertIsNone(self.js("GET", "/pedidos", cookie=ca)[1]["pedidos"][0]["repartidor_pos"])  # aún sin posición
        self.assertEqual(self.enviar()[0], 200)
        filas = self.mapa()
        self.assertEqual([(f["repartidor"], f["pedido_id"], f["lat"], f["viejo"]) for f in filas], [("Juan", a, 7.9667, False), ("Juan", b, 7.9667, False)])
        st, d, r = self.js("GET", "/pedidos", cookie=ca)
        self.assertEqual(r.getheader("Cache-Control"), "no-store")
        p = d["pedidos"][0]
        self.assertEqual((p["repartidor"], p["mensaje"], set(p["repartidor_pos"])), ("Juan", None, {"lat", "lon", "precision", "edad_s", "viejo"}))
        pb = self.js("GET", "/pedidos", cookie=cb)[1]["pedidos"][0]
        self.assertEqual((pb["repartidor_pos"], pb["mensaje"]), (None, ubicacion.REP_OTRA_PARADA))  # no es la siguiente parada
        self.assertEqual(self.js("GET", "/pedidos", cookie=cc)[1], {"pedidos": []})
        self.assertNotIn(PIN_J, str(d))
        # entregado A: B pasa a ser la siguiente
        self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": a, "estado": "entregado"}, cookie=self.juan)[0], 200)
        self.assertEqual(self.js("GET", "/pedidos", cookie=cb)[1]["pedidos"][0]["repartidor_pos"]["lat"], 7.9667)
        self.assertNotIn("repartidor_pos", str(self.js("GET", "/pedidos", cookie=ca)[1]))  # entregado: sin dato
        # entregado B (desde el panel): se borra
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": b, "estado": "entregado"}, cookie=self.pan)[0], 200)
        self.assertEqual(self.mapa(), [])
        acc = [x["accion"] for x in self.sv.listar("auditoria")]
        self.assertEqual((acc.count("ubicacion_on"), acc.count("ubicacion_off")), (1, 1))
        self.assertNotIn("7.96", str(self.sv.listar("auditoria")) + str(self.sv.listar("pedido")))  # ni en auditoría ni en el pedido

    def test_borrado_al_cancelar_salir_y_caducar(self):
        self.aceptar()
        pid = self.camino()
        self.enviar()
        self.sv.actualizar(pid, {"estado": "cancelado"}, tipo="pedido")
        self.assertEqual(self.mapa(), [])
        self.sv.actualizar(pid, {"estado": "en_camino"}, tipo="pedido")
        self.assertEqual(self.mapa(), [])  # borrado, no vuelve
        self.enviar()
        self.js("POST", "/repartidor/salir", cookie=self.juan)
        self.assertEqual(self.mapa(), [])
        self.assertEqual([x["accion"] for x in self.sv.listar("auditoria")][-2:], ["repartidor_salir", "ubicacion_off"])
        self.juan = self.cookie("/repartidor/entrar", PIN_J)
        self.enviar()
        self.assertEqual(len(self.mapa()), 1)
        hace3h = (self.sv.ahora() - dt.timedelta(hours=3)).isoformat()
        self.sv.actualizar(pid, {"en_camino_at": hace3h}, tipo="pedido")
        self.assertEqual(self.mapa(), [])  # más de 2 h en camino: ni se sirve
        self.assertEqual(self.enviar()[0], 409)  # ni se guarda

    def test_dato_viejo(self):
        self.aceptar()
        self.camino()
        self.enviar()
        with unittest.mock.patch.object(ubicacion, "REP_VIEJO_S", -1):
            self.assertTrue(self.mapa()[0]["viejo"])

    def test_estatico_js_css_y_csp(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "images").mkdir()
            for n in ("leaflet-1.9.4.js", "leaflet-1.9.4.css", "images/marker-icon.png"):
                (Path(d) / n).write_bytes(b"x")
            with unittest.mock.patch.object(web, "ESTATICO", Path(d)):
                for n, tipo in (("leaflet-1.9.4.js", "text/javascript"), ("leaflet-1.9.4.css", "text/css"), ("images/marker-icon.png", "image/png")):
                    r, raw = self.req("GET", "/estatico/" + n)
                    self.assertEqual((r.status, r.getheader("Content-Type"), r.getheader("X-Content-Type-Options")), (200, tipo, "nosniff"), n)
                self.assertEqual(self.req("GET", "/estatico/images/otra.png")[0].status, 404)
        for n in web.LEAFLET:
            self.assertTrue((web.ESTATICO / n).is_file(), n)  # autoalojado en motor/estatico/
            self.assertNotIn(n.encode(), web.SW_REP)  # el service worker no lo cachea
        self.assertIn("img-src 'self' blob: data: https://tile.openstreetmap.org;", web.CSP)
        self.assertNotIn("*", web.CSP)

    def test_restaurante_en_mapa_del_panel(self):
        r = self.js("GET", "/panel/repartidores_mapa", cookie=self.pan)[1]
        self.assertIsNone(r["restaurante"])
        with unittest.mock.patch.dict(os.environ, {"RESTAURANTE_LAT": "7.96", "RESTAURANTE_LON": "-80.43"}):
            r = self.js("GET", "/panel/repartidores_mapa", cookie=self.pan)[1]
        self.assertEqual((r["restaurante"]["lat"], r["restaurante"]["lon"]), (7.96, -80.43))
        with unittest.mock.patch.dict(os.environ, {"RESTAURANTE_LAT": "x", "RESTAURANTE_LON": "1"}):
            self.assertIsNone(self.js("GET", "/panel/repartidores_mapa", cookie=self.pan)[1]["restaurante"])

    def ruta_rep(self, ck=None):
        return self.js("GET", "/repartidor/pedidos", cookie=ck or self.juan)[1]

    def orden(self, ck=None):
        return [(p["id"], p["n"]) for p in self.ruta_rep(ck)["ruta"]["paradas"]]

    def test_ruta_repartidor_y_panel_aislada_por_repartidor(self):
        lejos, cerca = self.camino(TEL, LEJOS), self.listo(TEL_B, ubic=CERCA)  # en camino y listo: los dos entran en la ruta
        self.listo("50760004444")  # sin ubicación
        prep = herramientas.crear_pedido(self.sv, telefono="50760005555", **ENT)["pedido_id"]
        herramientas.confirmar_pedido(self.sv, prep)
        for e in ("pagado", "en_preparacion"):
            herramientas.marcar_pedido(self.sv, prep, e)
        panel_web.asignar(self.sv, prep, "Juan", ["Juan", "Pedro"])  # en preparación: aún no es parada
        otro = self.listo("50760003333", "Pedro", CERCA)
        pedro = self.cookie("/repartidor/entrar", PIN_L)
        with unittest.mock.patch.dict(os.environ, REST):
            d = self.ruta_rep()
            r = d["ruta"]
            self.assertEqual(r["origen"], {"lat": 7.96, "lon": -80.43})
            self.assertEqual([p["id"] for p in r["paradas"]][:2], [cerca, lejos])
            self.assertEqual([p["n"] for p in r["paradas"]], [1, 2, 3])
            self.assertEqual(set(r["paradas"][0]), {"id", "n", "lat", "lon", "eta_min", "dist_m", "fuera_de_viaje"})
            self.assertEqual((r["paradas"][2]["lat"], r["paradas"][2]["eta_min"]), (None, None))  # sin ubicación: al final
            self.assertEqual(r["total_min"], r["paradas"][1]["eta_min"])
            self.assertLess(r["paradas"][0]["eta_min"], r["paradas"][1]["eta_min"])
            self.assertEqual({p["id"]: p["parada"] for p in d["pedidos"]}, {**{p["id"]: p["n"] for p in r["paradas"]}, prep: None})
            dp = self.ruta_rep(pedro)  # Pedro solo ve la suya
            self.assertEqual([p["id"] for p in dp["ruta"]["paradas"]], [otro])
            self.assertEqual([p["id"] for p in dp["pedidos"]], [otro])
            self.assertEqual(self.js("GET", "/panel/estado", cookie=self.pan)[1]["rutas"], {"Juan": r, "Pedro": dp["ruta"]})
            self.assertEqual(self.js("POST", "/repartidor/marcar", {"pedido_id": otro, "estado": "en_camino"}, cookie=pedro)[1]["pedido"]["parada"], 1)
            self.sv.poner_ajuste("reparto", {"velocidad_kmh": 1})  # los ajustes del dueño no pisan reparto: de servicio.yaml
            self.assertEqual(self.ruta_rep()["ruta"], r)
        self.assertEqual(self.ruta_rep()["ruta"]["origen"], {"lat": LEJOS[0], "lon": LEJOS[1]})  # sin restaurante ni GPS: desde la primera parada
        for pid in (lejos, cerca):
            self.sv.actualizar(pid, {"estado": "entregado"}, tipo="pedido")
        self.assertEqual([p["lat"] for p in self.ruta_rep()["ruta"]["paradas"]], [None])

    def test_ruta_cambia_al_moverse_y_el_cliente_ve_su_parada(self):
        self.aceptar()
        lejos, cerca = self.camino(CID, LEJOS), self.camino(cid_tel(TEL_B), CERCA)
        ca, cb = self.entrar()[2], self.entrar(tel=TEL_B, nombre="Luis")[2]
        cli = lambda ck: self.js("GET", "/pedidos", cookie=ck)[1]["pedidos"][0]
        with unittest.mock.patch.dict(os.environ, REST):
            self.assertEqual(self.orden(), [(cerca, 1), (lejos, 2)])
            r = self.ruta_rep()["ruta"]
            a, b = cli(ca), cli(cb)
            self.assertEqual((a["parada"], a["eta_min"], a["mensaje"], a["repartidor_pos"]), (2, r["paradas"][1]["eta_min"], ubicacion.REP_OTRA_PARADA, None))
            self.assertEqual((b["parada"], b["eta_min"], b["mensaje"]), (1, r["paradas"][0]["eta_min"], None))
            self.assertNotIn("7.97", str(a))  # ni la ruta ni las coordenadas del otro cliente
            self.assertNotIn("ruta", a)
            self.assertEqual(self.enviar({"lat": 8.001, "lon": -80.43, "precision": 10})[0], 200)  # el repartidor está junto al lejano
            self.assertEqual(self.orden(), [(lejos, 1), (cerca, 2)])
            self.assertEqual(self.ruta_rep()["ruta"]["origen"], {"lat": 8.001, "lon": -80.43})
            self.assertEqual([f["pedido_id"] for f in self.mapa()], [lejos, cerca])
            a, b = cli(ca), cli(cb)
            self.assertEqual((a["parada"], a["mensaje"], a["repartidor_pos"]["lat"]), (1, None, 8.001))
            self.assertEqual((b["parada"], b["mensaje"], b["repartidor_pos"]), (2, ubicacion.REP_OTRA_PARADA, None))
            with unittest.mock.patch.object(ubicacion, "REP_VIEJO_S", -1):  # GPS viejo: vuelve a contar desde el restaurante
                self.assertEqual(self.orden(), [(cerca, 1), (lejos, 2)])


if __name__ == "__main__":
    unittest.main()
