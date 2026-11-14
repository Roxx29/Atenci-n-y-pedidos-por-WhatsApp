# Varios locales (cadena) — diseño

2026-11-14 · arquitecto · Solo diseño. Estado: pendiente de aprobar.

## Qué hay hoy (leído en el código)
- `Servicio(carpeta)` = una carpeta + una BD. Horario, zonas, envío, pago, menú con precio/agotado (`platos`), usuarios y PIN (`usuarios`), ajustes y auditoría ya viven **dentro de esa BD**. Un local = una carpeta ya da casi todo "por local" sin tocar nada.
- `web.crear_servidor(sv)` cierra todo su estado (límites, sesiones, posiciones) sobre un `sv`: se puede instanciar varias veces en un proceso.
- Lo que es **global por proceso** y estorba: `WHATSAPP_PHONE_ID`, `TURSO_DATABASE_URL/TOKEN`, `WEB_SECRET`, `PANEL_PIN*`, `REPARTIDORES`, `RESTAURANTE_LAT/LON`; rutas absolutas en los HTML (`fetch("/entrar")`, `/estatico/`), cookies con `Path=/` y `Path=/panel`.
- Hallazgo: `python -m motor web` (lo que arranca el `Dockerfile`) **no monta el webhook de WhatsApp ni el envío de recordatorios**; eso solo está en `motor servir`, otro proceso. En la nube hoy no hay WhatsApp.

## Rutas evaluadas
| | A: una instancia por local | B: `local_id` en una BD | C: un proceso, una carpeta/BD por local |
|---|---|---|---|
| Código nuevo | Casi cero (otro `fly.toml`) | Muy alto: `registros` es JSON genérico y `sv.listar("pedido")` se usa en todo el motor; cada consulta necesita filtro | Bajo: despachador por prefijo + Central |
| Aislamiento (Ley 81) | Total | Depende de no olvidar un `WHERE`; un fallo = fuga entre locales | Por BD (igual que A); falta aislar cookies y secretos |
| Hosting | N máquinas (Render starter US$7/mes c/u, según `render.yaml`) + N juegos de secretos y dominios | 1 máquina, 1 BD | 1 máquina, N BD Turso (verificar tope del plan gratis) |
| Operación | N despliegues por cada cambio | 1 | 1 |
| Central de cadena | Difícil: leer N BD remotas | Fácil | Fácil: los `Servicio` están en memoria |
| Migrar DeliFood | Nada | Reescribir su BD | Nada: sigue en `/` |
| Franquicia que se va | Ya está fuera | Extraer filas | Se lleva su carpeta y su BD |
| Un solo escritor (Turso) | OK | Todos los locales compiten por un candado | OK: un candado por BD |

## Recomendación: C
Es A sin el coste de operar N servidores, y se puede volver a A cuando quiera (un local es una carpeta: se saca a su propio proceso sin migración). B se descarta: es la más cara de construir y la única donde un error de código mezcla datos de locales.

**Forma:** el local actual es la **matriz**. Su `servicio.yaml` lleva el módulo y la lista:
```yaml
modulos: [estadisticas, multilocal]
locales: {las-tablas: ../delifood-las-tablas}   # slug de URL -> carpeta de servicio normal
```
- `python -m motor web servicios/restaurante-pedidos` no cambia (ni el `Dockerfile`). Sin `multilocal` o sin `locales`, el comportamiento es idéntico al de hoy.
- La matriz sigue en `/` (enlaces, QR y sesiones de DeliFood intactos). Cada sucursal en `/<slug>/…` con las mismas rutas (`/<slug>/panel`, `/<slug>/repartidor/`). El slug se valida al arrancar contra las rutas reservadas (`menu`, `panel`, `central`, `estatico`…).
- **Roles:** dentro de cada local no cambia nada: `dueno` = gerente de ese local, `personal` = cocina/caja, `repartidor`. Rol nuevo `cadena`, solo en la BD de la matriz: entra a `/central`. Un gerente no ve otros locales porque su PIN solo existe en su BD.
- **Central (`/central`):** solo lectura y solo agregados: por local y total, lo que ya calcula `panel_web.estadisticas` (sin teléfonos ni nombres), más abierto/cerrado y pedidos en curso. No lista pedidos ni clientes.
- **Menú compartido:** precio y agotado ya son por local (tabla `platos` de cada BD). "Compartido" = acción *Publicar a locales* desde Central: copia nombre, categoría y foto por `id`; nunca pisa precio ni agotado del local. Local nuevo = copia del menú de la matriz.
- **WhatsApp:** un número por local (`whatsapp: {phone_id: ...}` en su `servicio.yaml`; no es secreto). Meta manda todos los números de la app a un solo webhook con `metadata.phone_number_id`: se enruta por ese id. Un token y un `APP_SECRET` para toda la cadena.
- **Ley 81:** una BD por local; el consentimiento se registra en el local donde entra el cliente; Central no trata datos personales. Borrado a petición = recorrer los locales por teléfono.

## Fases
| Fase | Qué | Archivos | Pruebas |
|---|---|---|---|
| **F1** Varios locales en un proceso | Cargar `locales`; despachador que elige el handler por primer segmento y recorta el prefijo; prefijo en páginas (`armar_pagina` ya las reescribe), manifest/sw del repartidor y `Path` de las 3 cookies; secreto por local = HMAC(`WEB_SECRET`, slug); semillas `PANEL_PIN_<SLUG>`, `REPARTIDORES_<SLUG>` (las sin sufijo quedan solo para la matriz); `lat/lon` del local en `servicio.yaml`; `/salud` comprueba todas las BD | `motor/__main__.py`, `web.py`, `panel_web.py`, `web_index.html`, `panel_web.html`, `repartidor.html`, `MOTOR.md` | `tests/test_multilocal.py`: pedido en A no aparece en B; cookie de cliente, panel y repartidor de A da 401/403 en B; PIN de A no abre B; slug reservado no arranca. Las 266 actuales pasan sin tocar |
| **F2** Central | `/central` con rol `cadena`, cookie propia, bloqueo de PIN como el panel | `motor/central.py`, `motor/central.html`, `panel_web.py` (rol, extraer el cálculo de `estadisticas` del chequeo de módulo), `web.py` | Suma = suma de locales; el JSON no contiene teléfono, nombre ni dirección; 404 sin módulo; `dueno` de sucursal recibe 403 |
| **F3** WhatsApp por local | Montar webhook y bucle de recordatorios en el servidor web; enrutar por `phone_number_id`; `enviar(..., phone_id)` | `whatsapp.py`, `web.py`, `servicio.yaml` | Mensaje al número de B responde con el menú de B; id desconocido se descarta; sin firma sigue 403 |
| **F4** Nube | `TURSO_DATABASE_URL_<SLUG>`/`TOKEN_<SLUG>`; `migrar` por local; medir memoria con 3-5 locales | `nucleo.py` (`conectar`), `migrar.py`, `render.yaml`, `fly.toml`, `despliegue/nube.md` | `test_nube`: dos locales → dos réplicas distintas; arranque falla claro si a una sucursal le faltan credenciales |
| **F5** Facilidad | Central: *Nuevo local* (clona config y menú de la matriz) y *Publicar a locales*; página `/locales` para que el cliente elija sucursal | `central.py`, `central.html`, `web_index.html` | Publicar no cambia precio ni agotado; local nuevo arranca vacío de pedidos y usuarios |

F1 sola ya es vendible (cada sucursal con su web, panel y repartidores). F2 es el argumento de venta de la cadena. F3 solo si usan WhatsApp por local. Cada fase se integra por separado.

## Riesgos
- **Sesiones cruzadas (el más serio):** con un `WEB_SECRET` común y `Path=/`, una cookie de un local valdría en otro. F1 no se integra sin secreto derivado + `Path` por local + su prueba. Revisión de seguridad con `revisor` al cerrar F1.
- **Un proceso = caída de todos los locales.** Aceptable hasta cierto tamaño; salida: sacar locales a otra instancia (ruta A) sin cambiar código.
- **Memoria:** una réplica libsql por local en 512 MB. Tope desconocido: medir en F4 antes de prometer un número de locales.
- **PWA del repartidor:** `scope`/`start_url` cambian por local; los ya instalados en la matriz no se tocan.
- **Franquicias:** si cada local es otra empresa, cada una es responsable de sus datos y Central necesita base contractual (`legal-datos`).

## Decisiones del usuario
1. ¿Aprobar ruta C?
2. ¿Los locales son de un mismo dueño o franquicias? (cambia aviso de privacidad y contrato; lo revisa `legal-datos`)
3. ¿WhatsApp: un número por local (recomendado) o un número para toda la cadena con el bot preguntando el local? (lo segundo no está diseñado aquí y es más caro)
4. ¿El dueño de cadena necesita entrar a operar cada local desde Central, o le basta ver cifras y tener un PIN en cada panel? (recomendado: lo segundo en v1)
5. ¿Menú: publicar desde la matriz (recomendado) o cada local por libre?
6. ¿Precio del módulo `multilocal`: por local adicional o tarifa plana? (`comercial`)
7. ¿URL: `/las-tablas/` bajo el mismo dominio (recomendado) o subdominio por local?
8. ¿Hasta qué fase construir ahora? (recomendado: F1 + F2 y enseñar)
