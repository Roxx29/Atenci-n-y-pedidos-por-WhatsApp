# Panel web del restaurante

Prototipo: `diseno/panel-web.html` (un archivo, CSS y JS en línea, sin CDN). Sustituye a `PanelPedidos.exe`. Al abrirlo con doble clic (`file://`) o si el servidor no responde, entra en **modo demo**: cualquier PIN de 6 cifras, datos de ejemplo, un comprobante nuevo a los 12 s y el aviso "IA sin saldo" a los 40 s.

## Decisiones
- **Para PC y tablet en horizontal.** Un tablero de columnas. En 1366 px caben las 6; en una tablet de 1024 px se desplaza de lado. Botones de 52 px o más, poco texto y la acción siguiente siempre visible.
- **Columnas:** Nuevos (por confirmar y esperando pago) · Verificar pago · Pagado · En cocina · Listo · Entregado (columna estrecha). Cancelados y caducados se ven con el botón "Cancelados".
- **Tarjeta:** el número grande, el nombre debajo y el tiempo en ese estado arriba a la derecha. Ese tiempo se pone rojo y con texto cuando pasa del límite: 5 min en verificar o pagado, 25 en cocina y 15 en listo. Debajo van la modalidad (entrega y zona, o recoger y hora), los ítems, la nota, el total "B/. 0.00" y **un solo botón principal**: A cocina, Listo o Entregado. En "Verificar pago" aparecen "Comprobante recibido · Toca para verlo", **Pago correcto** (verde) y **No válido** (con borde).
- **Detalle (al tocar el número o el comprobante):** el comprobante en grande junto a "Total a pagar", la referencia y las acciones. Ahí se ven el teléfono completo con Llamar y WhatsApp y la dirección completa. "Pago no válido" pide confirmación en el mismo panel.
- **Alertas:**
  - Un pedido confirmado o un comprobante nuevo suenan con dos tonos (WebAudio, sin archivos), resaltan la tarjeta en amarillo maíz con la etiqueta "Nuevo" y suben el contador de la cabecera, que lleva al pedido. También aparecen en el título de la pestaña.
  - Los escalados y los avisos de sistema suenan con tres tonos.
  - El sonido se puede silenciar y la elección se recuerda.
- **Bandeja de avisos:** un panel lateral. Arriba, "Para atender" (escalados con Llamar, Ver pedido y Atendido); debajo, los últimos avisos (cambios de dirección, pagos) con "Ver pedido". Los avisos de **sistema** (IA sin saldo, WhatsApp caído) se muestran además en una **banda roja fija** encima del tablero.
- **Entrada:** PIN de 6 cifras con teclado numérico grande. El servidor da el mensaje de bloqueo tras 5 fallos (429). El botón Salir siempre está visible.
- **Mundo visual:** los mismos tokens que la web de pedidos (papel, achiote, maíz, hoja), con fondo claro para un local con luz de día. La cabecera es oscura para separarla del tablero.

## Privacidad (legal-datos, sección 2.8)
- En la lista, el teléfono va enmascarado (6\*\*\*-\*\*12), solo se muestra la zona y el comprobante es un aviso de "recibido" sin imagen.
- El detalle de un pedido abierto muestra el teléfono completo, la dirección y el comprobante (este, solo con el pago pendiente). En los pedidos cerrados, el teléfono va enmascarado y no hay dirección.
- **Bloqueo por inactividad (30 min):** se pide el PIN encima del tablero, que sigue a la vista pero inerte (sin teléfonos ni direcciones).
- Fuera de este prototipo: las pantallas del rol dueño (buscar cliente, exportar o borrar datos, PINs, registro de acciones). Apuntado en IDEAS.md.

## API (implementada por agentedeprogramacion, commit e48b732 y siguientes)
- `POST /panel/entrar {pin}` → `{ok, rol}` · 401 · 429. `POST /panel/salir`.
- `GET /panel/estado?desde=N` cada 3 s → `{pedidos, avisos, ultimo, escalados}`. Los pedidos traen `telefono` enmascarado, sin dirección, y `acciones:[…]`: **la web solo pinta los botones que vienen en `acciones`**.
- `GET /panel/pedido/<id>` → detalle con teléfono completo y dirección. `GET /panel/comprobante/<id>` → imagen.
- `POST /panel/marcar {pedido_id, estado}` → `{ok, pedido}` · 409 `{error}` (se muestra en un aviso rojo). Si llega `cancelado` en `acciones`, el detalle muestra "Cancelar pedido".
- `POST /panel/escalado/<id>/atendido`.
- Todo el texto del servidor se pinta con `textContent` (0 usos de `innerHTML`) y no hay URLs externas.
