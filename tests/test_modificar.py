"""modificar_pedido y el tope de un pedido activo por cliente."""
import json
import threading

from motor import herramientas, panel
from tests.test_motor import Pedidos

ITEMS_2 = [{"id": "sancocho", "cantidad": 2}, {"id": "chicha", "cantidad": 2}]


class Modificar(Pedidos):
    def m(self, sv, pid, cliente="A", turno=None, **kw):
        return herramientas.ejecutar(sv, "modificar_pedido", {"pedido_id": pid, **kw}, cliente, turno)

    def pedido(self, sv, pid):
        return next(p for p in sv.listar("pedido") if p["id"] == pid)

    def en_estado(self, sv, estado, **kw):
        pid = self.crear(sv, **kw)["pedido_id"]
        herramientas.confirmar_pedido(sv, pid)
        if estado != "confirmado":
            sv.actualizar(pid, {"estado": estado}, tipo="pedido")
        return pid

    def test_direccion_tras_confirmar_no_crea_pedido(self):
        sv = self.sv()
        pid = self.en_estado(sv, "confirmado")
        total = self.pedido(sv, pid)["total_c"]
        r = self.m(sv, pid, direccion="Calle 10, Chitré")
        self.assertEqual((r["ok"], r["estado"], r["reconfirmar"]), (True, "confirmado", False))
        self.assertEqual(len(sv.listar("pedido")), 1)
        p = self.pedido(sv, pid)
        self.assertEqual((p["estado"], p["direccion"], p["total_c"]), ("confirmado", "Calle 10, Chitré", total))
        self.assertIn(f"Pedido #{pid}: el cliente cambió la dirección a Calle 10, Chitré", sv.listar("aviso")[-1]["texto"])
        self.assertIn("Calle 10", panel.avisos_nuevos(sv, 0)[0][-1]["texto"])
        self.assertIn("Calle 10", panel.entrega(p))

    def test_items_en_confirmado_vuelve_a_por_confirmar_y_no_se_confirma_en_el_turno(self):
        sv = self.sv()
        pid = self.en_estado(sv, "confirmado")
        turno = {"texto": "sí", "creados": set()}
        r = self.m(sv, pid, turno=turno, items=[{"id": "chicha", "cantidad": 5}])
        self.assertEqual((r["estado"], r["reconfirmar"], r["total"]), ("por_confirmar", True, "B/. 11.86"))
        self.assertEqual(self.pedido(sv, pid)["total_c"], 1186)  # 875 + ITBMS 61 + envío 250
        with self.assertRaisesRegex(ValueError, "mensaje nuevo"):
            herramientas.ejecutar(sv, "confirmar_pedido", {"pedido_id": pid}, "A", turno)
        herramientas.ejecutar(sv, "confirmar_pedido", {"pedido_id": pid}, "A", {"texto": "sí", "creados": set()})  # en un turno posterior sí
        self.assertEqual(self.pedido(sv, pid)["estado"], "confirmado")

    def test_por_confirmar_se_modifica_y_notas(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        r = self.m(sv, pid, notas="sin cebolla", modalidad="recogida")
        self.assertEqual(r["estado"], "por_confirmar")
        self.assertIn("Notas: sin cebolla", r["resumen"])
        self.assertEqual(self.pedido(sv, pid)["notas"], "sin cebolla")
        self.assertEqual(sv.listar("aviso"), [])

    def test_pagado_cambio_de_zona_o_items_escala(self):
        for estado in ("pago_reportado", "pagado", "en_preparacion"):
            for cambio in ({"modalidad": "recogida"}, {"items": [{"id": "chicha", "cantidad": 1}]}):
                with self.subTest(estado=estado, cambio=cambio):
                    sv = self.sv()
                    pid = self.en_estado(sv, estado)
                    antes = self.pedido(sv, pid)
                    r = self.m(sv, pid, **cambio)
                    self.assertEqual((r["ok"], r["escalado"]), (False, True))
                    self.assertEqual(self.pedido(sv, pid), antes)
                    [e] = sv.listar("escalado")
                    self.assertEqual((e["telefono"], e["estado"]), ("A", "abierto"))
                    self.assertIn(f"pedido #{pid}", e["motivo"])
                    self.assertIn(f"#{pid}", sv.listar("aviso")[-1]["texto"])

    def test_direccion_en_pago_reportado_se_aplica_con_aviso(self):
        sv = self.sv()
        pid = self.en_estado(sv, "pago_reportado")
        r = self.m(sv, pid, direccion="Otra calle", notas="timbre roto")
        self.assertEqual((r["ok"], r["estado"]), (True, "pago_reportado"))
        self.assertEqual(self.pedido(sv, pid)["direccion"], "Otra calle")
        self.assertIn("la dirección a Otra calle", sv.listar("aviso")[-1]["texto"])
        self.assertEqual(sv.listar("escalado"), [])

    def test_dos_activos_imposibles_y_aparte(self):
        sv = self.sv()
        pid = self.en_estado(sv, "confirmado")
        with self.assertRaisesRegex(ValueError, rf"#{pid}.*confirmado.*modificar_pedido.*aparte=true"):
            self.crear(sv)
        r2 = self.crear(sv, aparte=True)
        self.assertNotEqual(r2["pedido_id"], pid)
        with self.assertRaisesRegex(ValueError, f"Confirme o cancele primero el pedido #{r2['pedido_id']}"):
            self.crear(sv, aparte=True)
        self.assertEqual(self.crear(sv)["pedido_id"], r2["pedido_id"])  # sin aparte, el por_confirmar se actualiza
        herramientas.confirmar_pedido(sv, r2["pedido_id"])
        r3 = self.crear(sv, aparte=True)
        herramientas.confirmar_pedido(sv, r3["pedido_id"])
        with self.assertRaisesRegex(ValueError, "3 pedidos"):
            self.crear(sv, aparte=True)
        self.crear(sv, "B")  # otro cliente no cuenta

    def test_estados_finales_no_se_modifican(self):
        for estado in ("listo", "entregado", "cancelado", "caducado"):
            with self.subTest(estado=estado):
                sv = self.sv()
                pid = self.en_estado(sv, estado)
                with self.assertRaisesRegex(ValueError, "ya no se puede modificar"):
                    self.m(sv, pid, direccion="X")

    def test_otro_cliente_no_puede_modificar(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        with self.assertRaisesRegex(ValueError, "este cliente"):
            self.m(sv, pid, cliente="B", direccion="X")

    def test_modelo_no_ve_centavos(self):
        sv = self.sv()
        pid = self.en_estado(sv, "confirmado")
        for r in (self.m(sv, pid, direccion="Z"), self.m(sv, pid, items=[{"id": "chicha", "cantidad": 6}])):
            self.assertNotRegex(json.dumps(r), r"_c\"")  # ningún campo *_c

    def test_recogida_con_direccion_y_hora_cerrada_fallan(self):
        sv = self.sv()
        pid = self.crear(sv, modalidad="recogida")["pedido_id"]
        with self.assertRaisesRegex(ValueError, "recogida"):
            self.m(sv, pid, direccion="Calle 1")
        with self.assertRaisesRegex(ValueError, "cerrado"):
            self.m(sv, pid, hora_deseada="2026-11-02 23:00")
        self.assertEqual(self.pedido(sv, pid)["hora_deseada"], "")

    def test_concurrencia_ultimo_gana_con_totales_coherentes(self):
        sv = self.sv()
        pid = self.en_estado(sv, "confirmado")
        errores = []

        def go(n):
            try:
                self.m(sv, pid, items=[{"id": "sancocho", "cantidad": n}])
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        hilos = [threading.Thread(target=go, args=(n,)) for n in (3, 4)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        p = self.pedido(sv, pid)
        n = p["lineas"][0]["cantidad"]
        self.assertIn(n, (3, 4))
        self.assertEqual(p["subtotal_c"], 500 * n)
        self.assertEqual(p["total_c"], p["subtotal_c"] + p["itbms_c"] + p["envio_c"])
        self.assertEqual(len(sv.listar("pedido")), 1)
        self.assertEqual(errores, [])
