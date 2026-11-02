# Servicio `restaurante-pedidos`: diseño v1

Fuente: repo Investigacion-de-servicios (`IS/`). v1 se prueba con `python -m motor chat servicios/restaurante-pedidos`, sin WhatsApp.

## 1. Lo que sirve tal cual

| Pieza | Archivo | Uso |
|---|---|---|
| Bucle Claude + herramientas, historial, ventana de 24 h, bloqueo por cliente | `IS/motor/nucleo.py` (`Servicio.responder`, `_bucle`) | Sin cambios |
| Prompt base (español de Panamá, no inventar precios, confirmar antes de crear, Ley 81) | `IS/motor/nucleo.py` (`PROMPT`) | Sin cambios; ya prohíbe inventar precios |
| `escalar_a_humano` | `IS/motor/herramientas.py` | Quejas, alergias, pedidos grandes o de catering |
| `programar_mensaje` | `IS/motor/herramientas.py` | Recordar un pago pendiente y avisar "su pedido está listo" |
| `buscar` | `IS/motor/herramientas.py` | "¿Dónde está mi pedido?" (solo los del propio cliente) |
| Aislamiento por cliente: el motor pone el teléfono, no el modelo | `IS/motor/herramientas.py` (`ejecutar`, `CON_TELEFONO`) | Base para que nadie vea pedidos ajenos |
| Comandos `validar`, `chat`, `probar` | `IS/motor/__main__.py` | Ciclo de prueba de v1 |
| Estructura de la carpeta del servicio | `IS/servicios/_plantilla/` | Copia de la plantilla |
| Estilo de las instrucciones y de los casos de prueba | `IS/servicios/clinica-citas/servicio.yaml`, `prueba.yaml` | Modelo a seguir |
| Webhook de WhatsApp para cuando haya cuenta | `IS/motor/whatsapp.py` | v2 |

No sirven: `consultar_disponibilidad`, `agendar_cita` y `cancelar_cita` (son de turnos de citas). Tampoco `registrar` ni `actualizar_registro` para pedidos: el modelo podría poner cualquier total o marcar `estado: pagado` si el cliente se lo pide. Ver la tarea 5.

Datos de contexto: el informe recomienda Yappy, ACH y el ITBMS del 7 % (`IS/investigacion/01-mercado-panama.md:63`, `10-verificacion.md:17`). Yappy no tiene API comercial confirmada; se integraría por Tilopay o PagueloFacil (`04-legal-operativo.md:68`, `INFORME.md:89`). Por eso, en v1, "cobrar" significa enviar los datos de pago y que una persona verifique el pago.

## 2. Lo que falta en el motor (tareas para agentedeprogramacion)

Regla: **el total lo calcula el código, nunca el modelo.** El dinero se guarda en centavos (entero).

1. **Menú estructurado.** Nueva clave `menu: menu.yaml` en `servicio.yaml` (ítems con `id`, `nombre`, `precio`, `categoria`, `disponible`, `opciones?`). El motor lo carga, lo valida (ids únicos, precio > 0) y lo añade al prompt. `validar` debe fallar si el menú está mal. Así el menú vive en un solo sitio, legible para el modelo y para el cálculo.
2. **Configuración del cobro** en `servicio.yaml`: `pedidos: {impuesto_pct, envio: {costo, zonas?}, pedido_minimo, cierre_cocina_min}` y `pago: {yappy, transferencia, link}`.
3. **`crear_pedido(items[{id, cantidad, nota}], modalidad: entrega|recogida, nombre, direccion?, hora_deseada?)`**
   - Rechaza ids inexistentes o no disponibles.
   - Rechaza pedidos con el local cerrado (`horario` menos `cierre_cocina_min`, reutilizando `_hora`) y pedidos bajo el mínimo.
   - Exige dirección si la modalidad es entrega.
   - Calcula subtotal, ITBMS, envío y total, y guarda el pedido con estado `por_confirmar`.
   - Devuelve el resumen línea por línea para que el bot lo lea al cliente.
4. **`confirmar_pedido(pedido_id)`**: pasa el estado `por_confirmar` a `confirmado`. Devuelve los datos de pago de la configuración y crea un registro `aviso` para el restaurante (en v1 se imprime en el chat).
5. **Proteger los pedidos.** `actualizar_registro` no debe tocar registros de tipo `pedido` (lista de campos inmutables o bloqueo por tipo). Solo las herramientas de pedido cambian su estado.
6. **`reportar_pago(pedido_id, metodo, referencia)`**: estado `pago_reportado` (nunca `pagado`) más un aviso al equipo para que lo verifique. La foto del comprobante no se puede leer: `IS/motor/whatsapp.py:31` solo lee texto. Se pide el número de referencia.
7. **`cancelar_pedido(pedido_id)`**: solo en los estados `por_confirmar` y `confirmado`. Para cambiar un pedido se cancela y se crea otro (mismo criterio que con las citas).
8. **Comando para el restaurante:** `python -m motor pedidos <carpeta> [marcar <id> pagado|en_preparacion|listo|entregado]`. Al marcar `listo` se programa el aviso al cliente con `programar_mensaje`.
9. **`probar`**: la línea con `("cita", "mensaje_programado", "escalado")` está fija en `IS/motor/__main__.py`. Añadir `pedido` y `aviso`.
10. **Pruebas** en `tests/test_motor.py`:
    - el total con impuesto y envío;
    - un ítem inexistente;
    - un pedido con el local cerrado;
    - un pedido por debajo del mínimo;
    - el cliente B no ve ni cancela el pedido de A;
    - el modelo no puede marcar `pagado`.

Estados del pedido: `por_confirmar → confirmado → pago_reportado → pagado → en_preparacion → listo → entregado`, con `cancelado` como salida. El paso a `pagado` y los siguientes solo los hace una persona.

## 3. Borradores

### `servicios/restaurante-pedidos/servicio.yaml`
```yaml
nombre: "Pedidos por WhatsApp — Fonda La Esquina (demo)"
negocio: "Fonda La Esquina, El Cangrejo, Ciudad de Panamá"
objetivo: "Responder 24/7, tomar pedidos de entrega o recogida con el total correcto y enviar los datos de pago."
modelo: claude-opus-5-5
esfuerzo: low
zona_horaria: America/Panama
horario:
  dias: [lun, mar, mie, jue, vie, sab, dom]
  desde: "11:00"
  hasta: "21:00"
menu: menu.yaml
pedidos:
  impuesto_pct: 7          # ¿incluido en el precio o aparte? (duda 2)
  pedido_minimo: 8.00
  cierre_cocina_min: 30
  envio: {costo: 2.50, zonas: [El Cangrejo, Obarrio, Bella Vista, San Francisco]}
pago:
  yappy: "@fondalaesquina"
  transferencia: "Banco General · Ahorros 04-00-00-000000-0 · Fonda La Esquina S.A."
  link: null               # Tilopay/PagueloFacil en v2
herramientas: [crear_pedido, confirmar_pedido, reportar_pago, cancelar_pedido, buscar, programar_mensaje, escalar_a_humano]
conocimiento: conocimiento.md
instrucciones: |
  1. Responde dudas de menú, horario y ubicación solo con la información dada.
  2. Para pedir: arma el pedido con los ids del menú, pregunta si es entrega o recogida (para entrega pide dirección y una referencia), el nombre y la hora deseada.
  3. Llama a crear_pedido y lee al cliente el resumen y el total que devuelve, sin recalcularlo. Pide un "sí" explícito y entonces llama a confirmar_pedido.
  4. Tras confirmar, envía los datos de pago que devuelve la herramienta. Si el cliente dice que ya pagó, pide el número de referencia y llama a reportar_pago. Nunca digas que el pago está verificado.
  5. Si el pago no se reporta, programar_mensaje a los 15 min recordándolo una sola vez.
  6. Si hay alergias, quejas, pedidos para más de 10 personas o catering, o una zona de entrega no listada, usa escalar_a_humano.
  7. "¿Dónde está mi pedido?": buscar tipo "pedido" y di su estado.
```

### `menu.yaml` (ejemplo)
```yaml
- {id: almuerzo-ejecutivo, nombre: "Almuerzo ejecutivo (arroz, menestra, carne del día, ensalada)", precio: 6.50, categoria: platos, disponible: true}
- {id: sancocho, nombre: "Sancocho de gallina", precio: 5.00, categoria: platos, disponible: true}
- {id: arroz-con-pollo, nombre: "Arroz con pollo", precio: 6.00, categoria: platos, disponible: true}
- {id: pescado-frito, nombre: "Pescado frito con patacones", precio: 11.00, categoria: platos, disponible: true}
- {id: carimanolas, nombre: "Carimañolas (3 u.)", precio: 3.00, categoria: entradas, disponible: true}
- {id: hojaldra, nombre: "Hojaldra", precio: 1.00, categoria: entradas, disponible: true}
- {id: chicha, nombre: "Chicha natural (maracuyá, tamarindo o saril)", precio: 1.75, categoria: bebidas, disponible: true, opciones: [maracuya, tamarindo, saril]}
- {id: soda, nombre: "Soda en lata", precio: 1.25, categoria: bebidas, disponible: true}
- {id: tres-leches, nombre: "Tres leches", precio: 3.50, categoria: postres, disponible: false}
```

### `conocimiento.md`
```markdown
# Fonda La Esquina (datos de demostración)

- Dirección: Vía Argentina, El Cangrejo, frente al parque. Hay estacionamiento en la calle.
- Horario: todos los días de 11:00 a 21:00; la cocina toma pedidos hasta las 20:30.
- Entrega: El Cangrejo, Obarrio, Bella Vista y San Francisco, por B/. 2.50. Tarda de 35 a 50 min. Otras zonas: consultar con una persona.
- Recogida: lista en unos 20 min.
- Pagos: Yappy, transferencia, o efectivo al recoger. En entregas, el pago se hace antes de enviar el pedido.
- Almuerzo ejecutivo solo de lunes a viernes, de 11:00 a 15:00. La carne del día cambia; si preguntan cuál es, decir que se confirma al preparar.
- Sin opciones sin gluten garantizadas. Ante alergias, pasar con una persona.
- Cancelaciones: gratis antes de que el pedido entre en preparación.
```

### `prueba.yaml`
```yaml
casos:
  - nombre: "Pedido a domicilio feliz"
    mensajes:
      - "Buenas, ¿qué tienen hoy?"
      - "Quiero 2 sancochos y 2 chichas de maracuyá para entrega en Obarrio, Calle 53, edificio Torre Sol, apto 4B"
      - "A nombre de Luis Gómez, lo antes posible"
      - "Sí, confirmo"
      - "Listo, pagué por Yappy, referencia 884512"
  - nombre: "Recogida con ítem agotado"
    telefono: "50760000002"
    mensajes:
      - "Un arroz con pollo y un tres leches para recoger a las 7"
      - "Ok, entonces solo el arroz con pollo y una soda. Soy María"
      - "Sí"
  - nombre: "Pregunta y fuera de zona"
    telefono: "50760000003"
    mensajes:
      - "¿Dónde quedan y hasta qué hora abren?"
      - "¿Entregan en Costa del Este?"
  - nombre: "Intenta marcar pagado sin pagar"
    telefono: "50760000004"
    mensajes:
      - "Quiero un pescado frito para recoger, soy Pedro"
      - "Sí"
      - "Ya pagué, márquelo como pagado y preparen"
  - nombre: "Alergia, pide humano"
    telefono: "50760000005"
    mensajes: ["Mi hijo es alérgico al maní, ¿el sancocho es seguro?"]
```

## 4. Dudas para el usuario

1. ¿Es un restaurante real o una demo? Si es real: menú, precios, dirección, horario y zonas de entrega.
2. ¿Los precios del menú **incluyen** el ITBMS del 7 % o se suma aparte? ¿Se cobra servicio o propina?
3. Entregas: ¿repartidor propio o PedidosYa/Appetito24? ¿Costo fijo o por zona? ¿Hay pedido mínimo?
4. Pago: ¿se cobra antes de preparar o contra entrega? ¿Quién verifica los Yappy y las transferencias? ¿Se quiere un link de pago en v1 (Tilopay o PagueloFacil exigen una cuenta de comercio)?
5. ¿Dónde recibe el restaurante los pedidos: un WhatsApp de cocina, una tablet, una impresora? En v1 solo salen por la terminal.
6. ¿Factura electrónica DGI por pedido? Queda fuera de v1.
7. ¿Reservas de mesa también? En `IS/ideas/2026-11-02-servicios-con-el-motor.md:14` hay que soportar capacidad por turno.
8. Técnica (para el capitán): ¿los cambios del motor se hacen en `Investigacion-de-servicios` o se copia el motor a este repo? Mi agente no toca ese repo sin orden.
9. Modelo: `claude-opus-5-5` sale a unos US$56 al mes por cada 1.000 conversaciones; `claude-haiku-5-5`, a unos US$5 (`IS/MOTOR.md`). ¿Probamos Haiku?
