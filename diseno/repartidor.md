# Ubicación del cliente, app del repartidor y asignación en el panel

## 1. Web de pedidos (`motor/web_index.html` = `diseno/web-pedidos.html`)
- **Pedido de entrega activo:** bajo el resumen aparece el botón con borde **"Compartir mi ubicación"**, y en "Mi pedido" la tarjeta "Entrega a domicilio". Es opcional (legal 2.9): la dirección se puede seguir escribiendo en el chat.
- **Hoja inferior:**
  - Botón grande "Compartir mi ubicación" (GPS con `navigator.geolocation`, alta precisión, 15 s).
  - Desplegable "Pegar un enlace de Google Maps", con los pasos para copiarlo.
  - Debajo: "Solo para que el repartidor te encuentre. Se borra al entregar el pedido." y "Ver aviso de privacidad".
  - **Sin GPS** (LAN sin HTTPS, navegador sin permiso o error): el campo del enlace aparece abierto con el motivo.
- **Al guardar:** "Ubicación recibida" con un check verde en el resumen, una línea en el chat y "Cambiar ubicación" en "Mi pedido".
- **Estado `en_camino`:** en las entregas, los pasos son Confirmado → Pago reportado → Pagado → En preparación → Listo → **En camino**. Cuando cambia, sale el aviso "¡Tu pedido va en camino!".

## 2. App del repartidor (`diseno/repartidor.html` → servir en `/repartidor`)
- **Pensada para el sol y una mano:**
  - Fondo blanco y tinta casi negra, sin modo oscuro.
  - Letra de 18 px; botones de 64 px y de 72 px los del pie.
  - La acción de estado va siempre abajo, al alcance del pulgar.
- **Entrada:** PIN de 6 cifras con teclado grande. El botón Salir está siempre en la barra.
- **Mis pedidos:** solo los activos asignados (en cocina, listo, en camino), primero los que van en camino. Cada uno muestra el nº grande, el nombre, la zona y la hora, una etiqueta **Pagado** (verde) o **Cobrar B/. 0.00** (roja) y el estado. **Lo entregado desaparece** (legal). Un pedido nuevo vibra, suena y queda resaltado.
- **Un pedido:**
  - Arriba, el cobro: "Ya pagado. No cobres nada." o "Cobrar en efectivo B/. X".
  - Botones: **Abrir ruta en Google Maps** (usa la `mapa` que manda el servidor), **Llamar** (`tel:`) y **WhatsApp** (`whatsapp://send?phone=`).
  - Dirección y notas, qué lleva y el total.
  - En el pie: "Recogí el pedido · En camino" y luego **Entregado**. Si hay que cobrar, primero pregunta "¿Ya cobraste B/. X en efectivo?".
- Sin historial, chat ni comprobante. Sin URLs http(s) en la página y 0 usos de `innerHTML`.

## 3. Panel (`motor/panel_web.html` = `diseno/panel-web.html`)
- La columna pasa a ser **"Listo / En camino"**. En una entrega lista, el botón principal es "Salió con el repartidor" (si viene `en_camino` en `acciones`); si va en camino, "Entregado".
- **Tarjeta de entrega** (pagado → en camino): "Juan" o **"Sin repartidor"** en rojo, y "Ubicación" en verde si el cliente la compartió.
- **Detalle:** "Ubicación del cliente: Recibida · Ver en el mapa" (con la `mapa` del servidor) y un **selector de Repartidor** ("Sin asignar" + `repartidores`), solo en pagado, en preparación o listo → `POST /panel/asignar {pedido_id, repartidor}` ("" quita la asignación).

## API acordada (contrato original de agentedeprogramacion)
- `POST /ubicacion {lat, lon}` o `{enlace}` → `{ok, pedido_id, ubicacion:true}` · 400 `{error}`. El pedido trae `ubicacion` (o `tiene_ubicacion`).
- `POST /repartidor/entrar {pin}` → `{ok, nombre}`; `POST /repartidor/salir`; `GET /repartidor/pedidos` (sondeo cada 6 s) → `{pedidos:[{id, estado, nombre, telefono, direccion, zona, notas, hora_deseada, items, total, pagado, mapa, acciones}]}`; `POST /repartidor/marcar {pedido_id, estado}`.
- Panel: `repartidor` (texto o `{id,nombre}`), `tiene_ubicacion`, `mapa` en el detalle, `repartidores` (textos u objetos), `POST /panel/asignar`.
- La web acepta las variantes (`lng`, `mapa_url`, `cobrar`) para no romperse.

## Archivos de la PWA (los sirve el servidor)
`/repartidor/manifest.webmanifest`:
```json
{
  "name": "Repartos · Fonda El Chitreano",
  "short_name": "Repartos",
  "lang": "es",
  "start_url": "/repartidor",
  "scope": "/repartidor/",
  "display": "standalone",
  "orientation": "portrait",
  "background_color": "#FFFFFF",
  "theme_color": "#A8321F",
  "icons": [
    {"src": "/repartidor/icono.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"},
    {"src": "/repartidor/icono.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "maskable"}
  ]
}
```
`/repartidor/sw.js`: no guarda en caché ningún dato personal (legal). Solo muestra una pantalla sin conexión.
```js
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", e => {
  if (e.request.mode !== "navigate") return;
  e.respondWith(fetch(e.request).catch(() => new Response(
    '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font:20px system-ui;padding:24px;text-align:center"><h1>Sin conexión</h1><p>Revisa tu señal y vuelve a abrir Repartos.</p>',
    {headers: {"Content-Type": "text/html; charset=utf-8"}})));
});
```
`/repartidor/icono.svg` (fondo achiote a sangre, con margen para la máscara):
```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><rect width="512" height="512" fill="#A8321F"/><g fill="none" stroke="#FFF" stroke-width="28" stroke-linecap="round" stroke-linejoin="round"><circle cx="170" cy="330" r="56"/><circle cx="342" cy="330" r="56"/><path d="M226 330h90l-50-120h-70M302 166h52l38 90"/></g></svg>
```
El servicio de trabajo (`sw.js`) solo se registra si la página se sirve por HTTPS o desde localhost. Sin eso no se puede instalar, aunque la página funciona igual.
