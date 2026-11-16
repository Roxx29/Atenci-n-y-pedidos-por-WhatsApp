"""Consola avanzada del operador: PIN, equipo, ajustes, archivar/eliminar y registro. Ejecutar: python -m unittest tests.test_consola"""
import datetime as dt
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import unittest.mock
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

from motor import clientes, operador, panel_web, web
from motor.nucleo import Servicio
from tests.test_alta import pedir

CLAVE = "clave-12-car"  # 12: el mínimo
PIN = "482913"
HOY = dt.date.today().isoformat()


class Base(unittest.TestCase):
    def setUp(self):
        p = unittest.mock.patch.dict(os.environ, {"WEB_SECRET": "maestro"}, clear=True)
        p.start(); self.addCleanup(p.stop)
        t = tempfile.TemporaryDirectory(); self.addCleanup(t.cleanup)
        self.raiz = Path(t.name).resolve()
        self.ruta = self.raiz / "clientes.yaml"
        self.op = operador.Operador(self.ruta, CLAVE)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), operador.handler(self.op))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.ck = pedir(self.srv.server_port, "POST", "/api/entrar", {"clave": CLAVE})[2]
        self.textos = []  # todo lo que respondió la consola, para buscar fugas
        self.op.nuevo({"nombre": "Fonda La Ñapa", "slug": "napa", "dominio": "napa.example.com", "pin": PIN})
        self.carpeta = self.raiz / "servicios" / "napa"

    def post(self, ruta, d=None, ck=None):
        st, j, _ = pedir(self.srv.server_port, "POST", ruta, {} if d is None else d, self.ck if ck is None else ck)
        self.textos.append(json.dumps(j) if not isinstance(j, bytes) else j.decode())
        return st, j

    def get(self, ruta, ck=None):
        st, j, _ = pedir(self.srv.server_port, "GET", ruta, cookie=self.ck if ck is None else ck)
        self.textos.append(json.dumps(j) if not isinstance(j, bytes) else j.decode())
        return st, j

    def usuarios(self):
        con = sqlite3.connect(self.carpeta / "datos.sqlite")
        try:
            return {n: (h, a) for n, h, a in con.execute("SELECT nombre, pin_hash, activo FROM usuarios")}
        finally:
            con.close()

    def entrada(self, slug="napa"):
        return yaml.safe_load(self.ruta.read_text(encoding="utf-8"))["clientes"].get(slug)

    def registro(self):
        return [json.loads(x) for x in (self.raiz / "registro.jsonl").read_text(encoding="utf-8").splitlines()]

    def sv(self):
        sv = Servicio(self.carpeta, env={})
        self.addCleanup(sv.cerrar)
        return sv


class Clave(Base):
    def test_minimo_12_con_mensaje_claro(self):
        with self.assertRaises(SystemExit) as e:
            operador.Operador(self.ruta, "a" * 11)
        self.assertIn("12", str(e.exception))
        operador.Operador(self.ruta, "a" * 12)


class Pin(Base):
    def test_restablece_el_del_dueno_y_cierra_sus_sesiones(self):
        sv = self.sv()
        acc = panel_web.Acceso("s", panel_web.Usuarios(sv, panel_web.PANEL, {}))
        cookie = acc.cookie("Dueno").split(";")[0]
        self.assertTrue(acc.sesion(cookie))
        st, j = self.post("/api/clientes/napa/pin")
        self.assertEqual((st, j["usuario"]), (200, "Dueno"))
        self.assertRegex(j["pin"], r"^\d{6}$"); self.assertNotIn(j["pin"], panel_web.PIN_DEBILES)
        h = self.usuarios()["Dueno"][0]
        self.assertTrue(panel_web._coincide(j["pin"], None, h))  # valida con el hash del panel
        self.assertFalse(panel_web._coincide(PIN, None, h) and j["pin"] != PIN)
        self.assertIsNone(acc.sesion(cookie))  # sesión cerrada, como en el panel
        self.assertEqual(self.post("/api/clientes/napa/pin", {"pin": "739155"})[1]["pin"], "739155")
        self.assertTrue(panel_web._coincide("739155", None, self.usuarios()["Dueno"][0]))

    def test_sirve_para_cualquier_usuario(self):
        panel_web.guardar_usuario(self.sv(), {"nombre": "Juan", "rol": "repartidor", "pin": "615243"}, "prueba")
        st, j = self.post("/api/clientes/napa/pin", {"usuario": "Juan"})
        self.assertEqual((st, j["usuario"]), (200, "Juan"))
        self.assertTrue(panel_web._coincide(j["pin"], None, self.usuarios()["Juan"][0]))
        self.assertTrue(panel_web._coincide(PIN, None, self.usuarios()["Dueno"][0]))  # el del dueño no cambió

    def test_hostiles(self):
        antes = self.usuarios()
        for d in ({"pin": "123456"}, {"pin": "12345"}, {"pin": 482913}, {"pin": "abcdef"}, {"usuario": 5}, {"usuario": "a b"}):
            st, j = self.post("/api/clientes/napa/pin", d)
            self.assertEqual(st, 400, d); self.assertIsInstance(j["error"], str)
        self.assertEqual(self.post("/api/clientes/napa/pin", {"usuario": "Nadie"})[0], 404)
        self.assertEqual(self.post("/api/clientes/nadie/pin")[0], 404)
        for s in ("..%2Fnapa", "..", "NAPA", "a" * 41):
            self.assertEqual(self.post(f"/api/clientes/{s}/pin")[0], 400, s)
        self.assertEqual(self.post("/api/clientes/napa/pin", ck="operador=9999999999.falsa")[0], 401)
        self.assertEqual(self.usuarios(), antes)

    def test_pin_de_otra_persona_da_409(self):
        panel_web.guardar_usuario(self.sv(), {"nombre": "Juan", "rol": "repartidor", "pin": "615243"}, "prueba")
        self.assertEqual(self.post("/api/clientes/napa/pin", {"pin": "615243"})[0], 409)


class Equipo(Base):
    def test_lista_sin_hash_y_desactiva(self):
        sv = self.sv()
        panel_web.guardar_usuario(sv, {"nombre": "Juan", "rol": "repartidor", "pin": "615243"}, "prueba")
        self.assertEqual(self.get("/api/clientes/napa/equipo"), (200, [{"nombre": "Dueno", "rol": "dueno", "activo": True},
                                                                       {"nombre": "Juan", "rol": "repartidor", "activo": True}]))
        acc = panel_web.Acceso("s", panel_web.Usuarios(sv, panel_web.REPARTO, {}), "repartidor", "/repartidor")
        cookie = acc.cookie("Juan").split(";")[0]
        self.assertTrue(acc.sesion(cookie))
        self.assertEqual(self.post("/api/clientes/napa/equipo/activo", {"nombre": "Juan", "activo": False})[0], 200)
        self.assertIsNone(acc.sesion(cookie))
        self.assertEqual(self.get("/api/clientes/napa/equipo")[1][1], {"nombre": "Juan", "rol": "repartidor", "activo": False})
        st, j = self.post("/api/clientes/napa/equipo/activo", {"nombre": "Juan", "activo": True})  # reactivar: PIN nuevo, como en el panel
        self.assertEqual(st, 400); self.assertIn("PIN", j["error"])
        st, j = self.post("/api/clientes/napa/equipo/activo", {"nombre": "Juan", "activo": True, "pin": "905317"})
        self.assertEqual((st, j), (200, {"ok": True, "nombre": "Juan", "rol": "repartidor", "activo": True}))

    def test_debe_quedar_un_dueno_activo(self):
        st, j = self.post("/api/clientes/napa/equipo/activo", {"nombre": "Dueno", "activo": False})
        self.assertEqual(st, 400); self.assertIn("dueño", j["error"])
        self.assertEqual(self.usuarios()["Dueno"][1], 1)

    def test_hostiles(self):
        for d in ({"nombre": "Dueno", "activo": "no"}, {"nombre": "Dueno"}, {"activo": False}, {"nombre": ["Dueno"], "activo": False}):
            self.assertEqual(self.post("/api/clientes/napa/equipo/activo", d)[0], 400, d)
        self.assertEqual(self.post("/api/clientes/napa/equipo/activo", {"nombre": "Nadie", "activo": False})[0], 404)
        self.assertEqual(self.get("/api/clientes/nadie/equipo")[0], 404)
        self.assertEqual(self.get("/api/clientes/..%2Fnapa/equipo")[0], 400)
        self.assertEqual(self.get("/api/clientes/napa/equipo", ck="x")[0], 401)


class Ajustes(Base):
    def foto(self):
        return {f: (self.carpeta / f).read_bytes() if (self.carpeta / f).exists() else None for f in ("servicio.yaml", "tema.css")} | {"y": self.ruta.read_bytes()}

    def test_dominio_nombre_y_color(self):
        st, _ = self.post("/api/clientes/napa/ajustes", {"dominio": " Nueva.Example.com ", "nombre": "La Ñapa: 2", "color": "#1A73E8"})
        self.assertEqual(st, 200)
        cfg = yaml.safe_load((self.carpeta / "servicio.yaml").read_text(encoding="utf-8"))
        self.assertEqual((cfg["nombre"], cfg["marca"], cfg["modulos"], cfg["pedidos"]["impuesto_pct"]), ("La Ñapa: 2", "La Ñapa: 2", [], 7))  # conserva el resto
        self.assertIn("--primary:#1a73e8", (self.carpeta / "tema.css").read_text(encoding="utf-8"))
        c, = clientes.cargar(self.ruta)
        self.assertEqual(c.dominio, "nueva.example.com")
        self.assertEqual(self.post("/api/clientes/napa/ajustes", {"color": "#000000"})[0], 200)  # uno solo también
        self.assertEqual(clientes.cargar(self.ruta)[0].dominio, "nueva.example.com")

    def test_invalidos_no_tocan_nada(self):
        self.op.nuevo({"nombre": "Otra", "slug": "otra", "dominio": "otra.example.com", "pin": "739155"})
        antes = self.foto()
        malas = [{}, {"otro": 1}, {"nombre": "a" * 61}, {"nombre": "a\nmodulos: [x]"}, {"nombre": 5}, {"color": "red"}, {"color": "#fff;}"},
                 {"dominio": "https://x.com"}, {"dominio": "x.localhost"}, {"dominio": None}, {"color": "#1a73e8", "dominio": "mal"}]
        for d in malas:
            self.assertEqual(self.post("/api/clientes/napa/ajustes", d)[0], 400, d)
            self.assertEqual(self.foto(), antes, d)
        self.assertEqual(self.post("/api/clientes/napa/ajustes", {"dominio": "otra.example.com", "color": "#1a73e8"})[0], 409)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.post("/api/clientes/nadie/ajustes", {"color": "#1a73e8"})[0], 404)
        self.assertEqual(self.post("/api/clientes/..%2Fnapa/ajustes", {"color": "#1a73e8"})[0], 400)

    def test_fallo_a_mitad_deshace_todo(self):
        antes = self.foto()
        with unittest.mock.patch.object(self.op, "_guardar", side_effect=RuntimeError("disco lleno")):
            st, j = self.post("/api/clientes/napa/ajustes", {"dominio": "nueva.example.com", "nombre": "Otro", "color": "#1a73e8"})
        self.assertEqual(st, 500); self.assertNotIn("disco", j["error"])
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.registro()[-1]["resultado"], "error 500")


class Archivo(Base):
    def test_archivar_restaurar_archivar_eliminar(self):
        self.assertEqual(self.post("/api/clientes/napa/archivar", {"confirmar": "otro"})[0], 400)
        self.assertEqual(self.post("/api/clientes/napa/archivar")[0], 400)
        self.assertEqual(self.post("/api/clientes/napa/eliminar", {"confirmar": "napa"})[0], 409)  # sin archivar no se elimina
        self.assertTrue(self.carpeta.exists()); self.assertEqual(self.entrada()["activo"], True)

        self.assertEqual(self.post("/api/clientes/napa/archivar", {"confirmar": "napa"})[0], 200)
        e = self.entrada()
        self.assertEqual((e["activo"], str(e["archivado"])), (False, HOY))
        self.assertEqual(len(list((self.raiz / "backups" / "napa").glob("*.sqlite"))), 1)  # copia antes de archivar
        self.assertTrue((self.carpeta / "datos.sqlite").exists())  # no borra datos
        j = self.get("/api/clientes")[1]
        self.assertEqual((j["clientes"][0]["archivado"], j["clientes"][0]["activo"]), (HOY, False))
        self.assertEqual((j["resumen"]["activos"], j["resumen"]["archivados"]), (0, 1))
        self.assertEqual(self.post("/api/clientes/napa/archivar", {"confirmar": "napa"})[0], 409)
        self.assertEqual(self.post("/api/clientes/napa/pausa", {"activo": True})[0], 409)  # un archivado solo vuelve con restaurar
        self.assertEqual(self.entrada()["activo"], False)

        self.assertEqual(self.post("/api/clientes/napa/restaurar")[0], 200)
        self.assertEqual(self.entrada(), {"carpeta": "servicios/napa", "dominio": "napa.example.com", "activo": True})
        j = self.get("/api/clientes")[1]
        self.assertEqual((j["clientes"][0]["archivado"], j["resumen"]["activos"], j["resumen"]["archivados"]), (None, 1, 0))
        self.assertEqual(self.post("/api/clientes/napa/restaurar")[0], 409)

        self.assertEqual(self.post("/api/clientes/napa/archivar", {"confirmar": "napa"})[0], 200)
        for d in ({}, {"confirmar": "otro"}, {"confirmar": ["napa"]}):
            self.assertEqual(self.post("/api/clientes/napa/eliminar", d)[0], 400, d)
        self.assertTrue((self.carpeta / "datos.sqlite").exists())
        (self.carpeta / "comprobantes").mkdir(); (self.carpeta / "comprobantes" / "a.jpg").write_bytes(b"foto")
        (self.carpeta / "tema.css").write_text("x", encoding="utf-8")
        self.assertEqual(self.post("/api/clientes/napa/eliminar", {"confirmar": "napa"})[0], 200)
        self.assertEqual(list((self.raiz / "servicios").iterdir()), [])  # carpeta, BD, -wal/-shm, comprobantes y tema: nada
        self.assertIsNone(self.entrada()); self.assertEqual(clientes.cargar(self.ruta), [])
        final = self.raiz / "backups" / "napa" / f"ELIMINADO-{HOY}.sqlite"
        con = sqlite3.connect(final)
        self.assertEqual(con.execute("SELECT nombre FROM usuarios").fetchall(), [("Dueno",)])
        con.close()
        self.assertEqual(self.post("/api/clientes/napa/eliminar", {"confirmar": "napa"})[0], 404)
        self.assertEqual([(r["accion"], r["resultado"]) for r in self.registro() if r["resultado"] == "ok"][-4:],
                         [("archivar", "ok"), ("restaurar", "ok"), ("archivar", "ok"), ("eliminar", "ok")])
        self.assertEqual(self.registro()[-2], {"fecha": self.registro()[-2]["fecha"], "accion": "eliminar", "slug": "napa", "ip": "127.0.0.1", "resultado": "ok"})

    def test_eliminar_no_sale_de_servicios(self):
        fuera = self.raiz / "otra" / "x"; fuera.mkdir(parents=True)
        (fuera / "servicio.yaml").write_text("nombre: x\n", encoding="utf-8")
        y = yaml.safe_load(self.ruta.read_text(encoding="utf-8"))
        y["clientes"]["fuera"] = {"carpeta": "otra/x", "dominio": "fuera.example.com", "activo": False, "archivado": HOY}
        self.ruta.write_text(yaml.safe_dump(y), encoding="utf-8")
        self.assertEqual(self.post("/api/clientes/fuera/eliminar", {"confirmar": "fuera"})[0], 400)
        self.assertTrue((fuera / "servicio.yaml").exists()); self.assertIsNotNone(self.entrada("fuera"))
        for s in ("..", "..%2F..", "%2e%2e", "napa%2F.."):
            self.assertEqual(self.post(f"/api/clientes/{s}/eliminar", {"confirmar": ".."})[0], 400, s)
        self.assertTrue(self.carpeta.exists())

    def test_archivado_da_404_en_alojar(self):
        srv = web.crear_alojamiento(clientes.cargar(self.ruta), 0, ruta=self.ruta)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: [sv.cerrar() for sv in srv.servicios.values()])
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        puerto = srv.server_address[1]
        self.assertEqual(pedir(puerto, "GET", "/config", host="napa.example.com")[0], 200)
        self.assertEqual(self.post("/api/clientes/napa/archivar", {"confirmar": "napa"})[0], 200)
        self.assertEqual(pedir(puerto, "GET", "/config", host="napa.example.com")[0], 404)

    def test_clientes_py_admite_archivado(self):
        y = yaml.safe_load(self.ruta.read_text(encoding="utf-8"))
        y["clientes"]["napa"]["archivado"] = HOY  # aunque diga activo: true, un archivado no se sirve
        self.ruta.write_text(yaml.safe_dump(y), encoding="utf-8")
        c, = clientes.cargar(self.ruta)
        self.assertEqual((c.activo, c.archivado), (False, dt.date.today()))
        y["clientes"]["napa"]["archivado"] = "ayer"
        self.ruta.write_text(yaml.safe_dump(y), encoding="utf-8")
        with self.assertRaises(ValueError):
            clientes.cargar(self.ruta)


class Registro(Base):
    def test_una_linea_por_accion_sin_pins(self):
        p = self.srv.server_port
        pedir(p, "POST", "/api/entrar", {"clave": "mala-" + PIN})
        pin = self.post("/api/clientes/napa/pin")[1]["pin"]
        self.post("/api/clientes/napa/pin", {"pin": "739155"})
        self.post("/api/clientes", {"nombre": "Dos", "slug": "dos", "dominio": "dos.example.com", "pin": "264815"})
        self.get("/api/clientes/napa/equipo")
        for ruta, d in (("equipo/activo", {"nombre": "Dueno", "activo": False}), ("ajustes", {"color": "#1a73e8"}), ("config", {"precio": 30}),
                        ("pago", {"meses": 1}), ("prueba", {"accion": "iniciar", "dias": 7}), ("respaldo", {}), ("pausa", {"activo": False}),
                        ("archivar", {"confirmar": "napa"}), ("restaurar", {})):
            self.post(f"/api/clientes/napa/{ruta}", d)
        self.post("/api/clientes/..%2Fx/pausa", {"activo": False})
        r = self.registro()
        self.assertEqual([(x["accion"], x["slug"], x["resultado"]) for x in r],
                         [("login", "", "ok"), ("login", "", "fallido"), ("pin", "napa", "ok"), ("pin", "napa", "ok"), ("alta", "dos", "ok"),
                          ("equipo", "napa", "error 400"), ("ajustes", "napa", "ok"), ("config", "napa", "ok"), ("pago", "napa", "ok"),
                          ("prueba", "napa", "ok"), ("backup", "napa", "ok"), ("pausa", "napa", "ok"), ("archivar", "napa", "ok"),
                          ("restaurar", "napa", "ok"), ("pausa", "", "error 400")])
        for x in r:
            self.assertEqual(set(x), {"fecha", "accion", "slug", "ip", "resultado"})
            self.assertEqual(x["ip"], "127.0.0.1"); dt.datetime.fromisoformat(x["fecha"])
        st, j = self.get("/api/registro?n=3")
        self.assertEqual((st, [x["accion"] for x in j]), (200, ["pausa", "restaurar", "archivar"]))  # más reciente primero
        self.assertEqual(len(self.get("/api/registro")[1]), len(r))
        self.assertEqual(len(self.get("/api/registro?n=9999")[1]), len(r))  # tope 500, no falla
        for mal in ("abc", "-1", "0", "1.5"):
            self.assertEqual(self.get(f"/api/registro?n={mal}")[0], 400, mal)
        self.assertEqual(self.get("/api/registro", ck="x")[0], 401)
        # ni PINs ni hashes: en el registro, en las respuestas (salvo el PIN nuevo en la suya) ni en claro en disco
        crudo = (self.raiz / "registro.jsonl").read_text(encoding="utf-8")
        for secreto in (PIN, pin, "739155", "264815", "pbkdf2", "pin_hash", CLAVE):
            self.assertNotIn(secreto, crudo, secreto)
        todo = "\n".join(self.textos)
        self.assertNotIn("pbkdf2", todo); self.assertNotIn("pin_hash", todo); self.assertNotIn(PIN, todo)
        self.assertEqual(todo.count(pin), 1)
        for f in self.raiz.rglob("*"):
            if f.is_file() and not f.name.startswith("datos.sqlite"):
                self.assertNotIn(pin.encode(), f.read_bytes(), f.name)

    def test_rota_al_pasar_del_tope(self):
        with unittest.mock.patch.object(operador, "MAX_REGISTRO", 200):
            for _ in range(6):
                self.post("/api/clientes/napa/pausa", {"activo": True})
        self.assertTrue((self.raiz / "registro.jsonl.1").exists())
        self.assertLess((self.raiz / "registro.jsonl").stat().st_size, 400)
        self.assertGreater(len(self.get("/api/registro")[1]), len(self.registro()))  # lee también el rotado


if __name__ == "__main__":
    unittest.main()
