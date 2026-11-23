# API que usan las páginas (contrato)

Lo que el servidor ofrece a la web de pedidos (`/`), al panel (`/panel`) y a la app del repartidor (`/repartidor/`). Una página propia
puede pintarlo como quiera, pero **no puede cambiar las reglas**: precios, impuestos, envío, estados del pedido, pagos y permisos los decide
el servidor. Los ejemplos salen de una ejecución real del motor (`motor/web.py`, `motor/panel_web.py`); la prueba
`tests/test_paginas.py` (Kit) recorre el mismo guion contra el motor y contra la vista previa y exige las mismas claves.

## Reglas generales
- **Mismo dominio siempre.** Rutas relativas a la raíz (`/menu`, no `https://…/menu`). La página no puede llamar a otro dominio (la CSP lo bloquea: `connect-src 'self'`).
- **JSON.** Toda petición con cuerpo lleva `Content-Type: application/json` (si no: 415), salvo `/comprobante` y la foto de plato, que llevan la imagen cruda. Cuerpo JSON de hasta 8 KB (413).
- **Errores:** código HTTP + `{"error": "texto en español"}`. 400 dato inválido · 401 sin sesión o vencida (vuelva a entrar) · 403 su rol no puede · 404 no existe · 409 el estado no lo permite (p. ej. marcar entregado algo que no salió) · 413 demasiado grande · 415 tipo de contenido · 429 demasiadas peticiones (espere un minuto).
- **Límites:** 60 peticiones/min por IP en la web; `/entrar` 10/min; chat, ubicación, efectivo y comprobante 30/min por cliente; fotos y `/web/…` 300/min.
- **Dinero:** las cantidades del pedido llegan como texto ya formateado (`"B/. 15.07"`); los precios del menú y del envío, como número (`5.0`). No calcule el total en la página: muéstrelo tal cual llega.
- **Tiempo real = sondeo.** No hay WebSocket ni SSE: la web pide `/pedidos` y `/avisos` cada 5 s, el panel `/panel/estado?desde=N` cada 3 s, el repartidor `/repartidor/pedidos` cada 6 s, los mapas cada 6 s.
- **Cookies** (las pone el servidor; la página no las lee: son `HttpOnly`, `SameSite=Strict`, solo de ese dominio):
  - `sesion` (cliente final): `Path=/`, 24 h. La da `POST /entrar`.
  - `panel`: `Path=/panel`, 30 min sin uso (se renueva en cada llamada), máximo 12 h. La da `POST /panel/entrar`.
  - `repartidor`: `Path=/repartidor`, 12 h. La da `POST /repartidor/entrar`.
- **Textos del servidor** (nombres, mensajes del asistente, avisos): píntelos con `textContent`, nunca con `innerHTML`.

## Archivos y marcadores de una página propia
- Carpeta en el servidor: `servicios/<cliente>/web/` con `index.html` (web, `/`), `panel.html` (`/panel`) y/o `repartidor.html` (`/repartidor/`). La que falte sale la estándar.
- `<!--TEMA-->` dentro de `<head>`: el servidor pone ahí los colores del cliente (su tema + su `personalizado.css`). Variables: `var(--primary)`, `var(--on-primary)`, `var(--surface)`, `var(--on-surface)`, `var(--sc-lowest)`, `var(--outline-variant)`, `var(--error)`, `var(--display)` (fuente de títulos), `var(--texto)`…
- `DeliFood` escrito en la página = el nombre del restaurante (se sustituye al servir).
- Sus archivos: `/web/<ruta>` (p. ej. `/web/carta.css`, `/web/img/logo.png`). Solo css, js, png, jpg, webp, svg y woff2; nombres con letras, números, `-` y `_`; hasta 4 niveles de carpeta. Son públicos: nada privado ahí.
- Fuentes del motor: `/estatico/epilogue-v1.woff2`, `/estatico/jakarta-v1.woff2`, `/estatico/mono-v1.woff2` (ya declaradas en el tema). Leaflet (mapas): `/estatico/leaflet-1.9.4.js` y `.css`.
- Lo que la página propia puede cargar (CSP): scripts y estilos propios (también en línea), imágenes propias, `data:`/`blob:` y teselas de `tile.openstreetmap.org`. De fuera, solo fuentes, hojas de estilo e imágenes de los dominios que el operador ponga en `web.permitir` del `servicio.yaml` del cliente (p. ej. `fonts.googleapis.com`, `fonts.gstatic.com`). Scripts de fuera: nunca.

---

## Web de pedidos (cliente final)

### `GET /config` → datos del restaurante
Sin sesión: lo público. Con sesión (`sesion`): añade `pago` (solo los métodos configurados).
```json
{"marca": "Fonda", "horario": {"dias": ["lun","mar","mie","jue","vie","sab","dom"], "desde": "11:00", "hasta": "21:00"},
 "impuesto_pct": 7, "envio": {"Centro": 2.5, "Norte": 3.0}, "telefono": "+507 6000-0000", "anuncio": "", "cerrado": false, "efectivo": true,
 "pago": {"yappy": "@fonda"}}
```
`envio`: zona → costo (`{"": 2.5}` = costo fijo sin zonas; `{}` = sin envío). `cerrado`: el restaurante cerró a mano. `efectivo`: acepta pago en efectivo al recibir (solo entregas).
**QR de mesa:** `GET /config?m=<codigo>` (8 letras/números, el de la URL `/?m=`) añade `"mesa": 4, "etiqueta": "Mesa 4"` si el código es de una mesa activa; si no, esas dos claves no salen. Con una sesión de mesa abierta, `mesa` sale aunque no se pase `?m=`. El QR impreso apunta a `/m/<codigo>`, que redirige a `/?m=<codigo>`: la página debe leer `m`, pedir `/config?m=` y, si trae `mesa`, ocultar el teléfono y mostrar «Estás en la Mesa N».

### `GET /menu` → platos (sin sesión)
```json
[{"id": "sancocho", "nombre": "Sancocho", "precio": 5.0, "categoria": "", "disponible": true, "foto": null, "foto2x": null},
 {"id": "tres-leches", "nombre": "Tres leches", "precio": 3.5, "categoria": "", "disponible": false, "foto": "/menu/foto/tres-leches.webp?v=1a2b3c", "foto2x": "/menu/foto/tres-leches.webp?v=1a2b3c&s=2"}]
```
`foto`/`foto2x`: URL de `GET /menu/foto/<id>.webp` (160 y 320 px) o `null`.

### `GET /aviso` → aviso de privacidad
`{"version": "v1", "texto": "Texto del aviso de privacidad"}`. La `version` se devuelve al entrar.

### `POST /entrar` → abre la sesión del cliente
Entrada: `{"nombre": "Ana", "telefono": "50760001111", "acepta_privacidad": true, "version_aviso": "v1"}` (`consentimiento: true` también vale). Teléfono: 8–15 dígitos (se quitan espacios, `-` y `+`); la web estándar pide celular de Panamá y antepone 507.
Salida: `{"ok": true, "nombre": "Ana"}` + cookie `sesion`. Errores 400: «Nombre inválido», «Teléfono inválido», «Se requiere el consentimiento», «El aviso cambió; vuelva a aceptarlo» (vuelva a pedir `/aviso`).
**En una mesa (QR):** `{"nombre": "Ana", "codigo": "abcd1234", "acepta_privacidad": true, "version_aviso": "v1"}`, **sin `telefono`**. El nombre es obligatorio (1–60). Salida: `{"ok": true, "nombre": "Ana", "mesa": 4}` + cookie `sesion` (dura una visita; recargar conserva la misma sesión). Si el código no es de una mesa, o se manda teléfono, entra como pedido normal. En modo mesa no pida ni guarde el teléfono.

### `POST /chat` → hablar con el asistente (crea y cambia pedidos) — requiere `sesion`
**No hay ruta para «crear pedido»:** la comanda se manda como texto al asistente (la estándar escribe `Quiero 2 Sancocho; 1 Chicha`), y él crea el pedido con las reglas del restaurante y pide confirmación; el cliente confirma respondiendo («Sí»).
Entrada: `{"texto": "quiero 2 sancochos y una chicha a Calle 5"}` (1–1000 caracteres).
Salida: `respuesta` (texto; puede traer `**negrita**` y `_cursiva_`) y `pedido` (el que se creó o cambió en ese turno, o `null`):
```json
{"respuesta": "Te resumo tu pedido... ¿Lo confirmas?",
 "pedido": {"id": 2, "estado": "por_confirmar", "modalidad": "entrega",
            "items": [{"nombre": "Sancocho", "cantidad": 2, "precio": "B/. 5.00"}, {"nombre": "Chicha", "cantidad": 1, "precio": "B/. 1.75"}],
            "ubicacion": false, "subtotal": "B/. 11.75", "impuesto": "B/. 0.82", "envio": "B/. 2.50", "total": "B/. 15.07", "por_cobrar": false}}
```
Pedido de **mesa**: `"modalidad": "mesa"` y además `"mesa": 4, "espera_personal": false` (sin dirección, zona, ubicación ni repartidor; sin pasos de entrega/recogida, «listo» = va a la mesa; `espera_personal: true` oculta el pago en línea y se cobra en la mesa o en caja; `POST /efectivo` también vale para mesas). Esas dos claves solo salen en pedidos de mesa.
Puede tardar (el asistente piensa): muestre «escribiendo…». Si falla por dentro, responde 200 con un texto genérico de disculpa.

### `GET /pedidos` → pedidos del cliente (sondeo cada 5 s) — requiere `sesion`
`{"pedidos": [ <pedido como en /chat>, … ]}`, el más reciente primero (los abiertos y el último cerrado). Si va `en_camino` añade el seguimiento:
```json
{"id": 2, "estado": "en_camino", "...": "...", "por_cobrar": true,
 "repartidor": "Juan", "repartidor_pos": {"lat": 7.962, "lon": -80.432, "precision": 10.0, "edad_s": 0, "viejo": false},
 "mensaje": null, "parada": 1, "eta_min": 1}
```
`repartidor_pos` solo si el pedido es su siguiente parada; si no, `null` y `mensaje` «Tu pedido va en camino; el repartidor tiene otra entrega antes». `viejo`: posición de más de 2 min.

**Estados del pedido:** `por_confirmar` → `confirmado` (falta pagar) → `pago_reportado` (envió comprobante) → `pagado` → `en_preparacion` → `listo` → `en_camino` (solo entregas) → `entregado`. También `cancelado` y `caducado` (sin confirmar a tiempo). `por_cobrar: true` = paga en efectivo al recibir.

### `GET /avisos` → mensajes nuevos del restaurante (sondeo) — requiere `sesion`
`{"mensajes": ["Tu pedido #2 va en camino."]}`. Cada mensaje sale una sola vez.

### `POST /ubicacion` → ubicación de entrega (opcional) — requiere `sesion`
Entrada: `{"lat": 7.961, "lon": -80.431, "precision": 15}` (del navegador) o `{"enlace": "https://maps.app.goo.gl/…"}` (Google Maps o «lat, lon»).
Salida: `{"ok": true, "pedido_id": 2, "ubicacion": true}` (`pedido_id: null` si aún no hay pedido de entrega: se une al siguiente). 400 si no vale.

### `POST /efectivo` → pagar en efectivo al recibir — requiere `sesion`
Entrada: `{"pedido": 2}`. Salida: `{"ok": true, "pedido": {…, "estado": "pagado", "por_cobrar": true}}`. 409 si no aplica (no es entrega, el restaurante no acepta efectivo, no está confirmado).

### `POST /sugerencia` → venta sugerida antes de confirmar — requiere `sesion`
Entrada: `{"pedido": 2}`. Salida: `{"sugerencia": {"id", "nombre", "precio": "B/. 1.75", "pct": 40|null, "texto"}}` o `{"sugerencia": null}` (ya se ofreció, no está por confirmar, el dueño la apagó o no hay qué ofrecer). Solo una vez por pedido; no cambia el pedido: para aceptarla, pídeselo al asistente por `/chat`. 404 si el pedido no es del cliente.

### `POST /comprobante?pedido=2&referencia=123456` → comprobante de pago (Yappy/transferencia) — requiere `sesion`
Cuerpo: la imagen cruda con `Content-Type: image/jpeg` o `image/png`, máx. 5 MB (413); `referencia` opcional ≤ 40. El servidor borra los metadatos y la guarda; solo la ve el personal.
Salida: `{"ok": true, "pedido": {…, "estado": "pago_reportado"}}`. 409 «El pedido no está esperando pago»; 415 si no es JPEG/PNG de verdad.

### Otras
- `GET /menu/foto/<id>.webp?v=<hash>[&s=2]`: foto del plato (pública, caché larga).
- `GET /salud`: `{"ok": true}` (monitor).

---

## Panel del restaurante (`/panel`)
Solo se abre desde la red del local (o con el panel público activado por el operador). Roles: `dueno` (todo) y `personal` (sin ajustes ni equipo).

### `POST /panel/entrar` → `{"pin": "482913"}` → `{"ok": true, "nombre": "Dueno", "rol": "dueno"}` + cookie `panel`
401 «PIN incorrecto»; 429 tras 5 fallos (espere 5 min). `POST /panel/salir` → `{"ok": true}`.

### `GET /panel/estado?desde=<ultimo>` → todo el tablero (sondeo cada 3 s)
```json
{"pedidos": [{"id": 2, "estado": "pagado", "nombre": "Ana", "telefono": "6***-**11", "modalidad": "entrega", "zona": "Centro", "hora_deseada": "", "notas": "",
              "items": [{"nombre": "Sancocho", "cantidad": 2, "precio": "B/. 5.00"}], "subtotal": "B/. 11.75", "impuesto": "B/. 0.82", "envio": "B/. 2.50",
              "total": "B/. 15.07", "metodo": "efectivo", "referencia": null, "comprobante": false, "creado_at": "2026-11-02T12:00:00-05:00",
              "estado_at": "2026-11-02T12:00:00-05:00", "acciones": ["en_preparacion"], "repartidor": null, "tiene_ubicacion": true,
              "por_cobrar": true, "cobrado_por": null, "fuera_de_zona": false}],
 "avisos": [{"id": 4, "tipo": "pedido", "texto": "Pedido #2 de Ana: pagará EN EFECTIVO al repartidor, B/. 15.07. …", "pedido_id": 2}],
 "ultimo": 4, "escalados": [], "repartidores": ["Juan"], "cerrado": false, "cerrado_hasta": null, "modulos": [], "rutas": {}}
```
- `acciones`: **los botones que puede mostrar** para ese pedido (el servidor ya aplicó las reglas). Valores: `pagado`, `pago_rechazado`, `en_preparacion`, `listo`, `en_camino`, `entregado`, `cancelado`.
- `avisos`: los de id > `desde` (y los de pagos pendientes); guarde `ultimo` y páselo en la siguiente llamada. `tipo`: `pago` o `pedido`.
- `escalados`: clientes que piden hablar con una persona `{id, nombre, telefono, motivo, pedido_id}`; `POST /panel/escalado/<id>/atendido` → `{"ok": true}`.
- `modulos`: extras contratados (p. ej. `estadisticas`). `rutas`: repartidor → ruta (como en `/repartidor/pedidos`).
- Teléfono enmascarado en la lista; completo solo en el detalle.

### `GET /panel/pedido/<id>` → detalle
`{"pedido": {…lo de la lista…, "telefono": "50760001111", "direccion": "Calle 5, casa 3", "mapa": "https://www.google.com/maps/dir/?api=1&destination=7.961,-80.431"}}` (teléfono y dirección solo si sigue abierto).
`GET /panel/comprobante/<id>` → la imagen (solo con pago pendiente; 404 «Sin comprobante»).

### `POST /panel/marcar` → `{"pedido_id": 2, "estado": "en_preparacion"}` → `{"ok": true, "pedido": {…}}`
409 con el motivo si no procede: «El pedido está 'pagado'; esta acción solo aplica a pedidos en: en_preparacion.»

### `POST /panel/asignar` → `{"pedido_id": 2, "repartidor": "Juan"}` (`""` quita) → `{"ok": true, "pedido": {…, "repartidor": "Juan"}}`
Solo entregas pagadas o en curso (409 si no).

### Menú y tienda
- `GET /panel/menu` → `{"menu": [ <plato como en /menu> ]}`.
- `POST /panel/agotado` → `{"plato": "chicha", "disponible": false}` → `{"ok": true, "plato": {…}}` (cualquier rol).
- `POST /panel/tienda` → `{"cerrado": true}` → `{"ok": true, "cerrado": true, "cerrado_hasta": null}` (cerrar/abrir a mano).
- `GET /panel/repartidores_mapa` → `{"restaurante": {"lat": 7.96, "lon": -80.43, "nombre": "Fonda"}, "repartidores": [{"repartidor": "Juan", "pedido_id": 2, "lat": 7.962, "lon": -80.432, "precision": 10.0, "edad_s": 0, "viejo": false}]}` (`restaurante` puede ser `null`).

### Solo el dueño: `/panel/dueno/…` (403 para el personal)
- `GET /panel/dueno/ajustes` → `{"ajustes": {"marca": "Fonda", "telefono": "+507 6000-0000", "anuncio": "", "cerrado": false, "cerrado_hasta": null, "excepciones": {}, "factura": {"razon_social": "", "ruc": "", "dv": "", "sucursal": ""}, "horario": {"lun": [["11:00", "21:00"]], …}, "pago": {"yappy": "@fonda", "transferencia": "", "efectivo": true}, "sugerencias": true}}` (`sugerencias`: venta sugerida activa); `POST /panel/dueno/ajustes` con las claves a cambiar → `{"ok": true, "ajustes": {…}}`.
- `GET /panel/dueno/menu` → `{"menu": […]}`; `POST /panel/dueno/plato {id?, nombre?, precio?, categoria?, disponible?}` → `{"ok": true, "plato": {…}}`; `POST /panel/dueno/plato_borrar {plato}` → `{"ok": true}`.
- `POST /panel/dueno/foto?plato=<id>` (imagen cruda JPEG/PNG/WebP ≤ 3 MB) → `{"ok": true, "foto": "…", "foto2x": "…"}`; `POST /panel/dueno/foto_borrar {plato}` → `{"ok": true}`.
- `GET /panel/dueno/usuarios` → `{"usuarios": [{"nombre": "Dueno", "rol": "dueno", "activo": true}, {"nombre": "Juan", "rol": "repartidor", "activo": true}]}`; `POST /panel/dueno/usuario {nombre, rol?, pin?, activo?}` → `{"ok": true, "usuario": {"nombre", "rol", "activo"}}`; `POST /panel/dueno/usuario_borrar {nombre}` → `{"ok": true}`. Nunca devuelve PINs.
- `POST /panel/dueno/estadisticas {dias: 7|30|90}` → resumen de ventas, con `comision {pct, ahorro}` y `sugerencias {ofrecidas, aceptadas, extra, pct_ticket}` (solo con el módulo `estadisticas`; si no, 404).
- `POST /panel/dueno/preguntar {texto ≤ 300}` → `{"respuesta": "…"}` (mismo módulo; 409 sin modelo o con el tope de IA alcanzado; 429 a la 11.ª pregunta del minuto).

---

## App del repartidor (`/repartidor/`)
Funciona desde la calle (no solo la red del local). Rutas fijas del motor: `/repartidor/manifest.webmanifest`, `/repartidor/sw.js`, `/repartidor/icono.svg`.

### `POST /repartidor/entrar` → `{"pin": "271828"}`
`{"ok": true, "nombre": "Juan", "aviso_ubicacion": {"version": "v1", "texto": "Mientras tengas un pedido en camino, esta app envía tu ubicación…", "aceptado": false}}` + cookie `repartidor`. `POST /repartidor/salir` → `{"ok": true}` (apaga su posición).

### `GET /repartidor/pedidos` → sus pedidos y su ruta (sondeo cada 6 s)
```json
{"nombre": "Juan",
 "pedidos": [{"id": 2, "estado": "listo", "nombre": "Ana", "telefono": "50760001111", "direccion": "Calle 5, casa 3", "zona": "Centro", "notas": "", "hora_deseada": "",
              "items": [{"nombre": "Sancocho", "cantidad": 2}, {"nombre": "Chicha", "cantidad": 1}], "total": "B/. 15.07", "pagado": false, "cobrar": "B/. 15.07",
              "mapa": "https://www.google.com/maps/dir/?api=1&destination=7.961,-80.431", "tiene_ubicacion": true, "fuera_de_zona": false, "parada": 1, "acciones": ["en_camino"]}],
 "ruta": {"origen": {"lat": 7.96, "lon": -80.43}, "total_min": 1, "paradas": [{"id": 2, "lat": 7.961, "lon": -80.431, "eta_min": 1, "dist_m": 211, "n": 1, "fuera_de_viaje": false}]},
 "aviso_ubicacion": {"version": "v1", "texto": "…", "aceptado": true}}
```
Solo los suyos en `en_preparacion`, `listo` o `en_camino`. `cobrar`: lo que debe cobrar en efectivo (o `null`). `parada`: su orden en la ruta (`null` si aún en cocina).

### `POST /repartidor/marcar` → `{"pedido_id": 2, "estado": "en_camino"}` (o `"entregado"`, con `"cobrado": true` si había que cobrar)
`{"ok": true, "pedido": {…}}` (`pedido: null` al entregar). 409 «No tiene ese pedido asignado» o si el paso no toca.

### `POST /repartidor/aviso_ubicacion` → `{"version": "v1"}` → `{"ok": true}` (acepta el aviso; 409 si la versión cambió).

### `POST /repartidor/posicion` → `{"lat": 7.962, "lon": -80.432, "precision": 10}` → `{"ok": true}`
Cada 10–15 s mientras tenga un pedido `en_camino`. 429 si llega antes de 5 s, 403 sin aviso aceptado, 409 «No tiene pedidos en camino», 400 fuera de rango.
