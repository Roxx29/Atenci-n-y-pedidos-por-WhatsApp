"""Usuarios por persona (panel y repartidores) y rutas del dueño. Ejecutar: python -m unittest tests.test_usuarios"""
import datetime as dt
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from motor import panel_web
from motor.nucleo import Servicio
from tests.test_motor import MENU, SERVICIO_PEDIDOS

ENV = {"PANEL_PIN": "483920", "PANEL_PIN_HASH": "", "PANEL_PIN_PERSONAL": "572910", "PANEL_PIN_PERSONAL_HASH": "",
       "REPARTIDORES": "Juan:482913", "REPARTIDOR_PIN": ""}


class BaseSv(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        (d / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (d / "menu.yaml").write_text(MENU, encoding="utf-8")
        self.dir = d
        self.sv = Servicio(d, db=":memory:")
        self.sv.ahora = lambda: dt.datetime(2026, 11, 2, 12, 0, tzinfo=self.sv.tz)
        self.addCleanup(self.sv.cerrar)
        p = unittest.mock.patch.dict(os.environ, ENV)
        p.start()
        self.addCleanup(p.stop)

    def api(self, metodo, ruta, d=None, quien="Dueno", rol="dueno"):
        return panel_web.api_dueno(self.sv, quien, rol, metodo, ruta, d)


class Usuarios(BaseSv):
    def setUp(self):
        super().setUp()
        self.panel = panel_web.Usuarios(self.sv, panel_web.PANEL)
        self.rep = panel_web.Usuarios(self.sv, panel_web.REPARTO)

    def test_semilla_solo_hash_y_no_pisa(self):
        us = {u["nombre"]: u for u in panel_web._todos(self.sv)}
        self.assertEqual({n: u["rol"] for n, u in us.items()}, {"Dueno": "dueno", "Personal": "personal", "Juan": "repartidor"})
        self.assertNotIn("483920", str(us))
        self.assertTrue(us["Dueno"]["pin_hash"].startswith("pbkdf2$"))
        self.api("POST", "usuario", {"nombre": "Juan", "pin": "739104"})
        panel_web.sembrar_usuarios(self.sv)  # reinicio: el .env no pisa lo cambiado en la BD
        self.assertEqual(len(panel_web.lista_usuarios(self.sv)), 3)
        self.assertEqual(panel_web.Acceso("x", self.rep).rol_de_pin("739104"), "Juan")

    def test_aviso_si_el_env_difiere_de_la_bd(self):
        self.api("POST", "usuario", {"nombre": "Juan", "pin": "739104"})
        with unittest.mock.patch("builtins.print") as pr:
            panel_web.sembrar_usuarios(self.sv)
        salida = " ".join(str(c) for c in pr.call_args_list)
        self.assertIn("Juan", salida)
        self.assertNotIn("Dueno", salida)  # el PIN del dueño coincide: sin aviso
        for pin in ("739104", "482913", "483920"):
            self.assertNotIn(pin, salida)

    def test_login_por_persona_y_rol(self):
        a = panel_web.Acceso("x", self.panel)
        self.assertEqual(a.rol_de_pin("572910"), "Personal")
        self.assertEqual(self.panel.rol("Personal"), "personal")
        self.assertIsNone(a.rol_de_pin("482913"))  # el repartidor no entra al panel
        self.assertEqual(panel_web.Acceso("x", self.rep).rol_de_pin("482913"), "Juan")

    def test_personal_403(self):
        for m, r in (("GET", "usuarios"), ("POST", "usuario")):
            with self.assertRaises(PermissionError):
                self.api(m, r, {"nombre": "Ana", "rol": "personal", "pin": "918273"}, quien="Personal", rol="personal")
        with self.assertRaises(LookupError):
            self.api("GET", "nada")

    def test_pin_unico_debil_y_sin_hash_en_api(self):
        for d in ({"nombre": "Ana", "rol": "personal", "pin": "572910"},  # PIN de Personal
                  {"nombre": "Ana", "rol": "personal", "pin": "482913"},  # PIN de un repartidor
                  {"nombre": "Ana", "rol": "personal", "pin": "123456"},
                  {"nombre": "Ana.x", "rol": "personal", "pin": "918273"},
                  {"nombre": "Ana", "rol": "jefe", "pin": "918273"},
                  {"nombre": "Ana", "rol": "personal"}):
            with self.assertRaises(ValueError, msg=d):
                self.api("POST", "usuario", d)
        self.api("POST", "usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"})
        out = str(self.api("GET", "usuarios"))
        self.assertIn("Ana", out)
        self.assertNotIn("pbkdf2", out)
        self.assertNotIn("918273", out)

    def test_cambio_pin_auditado_e_invalida_sesion(self):
        a = panel_web.Acceso("x", self.panel)
        ck = a.cookie("Personal").split(";")[0]
        self.assertEqual(a.sesion(ck)[0], "Personal")
        self.api("POST", "usuario", {"nombre": "Personal", "pin": "661834"})
        self.assertIsNone(a.sesion(ck))
        self.assertEqual(a.rol_de_pin("661834"), "Personal")
        self.assertIsNone(a.rol_de_pin("572910"))
        acc = [(x["usuario"], x["accion"]) for x in self.sv.listar("auditoria")]
        self.assertIn(("Dueno", "usuario_pin:Personal"), acc)
        self.assertNotIn("661834", str(self.sv.listar("auditoria")))

    def test_desactivar_y_reactivar(self):
        a = panel_web.Acceso("x", self.rep)
        ck = a.cookie("Juan").split(";")[0]
        self.api("POST", "usuario", {"nombre": "Juan", "activo": False})
        self.assertIsNone(a.sesion(ck))
        self.assertIsNone(a.rol_de_pin("482913"))
        self.assertFalse(self.rep)
        with self.assertRaises(ValueError):  # reactivar exige PIN nuevo
            self.api("POST", "usuario", {"nombre": "Juan", "activo": True})
        self.api("POST", "usuario", {"nombre": "Juan", "activo": True, "pin": "739104"})
        self.assertEqual(list(self.rep), ["Juan"])
        self.assertIn("usuario_desactivar:Juan", [x["accion"] for x in self.sv.listar("auditoria")])

    def test_queda_un_dueno(self):
        with self.assertRaises(ValueError):
            self.api("POST", "usuario", {"nombre": "Dueno", "activo": False})
        with self.assertRaises(ValueError):
            self.api("POST", "usuario", {"nombre": "Dueno", "rol": "personal"})


class Menu(BaseSv):
    def test_semilla_y_bd_manda(self):
        self.assertEqual(list(self.sv.menu), list(self.sv._menu_yaml))
        self.assertEqual(self.sv.listar("pedido"), [])  # la semilla no ocupa ids de registros
        self.api("POST", "plato", {"id": "sancocho", "precio": "6.25", "disponible": False})
        self.assertEqual(self.sv.menu["sancocho"]["precio_c"], 625)
        self.assertIn("B/. 6.25", self.sv.system)
        self.assertIn("AGOTADO", self.sv.system)
        sv2 = Servicio(self.dir, db=":memory:")  # BD nueva: vuelve a sembrar del yaml
        self.addCleanup(sv2.cerrar)
        self.assertNotEqual(sv2.menu["sancocho"]["precio_c"], 625)

    def test_crear_validar_y_auditar(self):
        self.api("POST", "plato", {"id": "flan", "nombre": " Flan ", "precio": 2, "categoria": "postres"})
        self.assertEqual(next(m for m in self.api("GET", "menu")["menu"] if m["id"] == "flan")["nombre"], "Flan")
        for d in ({"id": "flan", "precio": -1}, {"id": "flan", "nombre": " "}, {"id": "flan", "nombre": "x" * 81},
                  {"id": "flan", "precio": "1.001"}, {"id": "flan", "precio": True}, {"id": "Flan X"}, {"id": "nuevo", "nombre": "Sin precio"},
                  {"id": "flan", "disponible": "no"}, {"id": "flan", "categoria": "c" * 31}):
            with self.assertRaises(ValueError, msg=d):
                self.api("POST", "plato", d)
        self.assertIn(("Dueno", "plato_crear:flan"), [(x["usuario"], x["accion"]) for x in self.sv.listar("auditoria")])
        with self.assertRaises(PermissionError):
            self.api("POST", "plato", {"id": "flan", "precio": 0}, quien="Personal", rol="personal")


class Enmascarado(BaseSv):
    def test_formatos(self):
        m = panel_web._mask
        self.assertEqual(m("50761234512"), "6***-**12")
        self.assertEqual(m("+507 6123-4512", con_prefijo=True), "+507 6***-**12")
        self.assertEqual(m("5215512345678"), "+52155 1*****78")
        self.assertEqual(m(None), "—")

    def test_cerrado_enmascarado_y_caducado_con_estado_at(self):
        from motor import herramientas
        from tests.test_web import PEDIDO, TEL
        pid = herramientas.crear_pedido(self.sv, telefono=TEL, **PEDIDO)["pedido_id"]
        p = next(p for p in self.sv.listar("pedido") if p["id"] == pid)
        self.assertEqual(panel_web.pedido_json(p, True)["telefono"], TEL)  # abierto: completo en el detalle
        self.sv.ahora = lambda: dt.datetime(2026, 11, 2, 13, 0, tzinfo=self.sv.tz)
        self.assertEqual(herramientas.caducar_pedidos(self.sv), [pid])
        p = next(p for p in self.sv.listar("pedido") if p["id"] == pid)
        self.assertEqual(p["estado_at"], "2026-11-02T13:00:00-05:00")
        self.assertEqual(panel_web.pedido_json(p, True)["telefono"], "6***-**11")
        self.assertEqual(panel_web.pedido_json({**p, "telefono": None})["telefono"], "—")


if __name__ == "__main__":
    unittest.main()
