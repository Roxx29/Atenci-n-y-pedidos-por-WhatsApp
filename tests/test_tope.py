"""Tope de IA por cliente y día, y prueba gratis con fecha de fin. Ejecutar: python -m unittest tests.test_tope"""
import datetime as dt
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

from motor import clientes
from motor.nucleo import Servicio
from tests import test_alojar
from tests.test_motor import MENU, SERVICIO_PEDIDOS, ClienteFalso, texto

LIMITE = "Hoy el asistente alcanzó su límite"


class TestUsoIA(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        (self.d / "servicio.yaml").write_text(SERVICIO_PEDIDOS, encoding="utf-8")
        (self.d / "menu.yaml").write_text(MENU, encoding="utf-8")

    def sv(self, n=0, tope=2, carpeta=None):
        sv = Servicio(carpeta or self.d, cliente=ClienteFalso([texto("ok") for _ in range(n)]))
        self.addCleanup(sv.cerrar)
        sv.tope_ia = tope
        return sv

    def uso(self, sv):
        return dict(sv._q("SELECT dia, mensajes FROM uso_ia"))

    def test_al_llegar_al_tope_no_llama_al_modelo_y_avisa_una_vez(self):
        sv = self.sv(5)
        self.assertEqual([sv.responder("A", "hola"), sv.responder("B", "hola")], ["ok", "ok"])
        r = [sv.responder(c, "hola") for c in "ABC"]
        self.assertEqual(r, ["Hoy el asistente alcanzó su límite. Llama al restaurante para tu pedido."] * 3)
        self.assertEqual(len(sv._cliente.llamadas), 2)
        self.assertEqual([a["texto"] for a in sv.listar("aviso")], ["Se alcanzó el tope de IA de hoy"])
        self.assertEqual(self.uso(sv), {f"{sv.ahora():%Y-%m-%d}": 2})

    def test_el_mensaje_trae_el_telefono_del_restaurante(self):
        sv = self.sv(0, tope=1)
        sv.poner_ajuste("telefono", "994-1234")
        sv.tope_ia = 1
        sv._q("INSERT INTO uso_ia VALUES (?, 1)", (f"{sv.ahora():%Y-%m-%d}",))
        self.assertEqual(sv.responder("A", "hola"), "Hoy el asistente alcanzó su límite. Llama al restaurante al 994-1234 para tu pedido.")

    def test_cuenta_turnos_no_llamadas_del_bucle_de_herramientas(self):
        from tests.test_motor import herramienta
        sv = self.sv(0, tope=5)
        sv._cliente.respuestas = [herramienta("buscar", {"tipo": "pedido"}), texto("nada")]
        self.assertEqual(sv.responder("A", "mis pedidos"), "nada")
        self.assertEqual(list(self.uso(sv).values()), [1])

    def test_sin_tope_no_limita_pero_cuenta(self):
        sv = self.sv(4, tope=0)
        self.assertEqual([sv.responder("A", "x") for _ in range(4)], ["ok"] * 4)
        self.assertEqual(list(self.uso(sv).values()), [4])

    def test_reinicio_conserva_el_conteo(self):
        sv = self.sv(2)
        sv.responder("A", "1"), sv.responder("A", "2")
        sv.cerrar()
        otro = self.sv(1)
        self.assertIn(LIMITE, otro.responder("A", "3"))
        self.assertEqual(otro._cliente.llamadas, [])
        self.assertEqual(len(otro.listar("aviso")), 1)
        otro._tope_avisado = None  # como tras otro reinicio: el aviso de hoy ya está en la BD
        otro.responder("A", "4")
        self.assertEqual(len(otro.listar("aviso")), 1)

    def test_dia_nuevo_reinicia(self):
        sv = self.sv(3, tope=1)
        hoy = dt.datetime(2026, 11, 2, 23, 59, tzinfo=sv.tz)
        sv.ahora = lambda: hoy
        self.assertEqual(sv.responder("A", "1"), "ok")
        self.assertIn(LIMITE, sv.responder("A", "2"))
        sv.ahora = lambda: hoy + dt.timedelta(minutes=1)  # medianoche en la zona del servicio
        self.assertEqual(sv.responder("A", "3"), "ok")
        self.assertIn(LIMITE, sv.responder("A", "4"))
        self.assertEqual(self.uso(sv), {"2026-11-02": 1, "2026-11-03": 1})
        self.assertEqual(len(sv.listar("aviso")), 2)  # uno por día

    def test_en_turso_libsql_tambien(self):
        try:
            import libsql  # noqa: F401
        except ImportError:
            self.skipTest("sin libsql (requirements-nube.txt)")
        for _ in range(2):  # la segunda vuelta es un reinicio sobre la misma réplica
            sv = Servicio(self.d, cliente=ClienteFalso([texto("ok")]), env={"TURSO_SOLO_LOCAL": "1"})
            sv.tope_ia = 1
            self.assertTrue(sv.nube)
            r = [sv.responder("A", "1"), sv.responder("A", "2")]
            n = len(sv.listar("aviso"))
            sv.cerrar()
            self.assertIn(LIMITE, r[1])
            self.assertEqual(n, 1)
        self.assertEqual(r[0][:3], "Hoy")  # tras el reinicio ya estaba topado

    def test_dos_clientes_aislados(self):
        (self.d / "otro").mkdir()
        (self.d / "otro" / "servicio.yaml").write_text(SERVICIO_PEDIDOS.replace("menu: menu.yaml\n", ""), encoding="utf-8")
        a, b = self.sv(1, tope=1), self.sv(1, tope=1, carpeta=self.d / "otro")
        self.assertEqual(a.responder("A", "1"), "ok")
        self.assertIn(LIMITE, a.responder("A", "2"))
        self.assertEqual(b.responder("A", "1"), "ok")
        self.assertEqual(len(b.listar("aviso")), 0)

    def test_peticiones_simultaneas_no_superan_el_tope(self):
        """Dos conexiones a la misma BD (como dos procesos) y 40 hilos: el modelo se llama exactamente `tope` veces."""
        a, b = self.sv(40, tope=7), self.sv(40, tope=7)
        res, salida = [], threading.Barrier(40)

        def uno(i):
            salida.wait()
            res.append((a, b)[i % 2].responder(f"c{i}", "hola"))

        hilos = [threading.Thread(target=uno, args=(i,)) for i in range(40)]
        [h.start() for h in hilos]
        [h.join() for h in hilos]
        self.assertEqual(res.count("ok"), 7)
        self.assertEqual(len(a._cliente.llamadas) + len(b._cliente.llamadas), 7)
        self.assertEqual(list(self.uso(a).values()), [7])
        self.assertEqual(len(a.listar("aviso")), 1)


class TestTopePorDefecto(unittest.TestCase):
    def tope(self, **k):
        return clientes.tope_ia(clientes.Cliente("a", Path("."), "a.com", True, **k))

    def test_por_defecto_300_en_prueba_y_sin_tope_los_demas(self):
        hasta = dt.date(2026, 12, 1)
        with unittest.mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual([self.tope(), self.tope(pagado_hasta=hasta), self.tope(prueba_hasta=hasta)], [0, 0, 300])
            self.assertEqual([self.tope(tope_ia_dia=7), self.tope(tope_ia_dia=0, prueba_hasta=hasta)], [7, 0])
        with unittest.mock.patch.dict(os.environ, {"TOPE_IA_DIA": "50"}):
            self.assertEqual([self.tope(), self.tope(prueba_hasta=hasta), self.tope(tope_ia_dia=7, prueba_hasta=hasta)], [0, 50, 7])
        with unittest.mock.patch.dict(os.environ, {"TOPE_IA_DIA": "-3"}):  # mal escrita: no deja la prueba sin tope
            self.assertEqual(self.tope(prueba_hasta=hasta), 300)


class Alojado(test_alojar.Base):
    extra_a = ""

    def arrancar(self):
        (self.d / "clientes.yaml").write_text(f"clientes:\n  a: {{carpeta: a, dominio: a.com{self.extra_a}}}\n  b: {{carpeta: b, dominio: b.com}}\n", encoding="utf-8")
        super().arrancar()

    def chat(self, host, ck):
        st, raw, _ = self.req(host, "POST", "/chat", {"texto": "hola"}, cookie=ck)
        return json.loads(raw)["respuesta"] if st == 200 else st


class TestTopeAlojado(Alojado):
    extra_a = ", tope_ia_dia: 1"

    def test_el_chat_se_corta_y_lo_demas_sigue(self):
        a, b = self.srv.servicios["a"], self.srv.servicios["b"]
        a._cliente, b._cliente = ClienteFalso([texto("hola a")] * 3), ClienteFalso([texto("hola b")] * 3)
        cka, ckb = self.entrar("a.com")[2], self.entrar("b.com")[2]
        self.assertEqual(self.chat("a.com", cka), "hola a")
        self.assertIn(LIMITE, self.chat("a.com", cka))
        self.assertIn(LIMITE, self.chat("a.com", cka))
        self.assertEqual(len(a._cliente.llamadas), 1)
        self.assertEqual([self.chat("b.com", ckb)] * 2, ["hola b"] * 2)  # b no tiene tope ni hereda el de a
        self.assertEqual(self.chat("b.com", ckb), "hola b")
        self.assertEqual([len(a.listar("aviso")), len(b.listar("aviso"))], [1, 0])
        self.assertEqual(self.req("a.com", "GET", "/pedidos", cookie=cka)[0], 200)
        self.assertEqual(self.req("a.com", "POST", "/panel/entrar", {"pin": test_alojar.PIN_A})[0], 200)
        self.assertEqual(self.req("a.com", "POST", "/repartidor/entrar", {"pin": "271828"})[0], 200)

    def test_el_tope_cambia_en_caliente(self):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text("clientes:\n  a: {carpeta: a, dominio: a.com, tope_ia_dia: 9}\n  b: {carpeta: b, dominio: b.com}\n", encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))
        self.marca("a.com")
        self.assertEqual(self.srv.servicios["a"].tope_ia, 9)


class TestPrueba(Alojado):
    def arrancar(self):
        self.hoy = dt.datetime.now(dt.timezone(dt.timedelta(hours=-5))).date()  # America/Panama no tiene horario de verano
        self.extra_a = f", prueba_hasta: {self.hoy}"
        super().arrancar()

    def ask(self, dominio):
        return self.req("127.0.0.1", "GET", f"/_ask?domain={dominio}")[0]

    def test_vigente_funciona_y_vencida_es_el_404_de_un_pausado_sin_recargar(self):
        sv = self.srv.servicios["a"]
        self.assertEqual([self.marca("a.com"), self.marca("a.localhost"), self.ask("a.com")], ["Marca-a", "Marca-a", 200])  # hoy == prueba_hasta
        self.assertEqual(sv.tope_ia, 300)
        manana = dt.datetime.combine(self.hoy + dt.timedelta(days=1), dt.time(0, 0), sv.tz)
        sv.ahora = lambda: manana  # se evalúa en cada petición, con la fecha de la zona del servicio
        self.assertEqual([self.marca("a.com"), self.marca("a.localhost"), self.ask("a.com")], [404, 404, 404])
        self.assertEqual(self.req("a.com", "GET", "/panel")[:2], self.req("otro.com", "GET", "/panel")[:2])
        self.assertEqual([self.marca("b.com"), self.ask("b.com")], ["Marca-b", 200])
        self.assertEqual(self.req("x", "GET", "/salud")[0], 200)

    def test_sin_prueba_hasta_no_se_pausa_por_fecha(self):
        f = self.d / "clientes.yaml"
        antes = f.stat().st_mtime_ns
        f.write_text("clientes:\n  a: {carpeta: a, dominio: a.com, pagado_hasta: 2020-01-01}\n  b: {carpeta: b, dominio: b.com, prueba_hasta: 2020-01-01}\n", encoding="utf-8")
        os.utime(f, ns=(antes + 10**9, antes + 10**9))
        self.assertEqual([self.marca("a.com"), self.ask("a.com")], ["Marca-a", 200])  # impago: la pausa sigue siendo manual
        self.assertEqual([self.marca("b.com"), self.ask("b.com")], [404, 404])  # vencida al recargar en caliente
        self.assertEqual(self.srv.servicios["a"].tope_ia, 0)


class TestValidacion(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        (self.d / "a").mkdir()
        (self.d / "a" / "servicio.yaml").write_text("nombre: A\n", encoding="utf-8")

    def uno(self, extra=""):
        (self.d / "clientes.yaml").write_text(f"clientes:\n  a: {{carpeta: a, dominio: a.com{extra}}}\n", encoding="utf-8")
        return clientes.cargar(self.d / "clientes.yaml")[0]

    def test_campos_opcionales(self):
        c = self.uno()
        self.assertEqual((c.tope_ia_dia, c.prueba_hasta), (None, None))
        c = self.uno(', tope_ia_dia: 0, prueba_hasta: "2026-12-01"')
        self.assertEqual((c.tope_ia_dia, c.prueba_hasta), (0, dt.date(2026, 12, 1)))
        c = self.uno(", tope_ia_dia: 100000, prueba_hasta: 2026-12-01")
        self.assertEqual((c.tope_ia_dia, c.prueba_hasta), (100000, dt.date(2026, 12, 1)))

    def test_invalidos_nombran_el_campo(self):
        for extra, campo in ((", tope_ia_dia: -1", "tope_ia_dia"), (", tope_ia_dia: 100001", "tope_ia_dia"), (", tope_ia_dia: 3.5", "tope_ia_dia"),
                             (', tope_ia_dia: "20"', "tope_ia_dia"), (", tope_ia_dia: true", "tope_ia_dia"), (", tope_ia_dia: null", "tope_ia_dia"),
                             (', prueba_hasta: "01/12/2026"', "prueba_hasta"), (", prueba_hasta: 2026-13-01", "prueba_hasta"),
                             (", prueba_hasta: 20261201", "prueba_hasta"), (", prueba_hasta: 2026-12-01 10:00:00", "prueba_hasta"),
                             (", prueba_hasta: null", "prueba_hasta")):
            with self.assertRaisesRegex(ValueError, campo, msg=extra):
                self.uno(extra)


if __name__ == "__main__":
    unittest.main()
