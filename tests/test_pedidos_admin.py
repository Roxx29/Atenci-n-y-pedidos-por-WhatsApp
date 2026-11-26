"""Editar pedidos, exportar CSV y aviso sin repetir. Ejecutar: pytest tests/test_pedidos_admin.py"""
import csv
import io
import os
import unittest
import unittest.mock

from motor import factura, panel_web
from tests import test_panel_web as tp
from tests.test_web import Base


class Admin(Base):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": tp.PIN, "PANEL_PIN_PERSONAL": "", "PANEL_PIN_HASH": ""})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    login, pedido = tp.PanelWeb.login, tp.PanelWeb.pedido

    def ped(self, pid):
        return next(p for p in self.sv.listar("pedido") if p["id"] == pid)

    def test_aviso_no_se_repite_en_cada_sondeo(self):  # regresión: los avisos de pedidos pendientes volvían en cada sondeo (cada 3 s) y sonaban otra vez
        self.pedido()  # confirmado = esperando al restaurante
        e = panel_web.estado_panel(self.sv, 0)
        self.assertEqual(len(e["avisos"]), 1)
        self.assertEqual(e["avisos"][0]["tipo"], "pago")
        self.assertEqual(panel_web.estado_panel(self.sv, e["ultimo"])["avisos"], [])
        self.sv.insertar("aviso", {"texto": "otro", "telefono": "x"})
        self.assertEqual([a["texto"] for a in panel_web.estado_panel(self.sv, e["ultimo"])["avisos"]], ["otro"])

    def test_editar_recalcula_con_precios_del_servidor_y_audita(self):
        pid = self.pedido()
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid, "notas": "sin sal", "items": [{"id": "sancocho", "cantidad": 3, "precio_c": 1, "precio": 0.01}, {"id": "chicha", "cantidad": 1}]}, cookie=ck)
        self.assertEqual(st, 200)
        p = self.ped(pid)
        self.assertEqual((p["subtotal_c"], p["notas"], p["editado_por"]), (3 * 500 + 175, "sin sal", "Dueno"))  # sancocho 5.00, chicha 1.75: del menú
        self.assertEqual(p["total_c"], p["subtotal_c"] + p["envio_c"])
        self.assertTrue(any(a["accion"].startswith("pedido_editar:items+notas") and a["pedido_id"] == pid for a in self.sv.listar("auditoria")))
        self.assertEqual(self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid, "items": [{"id": "no-existe", "cantidad": 1}]}, cookie=ck)[0], 409)

    def test_editar_estado_y_cerrado(self):
        pid = self.pedido()
        _, ck = self.login()
        self.assertEqual(self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid, "estado": "en_preparacion"}, cookie=ck)[0], 409)  # mismas reglas que marcar
        self.assertEqual(self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid, "estado": "pagado"}, cookie=ck)[0], 200)
        self.assertEqual(self.ped(pid)["estado"], "pagado")
        for e in ("en_preparacion", "listo", "entregado"):
            panel_web.marcar(self.sv, pid, e, "x")
        self.assertEqual(self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid, "notas": "tarde"}, cookie=ck)[0], 409)  # entregado: no se edita
        _, ck2 = self.login()
        self.assertEqual(self.js("POST", "/panel/dueno/pedido_editar", {"pedido_id": pid}, cookie=ck2)[0], 409)  # nada que cambiar

    def test_exportar_csv(self):
        pid = self.pedido()
        _, ck = self.login()
        r = lambda tipo, **k: self.js("POST", "/panel/dueno/exportar", {"tipo": tipo, "desde": "2026-11-01", "hasta": "2026-11-30", **k}, cookie=ck)
        st, d, _ = r("pedidos")
        filas = list(csv.reader(io.StringIO(d["csv"])))
        self.assertEqual((st, len(filas)), (200, 2))
        h = dict(zip(filas[0], filas[1]))
        self.assertEqual((h["pedido_id"], h["total"], h["items"]), (str(pid), self.js("GET", f"/panel/pedido/{pid}", cookie=ck)[1]["pedido"]["total"][4:], "2 x Sancocho"))
        self.assertNotIn("telefono", filas[0])
        f = list(csv.reader(io.StringIO(r("facturas")[1]["csv"])))
        self.assertEqual((len(f), f[1][0], f[1][3]), (2, str(pid), "consumidor_final"))
        self.assertEqual(len(list(csv.reader(io.StringIO(r("pedidos", desde="2026-12-01", hasta="2026-12-02")[1]["csv"])))), 1)  # fuera del rango: solo encabezado
        self.assertEqual(r("pedidos", desde="2026-11-30", hasta="2026-11-01")[0], 409)
        self.assertEqual(r("pedidos", desde="2024-01-01")[0], 409)  # rango > 366 días
        self.assertEqual(r("x")[0], 409)
        self.assertTrue(any(a["accion"].startswith("exportar_pedidos") for a in self.sv.listar("auditoria")))

    def test_pedido_viejo_con_itbms_no_rompe(self):
        pid = self.pedido()
        p = dict(self.ped(pid), impuesto_pct=7, itbms_c=70, total_c=self.ped(pid)["total_c"] + 70)  # como los guardados antes de quitar el ITBMS
        r = factura.resumen(self.sv, p)
        self.assertTrue(r["coincide"])
        self.assertNotIn("itbms", r["totales"])
