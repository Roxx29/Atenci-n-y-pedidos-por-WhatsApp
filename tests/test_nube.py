"""BD conmutable (Turso/libsql), migración y /salud. Ejecutar: python -m unittest tests.test_nube"""
import os
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from motor import herramientas, migrar
from motor.nucleo import Servicio
from tests.test_web import MENU, SERVICIO_PEDIDOS, Base

try:
    import libsql  # noqa: F401
except ImportError:
    libsql = None

SIN_NUBE = {"TURSO_DATABASE_URL": "", "TURSO_AUTH_TOKEN": "", "TURSO_SOLO_LOCAL": ""}


def carpeta(test):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    (Path(tmp.name) / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
    (Path(tmp.name) / "menu.yaml").write_text(MENU, encoding="utf-8")
    return Path(tmp.name)


class Salud(Base):
    def test_salud_ok_sin_cookie(self):
        self.arrancar()
        r, raw = self.req("GET", "/salud")
        self.assertEqual((r.status, raw), (200, b'{"ok": true}'))
        self.assertIsNone(r.getheader("Set-Cookie"))

    def test_salud_503_si_la_bd_no_responde(self):
        self.arrancar()
        self.sv.db.close()
        self.assertEqual(self.req("GET", "/salud")[0].status, 503)


class Conmutable(unittest.TestCase):
    def test_sin_variables_es_sqlite_local(self):
        with unittest.mock.patch.dict(os.environ, SIN_NUBE):
            d = carpeta(self)
            sv = Servicio(d)
            self.addCleanup(sv.cerrar)
            self.assertFalse(sv.nube)
            self.assertTrue((d / "datos.sqlite").exists())

    @unittest.skipUnless(libsql, "libsql no instalado (requirements-nube.txt)")
    def test_libsql_solo_local_corre_el_motor(self):
        with unittest.mock.patch.dict(os.environ, {**SIN_NUBE, "TURSO_SOLO_LOCAL": "1"}):
            d = carpeta(self)
            sv = Servicio(d)
            self.addCleanup(sv.cerrar)
            self.assertTrue(sv.nube)
            self.assertTrue((d / "datos-nube.db").exists() and not (d / "datos.sqlite").exists())
            pid = sv.insertar("pedido", {"estado": "confirmado", "total_c": 100})
            self.assertEqual(sv.actualizar(pid, {"estado": "pagado"}, tipo="pedido", desde={"confirmado"})["estado"], "pagado")
            self.assertTrue(sv.primera_vez("m1") and not sv.primera_vez("m1"))
            sv.guardar_plato("x", {"nombre": "X", "precio_c": 100})
            self.assertTrue(sv.poner_foto("x", b"f1", b"f2", "v1"))
            self.assertEqual((sv.foto("x"), sv.foto("x", True), sv.fotos()), (b"f1", b"f2", {"x": "v1"}))
            sv._q("INSERT INTO comprobantes(nombre, datos, creado) VALUES (?,?,?)", ("a" * 32 + ".jpg", b"\xff\xd8\xff", 1.0))
            self.assertEqual(herramientas.leer_comprobante(sv, {"comprobante": "a" * 32 + ".jpg"}), (b"\xff\xd8\xff", "jpg"))
            with self.assertRaises(ValueError):
                sv.actualizar(pid, {}, desde={"nada"})


class Migrar(unittest.TestCase):
    def origen(self):
        with unittest.mock.patch.dict(os.environ, SIN_NUBE):
            d = carpeta(self)
            sv = Servicio(d)
            sv.insertar("pedido", {"estado": "pagado"})
            sv.insertar("pedido", {"estado": "listo"})
            sv._q("INSERT INTO comprobantes(nombre, datos, creado) VALUES (?,?,?)", ("b" * 32 + ".png", b"\x89PNG", 1.0))
            sv.cerrar()
        return d

    def test_sin_variables_falla_claro(self):
        d = self.origen()
        with unittest.mock.patch.dict(os.environ, SIN_NUBE), self.assertRaises(SystemExit) as e:
            migrar.migrar(d)
        self.assertIn("TURSO_DATABASE_URL", str(e.exception))

    def test_copia_y_se_niega_si_el_destino_tiene_datos(self):
        d = self.origen()
        dest = sqlite3.connect(":memory:")
        migrar.migrar(d, destino=dest)
        self.assertEqual(dest.execute("SELECT COUNT(*) FROM registros").fetchone(), (2,))
        self.assertEqual(dest.execute("SELECT datos FROM comprobantes").fetchone(), (b"\x89PNG",))
        with self.assertRaises(SystemExit) as e:
            migrar.migrar(d, destino=dest)
        self.assertIn("--forzar", str(e.exception))
        migrar.migrar(d, forzar=True, destino=dest)
        self.assertEqual(dest.execute("SELECT COUNT(*) FROM registros").fetchone(), (2,))  # sobrescribe, no duplica


if __name__ == "__main__":
    unittest.main()
