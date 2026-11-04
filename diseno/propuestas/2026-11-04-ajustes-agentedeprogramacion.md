# Ajustes profesionales — propuesta de agentedeprogramacion (datos y API)

## Hoy
- `servicio.yaml` se lee una vez en `Servicio.__init__` → `sv.cfg`; ~40 lecturas en motor (`pedidos.*`, `pago.*`, `horario`, `nombre`, `negocio`).
- El prompt se arma **una vez** al arrancar (`_prompt`): cambiar nombre u horario hoy exige reiniciar.
- Menú, usuarios y fotos ya están en BD (`platos`, `usuarios`, BLOB WebP con `?v=hash` immutable). El patrón funciona: lo repetimos.
- Nombre fijo en HTML: `web_index.html` (`<title>`, línea 600) y el panel.

## (a) Estructura
Una sola página **Ajustes** con pestañas o anclas laterales (en móvil, una lista que abre cada sección). No hacen falta varias rutas: una página, un JS y una API. Secciones: **Mi restaurante · Menú · Equipo · Pedidos y entrega · Pagos · Cuenta**. El diseño visual lo decide el disenador.

## (b) Campos y permisos
| Sección | Campos | Quién |
|---|---|---|
| Mi restaurante | nombre visible, negocio (frase para el bot), logo, color de marca (1 acento), teléfono, dirección, ubicación (lat/lon, hoy en .env) | dueño |
| Pedidos y entrega | horario (días + turnos), pedido mínimo, ITBMS %, caducidad, cierre de cocina, zonas + precio + alias | dueño |
| Pagos | Yappy, transferencia | dueño (cambia a dónde llega el dinero: el más sensible) |
| Menú | platos, precio, foto, disponible | dueño; **personal solo `disponible`** (agotado hoy) |
| Equipo | usuarios/PIN/rol (ya existe) | dueño |
| Cuenta | cambiar mi PIN | cada uno |
Fuera del panel: `modelo`, `herramientas`, `instrucciones`, `.env` (claves de API). Lo técnico no se toca desde ahí.

## (c) Modelo de datos y API
- **Tabla `ajustes(clave TEXT PRIMARY KEY, valor TEXT /*JSON*/, at, por)`**. El YAML queda como **semilla y valores por defecto**: `sv.cfg` pasa a ser una propiedad = YAML ∪ filas de `ajustes` (merge profundo, solo claves de una **lista blanca**). Así las ~40 lecturas no cambian y el diff es mínimo. Sin caché: se lee en cada uso, igual que `sv.menu`; si hiciera falta, se cachea por `max(at)`.
- **Prompt por turno:** `system` formatea `negocio` y `horario` en cada llamada (no en `__init__`). Ojo: cambiar el prompt rompe la caché de prompt de la API solo cuando cambian los ajustes. Es aceptable.
- **Logo:** tabla `marca(id=1, logo BLOB, logo2x BLOB, v TEXT)` o fila en `ajustes` con BLOB. Se reusa `procesar_foto`, generalizada a `procesar_imagen(datos, lado, cuadrado=False, fondo=None)`: el logo **conserva la transparencia** (WebP RGBA) y no se recorta a cuadrado (`ImageOps.contain` 96/192 px de alto, tope ~20 KB). Mismos límites (3 MB, 40 Mpx, JPEG/PNG/WebP por contenido). **SVG no**: puede ejecutar scripts.
- Se sirve como `GET /marca/logo.webp?v=<hash>` (immutable, el mismo código que `/menu/foto`). Además `/favicon` sale del mismo BLOB.
- **API:** `GET /panel/dueno/ajustes` → `{ajustes, logo}` · `POST /panel/dueno/ajustes {clave: valor…}` (parcial, validado por clave; 409 con el mensaje) · `POST /panel/dueno/logo` (bytes crudos, image/*, mismo anti-CSRF y límite por minuto que la foto) · `POST /panel/dueno/logo_borrar`. Personal: `POST /panel/disponible {plato, disponible}`.
- **Web del cliente:** `GET /config` público y sin datos sensibles (`{nombre, color, logo, telefono, horario, abierto_ahora}`). El HTML aplica `--acento` y el logo desde ahí, y el `<title>` se pone en JS. El servidor puede inyectarlos en el HTML para evitar el parpadeo (un `replace` de marcador).
- **Color:** solo 1 acento `#rrggbb` validado. El servidor calcula el texto encima (blanco o negro) por contraste WCAG ≥ 4.5 y **rechaza** los colores que no lo cumplen. No se aceptan CSS libre ni temas.
- **Validación:** un dict `{clave: validador}` en `panel_web.py`, como `guardar_plato`. Ejemplos: horario `HH:MM` con inicio < fin, zonas con precio ≥ 0, ITBMS de 0 a 15, Yappy `@[a-z0-9_.]{3,30}`, textos con largo máximo.
- **Auditoría:** `auditar(sv, quien, "ajuste:<clave>")` y, para **pagos**, además un aviso en la bandeja ("Yappy cambiado por X"), por si alguien con el PIN del dueño desvía cobros.
- **Turso:** todo son tablas y BLOBs pequeños (logo ≤ 20 KB): migra igual que `platos` o comprobantes. No guardar en disco.
- **Pruebas:** merge YAML ∪ BD y lista blanca; cada validador (bueno y malo); personal → 403 en ajustes y 200 en disponible; el logo PNG con transparencia la conserva y un SVG da 415; el cambio de nombre llega al `system` sin reiniciar; `/config` sin datos privados; la versión `?v` cambia al subir otro logo.

## (d) Riesgos / qué NO hacer
- No escribir el YAML desde el panel: rompe comentarios y git, y choca con la semilla.
- No permitir editar `instrucciones` ni el prompt libre: es fácil inyectar órdenes y romper el bot.
- No dar al personal acceso a pagos o zonas. No exponer en `/config` el teléfono del dueño, la transferencia ni los PIN.
- Cambiar precios, envío o ITBMS **no** afecta a los pedidos ya creados (ya guardan su total). Hay que comprobarlo en una prueba.
- Horario con turnos que cruzan medianoche: hoy no se soporta. Se valida y se rechaza.

## (e) Fases y tamaño
1. **F1 Identidad** (nombre, negocio, logo, color, contacto): tabla `ajustes` + merge de `cfg`, prompt por turno, logo, `/config`, web aplicando la marca. ~250 líneas de Python + JS + ~15 pruebas. *arquitecto* (merge y prompt) + *programador*.
2. **F2 Pedidos y entrega + Pagos**: validadores de horario, zonas y alias, ITBMS, mínimo y Yappy, y aviso al cambiar pagos. ~200 líneas + ~20 pruebas. *programador*.
3. **F3 Reorganizar Ajustes** en secciones (diseño del disenador), personal → solo disponibilidad, Cuenta → cambiar mi PIN. Sobre todo front, ~300 líneas de HTML/JS. *programador* + *disenador*.
4. **F4 (opcional)**: exportar/importar ajustes (JSON) para clonar en otro restaurante. Es útil para *comercial*.
