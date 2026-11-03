"""Panel web /panel. Ejecutar: python -m unittest tests.test_panel_web"""
import os
import unittest
import unittest.mock

from motor import herramientas, panel_web
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
        self.assertEqual((p["estado"], p["pagado_por"]), ("pagado", "dueno"))
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


if __name__ == "__main__":
    unittest.main()
