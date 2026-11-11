"""Panel web /panel. Ejecutar: python -m unittest tests.test_panel_web"""
import os
import threading
import unittest
import unittest.mock

from motor import herramientas, panel_web, web
from motor.nucleo import Servicio
from tests.test_web import PEDIDO, TEL, Base

PIN = "483920"


class PanelWeb(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN, "PANEL_PIN_PERSONAL": "", "PANEL_PIN_HASH": ""})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    def login(self, pin=PIN):
        st, d, r = self.js("POST", "/panel/entrar", {"pin": pin})
        return st, (r.getheader("Set-Cookie") or "").split(";")[0]

    def pedido(self, hasta="confirmado"):
        p = herramientas.crear_pedido(self.sv, telefono=TEL, **PEDIDO)
        pid = p.get("pedido_id") or p["pedido"]["id"]
        if hasta != "por_confirmar":
            herramientas.confirmar_pedido(self.sv, pid)
        return pid

    def test_sin_pin_deshabilitado(self):
        with unittest.mock.patch.dict(os.environ, {"PANEL_PIN": ""}):
            self.arrancar()
        self.assertEqual(self.req("GET", "/panel")[0].status, 404)

    def test_pin_debil_ignorado(self):
        with unittest.mock.patch.dict(os.environ, {"PANEL_PIN": "123456"}):
            self.assertFalse(panel_web.Acceso("x").activo)

    def test_login_estado_y_cookie_distinta(self):
        self.assertEqual(self.js("GET", "/panel/estado")[0], 401)
        st, ck = self.login()
        self.assertEqual(st, 200)
        self.assertTrue(ck.startswith("panel="))
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[0], 200)
        cli = self.entrar()[2]  # la cookie del cliente no abre el panel
        self.assertEqual(self.js("GET", "/panel/estado", cookie=cli)[0], 401)

    def test_bloqueo_tras_5_fallos(self):
        for _ in range(5):
            self.assertEqual(self.login("000111")[0], 401)
        self.assertEqual(self.login(PIN)[0], 429)

    def test_hash(self):
        with unittest.mock.patch.dict(os.environ, {"PANEL_PIN": "", "PANEL_PIN_HASH": panel_web.hash_pin("918273")}):
            a = panel_web.Acceso("x")
        self.assertEqual(a.rol_de_pin("918273"), "dueno")
        self.assertIsNone(a.rol_de_pin(PIN))

    def test_lista_enmascara_y_detalle_completo(self):
        pid = self.pedido()
        _, ck = self.login()
        lista = self.js("GET", "/panel/estado", cookie=ck)[1]["pedidos"][0]
        self.assertNotIn(TEL, str(lista))
        self.assertNotIn("direccion", lista)
        det = self.js("GET", f"/panel/pedido/{pid}", cookie=ck)[1]["pedido"]
        self.assertEqual(det["telefono"], TEL)
        self.assertEqual(det["total"][:4], "B/. ")

    def test_marcar_reglas_y_registro(self):
        pid = self.pedido()
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/marcar", {"pedido_id": pid, "estado": "en_preparacion"}, cookie=ck)
        self.assertEqual(st, 409)  # el pago va antes
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": pid, "estado": "pagado"}, cookie=ck)[0], 200)
        p = next(p for p in self.sv.listar("pedido") if p["id"] == pid)
        self.assertEqual((p["estado"], p["pagado_por"]), ("pagado", "Dueno"))  # nombre de la persona
        for e in ("en_preparacion", "listo", "entregado"):
            self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": pid, "estado": e}, cookie=ck)[0], 200)
        acc = [a["accion"] for a in self.sv.listar("auditoria")]
        self.assertIn("pagado", acc)
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": "x", "estado": "pagado"}, cookie=ck)[0], 400)

    def test_cancelar(self):
        pid = self.pedido()
        _, ck = self.login()
        self.assertEqual(self.js("POST", "/panel/marcar", {"pedido_id": pid, "estado": "cancelar"}, cookie=ck)[0], 200)
        self.assertEqual(next(p for p in self.sv.listar("pedido") if p["id"] == pid)["estado"], "cancelado")

    def test_comprobante_sin_archivo_y_salir(self):
        pid = self.pedido()
        _, ck = self.login()
        self.assertEqual(self.req("GET", f"/panel/comprobante/{pid}", cookie=ck)[0].status, 404)
        self.assertEqual(self.js("POST", "/panel/salir", {}, cookie=ck)[0], 200)

    def test_escalado(self):
        eid = self.sv.insertar("escalado", {"telefono": TEL, "motivo": "queja", "estado": "abierto"})
        _, ck = self.login()
        e = self.js("GET", "/panel/estado", cookie=ck)[1]["escalados"]
        self.assertEqual(e[0]["motivo"], "queja")
        self.assertNotIn(TEL, str(e))
        self.assertEqual(self.js("POST", f"/panel/escalado/{eid}/atendido", {}, cookie=ck)[0], 200)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[1]["escalados"], [])

    def test_dueno_gestiona_y_personal_403(self):
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/usuario", {"nombre": "Pedro", "rol": "repartidor", "pin": "739104"}, cookie=ck)
        self.assertEqual((st, d["usuario"]), (200, {"nombre": "Pedro", "rol": "repartidor", "activo": True}))
        self.assertEqual(self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "739104"}, cookie=ck)[0], 409)  # PIN repetido
        self.assertEqual(self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)[0], 200)
        self.assertNotIn("pbkdf2", str(self.js("GET", "/panel/dueno/usuarios", cookie=ck)[1]))
        st, d, _ = self.js("POST", "/repartidor/entrar", {"pin": "739104"})
        self.assertEqual((st, d["nombre"]), (200, "Pedro"))
        self.assertIn("Pedro", self.js("GET", "/panel/estado", cookie=ck)[1]["repartidores"])
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"id": "sancocho", "precio": 6, "disponible": False}, cookie=ck)[0], 200)
        m = next(m for m in self.js("GET", "/menu")[1] if m["id"] == "sancocho")
        self.assertEqual((m["precio"], m["disponible"]), (6, False))
        st, ck2 = self.login("918273")
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck2)[0], 200)
        for m, r, cuerpo in (("GET", "/panel/dueno/usuarios", None), ("POST", "/panel/dueno/plato", {"id": "sancocho", "precio": 0})):
            self.assertEqual(self.js(m, r, cuerpo, cookie=ck2)[0], 403)
        self.assertEqual(self.js("GET", "/panel/dueno/nada", cookie=ck)[0], 404)
        self.assertIn(("Ana", "login"), [(a["usuario"], a["accion"]) for a in self.sv.listar("auditoria")])

    def test_menu_en_fila_agotado_del_personal_y_borrar(self):
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/plato", {"nombre": "Arroz con Pollo", "precio": "4.50", "categoria": "platos"}, cookie=ck)  # sin id: sale del nombre
        self.assertEqual((st, d["plato"]["id"], d["plato"]["precio"]), (200, "arroz-con-pollo", 4.5))
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"nombre": "Arroz con pollo", "precio": 5}, cookie=ck)[1]["plato"]["id"], "arroz-con-pollo-2")  # no pisa al otro
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"precio": 5}, cookie=ck)[0], 409)  # sin id ni nombre
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        self.assertIn("sancocho", [m["id"] for m in self.js("GET", "/panel/menu", cookie=ck2)[1]["menu"]])  # el personal ve el menú
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "sancocho", "disponible": False}, cookie=ck2)[0], 200)  # y marca lo agotado
        self.assertFalse(next(m for m in self.js("GET", "/menu")[1] if m["id"] == "sancocho")["disponible"])
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "nada", "disponible": False}, cookie=ck2)[0], 404)
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "sancocho", "disponible": "no"}, cookie=ck2)[0], 400)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "sancocho"}, cookie=ck2)[0], 403)  # borrar es del dueño
        self.assertEqual(self.js("GET", "/panel/menu")[0], 401)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "arroz-con-pollo-2"}, cookie=ck)[0], 200)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "arroz-con-pollo-2"}, cookie=ck)[0], 404)
        self.assertNotIn("arroz-con-pollo-2", [m["id"] for m in self.js("GET", "/menu")[1]])
        self.assertIn("plato_borrar:arroz-con-pollo-2", [a["accion"] for a in self.sv.listar("auditoria")])

    def test_borrar_persona(self):
        _, ck = self.login()
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Ana"}, cookie=ck)[0], 200)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck2)[0], 401)  # su sesión deja de valer
        self.assertEqual(self.login("918273")[0], 401)
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Ana"}, cookie=ck)[0], 404)
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Dueno"}, cookie=ck)[0], 409)  # queda al menos un dueño
        self.assertIn(("Dueno", "usuario_borrar:Ana"), [(a["usuario"], a["accion"]) for a in self.sv.listar("auditoria")])

    def test_paginas_con_marca_y_tema(self):
        self.sv.cfg["marca"] = "Café <Luna>"
        panel_web.guardar_usuario(self.sv, {"nombre": "Juan", "rol": "repartidor", "pin": "739104"}, "prueba")  # sin repartidores /repartidor no existe
        srv = web.crear_servidor(self.sv, 0, "secreto-de-prueba")  # las páginas se arman al crear el servidor
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        self.puerto = srv.server_address[1]
        for ruta in ("/", "/panel", "/repartidor"):
            r, raw = self.req("GET", ruta)
            h = raw.decode()
            self.assertEqual(r.status, 200, ruta)
            self.assertIn("Café &lt;Luna&gt;", h)  # nombre de servicio.yaml, escapado
            self.assertNotIn("DeliFood", h)
            self.assertIn("--primary:", h)  # tema incrustado
            self.assertNotIn("estatico/tema.css", h)

    def test_config_publica_y_pago_solo_con_sesion(self):
        st, d, _ = self.js("GET", "/config")
        self.assertEqual((st, d["marca"], d["impuesto_pct"]), (200, "DeliFood", self.sv.cfg["pedidos"]["impuesto_pct"]))
        self.assertEqual(d["envio"], {"": 2.5})  # costo fijo de la configuración de prueba
        self.assertEqual(web.config_publica(Servicio("servicios/restaurante-pedidos", db=":memory:"))["envio"], {"Chitré": 2.0, "La Villa de Los Santos": 3.0})
        self.assertNotIn("pago", d)
        self.assertEqual(self.js("GET", "/config", cookie=self.entrar()[2])[1]["pago"]["yappy"], self.sv.cfg["pago"]["yappy"])


if __name__ == "__main__":
    unittest.main()
