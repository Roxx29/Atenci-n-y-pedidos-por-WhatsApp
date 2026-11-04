# Web de pedidos — consentimiento, aviso de privacidad y requisitos (BORRADOR)

> **Borrador, no es asesoría legal.** Base: Ley 81 de 26-mar-2019 (Panamá) y su reglamento, Decreto Ejecutivo 285 de 28-may-2021. Autoridad: ANTAI. Todo lo marcado **[ABOGADO]** necesita revisión antes de producción.

Roles: el **restaurante es el responsable** del tratamiento; nosotros (proveedor del bot) somos **encargado** **[ABOGADO: confirmar y firmar contrato de encargado]**. El proveedor de IA es **subencargado**.

---

## 1. Textos para la web

### 1.1 Casilla de consentimiento (obligatoria, sin marcar por defecto)

> ☐ Acepto que **{Restaurante}** use mi nombre, teléfono y esta conversación para tomar y entregar mi pedido. Un asistente de IA me atiende. [Ver aviso de privacidad](#aviso)

Variante corta (móvil):

> ☐ Acepto el uso de mis datos para gestionar mi pedido, con ayuda de IA. [Privacidad](#aviso)

### 1.2 Aviso de privacidad breve

> **Aviso de privacidad — {Restaurante}**
>
> **Responsable:** {Razón social}, RUC {RUC}, {dirección}, {ciudad}, Panamá.
>
> **Datos que pedimos:** nombre, teléfono, dirección y, si la compartes, ubicación de entrega (si pides a domicilio), tu pedido, los mensajes que escribes en el chat y, si pagas con Yappy, la captura del comprobante.
>
> **Comprobante de Yappy:** la captura puede mostrar tu nombre y datos de tu cuenta. Solo la ve el personal del restaurante para confirmar tu pago, no se envía al asistente de IA y se borra al confirmar o rechazar el pago (como máximo a los 7 días).
>
> **Ubicación:** si pides a domicilio, puedes compartir tu ubicación o un enlace de mapa (es opcional; también puedes escribir la dirección). Solo sirve para que el repartidor te encuentre, no se envía al asistente de IA y se borra al entregar o cancelar el pedido (como máximo a las 24 horas).
>
> **Para qué:** tomar, preparar, cobrar y entregar tu pedido, y contactarte sobre él. No los usamos para publicidad sin tu permiso aparte.
>
> **Asistente de IA:** un sistema de inteligencia artificial lee tus mensajes para responderte. Ese servicio lo presta un proveedor tecnológico que puede estar fuera de Panamá ({proveedor}, {país}); solo recibe lo necesario para contestarte y no puede usarlo para otros fines. Si quieres, pide hablar con una persona del restaurante.
>
> **Cuánto tiempo:** borramos la conversación a los **90 días** y los datos del pedido a los **{N} meses/años**, salvo lo que la ley obligue a conservar (facturación).
>
> **Tus derechos (ARCO y portabilidad):** puedes pedir ver, corregir, borrar u oponerte al uso de tus datos, o recibir una copia. Escríbenos a **{correo / WhatsApp}**. Responderemos en el plazo legal **[ABOGADO: confirmar plazo, el reglamento fija días hábiles]**. Si no quedas conforme, puedes acudir a la ANTAI.
>
> **Compartimos tus datos solo con:** el proveedor del sistema de pedidos, el proveedor de IA y, si aplica, el repartidor asignado a tu pedido (nombre, teléfono, dirección y ubicación, solo hasta la entrega). El repartidor puede abrir la ubicación en una app de mapas para llegar.
>
> Última actualización: {fecha}.

---

## 2. Requisitos para el código (agentedeprogramacion)

### 2.1 Consentimiento
- Casilla **no premarcada**; no se envía el formulario ni se abre el chat sin marcarla.
- Guardar prueba: `consentimiento_at` (fecha-hora), `version_aviso` (p. ej. `2026-11-02`) junto al cliente. Sin esto no podemos demostrar el consentimiento (la carga de la prueba es del responsable).
- Si el aviso cambia de forma sustancial → nueva versión y volver a pedir la casilla.

### 2.2 Qué guardar y qué no (minimización)
- **Sí:** nombre, teléfono, dirección de entrega (solo delivery), ítems, total, estado, método de pago (texto: "Yappy", "efectivo"), mensajes del chat.
- **No guardar:**
  - Datos bancarios ni de tarjeta. La captura de Yappy solo se guarda con la regla de 2.7.
  - Cédula, correo, fecha de nacimiento: no se piden.
  - **Datos sensibles** (salud: alergias, religión, etc.): si el cliente escribe "soy alérgico a X", se usa para el pedido como nota del ítem, pero **no** se guarda como dato del perfil del cliente **[ABOGADO: alergias = dato de salud; valorar si basta con nota del pedido]**.
  - IP y user-agent: no guardar salvo necesidad de seguridad (logs técnicos ≤ 30 días). Ubicación del cliente: solo con la regla de 2.9; nunca en logs ni en la auditoría.
  - Prompts del sistema, notas internas del bot o respuestas crudas de la IA con datos: no en logs de texto plano.
- Logs de la app: enmascarar teléfono (`+507 6***-**12`) y no volcar mensajes completos.

### 2.3 Plazos de conservación (propuesta)
| Dato | Plazo | Luego |
|---|---|---|
| Mensajes del chat | 90 días desde el último mensaje | borrar |
| Datos personales en el pedido (nombre/teléfono/dirección) | 12 meses desde el último pedido | anonimizar |
| Pedido anonimizado (ítems, total, fecha, estado) | 5 años **[ABOGADO: plazo fiscal/contable]** | borrar |
| Registro de solicitudes ARCO | 24 meses **[ABOGADO]** | borrar |
| Cliente (nombre, teléfono) | 12 meses desde el último pedido | borrar |
| Logs técnicos | 30 días | borrar |
| Captura de Yappy | al marcar pagado o rechazado; como máximo 7 días desde la subida | borrar el archivo |

- Tarea programada diaria (al arrancar el motor basta) que aplique estos plazos. Plazos en una tabla/constantes, no esparcidos.

### 2.4 Borrado y derechos ARCO a petición
- Panel del restaurante: buscar por teléfono → **ver/exportar** (JSON o texto) y **borrar**.
- Borrar = eliminar mensajes y datos del cliente; los pedidos que deban quedar por ley se **anonimizan** (no se borran las cifras).
- Registrar la solicitud: `fecha`, `tipo` (acceso/rectificación/cancelación/oposición/portabilidad), `estado`, `respondida_at`. Sin el contenido borrado.
- SQLite: tras borrar, `PRAGMA secure_delete=ON` (o `VACUUM`) para que no quede en páginas libres. Copias de seguridad: caducan por rotación en ≤ 90 días; documentarlo.

**Detalle (acordado con agentedeprogramacion, 2026-11-03)**
- **Exportar** (JSON que se descarga y no se guarda en el servidor): cliente (nombre, teléfono, `consentimiento_at`, `version_aviso`); pedidos (fecha, ítems y notas, total, estado, entrega, dirección, método de pago, `pago_ref`, monto); mensajes vivos; ubicación y comprobante solo si aún existen (el comprobante como metadato, sin la imagen); solicitudes ARCO previas. **No** se exporta: la auditoría, `pagado_por` ni los hashes de PIN.
- **Borrar:** cliente, mensajes, capturas, ubicación y notas de ítems (pueden contener alergias). **Anonimizar** los pedidos: nombre, teléfono, dirección y `cliente_id` pasan a NULL; se conservan las cifras, `pago_ref` y `pagado_por/at` **[ABOGADO: confirmar que `pago_ref` puede quedar]**. Con un pedido abierto no se borra: el panel pide cerrarlo o cancelarlo antes.
- **Registro `arco_solicitud`:** `id`, `tipo`, `canal`, `recibida_at`, `respondida_at`, `estado`, `usuario`, `telefono_hmac` (HMAC-SHA256 con clave secreta sobre el número E.164; no un SHA simple, que se revierte por fuerza bruta). Se conserva 24 meses **[ABOGADO: plazo]**. En la auditoría: `arco_exportar`/`arco_borrar` con `solicitud_id`.
- **Plazos** (2.3): los datos personales del pedido se anonimizan a los 12 meses del último pedido del cliente; el pedido anonimizado se guarda 5 años **[ABOGADO]**.
- **Enmascarado:** primer dígito local y 2 últimos (`+507 6***-**12`; en otros países, prefijo + 1.er dígito + `*` + 2 últimos). Solo al mostrar: en la BD el número sigue completo hasta la anonimización. Los pedidos anonimizados muestran "—".

### 2.5 Transferencia al proveedor de IA
- **Demo (Ollama local):** no hay transferencia; todo queda en la PC.
- **Producción (Anthropic, Claude Haiku — EE. UU.):** es transferencia internacional a un subencargado.
  - Informarla en el aviso (hecho en 1.2) y cubrirla con el consentimiento **[ABOGADO: confirmar que el consentimiento basta para la transferencia internacional o si hace falta cláusula contractual / nivel adecuado]**.
  - Usar API comercial con términos que **no entrenan con los datos** y retención limitada; guardar copia de los términos/DPA del proveedor **[ABOGADO: revisar DPA de Anthropic]**.
  - Enviar a la IA **solo** lo necesario: mensajes de la conversación en curso y el menú. No mandar historial de otros pedidos ni el teléfono completo si no hace falta.
- Repartidor: compartir solo nombre, teléfono y dirección del pedido concreto.

### 2.7 Captura del comprobante de Yappy
- **Finalidad única:** que el personal verifique el pago de **ese** pedido. No se usa para nada más.
- **Subida:** solo desde la web del pedido, ligada a `pedido_id`. Solo imagen (jpg/png/webp) de 5 MB como máximo. **Quitar los metadatos EXIF** y guardarla con un nombre aleatorio, en una carpeta fuera de lo que sirve la web (o como BLOB en SQLite).
- **Quién la ve:** solo el personal con acceso al panel, dentro del detalle del pedido. Nunca llega al cliente ni a otros pedidos, y **nunca se envía al proveedor de IA** (el bot solo sabe "comprobante recibido").
- **Lo que se guarda en texto:** `pago_ref` (número de operación), monto, fecha y `pago_estado`. Eso es lo que queda tras borrar la imagen.
- **Borrado:** al marcar `pagado` o `rechazado`; como máximo 7 días desde la subida; también ante una petición ARCO (2.4). Si hay reclamo, la prueba es `pago_ref`, que se conserva. Los plazos van configurables en `servicio.yaml`.
- No hace falta recortar la imagen (recortar automáticamente es frágil); basta con quitar el EXIF.
- **No** se copia a logs, a avisos ni a WhatsApp del personal.
- Mencionado en el aviso (1.2). **[ABOGADO: confirmar que basta con conservar `pago_ref` como prueba ante reclamos]**

### 2.8 Panel web (/panel, red del local, PIN)
**Qué ve el personal** (solo lo necesario para atender):
| Dato | Pedido abierto (lista) | Pedido abierto (detalle) | Pedido cerrado / historial |
|---|---|---|---|
| Nombre | sí | sí | sí |
| Teléfono | enmascarado (`6***-**12`) | completo, con botón llamar/WhatsApp | enmascarado |
| Dirección | barrio/zona | completa (solo delivery) | oculta |
| Comprobante Yappy | icono "recibido" | imagen, solo mientras el pago está pendiente | no existe (ya borrado, 2.7) |
| Chat | — | conversación del pedido | hasta su borrado (90 días) |
- Sin búsqueda libre de clientes para el personal; buscar por teléfono y exportar/borrar (ARCO, 2.4) solo con el rol **dueño**.

**Usuarios y PIN**
- Un PIN **por persona** (no compartido): sin eso no se sabe quién marcó pagado. Dos roles: `personal` y `dueno` (gestiona PINs, ARCO y exportar).
- PIN de 6 dígitos, guardado con hash lento (`hashlib.scrypt` o `pbkdf2_hmac` con sal), nunca en texto. Prohibir 000000, 123456 y similares.
- 5 intentos fallidos → bloqueo de 5 minutos para ese dispositivo/usuario.
- Sesión: cookie `HttpOnly`, `SameSite=Strict`, aleatoria. **Bloqueo por inactividad de 30 min** (vuelve a pedir PIN; la pantalla de pedidos de cocina puede quedar visible sin datos de contacto). La sesión caduca al cierre del día (máx. 12 h). Botón "Salir" visible.
- Solo red local: escuchar en la IP de la LAN y rechazar accesos desde fuera. Recomendación al restaurante: el Wi-Fi del personal debe ser distinto del Wi-Fi de los clientes (en HTTP el PIN viaja sin cifrar en la red local) **[ponytail: HTTP en la LAN; HTTPS local si el Wi-Fi es compartido]**.

**Registro de accesos y acciones**
- En el pedido: `pagado_por`, `pagado_at` (y lo mismo para rechazado/cancelado). Se conservan lo mismo que el pedido (anonimizado).
- Tabla `auditoria(usuario, accion, pedido_id, at)` con: login, login fallido, salir, marcar pagado/rechazado/cancelado, ver comprobante, exportar, borrar (ARCO), cambiar PIN. **Sin** datos personales: solo `pedido_id`.
- Conservación de la auditoría: **12 meses** y luego se borra **[ABOGADO: confirmar plazo]**. Solo la ve el `dueno`; no se puede editar desde el panel.

### 2.9 Ubicación del cliente y app del repartidor (/repartidor)
**Ubicación**
- Es **opcional**: siempre se puede escribir la dirección. El permiso del navegador no basta como aviso. Junto al botón "Compartir ubicación" va una línea: *"Solo para que el repartidor te encuentre. Se borra al entregar el pedido."* con enlace al aviso. No hace falta otra casilla, porque pulsar el botón ya es el consentimiento **[ABOGADO: confirmar, pendiente 8]**.
- Una sola lectura (`getCurrentPosition`), **nunca seguimiento** (`watchPosition`) ni ubicación en segundo plano.
- Enlace de Google Maps: guardar el enlace tal cual, sin expandirlo ni consultar a Google desde el servidor.
- Nunca va a la IA (el bot solo recibe "ubicación recibida"), ni a los logs, ni a la auditoría.
- **Borrado:** coordenadas y enlace al pasar a `entregado`, `cancelado` o `caducado`; **como máximo 24 h** desde la creación del pedido, para que no queden si el pedido se queda colgado. La dirección en texto sigue los plazos de 2.3.

**Qué ve el repartidor**
- Solo los pedidos **asignados a ese repartidor** en `en_preparacion`, `listo` o `en_camino`. Al pasar a `entregado` o `cancelado`, o si se le reasigna a otro, desaparecen de su vista. No tiene historial, búsqueda ni exportación.
- Ve el nombre, el **teléfono completo** (botón llamar/WhatsApp), la dirección, la ubicación (botón "Abrir en mapas"), los ítems y el monto a cobrar si paga en efectivo. **No** ve el chat, el comprobante ni otros pedidos del cliente.
- La página lleva `Cache-Control: no-store` y no guarda nada en el móvil (sin localStorage ni modo offline con datos).

**Acceso**
- Un PIN por repartidor con las reglas de 2.8 (6 dígitos con hash; 5 fallos → bloqueo de 5 min). Sesión de 12 h como máximo o hasta el fin del turno, con botón Salir. Sin bloqueo por inactividad: se confía en el bloqueo de pantalla del móvil, que es obligatorio (se le dice al repartidor).
- /repartidor sale a Internet (datos móviles), así que **HTTPS es obligatorio** fuera de la demo. Solo se expone /repartidor; /panel sigue solo en la LAN (devops).
- Auditoría (2.8): login, login fallido, ver pedido, recogido (`en_camino`), entregado, y asignar/reasignar desde el panel.

**Repartidor empleado o externo**
- **Empleado** del restaurante: forma parte del responsable. Basta con una cláusula de confidencialidad en su contrato o el reglamento interno: usar los datos solo para entregar y no guardar contactos de clientes.
- **Externo** (freelance o motorizado por cuenta propia): es **encargado** del restaurante. Antes de darle acceso firma un acuerdo corto con estas condiciones: solo entregar, no guardar ni reutilizar datos, no contactar al cliente después y borrar todo al acabar **[ABOGADO: modelo de acuerdo, pendiente 7]**. Para el código es lo mismo; el panel marca `externo: sí/no` en el repartidor para recordar el acuerdo.
- **Plataforma** (PedidosYa, etc.): queda fuera de esta app; los datos se rigen por su propio contrato.

### 2.6 Seguridad mínima
- BD SQLite en la PC del restaurante con usuario de Windows con contraseña; panel sin acceso desde Internet o con login.
- Web: HTTPS obligatorio.
- Brecha de seguridad: el restaurante debe notificar a la ANTAI y a los afectados **[ABOGADO: plazo de notificación según reglamento]**; dejar un procedimiento de una página.

---

## Pendientes [ABOGADO]
1. Rol responsable/encargado y contrato de encargado con el restaurante.
2. Plazo legal de respuesta ARCO y de notificación de brechas.
3. Base para la transferencia internacional a Anthropic.
4. Plazo de conservación de pedidos por obligaciones fiscales.
5. Tratamiento de alergias (dato de salud).
6. Si la web la usan menores de edad.
7. Modelo de acuerdo de confidencialidad/encargado para repartidores externos (2.9).
8. Si la geolocalización precisa necesita un consentimiento aparte de la casilla general.
