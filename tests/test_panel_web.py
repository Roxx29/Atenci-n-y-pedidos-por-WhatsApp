"""Panel web /panel. Ejecutar: python -m unittest tests.test_panel_web"""
import os
import threading
import unittest
import unittest.mock

from motor import herramientas, panel_web, web
from motor.nucleo import Servicio
from tests.test_web import CID, PEDIDO, TEL, Base

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
        self.assertEqual((p["estado"], p["pagado_por"]), ("pagado", "Dueno"))  # nombre de la persona
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
        self.assertEqual(e[0]["telefono"], TEL)  # completo mientras está abierto: el restaurante tiene que poder llamarlo
        self.assertEqual(self.js("POST", f"/panel/escalado/{eid}/atendido", {}, cookie=ck)[0], 200)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[1]["escalados"], [])

    def test_dueno_gestiona_y_personal_403(self):
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/usuario", {"nombre": "Pedro", "rol": "repartidor", "pin": "739104"}, cookie=ck)
        self.assertEqual((st, d["usuario"]), (200, {"nombre": "Pedro", "rol": "repartidor", "activo": True}))
        self.assertEqual(self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "739104"}, cookie=ck)[0], 409)  # PIN repetido
        self.assertEqual(self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)[0], 200)
        self.assertNotIn("pbkdf2", str(self.js("GET", "/panel/dueno/usuarios", cookie=ck)[1]))
        st, d, _ = self.js("POST", "/repartidor/entrar", {"pin": "739104"})
        self.assertEqual((st, d["nombre"]), (200, "Pedro"))
        self.assertIn("Pedro", self.js("GET", "/panel/estado", cookie=ck)[1]["repartidores"])
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"id": "sancocho", "precio": 6, "disponible": False}, cookie=ck)[0], 200)
        m = next(m for m in self.js("GET", "/menu")[1] if m["id"] == "sancocho")
        self.assertEqual((m["precio"], m["disponible"]), (6, False))
        st, ck2 = self.login("918273")
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck2)[0], 200)
        for m, r, cuerpo in (("GET", "/panel/dueno/usuarios", None), ("POST", "/panel/dueno/plato", {"id": "sancocho", "precio": 0})):
            self.assertEqual(self.js(m, r, cuerpo, cookie=ck2)[0], 403)
        self.assertEqual(self.js("GET", "/panel/dueno/nada", cookie=ck)[0], 404)
        self.assertIn(("Ana", "login"), [(a["usuario"], a["accion"]) for a in self.sv.listar("auditoria")])

    def test_menu_en_fila_agotado_del_personal_y_borrar(self):
        _, ck = self.login()
        st, d, _ = self.js("POST", "/panel/dueno/plato", {"nombre": "Arroz con Pollo", "precio": "4.50", "categoria": "platos"}, cookie=ck)  # sin id: sale del nombre
        self.assertEqual((st, d["plato"]["id"], d["plato"]["precio"]), (200, "arroz-con-pollo", 4.5))
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"nombre": "Arroz con pollo", "precio": 5}, cookie=ck)[1]["plato"]["id"], "arroz-con-pollo-2")  # no pisa al otro
        self.assertEqual(self.js("POST", "/panel/dueno/plato", {"precio": 5}, cookie=ck)[0], 409)  # sin id ni nombre
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        self.assertIn("sancocho", [m["id"] for m in self.js("GET", "/panel/menu", cookie=ck2)[1]["menu"]])  # el personal ve el menú
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "sancocho", "disponible": False}, cookie=ck2)[0], 200)  # y marca lo agotado
        self.assertFalse(next(m for m in self.js("GET", "/menu")[1] if m["id"] == "sancocho")["disponible"])
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "nada", "disponible": False}, cookie=ck2)[0], 404)
        self.assertEqual(self.js("POST", "/panel/agotado", {"plato": "sancocho", "disponible": "no"}, cookie=ck2)[0], 400)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "sancocho"}, cookie=ck2)[0], 403)  # borrar es del dueño
        self.assertEqual(self.js("GET", "/panel/menu")[0], 401)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "arroz-con-pollo-2"}, cookie=ck)[0], 200)
        self.assertEqual(self.js("POST", "/panel/dueno/plato_borrar", {"plato": "arroz-con-pollo-2"}, cookie=ck)[0], 404)
        self.assertNotIn("arroz-con-pollo-2", [m["id"] for m in self.js("GET", "/menu")[1]])
        self.assertIn("plato_borrar:arroz-con-pollo-2", [a["accion"] for a in self.sv.listar("auditoria")])

    def test_borrar_persona(self):
        _, ck = self.login()
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Ana"}, cookie=ck)[0], 200)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck2)[0], 401)  # su sesión deja de valer
        self.assertEqual(self.login("918273")[0], 401)
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Ana"}, cookie=ck)[0], 404)
        self.assertEqual(self.js("POST", "/panel/dueno/usuario_borrar", {"nombre": "Dueno"}, cookie=ck)[0], 409)  # queda al menos un dueño
        self.assertIn(("Dueno", "usuario_borrar:Ana"), [(a["usuario"], a["accion"]) for a in self.sv.listar("auditoria")])

    def test_paginas_con_marca_y_tema(self):
        self.sv.cfg["marca"] = "Café <Luna>"
        panel_web.guardar_usuario(self.sv, {"nombre": "Juan", "rol": "repartidor", "pin": "739104"}, "prueba")  # sin repartidores /repartidor no existe
        srv = web.crear_servidor(self.sv, 0, "secreto-de-prueba")  # las páginas se arman al crear el servidor
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close()))
        self.puerto = srv.server_address[1]
        for ruta in ("/", "/panel", "/repartidor"):
            r, raw = self.req("GET", ruta)
            h = raw.decode()
            self.assertEqual(r.status, 200, ruta)
            self.assertIn("Café &lt;Luna&gt;", h)  # nombre de servicio.yaml, escapado
            self.assertNotIn("DeliFood", h)
            self.assertIn("--primary:", h)  # tema incrustado
            self.assertNotIn("estatico/tema.css", h)

    def test_dias_especiales_y_cierre_temporal(self):
        import datetime as dt
        _, ck = self.login()
        hoy = self.sv.ahora().date()
        man, pasado = hoy + dt.timedelta(days=1), hoy - dt.timedelta(days=1)
        todo = {d: [["00:00", "23:59"]] for d in herramientas.DIAS}
        ex = {f"{man}": [], f"{hoy + dt.timedelta(days=2)}": [["09:00", "10:00"]]}
        st, d, _ = self.js("POST", "/panel/dueno/ajustes", {"horario": todo, "excepciones": ex}, cookie=ck)
        self.assertEqual((st, d["ajustes"]["excepciones"]), (200, ex))
        # el día cerrado no abre aunque el día de semana sí; el de turnos propios solo esos
        self.assertEqual(herramientas._turnos(self.sv, man), [])
        self.assertEqual(len(herramientas._turnos(self.sv, hoy + dt.timedelta(days=2))), 1)
        self.assertEqual(len(herramientas._turnos(self.sv, hoy + dt.timedelta(days=3))), 1)
        # guardar solo el horario conserva los días especiales
        self.js("POST", "/panel/dueno/ajustes", {"horario": todo}, cookie=ck)
        self.assertEqual(self.sv.cfg["horario"]["excepciones"], ex)
        for malo in ({"excepciones": {str(pasado): []}}, {"excepciones": {"2026-02-31": []}}, {"excepciones": {str(man): [["15:00", "11:00"]]}},
                     {"excepciones": {str(hoy + dt.timedelta(days=i)): [] for i in range(61)}}, {"excepciones": []}):
            self.assertEqual(self.js("POST", "/panel/dueno/ajustes", malo, cookie=ck)[0], 409, malo)
        # cierre temporal: se reabre solo al llegar la hora
        h = f"{self.sv.ahora() + dt.timedelta(hours=1):%Y-%m-%d %H:%M}"
        self.assertEqual(self.js("POST", "/panel/tienda", {"cerrado": True, "hasta": h}, cookie=ck)[1]["cerrado_hasta"], h)
        self.assertTrue(herramientas.cerrado_ahora(self.sv))
        self.assertEqual(self.js("GET", "/panel/dueno/ajustes", cookie=ck)[1]["ajustes"]["cerrado_hasta"], h)
        for malo in ({"cerrado": True, "hasta": f"{pasado} 10:00"}, {"cerrado": True, "hasta": "x"}, {"cerrado": False, "hasta": h}):
            self.assertEqual(self.js("POST", "/panel/tienda", malo, cookie=ck)[0], 400, malo)
        self.sv.poner_ajuste("cerrado_hasta", f"{pasado} 10:00")
        self.assertFalse(herramientas.cerrado_ahora(self.sv))
        self.assertFalse(self.sv.cfg["cerrado"])

    def test_ajustes_generales_tienda_y_promos(self):
        _, ck = self.login()
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        dias = {d: [["11:00", "15:00"], ["18:00", "22:00"]] for d in herramientas.DIAS}
        dias["lun"] = []
        cuerpo = {"marca": "La Fonda", "telefono": "6123-4567", "anuncio": "Hoy 2x1 en chichas", "horario": dias, "pago": {"yappy": "@lafonda", "transferencia": ""}}
        self.assertEqual(self.js("POST", "/panel/dueno/ajustes", cuerpo, cookie=ck2)[0], 403)  # solo el dueño
        st, d, _ = self.js("POST", "/panel/dueno/ajustes", cuerpo, cookie=ck)
        self.assertEqual((st, d["ajustes"]["horario"]["lun"], d["ajustes"]["telefono"]), (200, [], "6123-4567"))
        c = self.js("GET", "/config")[1]
        self.assertEqual((c["marca"], c["telefono"], c["anuncio"], c["cerrado"], c["horario"]["por_dia"]["mar"][1]), ("La Fonda", "6123-4567", "Hoy 2x1 en chichas", False, ["18:00", "22:00"]))
        self.assertIn("La Fonda", self.req("GET", "/")[1].decode())  # el nombre nuevo sale sin reiniciar
        self.assertEqual(self.sv.cfg["pago"]["yappy"], "@lafonda")
        for malo in ({"horario": {"mar": [["15:00", "11:00"]]}}, {"horario": {"mar": [["11:00", "15:00"], ["14:00", "20:00"]]}}, {"horario": {"mar": [["25:00", "26:00"]]}},
                     {"telefono": "abc"}, {"marca": ""}, {"cerrado": "si"}):
            self.assertEqual(self.js("POST", "/panel/dueno/ajustes", malo, cookie=ck)[0], 409, malo)
        # cerrar la tienda: también el personal; se ve en el panel y en la web
        self.assertEqual(self.js("POST", "/panel/tienda", {"cerrado": True}, cookie=ck2)[0], 200)
        self.assertTrue(self.js("GET", "/panel/estado", cookie=ck2)[1]["cerrado"])
        self.assertTrue(self.js("GET", "/config")[1]["cerrado"])
        self.assertEqual(self.js("POST", "/panel/tienda", {"cerrado": False}, cookie=ck2)[0], 200)
        self.assertIn(("Ana", "tienda_cerrar"), [(a["usuario"], a["accion"]) for a in self.sv.listar("auditoria")])
        # promoción = plato de la categoría promociones con descripción
        st, d, _ = self.js("POST", "/panel/dueno/plato", {"nombre": "Combo almuerzo", "precio": "6.50", "categoria": "promociones", "descripcion": "sancocho + chicha"}, cookie=ck)
        self.assertEqual((st, d["plato"]["descripcion"]), (200, "sancocho + chicha"))
        self.assertIn("combo-almuerzo | Combo almuerzo (sancocho + chicha) | B/. 6.50 | promociones", herramientas.texto_menu(self.sv.menu))

    def test_modulo_estadisticas(self):
        _, ck = self.login()
        self.js("POST", "/panel/dueno/usuario", {"nombre": "Ana", "rol": "personal", "pin": "918273"}, cookie=ck)
        _, ck2 = self.login("918273")
        self.sv.cfg.pop("modulos", None)  # sin contratar: no hay datos ni pestaña
        self.assertEqual(self.js("POST", "/panel/dueno/estadisticas", {}, cookie=ck)[0], 404)
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[1]["modulos"], [])
        self.sv.cfg["modulos"] = ["estadisticas", "otro"]
        self.assertEqual(self.js("GET", "/panel/estado", cookie=ck)[1]["modulos"], ["estadisticas"])
        self.assertEqual(self.js("POST", "/panel/dueno/estadisticas", {}, cookie=ck2)[0], 403)  # solo el dueño
        for _ in range(2):
            pid = self.pedido()
            for est in ("pagado", "en_preparacion", "listo", "entregado"):
                herramientas.marcar_pedido(self.sv, pid, est)
        st, d, _ = self.js("POST", "/panel/dueno/estadisticas", {"dias": 7}, cookie=ck)
        self.assertEqual((st, d["dias"], d["pedidos"], len(d["por_dia"])), (200, 7, 2, 7))
        self.assertEqual(sum(x["pedidos"] for x in d["por_dia"]), 2)
        self.assertEqual(d["platos"][0]["cantidad"], sum(l["cantidad"] for l in self.sv.listar("pedido")[0]["lineas"]) * 2)
        self.assertNotIn(TEL, str(d))  # nada de teléfonos
        self.assertEqual(self.js("POST", "/panel/dueno/estadisticas", {"dias": 5}, cookie=ck)[1]["dias"], 30)

    def test_efectivo_contra_entrega_y_cobro_del_repartidor(self):
        sv = self.sv
        ent = {**PEDIDO, "modalidad": "entrega", "zona": "Chitré", "direccion": "Calle 4"}
        pid = herramientas.crear_pedido(sv, telefono=TEL, **ent)["pedido_id"]
        herramientas.confirmar_pedido(sv, pid)
        total = sv.listar("pedido")[0]["total_c"]
        herramientas.pagar_efectivo(sv, pid)  # pasa a cocina sin esperar pago
        p = sv.listar("pedido")[0]
        self.assertEqual((p["estado"], p["metodo"], p["por_cobrar"]), ("pagado", "efectivo", True))
        self.assertIn("EN EFECTIVO", [a["texto"] for a in sv.listar("aviso")][-1])
        panel_web.asignar(sv, pid, "Juan", {"Juan"})
        for est in ("en_preparacion", "listo"):
            panel_web.marcar(sv, pid, est, "Ana")
        v = panel_web.pedidos_repartidor(sv, "Juan")[0]
        self.assertEqual((v["pagado"], v["cobrar"]), (False, herramientas.dinero(total)))
        panel_web.marcar_repartidor(sv, "Juan", pid, "en_camino")
        with self.assertRaisesRegex(ValueError, "Cobra"):  # sin confirmar el cobro no se entrega
            panel_web.marcar_repartidor(sv, "Juan", pid, "entregado")
        panel_web.marcar_repartidor(sv, "Juan", pid, "entregado", True)
        p = sv.listar("pedido")[0]
        self.assertEqual((p["estado"], p["por_cobrar"], p["cobrado_por"], p["cobrado_c"]), ("entregado", False, "Juan", total))
        self.assertIn(f"Juan cobró {herramientas.dinero(total)} en efectivo", [a["texto"] for a in sv.listar("aviso")][-1])
        # recoger no admite efectivo; apagado desde Configuración tampoco, y la web lo sabe
        rec = herramientas.crear_pedido(sv, telefono=TEL, **PEDIDO)["pedido_id"]
        herramientas.confirmar_pedido(sv, rec)
        with self.assertRaisesRegex(ValueError, "entrega a domicilio"):
            herramientas.pagar_efectivo(sv, rec)
        _, ck = self.login()
        self.assertTrue(self.js("GET", "/config")[1]["efectivo"])
        self.assertEqual(self.js("POST", "/panel/dueno/ajustes", {"pago": {"efectivo": False}}, cookie=ck)[0], 200)
        self.assertFalse(self.js("GET", "/config")[1]["efectivo"])
        with self.assertRaisesRegex(ValueError, "no aceptamos efectivo"):
            herramientas.pagar_efectivo(sv, rec)
        self.assertEqual(self.js("POST", "/panel/dueno/ajustes", {"pago": {"efectivo": "si"}}, cookie=ck)[0], 409)

    def test_web_cliente_paga_en_efectivo(self):
        ck = self.entrar()[2]
        pid = herramientas.crear_pedido(self.sv, telefono=CID, **{**PEDIDO, "modalidad": "entrega", "zona": "Chitré", "direccion": "Calle 4"})["pedido_id"]
        herramientas.confirmar_pedido(self.sv, pid)
        self.assertEqual(self.js("POST", "/efectivo", {"pedido": pid})[0], 401)
        self.assertEqual(self.js("POST", "/efectivo", {"pedido": 9999}, cookie=ck)[0], 404)
        st, d, _ = self.js("POST", "/efectivo", {"pedido": pid}, cookie=ck)
        self.assertEqual((st, d["pedido"]["estado"], d["pedido"]["por_cobrar"]), (200, "pagado", True))
        self.assertEqual(self.js("POST", "/efectivo", {"pedido": pid}, cookie=ck)[0], 409)  # ya no está esperando pago

    def test_config_publica_y_pago_solo_con_sesion(self):
        st, d, _ = self.js("GET", "/config")
        self.assertEqual((st, d["marca"], d["impuesto_pct"]), (200, "DeliFood", self.sv.cfg["pedidos"]["impuesto_pct"]))
        self.assertEqual(d["envio"], {"": 2.5})  # costo fijo de la configuración de prueba
        self.assertEqual(web.config_publica(Servicio("servicios/restaurante-pedidos", db=":memory:"))["envio"], {"Chitré": 2.0, "La Villa de Los Santos": 3.0})
        self.assertNotIn("pago", d)
        self.assertEqual(self.js("GET", "/config", cookie=self.entrar()[2])[1]["pago"]["yappy"], self.sv.cfg["pago"]["yappy"])


if __name__ == "__main__":
    unittest.main()
