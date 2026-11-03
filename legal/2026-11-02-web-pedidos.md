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
> **Datos que pedimos:** nombre, teléfono, dirección de entrega (si pides a domicilio), tu pedido, los mensajes que escribes en el chat y, si pagas con Yappy, la captura del comprobante.
>
> **Comprobante de Yappy:** la captura puede mostrar tu nombre y datos de tu cuenta. Solo la ve el personal del restaurante para confirmar tu pago, no se envía al asistente de IA y se borra al confirmar o rechazar el pago (como máximo a los 7 días).
>
> **Para qué:** tomar, preparar, cobrar y entregar tu pedido, y contactarte sobre él. No los usamos para publicidad sin tu permiso aparte.
>
> **Asistente de IA:** un sistema de inteligencia artificial lee tus mensajes para responderte. Ese servicio lo presta un proveedor tecnológico que puede estar fuera de Panamá ({proveedor}, {país}); solo recibe lo necesario para contestarte y no puede usarlo para otros fines. Si quieres, pide hablar con una persona del restaurante.
>
> **Cuánto tiempo:** borramos la conversación a los **90 días** y los datos del pedido a los **{N} meses/años**, salvo lo que la ley obligue a conservar (facturación).
>
> **Tus derechos (ARCO y portabilidad):** puedes pedir ver, corregir, borrar u oponerte al uso de tus datos, o recibir una copia. Escríbenos a **{correo / WhatsApp}**. Responderemos en el plazo legal **[ABOGADO: confirmar plazo, el reglamento fija días hábiles]**. Si no quedas conforme, puedes acudir a la ANTAI.
>
> **Compartimos tus datos solo con:** el proveedor del sistema de pedidos, el proveedor de IA y, si aplica, el repartidor (nombre, teléfono y dirección para la entrega).
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
  - IP, user-agent, ubicación GPS: no guardar salvo necesidad de seguridad (logs técnicos ≤ 30 días).
  - Prompts del sistema, notas internas del bot o respuestas crudas de la IA con datos: no en logs de texto plano.
- Logs de la app: enmascarar teléfono (`+507 6***-**12`) y no volcar mensajes completos.

### 2.3 Plazos de conservación (propuesta)
| Dato | Plazo | Luego |
|---|---|---|
| Mensajes del chat | 90 días desde el último mensaje | borrar |
| Pedido (ítems, total, fecha, estado) | **[ABOGADO: plazo fiscal/contable; Código de Comercio exige libros ~5 años]** | anonimizar (quitar nombre/teléfono/dirección) |
| Cliente (nombre, teléfono) | 12 meses desde el último pedido | borrar |
| Logs técnicos | 30 días | borrar |
| Captura de Yappy | al marcar pagado o rechazado; como máximo 7 días desde la subida | borrar el archivo |

- Tarea programada diaria (al arrancar el motor basta) que aplique estos plazos. Plazos en una tabla/constantes, no esparcidos.

### 2.4 Borrado y derechos ARCO a petición
- Panel del restaurante: buscar por teléfono → **ver/exportar** (JSON o texto) y **borrar**.
- Borrar = eliminar mensajes y datos del cliente; los pedidos que deban quedar por ley se **anonimizan** (no se borran las cifras).
- Registrar la solicitud: `fecha`, `tipo` (acceso/rectificación/cancelación/oposición/portabilidad), `estado`, `respondida_at`. Sin el contenido borrado.
- SQLite: tras borrar, `PRAGMA secure_delete=ON` (o `VACUUM`) para que no quede en páginas libres. Copias de seguridad: caducan por rotación en ≤ 90 días; documentarlo.

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
