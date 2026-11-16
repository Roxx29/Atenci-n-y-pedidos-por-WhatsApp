"""Ruta del repartidor con varios pedidos: orden de paradas que minimiza la suma de las horas de llegada a cada cliente
(camino abierto desde el origen, sin volver), no la distancia total. Funciones puras; se recalcula en cada petición, nada se guarda."""
import datetime as dt
import itertools

from .ubicacion import distancia_km, validar

EXACTO_HASTA = 8  # hasta aquí, todas las permutaciones (8! = 40 320); más, vecino más cercano + 2-opt
# servicio.yaml reparto: {...}. peso_espera: cuánto pesa de más la llegada a un pedido por cada minuto que lleva esperando (0 = no prioriza)
DEFECTO = {"factor_calle": 1.35, "velocidad_kmh": 25, "min_por_parada": 3, "max_por_viaje": 6, "peso_espera": 0.02}
MINIMO = {"factor_calle": 0.01, "velocidad_kmh": 0.01, "min_por_parada": 0, "max_por_viaje": 1, "peso_espera": 0}


def parametros(cfg):
    """Los de `reparto:` de servicio.yaml; lo que falte o no sea un número válido, el valor por defecto."""
    r = cfg.get("reparto") or {}
    ok = lambda k: isinstance(r.get(k), (int, float)) and not isinstance(r[k], bool) and r[k] >= MINIMO[k]
    return {k: r[k] if ok(k) else d for k, d in DEFECTO.items()}


def minutos(a, b, pr):
    """Minutos estimados entre dos puntos (lat, lon): línea recta × factor_calle a velocidad_kmh."""
    return distancia_km(a, b) * pr["factor_calle"] / pr["velocidad_kmh"] * 60


def coste(orden, m, o, pesos, parada):
    """Suma ponderada de las horas de llegada. m[i][j]: minutos entre paradas; o[i]: minutos desde el origen; parada: minutos en cada entrega."""
    t, total, antes = 0.0, 0.0, None
    for i in orden:
        t += o[i] if antes is None else parada + m[antes][i]
        total += pesos[i] * t
        antes = i
    return total


def vecino(m, o):
    """Vecino más cercano desde el origen."""
    orden, libres = [], set(range(len(o)))
    while libres:
        i = min(libres, key=lambda j: (m[orden[-1]][j] if orden else o[j], j))
        orden.append(i)
        libres.remove(i)
    return orden


def dos_opt(orden, c):
    """Invierte tramos mientras baje el coste c(orden)."""
    mejor, cm, sigue = list(orden), c(orden), True
    while sigue:
        sigue = False
        for a in range(len(mejor) - 1):
            for b in range(a + 1, len(mejor)):
                cand = mejor[:a] + mejor[a:b + 1][::-1] + mejor[b + 1:]
                cc = c(cand)
                if cc < cm - 1e-9:
                    mejor, cm, sigue = cand, cc, True
    return mejor


def ordenar(m, o, pesos, parada):
    """Índices de las paradas en orden de visita."""
    c = lambda orden: coste(orden, m, o, pesos, parada)
    if len(o) <= EXACTO_HASTA:  # ponytail: fuerza bruta, ~0,1 s con 8 paradas; programación dinámica por subconjuntos si llega a pesar
        return list(min(itertools.permutations(range(len(o))), key=c))
    return dos_opt(vecino(m, o), c)


def calcular(paradas, origen, pr):
    """paradas: [{id, lat, lon, espera_min}] (lat/lon None = sin ubicación); origen: (lat, lon) o None (sale de la parada más antigua con ubicación).
    -> {origen, total_min, paradas: [{id, n, lat, lon, eta_min, dist_m, fuera_de_viaje}]}; eta_min y dist_m son acumulados desde el origen."""
    viejas = sorted(paradas, key=lambda p: (-p["espera_min"], p["id"]))
    con = [p for p in viejas if p["lat"] is not None]
    pts = [(p["lat"], p["lon"]) for p in con]
    origen = origen or (pts[0] if pts else None)
    orden = ordenar([[minutos(a, b, pr) for b in pts] for a in pts], [minutos(origen, p, pr) for p in pts],
                    [1 + pr["peso_espera"] * max(0, p["espera_min"]) for p in con], pr["min_por_parada"])
    out, t, km, antes = [], 0.0, 0.0, origen
    for k, i in enumerate(orden):
        t += minutos(antes, pts[i], pr) + (pr["min_por_parada"] if k else 0)
        km += distancia_km(antes, pts[i]) * pr["factor_calle"]
        antes = pts[i]
        out.append({"id": con[i]["id"], "lat": pts[i][0], "lon": pts[i][1], "eta_min": round(t), "dist_m": round(km * 1000)})
    out += [{"id": p["id"], "lat": None, "lon": None, "eta_min": None, "dist_m": None} for p in viejas if p["lat"] is None]
    return {"origen": {"lat": origen[0], "lon": origen[1]} if origen else None, "total_min": round(t) if con else None,
            "paradas": [{**p, "n": n, "fuera_de_viaje": n > pr["max_por_viaje"]} for n, p in enumerate(out, 1)]}


def de_pedidos(pedidos, origen, ahora, cfg):
    """Ruta de los pedidos de UN repartidor. La espera cuenta desde que el pedido quedó listo (o salió)."""
    ps = []
    for p in pedidos:
        u = p.get("ubicacion") or {}
        try:
            lat, lon = validar(u.get("lat"), u.get("lon"))
        except ValueError:
            lat = lon = None
        desde = p.get("listo_at") or p.get("en_camino_at") or p.get("estado_at") or p.get("creado_at")
        ps.append({"id": p["id"], "lat": lat, "lon": lon, "espera_min": (ahora - dt.datetime.fromisoformat(desde)).total_seconds() / 60 if desde else 0})
    return calcular(ps, origen, parametros(cfg))


def parada(r, pedido_id):
    """La parada de ese pedido en la ruta `r`, o None."""
    return next((p for p in (r or {}).get("paradas", ()) if p["id"] == pedido_id), None)
