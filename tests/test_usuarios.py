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
        us = {u["nombre"]: u for u in self.sv.listar("usuario")}
        self.assertEqual({n: u["rol"] for n, u in us.items()}, {"Dueno": "dueno", "Personal": "personal", "Juan": "repartidor"})
        self.assertNotIn("483920", str(us))
        self.assertTrue(us["Dueno"]["pin_hash"].startswith("pbkdf2$"))
        self.api("POST", "usuario", {"nombre": "Juan", "pin": "739104"})
        panel_web.sembrar_usuarios(self.sv)  # reinicio: el .env no pisa lo cambiado en la BD
        self.assertEqual(len(self.sv.listar("usuario")), 3)
        self.assertEqual(panel_web.Acceso("x", self.rep).rol_de_pin("739104"), "Juan")

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


if __name__ == "__main__":
    unittest.main()
