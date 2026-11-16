"""Ruta óptima del repartidor (funciones puras). Ejecutar: python -m unittest tests.test_ruta"""
import datetime as dt
import itertools
import random
import unittest

from motor import ruta

PR = ruta.parametros({})
REST = (7.96, -80.43)


def punto(rnd):
    return REST[0] + rnd.uniform(-0.05, 0.05), REST[1] + rnd.uniform(-0.05, 0.05)


def ref_coste(orden, origen, pts, pesos, pr):
    """Referencia independiente: suma ponderada de las horas de llegada."""
    t, total, antes = 0.0, 0.0, origen
    for k, i in enumerate(orden):
        t += ruta.minutos(antes, pts[i], pr) + (pr["min_por_parada"] if k else 0)
        total += pesos[i] * t
        antes = pts[i]
    return total


def caso(rnd, n):
    pts, pesos = [punto(rnd) for _ in range(n)], [1 + rnd.random() for _ in range(n)]
    return pts, pesos, [[ruta.minutos(a, b, PR) for b in pts] for a in pts], [ruta.minutos(REST, p, PR) for p in pts]


def paradas(*xs):
    return [{"id": i, "lat": la, "lon": lo, "espera_min": e} for i, (la, lo, e) in enumerate(xs, 1)]


class Orden(unittest.TestCase):
    def test_optimo_contra_fuerza_bruta(self):
        rnd = random.Random(7)
        for _ in range(40):
            n = rnd.randint(1, 7)
            pts, pesos, m, o = caso(rnd, n)
            orden = ruta.ordenar(m, o, pesos, PR["min_por_parada"])
            self.assertEqual(sorted(orden), list(range(n)))
            mejor = min(ref_coste(p, REST, pts, pesos, PR) for p in itertools.permutations(range(n)))
            self.assertAlmostEqual(ref_coste(orden, REST, pts, pesos, PR), mejor, places=6)

    def test_dos_opt_no_peor_que_vecino(self):
        rnd = random.Random(3)
        mejoras = 0
        for _ in range(15):
            pts, pesos, m, o = caso(rnd, 12)
            cv = ref_coste(ruta.vecino(m, o), REST, pts, pesos, PR)
            orden = ruta.ordenar(m, o, pesos, PR["min_por_parada"])  # más de 8: vecino + 2-opt
            self.assertEqual(sorted(orden), list(range(12)))
            c = ref_coste(orden, REST, pts, pesos, PR)
            self.assertLessEqual(c, cv + 1e-6)
            mejoras += c < cv - 1e-6
        self.assertGreater(mejoras, 0)  # el 2-opt hace algo


class Calcular(unittest.TestCase):
    def test_vacia(self):
        self.assertEqual(ruta.calcular([], REST, PR), {"origen": {"lat": REST[0], "lon": REST[1]}, "total_min": None, "paradas": []})
        self.assertEqual(ruta.calcular([], None, PR), {"origen": None, "total_min": None, "paradas": []})

    def test_un_solo_pedido(self):
        r = ruta.calcular(paradas((7.97, -80.43, 0)), REST, PR)
        km = ruta.distancia_km(REST, (7.97, -80.43)) * 1.35
        self.assertEqual(r["paradas"], [{"id": 1, "n": 1, "lat": 7.97, "lon": -80.43, "eta_min": round(km / 25 * 60), "dist_m": round(km * 1000), "fuera_de_viaje": False}])
        self.assertEqual(r["total_min"], r["paradas"][0]["eta_min"])

    def test_cercano_primero_y_eta_creciente(self):
        r = ruta.calcular(paradas((8.00, -80.43, 0), (7.97, -80.43, 0), (7.98, -80.43, 0), (7.99, -80.43, 0)), REST, PR)
        self.assertEqual([p["id"] for p in r["paradas"]], [2, 3, 4, 1])
        self.assertEqual([p["n"] for p in r["paradas"]], [1, 2, 3, 4])
        etas, ds = [p["eta_min"] for p in r["paradas"]], [p["dist_m"] for p in r["paradas"]]
        self.assertEqual(etas, sorted(set(etas)))  # estrictamente creciente
        self.assertEqual(ds, sorted(ds))  # distancia acumulada
        self.assertEqual(r["total_min"], etas[-1])
        self.assertGreaterEqual(etas[1] - etas[0], PR["min_por_parada"])  # cuenta el tiempo de la parada anterior

    def test_minimiza_llegadas_no_distancia(self):
        # dos clientes juntos a un lado y uno un poco más cerca al otro: primero los dos (menos gente esperando), aunque el camino total sea más largo
        r = ruta.calcular(paradas((7.96, -80.42, 0), (7.96, -80.442, 0), (7.96, -80.443, 0)), REST, {**PR, "min_por_parada": 0})
        self.assertEqual([p["id"] for p in r["paradas"]], [2, 3, 1])

    def test_sin_ubicacion_al_final_por_antiguedad(self):
        ps = paradas((None, None, 5), (7.97, -80.43, 0), (None, None, 40))
        r = ruta.calcular(ps, REST, PR)
        self.assertEqual([(p["id"], p["n"]) for p in r["paradas"]], [(2, 1), (3, 2), (1, 3)])
        self.assertEqual([(p["lat"], p["eta_min"], p["dist_m"]) for p in r["paradas"][1:]], [(None, None, None)] * 2)
        self.assertEqual(r["total_min"], r["paradas"][0]["eta_min"])
        solo = ruta.calcular(ps[::2], None, PR)
        self.assertEqual((solo["origen"], solo["total_min"], [p["id"] for p in solo["paradas"]]), (None, None, [3, 1]))

    def test_tope_por_viaje(self):
        r = ruta.calcular(paradas(*[(7.96 + i / 1000, -80.43, 0) for i in range(1, 9)]), REST, PR)
        self.assertEqual([p["fuera_de_viaje"] for p in r["paradas"]], [False] * 6 + [True] * 2)
        self.assertEqual(len(r["paradas"]), 8)  # se calcula la ruta de todos

    def test_cambia_al_moverse_el_repartidor(self):
        ps = paradas((7.97, -80.43, 0), (8.00, -80.43, 0))
        self.assertEqual([p["id"] for p in ruta.calcular(ps, REST, PR)["paradas"]], [1, 2])
        self.assertEqual([p["id"] for p in ruta.calcular(ps, (8.001, -80.43), PR)["paradas"]], [2, 1])

    def test_sin_origen_sale_de_la_parada_mas_antigua(self):
        r = ruta.calcular(paradas((7.97, -80.43, 0), (8.00, -80.43, 30)), None, PR)
        self.assertEqual(r["origen"], {"lat": 8.00, "lon": -80.43})
        self.assertEqual([(p["id"], p["eta_min"]) for p in r["paradas"]][0], (2, 0))

    def test_prioridad_por_antiguedad(self):
        ps = paradas((7.9690, -80.43, 0), (7.9505, -80.43, 120))  # el 1 queda algo más cerca; el 2 lleva dos horas esperando
        self.assertEqual([p["id"] for p in ruta.calcular(ps, REST, {**PR, "peso_espera": 0})["paradas"]], [1, 2])
        self.assertEqual([p["id"] for p in ruta.calcular(ps, REST, PR)["paradas"]], [2, 1])

    def test_parametros(self):
        self.assertEqual(PR, {"factor_calle": 1.35, "velocidad_kmh": 25, "min_por_parada": 3, "max_por_viaje": 6, "peso_espera": 0.02})
        cfg = {"reparto": {"velocidad_kmh": 40, "min_por_parada": 0, "factor_calle": 0, "max_por_viaje": "x", "peso_espera": -1}}
        self.assertEqual(ruta.parametros(cfg), {"factor_calle": 1.35, "velocidad_kmh": 40, "min_por_parada": 0, "max_por_viaje": 6, "peso_espera": 0.02})

    def test_de_pedidos(self):
        ahora = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)
        hace = lambda m: (ahora - dt.timedelta(minutes=m)).isoformat()
        peds = [{"id": 1, "ubicacion": {"lat": 7.97, "lon": -80.43}, "listo_at": hace(10), "en_camino_at": hace(1)},
                {"id": 2, "ubicacion": {"lat": None, "lon": None, "enlace": "https://maps.app.goo.gl/x"}, "estado_at": hace(5)},
                {"id": 3, "ubicacion": None, "creado_at": hace(50)},
                {"id": 4, "ubicacion": {"lat": 700, "lon": -80.43}, "en_camino_at": hace(20)}]  # coordenadas imposibles: como si no tuviera
        r = ruta.de_pedidos(peds, REST, ahora, {})
        self.assertEqual([(p["id"], p["lat"]) for p in r["paradas"]], [(1, 7.97), (3, None), (4, None), (2, None)])
        self.assertEqual(ruta.parada(r, 4)["n"], 3)
        self.assertIsNone(ruta.parada(r, 9))
        self.assertIsNone(ruta.parada(None, 1))


if __name__ == "__main__":
    unittest.main()
