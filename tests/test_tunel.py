"""Detrás del túnel de Cloudflare. Ejecutar: python -m unittest tests.test_tunel"""
import http.client
import json
import os
import unittest
import unittest.mock

from tests.test_web import Base

PIN = "483920"
CF = {"CF-Connecting-IP": "8.8.8.8", "X-Forwarded-Proto": "https"}


class Tunel(Base):
    def entorno(self, **env):
        p = unittest.mock.patch.dict(os.environ, {"PANEL_PIN": PIN, "PANEL_PIN_PERSONAL": "", "PANEL_PIN_HASH": "", "TUNEL": "", "PANEL_PUBLICO": "", **env})
        p.start()
        self.addCleanup(p.stop)
        self.arrancar()

    def post(self, ruta, cuerpo, cab):
        c = http.client.HTTPConnection("127.0.0.1", self.puerto, timeout=10)
        c.request("POST", ruta, json.dumps(cuerpo), {"Content-Type": "application/json", **cab})
        r = c.getresponse()
        r.read()
        c.close()
        return r

    def test_sin_tunel_cabeceras_cf_se_ignoran(self):
        self.entorno()
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, CF).status, 200)  # peer loopback = LAN

    def test_panel_publico_cerrado_por_defecto(self):
        self.entorno(TUNEL="1")
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, CF).status, 404)
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, {}).status, 200)  # local sin cabecera

    def test_panel_publico_con_pin_cookie_secure_y_bloqueo_por_ip_real(self):
        self.entorno(TUNEL="1", PANEL_PUBLICO="1")
        r = self.post("/panel/entrar", {"pin": PIN}, CF)
        self.assertEqual(r.status, 200)
        self.assertIn("Secure", r.getheader("Set-Cookie"))
        otro = {**CF, "CF-Connecting-IP": "1.1.1.1"}
        for _ in range(5):
            self.assertEqual(self.post("/panel/entrar", {"pin": "000111"}, CF).status, 401)
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, CF).status, 429)  # esa IP bloqueada
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, otro).status, 200)  # otra IP no

    def test_cf_ip_falsa_no_vale(self):
        self.entorno(TUNEL="1")
        self.assertEqual(self.post("/panel/entrar", {"pin": PIN}, {"CF-Connecting-IP": "basura"}).status, 200)  # cae a loopback

    def test_cookie_cliente_secure_solo_con_https(self):
        self.entorno(TUNEL="1")
        cuerpo = {"nombre": "Ana", "telefono": "50760001111", "consentimiento": True, "version_aviso": "v1"}
        self.assertIn("Secure", self.post("/entrar", cuerpo, CF).getheader("Set-Cookie"))
        self.assertNotIn("Secure", self.post("/entrar", cuerpo, {"CF-Connecting-IP": "8.8.8.8"}).getheader("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
