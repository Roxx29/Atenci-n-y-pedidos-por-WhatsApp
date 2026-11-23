"""Resumen para factura. Ejecutar: python -m unittest tests.test_factura"""
import csv
import io
import unittest

from motor import factura, herramientas
import os
import unittest.mock

from tests import test_panel_web as tp
from tests.test_web import PEDIDO, TEL, Base


class Resumen(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": tp.PIN, "PANEL_PIN_PERSONAL": "", "PANEL_PIN_HASH": ""})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    login, pedido = tp.PanelWeb.login, tp.PanelWeb.pedido

    def pedido_con(self, items, modalidad="recogida", **k):
        r = herramientas.crear_pedido(self.sv, items=items, modalidad=modalidad, nombre="Ana", telefono=TEL, **k)
        herramientas.confirmar_pedido(self.sv, r["pedido_id"])
        return next(p for p in self.sv.listar("pedido") if p["id"] == r["pedido_id"])

    def test_totales_en_centavos_y_coincide(self):
        p = self.pedido_con([{"id": "sancocho", "cantidad": 2}, {"id": "chicha", "cantidad": 3}])  # 10.00 + 5.25 = 15.25; ITBMS 7 % = 1.0675 -> 1.07
        r = factura.resumen(self.sv, p)
        self.assertEqual((r["totales"]["subtotal"], r["totales"]["itbms"], r["totales"]["total"]), ("15.25", "1.07", "16.32"))
        self.assertEqual(sum(int(l["itbms"].replace(".", "")) for l in r["lineas"]), 107)  # las líneas suman el ITBMS del pedido, sin centavos perdidos
        self.assertTrue(r["coincide"])
        self.assertEqual(r["documento"]["modalidad"], "recoger")
        self.assertEqual(r["receptor"]["tipo"], "consumidor_final")
        self.assertEqual(r["faltan"], ["razon_social", "ruc", "dv", "sucursal"])

    def test_envio_aparte(self):
        p = self.pedido_con([{"id": "sancocho", "cantidad": 2}], "entrega", direccion="Calle 1", zona="x")
        r = factura.resumen(self.sv, p)
        self.assertEqual((r["envio"]["total"], r["envio"]["itbms"], r["envio"]["base"]), ("2.50", "0.16", "2.34"))  # 2.50 con ITBMS incluido
        self.assertTrue(r["coincide"])
        self.assertEqual(r["totales"]["total"], "13.20")

    def test_no_coincide_no_inventa(self):
        p = self.pedido_con([{"id": "sancocho", "cantidad": 2}])
        p["total_c"] += 1
        self.assertFalse(factura.resumen(self.sv, p)["coincide"])

    def test_csv(self):
        self.sv.poner_ajuste("razon_social", "=CMD() \"Fonda\", S.A.")
        p = self.pedido_con([{"id": "sancocho", "cantidad": 2, "nota": "sin sal, \"poco\""}])
        b = factura.to_csv(factura.resumen(self.sv, p, {"tipo": "contribuyente", "ruc": "8-123-45", "dv": "12", "nombre": "+Peña @x"}))
        self.assertTrue(b.startswith(b"\xef\xbb\xbf"))
        filas = list(csv.reader(io.StringIO(b.decode("utf-8-sig"))))
        self.assertIn(["emisor_razon_social", "'=CMD() \"Fonda\", S.A."], filas)
        self.assertIn(["receptor_nombre", "'+Peña @x"], filas)
        self.assertTrue(any(f and f[0].startswith("Sancocho (sin sal, \"poco\")") for f in filas))

    def test_html_escapado(self):
        p = self.pedido_con([{"id": "sancocho", "cantidad": 2}])
        h = factura.to_html(factura.resumen(self.sv, p, {"tipo": "contribuyente", "ruc": "1-1", "nombre": "<script>alert(1)</script>"})).decode()
        self.assertNotIn("<script", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertIn("no es una factura fiscal", h)

    def test_rutas(self):
        pid = self.pedido()
        self.assertEqual(self.js("GET", f"/panel/factura?pedido={pid}")[0], 401)  # sin sesión
        _, ck = self.login()
        st, d, _ = self.js("GET", f"/panel/factura?pedido={pid}", cookie=ck)
        self.assertEqual((st, d["resumen"]["coincide"]), (200, True))
        self.assertEqual(self.js("GET", "/panel/factura?pedido=999", cookie=ck)[0], 404)
        self.assertEqual(self.js("POST", "/panel/factura", {"pedido_id": 999}, cookie=ck)[0], 404)
        r, raw = self.req("GET", f"/panel/factura?pedido={pid}&formato=csv", cookie=ck)
        self.assertEqual((r.status, r.getheader("Content-Type")[:8]), (200, "text/csv"))
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        r, raw = self.req("GET", f"/panel/factura?pedido={pid}&formato=html&tipo=contribuyente&ruc=1-2&nombre=%3Cb%3E", cookie=ck)
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertIn(b"&lt;b&gt;", raw)
        st, d, _ = self.js("POST", "/panel/factura", {"pedido_id": pid, "receptor": {"tipo": "contribuyente", "ruc": "8-123-45", "dv": "7", "nombre": "Ana"}}, cookie=ck)
        self.assertEqual((st, d["resumen"]["receptor"]["ruc"]), (200, "8-123-45"))
        for malo in ({"tipo": "contribuyente", "ruc": "<x>"}, {"tipo": "contribuyente", "ruc": "1" * 21}, {"tipo": "contribuyente", "ruc": "1-1", "nombre": "x" * 81}, {"tipo": "otro"}):
            self.assertEqual(self.js("POST", "/panel/factura", {"pedido_id": pid, "receptor": malo}, cookie=ck)[0], 400, malo)
        self.assertEqual(self.sv.listar("auditoria")[-1]["accion"], "factura")
        self.assertNotIn("Ana", str(self.sv.listar("auditoria")[-1]))

    def test_cancelado_409_y_ajustes(self):
        pid = self.pedido()
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/ajustes", {"factura": {"razon_social": "Fonda SA", "ruc": "8-1-2", "dv": "55", "sucursal": "0001"}}, cookie=ck)
        self.assertEqual(st, 200)
        self.assertEqual(d["ajustes"]["factura"]["ruc"], "8-1-2")
        self.assertEqual(self.js("GET", f"/panel/factura?pedido={pid}", cookie=ck)[1]["resumen"]["faltan"], [])
        for malo in ({"ruc": "a b"}, {"dv": "abc"}, {"razon_social": "x" * 81}, {"otro": "x"}):
            self.assertEqual(self.js("POST", "/panel/dueno/ajustes", {"factura": malo}, cookie=ck)[0], 409, malo)
        herramientas.cancelar_pedido(self.sv, pid)
        self.assertEqual(self.js("GET", f"/panel/factura?pedido={pid}", cookie=ck)[0], 409)


if __name__ == "__main__":
    unittest.main()
