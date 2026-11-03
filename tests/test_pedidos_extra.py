"""Casos extra de pedidos que test_motor.py no cubre. Ejecutar: python -m unittest tests.test_pedidos_extra"""
import contextlib
import datetime as dt
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from motor import __main__ as cli
from motor import herramientas, panel
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso, Pedidos, herramienta, texto

RAIZ = Path(__file__).resolve().parent.parent
SANC = {"id": "sancocho", "cantidad": 2}


class Base(unittest.TestCase):
    crear = Pedidos.crear

    def sv(self, menu=MENU, cfg=None, ahora=(2026, 11, 2, 12, 0), respuestas=()):
        sv = Pedidos.sv(self, menu)
        sv._cliente = ClienteFalso(respuestas)
        if cfg is not None:
            sv.cfg["pedidos"] = cfg
        sv.ahora = lambda: dt.datetime(*ahora, tzinfo=sv.tz)
        return sv

    def falla(self, sv, regex=None, tipo=ValueError, **kw):
        with self.assertRaisesRegex(tipo, regex or ""):
            self.crear(sv, **kw)
        self.assertEqual(sv.listar("pedido"), [], "no debe quedar pedido guardado")

    def e(self, sv, n, x, c="A"):
        return herramientas.ejecutar(sv, n, x, c)


class Cantidades(Base):
    def test_cantidades_invalidas(self):
        sv = self.sv()
        for c in (0, -1, 1.5, 2.0, "2", True, None):
            self.falla(sv, "Cantidad", items=[{"id": "sancocho", "cantidad": c}])
        self.falla(sv, "Cantidad", items=[{"id": "sancocho"}])

    def test_items_vacios_y_mismo_id_repetido(self):
        sv = self.sv()
        self.falla(sv, "ítems", items=[])
        r = self.crear(sv, items=[SANC, SANC])
        self.assertEqual(r["total_c"], 2000 + 140 + 250)
        self.assertEqual(len(sv.listar("pedido")[0]["lineas"]), 2)

    def test_id_no_disponible_aunque_otra_linea_sea_valida(self):
        sv = self.sv()
        self.falla(sv, "no está disponible", items=[SANC, {"id": "tres-leches", "cantidad": 1}])

    def test_entradas_malformadas_del_modelo(self):
        """El bucle solo atrapa ValueError/TypeError: cualquier otra excepcion tumba responder()."""
        sv = self.sv()
        for kw in ({"items": ["sancocho"]}, {"items": "sancocho"}, {"items": [None]}, {"direccion": None}, {"items": [{"id": ["x"], "cantidad": 1}]},
                   {"items": {"id": "sancocho", "cantidad": 2}}):
            with self.subTest(kw=kw):
                try:
                    self.crear(sv, **kw)
                except (ValueError, TypeError):
                    pass  # controlado por el bucle
                except Exception as e:  # noqa
                    self.fail(f"{type(e).__name__}: {e} escaparia del bucle (solo se atrapa ValueError/TypeError)")


class Topes(Base):
    def test_topes_de_cantidad_lineas_y_texto(self):
        sv = self.sv()
        self.falla(sv, "Cantidad", items=[{"id": "sancocho", "cantidad": 51}])
        self.assertTrue(self.crear(sv, items=[{"id": "sancocho", "cantidad": 50}])["ok"])
        self.falla(self.sv(), "líneas", items=[SANC] * 31)
        self.assertTrue(self.crear(self.sv(), items=[SANC] * 30)["ok"])
        sv = self.sv()
        for campo in ("nombre", "direccion", "zona", "hora_deseada"):
            with self.subTest(campo=campo):
                self.falla(sv, "largo", **{campo: "x" * 201})
        self.falla(sv, "largo", items=[{**SANC, "nota": "x" * 201}])
        pid = self.crear(sv, nombre="x" * 200)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        with self.assertRaisesRegex(ValueError, "largo"):
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "y" * 201, "referencia": "1"})
        with self.assertRaisesRegex(ValueError, "largo"):
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "y", "referencia": "1" * 201})

    def test_un_solo_pedido_en_construccion_se_actualiza(self):
        sv = self.sv()
        r1 = self.crear(sv)
        self.assertFalse(r1["actualizado"])
        r2 = self.crear(sv, items=[{"id": "chicha", "cantidad": 5}], modalidad="recogida")
        self.assertEqual((r2["pedido_id"], r2["actualizado"]), (r1["pedido_id"], True))
        [p] = sv.listar("pedido")
        self.assertEqual(([l["id"] for l in p["lineas"]], p["total_c"], p["modalidad"]), (["chicha"], 875 + 61, "recogida"))  # recalculado en código
        rb = self.crear(sv, "B")  # otro cliente no interfiere
        self.assertNotEqual(rb["pedido_id"], r1["pedido_id"])
        self.assertEqual(len(sv.listar("pedido")), 2)
        self.e(sv, "confirmar_pedido", {"pedido_id": r1["pedido_id"]})  # confirmado: no se toca y permite uno nuevo
        r3 = self.crear(sv)
        self.assertNotEqual(r3["pedido_id"], r1["pedido_id"])
        self.assertEqual(next(p for p in sv.listar("pedido") if p["id"] == r1["pedido_id"])["lineas"][0]["id"], "chicha")

    def test_caducidad(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 29, tzinfo=sv.tz)
        self.assertEqual(herramientas.caducar_pedidos(sv), [])
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 30, tzinfo=sv.tz)
        with self.assertRaisesRegex(ValueError, "caducó; cree uno nuevo"):
            self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        self.assertEqual(sv.listar("pedido")[0]["estado"], "caducado")
        with self.assertRaises(ValueError):
            herramientas.marcar_pedido(sv, pid, "pagado")
        self.assertEqual(panel.activos(sv), [])
        r = self.crear(sv)  # caducado permite crear uno nuevo (no lo actualiza)
        self.assertNotEqual((r["pedido_id"], r["actualizado"]), (pid, True))
        sv.cfg["pedidos"]["caduca_min"] = 5  # configurable
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 36, tzinfo=sv.tz)
        self.assertEqual(herramientas.caducar_pedidos(sv), [r["pedido_id"]])

    def test_caducar_no_toca_confirmados_ni_pedidos_viejos_sin_fecha(self):
        sv = self.sv()
        a, b = self.crear(sv, "A")["pedido_id"], self.crear(sv, "B")["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": a})
        sv.actualizar(b, {"creado_at": None}, tipo="pedido")
        sv.ahora = lambda: dt.datetime(2026, 11, 3, 12, 0, tzinfo=sv.tz)
        self.assertEqual(herramientas.caducar_pedidos(sv), [])

    def test_el_modelo_no_ve_centavos(self):
        sv = self.sv()
        antes = self.e(sv, "crear_pedido", {"items": [SANC, {"id": "chicha", "cantidad": 2}], "modalidad": "entrega", "nombre": "Luis", "direccion": "Calle 53"})  # sin el ayudante crear(): respuesta tal cual
        pid = antes["pedido_id"]
        with contextlib.redirect_stdout(io.StringIO()):
            resps = [antes, self.e(sv, "buscar", {"tipo": "pedido"}), self.e(sv, "confirmar_pedido", {"pedido_id": pid}),
                     self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "1"}), self.e(sv, "buscar", {"tipo": "pedido"})]
        def claves(x):
            return [k for k, v in x.items()] + [c for v in x.values() for c in claves(v)] if isinstance(x, dict) else [c for v in x for c in claves(v)] if isinstance(x, list) else []
        for r in resps:
            self.assertEqual([k for k in claves(r) if k.endswith("_c")], [], r)
            self.assertNotIn("1695", json.dumps(r))
        p = resps[1][0]
        self.assertEqual((p["total"], p["subtotal"], p["impuesto"], p["envio"]), ("B/. 16.95", "B/. 13.50", "B/. 0.95", "B/. 2.50"))
        self.assertEqual((p["lineas"][0]["precio"], p["lineas"][0]["importe"]), ("B/. 5.00", "B/. 10.00"))

    def test_datos_incompletos_no_crean_pedido(self):
        sv = self.sv()
        for nombre in ("", "  ", "L", "123456", "cliente 50760000001", "Cliente", "50760000001"):
            with self.subTest(nombre=nombre):
                with self.assertRaisesRegex(ValueError, "nombre"):
                    herramientas.crear_pedido(sv, [SANC], "recogida", nombre, "50760000001")
        with self.assertRaisesRegex(ValueError, "nombre"):
            herramientas.crear_pedido(sv, [SANC], "recogida", "Luis 50760000001", "50760000001")
        self.falla(sv, "Falta la dirección", direccion=" ")
        self.falla(sv, "Modalidad", modalidad="")
        sv.cfg["pedidos"] = {"envio": {"zonas": {"Chitré": 1}}}
        self.falla(sv, "Falta la zona", zona="")
        self.falla(sv, "ya pasó", hora_deseada="2026-11-02 11:00")
        self.assertTrue(herramientas.crear_pedido(sv, [SANC], "recogida", "Luis", "50760000001")["ok"])

    def test_avisos_sin_saltos_de_linea(self):
        sv = self.sv()
        pid = self.crear(sv, nombre="Luis\nGómez\r\nX")["pedido_id"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.e(sv, "confirmar_pedido", {"pedido_id": pid})
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "ya\nppy", "referencia": "1\n2"})
        for a in sv.listar("aviso"):
            self.assertNotRegex(a["texto"], "[\r\n]")


class Dinero(Base):
    def menu(self, precio):
        return f"- {{id: x, nombre: X, precio: {precio}}}\n"

    def test_redondeo_itbms_mitad_hacia_arriba(self):
        for precio, cant, pct, esperado in ((1.10, 1, 7, 8), (0.50, 1, 7, 4), (0.10, 3, 7, 2), (0.07, 1, 50, 4), (10, 1, 7.5, 75), (1.99, 1, 7, 14)):
            with self.subTest(precio=precio, pct=pct):
                sv = self.sv(self.menu(precio), cfg={"impuesto_pct": pct})
                self.crear(sv, items=[{"id": "x", "cantidad": cant}], modalidad="recogida")
                self.assertEqual(sv.listar("pedido")[-1]["itbms_c"], esperado)

    def test_precios_sin_flotantes(self):
        sv = self.sv(self.menu("1.10"), cfg={})
        self.assertEqual(self.crear(sv, items=[{"id": "x", "cantidad": 3}], modalidad="recogida")["total_c"], 330)
        sv = self.sv(self.menu(0.3), cfg={})
        self.assertEqual(self.crear(sv, items=[{"id": "x", "cantidad": 7}], modalidad="recogida")["total_c"], 210)
        with self.assertRaises(ValueError):  # 0.1+0.2 escrito como flotante largo en el yaml
            self.sv(self.menu(0.1 + 0.2))

    def test_impuesto_ausente_none_o_cero(self):
        for cfg in ({}, {"impuesto_pct": None}, {"impuesto_pct": 0}):
            sv = self.sv(cfg=cfg)
            r = self.crear(sv, modalidad="recogida")
            self.assertEqual(r["total_c"], 1350)
            self.assertNotIn("ITBMS", r["resumen"])

    def test_pedido_minimo_exacto_pasa(self):
        sv = self.sv(cfg={"pedido_minimo": 13.50})
        self.assertTrue(self.crear(sv)["ok"])
        sv = self.sv(cfg={"pedido_minimo": 13.51})
        self.falla(sv, "mínimo")

    def test_envio(self):
        sv = self.sv(cfg={"envio": {"zonas": {"Bella Vista": 1, "El Cangrejo": 2.5}}})
        self.assertEqual(self.crear(sv, zona="  BELLA vista ")["total_c"], 1350 + 100)
        self.assertEqual(self.crear(sv, zona="el cangrejo")["total_c"], 1600)
        self.assertEqual(self.crear(sv, modalidad="recogida", zona="Marte")["total_c"], 1350)  # recogida ignora zona
        self.assertEqual(sv.listar("pedido")[-1]["envio_c"], 0)
        with self.assertRaisesRegex(ValueError, "Zona sin entrega"):
            self.crear(sv, zona="Costa del Este")
        sv = self.sv(cfg={"envio": {"costo": 0}})  # envío gratis
        self.assertEqual(self.crear(sv)["total_c"], 1350)
        sv = self.sv(cfg={})  # sin bloque envío
        self.assertEqual(self.crear(sv)["total_c"], 1350)

    def test_zona_con_costo_invalido(self):
        sv = self.sv(cfg={"envio": {"zonas": {"A": 0.001}}})
        with self.assertRaises(ValueError):
            self.crear(sv, zona="A")


class Horario(Base):
    def test_limites(self):
        sv = self.sv(ahora=(2026, 11, 2, 8, 0))
        for h, ok in (("11:00", True), ("10:59", False), ("20:30", True), ("20:31", False), ("21:00", False)):
            with self.subTest(h=h):
                if ok:
                    self.assertTrue(self.crear(sv, hora_deseada=f"2026-11-02 {h}")["ok"])
                else:
                    with self.assertRaisesRegex(ValueError, "cerrado"):
                        self.crear(sv, hora_deseada=f"2026-11-02 {h}")

    def test_ahora_antes_de_abrir_sin_hora_deseada(self):
        self.falla(self.sv(ahora=(2026, 11, 2, 10, 0)), "cerrado")

    def test_hora_desde_dia_siguiente(self):
        sv = self.sv(ahora=(2026, 11, 2, 22, 0))
        self.assertTrue(self.crear(sv, hora_deseada="2026-11-03 12:00")["ok"])
        sv2 = self.sv(ahora=(2026, 11, 2, 22, 0))
        self.falla(sv2, "cerrado")  # ahora (22:00) sin hora_deseada: cerrado
        self.falla(sv2, "ya pasó", hora_deseada="2026-11-02 21:00")

    def test_dia_no_listado(self):
        sv = self.sv()
        sv.cfg["horario"]["dias"] = ["lun", "mar"]
        self.assertTrue(self.crear(sv, hora_deseada="2026-11-03 12:00")["ok"])  # martes
        with self.assertRaisesRegex(ValueError, "cerrado"):
            self.crear(sv, hora_deseada="2026-11-04 12:00")  # miércoles

    def test_sin_cierre_cocina_y_sin_bloque_pedidos(self):
        sv = self.sv(cfg={})
        self.assertTrue(self.crear(sv, hora_deseada="2026-11-02 21:00")["ok"])
        sv.cfg.pop("pedidos")
        self.assertTrue(self.crear(sv, hora_deseada="2026-11-02 21:00")["ok"])

    def test_hora_deseada_misma_hora_actual_no_es_pasada(self):
        self.assertTrue(self.crear(self.sv(), hora_deseada="2026-11-02 12:00")["ok"])

    def test_hora_deseada_valida_se_guarda_canonica_y_texto_libre_tal_cual(self):
        sv = self.sv()
        self.crear(sv, "A", hora_deseada="2026-11-3 12:00")
        self.crear(sv, "B", hora_deseada="lo antes posible")  # otro cliente: el mismo cliente actualizaría su pedido
        self.assertEqual([p["hora_deseada"] for p in sv.listar("pedido")], ["2026-11-03 12:00", "lo antes posible"])


class Turnos(Base):
    def sv2(self, ahora=(2026, 11, 3, 9, 0)):  # 2026-11-03 es martes; el 02 es lunes
        sv = self.sv(cfg={"cierre_cocina_min": 30}, ahora=ahora)
        sv.cfg["horario"] = {"dias": ["mar", "mie", "jue", "vie", "sab", "dom"], "turnos": [["11:00", "15:00"], ["18:00", "22:00"]]}
        return sv

    def test_varios_turnos(self):
        for dia, h, ok in (("03", "14:30", True), ("03", "14:31", False), ("03", "16:00", False), ("03", "18:00", True), ("03", "21:30", True), ("03", "21:31", False),
                           ("03", "10:59", False), ("04", "12:00", True), ("02", "12:00", False), ("09", "12:00", False)):
            with self.subTest(dia=dia, h=h):
                sv = self.sv2(ahora=(2026, 11, 2, 9, 0) if dia in ("02", "09") else (2026, 11, 3, 9, 0))
                if ok:
                    self.assertTrue(self.crear(sv, hora_deseada=f"2026-11-{dia} {h}")["ok"])
                else:
                    with self.assertRaisesRegex(ValueError, "cerrado"):
                        self.crear(sv, hora_deseada=f"2026-11-{dia} {h}")

    def test_ahora_dentro_y_entre_turnos(self):
        self.assertTrue(self.crear(self.sv2(ahora=(2026, 11, 3, 19, 0)))["ok"])
        self.falla(self.sv2(ahora=(2026, 11, 3, 16, 0)), "cerrado")


class ModeloProveedor(Base):
    def test_rama_anthropic_y_rama_compatible(self):
        for env, rama in (({"ANTHROPIC_BASE_URL": ""}, "beta"), ({"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}, "beta"),
                          ({"ANTHROPIC_BASE_URL": "http://localhost:11434", "LLM_MODEL": "qwen3:8b"}, "simple")):
            with self.subTest(env=env), mock.patch.dict(os.environ, env):
                sv = self.sv(respuestas=[texto("hola")])
                sv.cfg["modelo"] = "claude-haiku-5-5"
                self.assertEqual(sv.responder("A", "hi"), "hola")
                if rama == "simple":
                    self.assertEqual(sorted(sv.cliente.simple), ["max_tokens", "messages", "model", "system", "thinking", "tools"])
                    self.assertEqual(sv.cliente.simple["model"], "qwen3:8b")
                    self.assertEqual(sv.cliente.simple["thinking"], {"type": "disabled"})
                else:
                    self.assertFalse(hasattr(sv.cliente, "simple"))

    def test_llm_model_manda_sobre_el_yaml(self):
        sv = self.sv()
        sv.cfg["modelo"] = "del-yaml"
        sv._cliente = ClienteFalso([texto("a")])
        with mock.patch.dict(os.environ, {"ANTHROPIC_BASE_URL": "http://x:1", "LLM_MODEL": "del-env"}):
            sv._llamar_modelo([])
        self.assertEqual(sv._cliente.simple["model"], "del-env")


class Estados(Base):
    def test_confirmar_y_reportar_dos_veces_y_cancelar_tras_pago(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        with self.assertRaises(ValueError):
            self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "1"})
        with self.assertRaises(ValueError):
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "2"})
        with self.assertRaises(ValueError):
            self.e(sv, "cancelar_pedido", {"pedido_id": pid})
        p = sv.listar("pedido")[0]
        self.assertEqual((p["estado"], p["referencia"]), ("pago_reportado", "1"))
        self.assertEqual(len(sv.listar("aviso")), 2)  # confirmación + pago, sin duplicados por reintentos

    def test_cancelar_dos_veces_y_confirmar_cancelado(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "cancelar_pedido", {"pedido_id": pid})
        for n, x in (("cancelar_pedido", {}), ("confirmar_pedido", {}), ("reportar_pago", {"metodo": "m", "referencia": "r"})):
            with self.assertRaises(ValueError):
                self.e(sv, n, {"pedido_id": pid, **x})
        self.assertEqual(sv.listar("pedido")[0]["estado"], "cancelado")

    def test_cancelar_confirmado_ok(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        self.e(sv, "cancelar_pedido", {"pedido_id": pid})
        self.assertEqual(sv.listar("pedido")[0]["estado"], "cancelado")

    def test_marcar_saltando_estados_y_terminales(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        for est in ("en_preparacion", "listo", "entregado"):
            with self.assertRaises(ValueError):
                herramientas.marcar_pedido(sv, pid, est)
        for est in ("cancelado", "confirmado", "pago_reportado", "", None, "PAGADO"):
            with self.assertRaisesRegex(ValueError, "inválido"):
                herramientas.marcar_pedido(sv, pid, est)
        herramientas.marcar_pedido(sv, pid, "pagado")
        with self.assertRaises(ValueError):  # pagado dos veces
            herramientas.marcar_pedido(sv, pid, "pagado")
        with self.assertRaises(ValueError):  # no se cancela un pagado
            self.e(sv, "cancelar_pedido", {"pedido_id": pid})
        with self.assertRaises(ValueError):  # ni se reporta pago sobre pagado
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "y", "referencia": "1"})
        for est in ("listo", "entregado"):
            with self.assertRaises(ValueError):
                herramientas.marcar_pedido(sv, pid, est)
        herramientas.marcar_pedido(sv, pid, "en_preparacion")
        herramientas.marcar_pedido(sv, pid, "listo")
        herramientas.marcar_pedido(sv, pid, "entregado")
        with self.assertRaises(ValueError):
            herramientas.marcar_pedido(sv, pid, "entregado")

    def test_marcar_pagado_desde_confirmado_sin_reporte_es_decision(self):
        """DECISION discutible: PREVIOS permite pagado desde 'confirmado' (humano cobra en persona)."""
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        herramientas.marcar_pedido(sv, pid, "pagado")

    def test_pagado_solo_personal_y_el_modelo_no_llega_a_pagado(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        with self.assertRaises(ValueError):  # en_preparacion desde confirmado: primero el pago
            herramientas.marcar_pedido(sv, pid, "en_preparacion")
        herramientas.marcar_pedido(sv, pid, "pagado")  # efectivo o Yappy visto por el personal
        self.assertEqual(sv.listar("pedido")[0]["estado"], "pagado")
        self.assertNotIn("marcar_pedido", herramientas.CATALOGO)
        sv2 = self.sv()
        pid2 = self.crear(sv2)["pedido_id"]
        for n, x in (("actualizar_registro", {"id": pid2, "cambios": {"estado": "pagado"}}), ("registrar", {"tipo": "pedido", "datos": {"estado": "pagado"}})):
            with self.assertRaises(ValueError):
                self.e(sv2, n, x)

    def test_marcar_id_inexistente_o_de_otro_tipo(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        aviso = sv.listar("aviso")[0]["id"]
        for i in (9999, aviso, 0, -1):
            with self.assertRaisesRegex(ValueError, "No existe"):
                herramientas.marcar_pedido(sv, i, "pagado")

    def test_marcar_listo_fallido_no_programa_mensaje(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        with self.assertRaises(ValueError):
            herramientas.marcar_pedido(sv, pid, "listo")
        self.assertEqual(sv.listar("mensaje_programado"), [])

    def test_aviso_de_pago_contiene_referencia(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.e(sv, "reportar_pago", {"pedido_id": pid, "metodo": "yappy", "referencia": "XYZ"})
        self.assertIn("XYZ", out.getvalue())
        self.assertIn("XYZ", sv.listar("aviso")[-1]["texto"])


class Aislamiento(Base):
    def test_b_no_puede_nada_y_estado_intacto(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        for n, x in (("confirmar_pedido", {}), ("cancelar_pedido", {}), ("reportar_pago", {"metodo": "y", "referencia": "1"}),
                     ("actualizar_registro", {"cambios": {"estado": "pagado"}})):
            with self.assertRaises(ValueError):
                self.e(sv, n, {"pedido_id" if n != "actualizar_registro" else "id": pid, **x}, "B")
        self.assertEqual(sv.listar("pedido")[0]["estado"], "confirmado")

    def test_pedido_id_de_otro_tipo_o_inexistente(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        rid = self.e(sv, "registrar", {"tipo": "lead", "datos": {"n": 1}})["id"]
        self.e(sv, "confirmar_pedido", {"pedido_id": pid})
        aviso = sv.listar("aviso")[0]["id"]  # tiene telefono "A": A es dueño de su aviso
        for i in (rid, aviso, 9999, None, "x", "1; DROP TABLE registros"):
            for n, x in (("confirmar_pedido", {}), ("cancelar_pedido", {}), ("reportar_pago", {"metodo": "y", "referencia": "1"})):
                with self.subTest(i=i, n=n), self.assertRaises(ValueError):
                    self.e(sv, n, {"pedido_id": i, **x})
        self.assertEqual(sv.listar("lead")[0]["n"], 1)

    def test_pedido_id_ausente(self):
        sv = self.sv()
        for n in ("confirmar_pedido", "cancelar_pedido"):
            with self.assertRaises(ValueError):
                self.e(sv, n, {})

    def test_campos_inyectados_en_crear_pedido(self):
        sv = self.sv()
        self.assertTrue(self.crear(sv, telefono="B")["ok"])  # telefono se sobreescribe con el cliente
        self.assertEqual(sv.listar("pedido")[0]["telefono"], "A")
        for extra in ({"estado": "pagado"}, {"total_c": 1}, {"subtotal_c": 1}, {"envio_c": 0}):
            with self.assertRaises(TypeError):  # kwarg inesperado -> TypeError, atrapado por el bucle
                self.crear(sv, **extra)
        self.assertEqual([p["estado"] for p in sv.listar("pedido")], ["por_confirmar"])

    def test_campos_inyectados_en_otras_herramientas(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        with self.assertRaises(TypeError):
            self.e(sv, "confirmar_pedido", {"pedido_id": pid, "estado": "pagado"})
        with self.assertRaises(TypeError):
            self.e(sv, "cancelar_pedido", {"pedido_id": pid, "telefono": "B"})

    def test_registrar_tipo_pedido_con_variantes(self):
        """registrar bloquea 'pedido' exacto; variantes crean otro tipo y no afectan a listar('pedido')."""
        sv = self.sv()
        for t in ("Pedido", " pedido", "pedido ", "PEDIDO", "Aviso", "cita"):
            try:
                self.e(sv, "registrar", {"tipo": t, "datos": {"estado": "pagado", "total_c": 1}})
            except ValueError:
                continue
        self.assertEqual(sv.listar("pedido"), [])
        self.assertEqual(sv.listar("aviso"), [])
        self.assertEqual(sv.listar("cita"), [])
        self.assertEqual(self.e(sv, "buscar", {"tipo": "pedido"}), [])

    def test_buscar_pedido_devuelve_solo_los_propios(self):
        sv = self.sv()
        self.crear(sv, "A")
        self.crear(sv, "B")
        self.assertEqual({p["telefono"] for p in self.e(sv, "buscar", {"tipo": "pedido"}, "A")}, {"A"})
        self.assertEqual(self.e(sv, "buscar", {"tipo": "aviso"}, "C"), [])

    def test_cliente_no_puede_cambiar_pedido_via_actualizar(self):
        sv = self.sv()
        pid = self.crear(sv)["pedido_id"]
        for cambios in ({"estado": "pagado"}, {"total_c": 1}, {"telefono": "B"}):
            with self.assertRaises(ValueError):
                self.e(sv, "actualizar_registro", {"id": pid, "cambios": cambios})

    def test_herramienta_de_pedido_no_listada_en_yaml(self):
        sv = self.sv()
        sv.cfg["herramientas"] = ["buscar"]
        with self.assertRaisesRegex(ValueError, "no permitida"):
            self.crear(sv)


class Bucle(Base):
    def test_error_de_crear_pedido_vuelve_como_tool_result_is_error(self):
        entrada = {"items": [{"id": "pizza", "cantidad": 1}], "modalidad": "recogida", "nombre": "Luis"}
        sv = self.sv(respuestas=[herramienta("crear_pedido", entrada), texto("No tenemos pizza")])
        self.assertEqual(sv.responder("A", "quiero pizza"), "No tenemos pizza")
        res = sv.cliente.llamadas[1][-1]["content"][0]
        self.assertTrue(res["is_error"])
        self.assertIn("No existe 'pizza'", res["content"])
        self.assertEqual(sv.listar("pedido"), [])

    def test_kwarg_inesperado_no_rompe_el_bucle(self):
        entrada = {"items": [SANC], "modalidad": "recogida", "nombre": "L", "estado": "pagado"}
        sv = self.sv(respuestas=[herramienta("crear_pedido", entrada), texto("ok")])
        self.assertEqual(sv.responder("A", "hola"), "ok")
        self.assertTrue(sv.cliente.llamadas[1][-1]["content"][0]["is_error"])

    def test_entrada_malformada_no_rompe_el_bucle(self):
        for items in (["sancocho"], "sancocho", [None]):
            with self.subTest(items=items):
                entrada = {"items": items, "modalidad": "recogida", "nombre": "Luis"}
                sv = self.sv(respuestas=[herramienta("crear_pedido", entrada), texto("ok")])
                self.assertEqual(sv.responder("A", "hola"), "ok")

    def test_flujo_completo_por_el_bucle(self):
        # crear y confirmar ya no pueden ir en el mismo turno: el "sí" llega en un mensaje nuevo
        sv = self.sv(respuestas=[herramienta("crear_pedido", {"items": [SANC, {"id": "chicha", "cantidad": 2}], "modalidad": "recogida", "nombre": "Luis"}), texto("¿confirma?"),
                                 herramienta("confirmar_pedido", {"pedido_id": 1}), herramienta("reportar_pago", {"pedido_id": 1, "metodo": "yappy", "referencia": "77"}, "t2"), texto("listo")])
        with contextlib.redirect_stdout(io.StringIO()):
            sv.responder("A", "pedido")
            self.assertEqual(sv.responder("A", "sí, pagué con ref 77"), "listo")
        self.assertEqual(sv.listar("pedido")[0]["estado"], "pago_reportado")


class Validar(unittest.TestCase):
    def test_validar_servicio_restaurante(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(["validar", str(RAIZ / "servicios" / "restaurante-pedidos")])
        self.assertTrue(out.getvalue().startswith("OK"))

    def carpeta(self, menu):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        (Path(tmp.name) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (Path(tmp.name) / "menu.yaml").write_text(menu, encoding="utf-8")
        return tmp.name

    def test_menus_invalidos_hacen_fallar_validar(self):
        for menu in ("- {id: a, nombre: A, precio: 0}", "- {nombre: A, precio: 1}", "- {id: a, nombre: A, precio: 1}\n- {id: a, nombre: B, precio: 2}",
                     "- {id: a, nombre: A, precio: -1}", "- {id: a, nombre: A, precio: abc}", "- {id: a, nombre: '', precio: 1}", "id: a", "- solo texto",
                     "- {id: a, nombre: A, precio: 0.001}"):
            with self.subTest(menu=menu), self.assertRaises(ValueError):
                cli.main(["validar", self.carpeta(menu)])

    def test_menu_vacio_falla_validar(self):
        with self.assertRaisesRegex(ValueError, "vacío"):
            cli.main(["validar", self.carpeta("[]")])

    def test_menu_con_id_numerico(self):
        """El id YAML numerico (id: 1) se guarda como texto: el modelo manda '1' y existe."""
        sv = Servicio(self.carpeta("- {id: 1, nombre: A, precio: 20}"), db=":memory:", cliente=ClienteFalso([]))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        self.assertTrue(herramientas.ejecutar(sv, "crear_pedido", {"items": [{"id": "1", "cantidad": 1}], "modalidad": "recogida", "nombre": "Luis"}, "A")["ok"])


class ComandoPedidos(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        (Path(self.dir) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (Path(self.dir) / "menu.yaml").write_text(MENU, encoding="utf-8")
        sv = Servicio(self.dir, cliente=ClienteFalso([]))
        sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=sv.tz)
        self.pid = herramientas.ejecutar(sv, "crear_pedido", {"items": [SANC], "modalidad": "recogida", "nombre": "Luis"}, "507")["pedido_id"]
        herramientas.ejecutar(sv, "confirmar_pedido", {"pedido_id": self.pid}, "507")
        sv.db.close()

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(["pedidos", self.dir, *map(str, args)])
        return out.getvalue()

    def test_listar_y_marcar(self):
        self.assertIn(f"#{self.pid} confirmado · Luis · recogida · B/. 10.70", self.main())
        self.assertIn("pagado", self.main("marcar", self.pid, "pagado"))
        self.main("marcar", self.pid, "en_preparacion")
        self.assertIn("listo", self.main("marcar", self.pid, "listo"))
        sv = Servicio(self.dir, cliente=ClienteFalso([]))
        self.assertEqual(len(sv.listar("mensaje_programado")), 1)
        sv.db.close()

    def test_errores_son_mensaje_limpio(self):
        for args in (("marcar", self.pid, "entregado"), ("marcar", 999, "pagado"), ("marcar", self.pid, "cancelado"), ("marcar", "abc", "pagado")):
            with self.subTest(args=args), self.assertRaises(SystemExit) as cm:
                self.main(*args)
            self.assertTrue(str(cm.exception).startswith("Error:"), cm.exception)

    def test_marcar_incompleto_muestra_el_uso(self):
        with self.assertRaisesRegex(SystemExit, "Uso:"):
            self.main("marcar", self.pid)

    def test_listar_muestra_metodo_y_referencia_del_pago_reportado(self):
        sv = Servicio(self.dir, cliente=ClienteFalso([]))
        with contextlib.redirect_stdout(io.StringIO()):
            herramientas.ejecutar(sv, "reportar_pago", {"pedido_id": self.pid, "metodo": "yappy", "referencia": "R-77"}, "507")
        sv.db.close()
        self.assertIn("pago yappy ref R-77", self.main())


if __name__ == "__main__":
    unittest.main()
