# VPS único para ~20 clientes (Docker + Caddy)

Un servidor, todos los clientes, cada uno con su dominio y HTTPS automático. Archivos en `despliegue/vps/`. Los comandos `alojar` y `operador` del motor los escribe agentedeprogramacion; hasta que estén en la rama, esos servicios no arrancan.

## 1. Crear el VPS
1. Hetzner Cloud, Ubuntu 24.04, plan CX22 (2 vCPU, 4 GB). Región que acepte `legal-datos` (Ley 81). Activar copias del proveedor (+20%).
2. Entrar por SSH e instalar Docker: `curl -fsSL https://get.docker.com | sh`.
3. `git clone https://github.com/Roxx29/Atenci-n-y-pedidos-por-WhatsApp bot && cd bot/despliegue/vps`
4. `cp .env.ejemplo .env && chmod 600 .env` y rellenar (secretos solo aquí).
5. Crear datos: `docker compose run --rm alojar sh -c "mkdir -p /datos/servicios /datos/backups"` y dejar un `clientes.yaml` inicial en `/datos` (formato: el que defina el motor).
6. `docker compose up -d --build`. Comprobar `docker compose ps` y `docker compose logs -f caddy`.

## 2. Consola del operador: acceso
- Caddy solo deja pasar a `operador.<dominio>` las IPs de `OPERADOR_IPS` (en `.env`, separadas por espacio); el resto recibe 403. Con IP dinámica, usa el túnel.
- Alternativa túnel SSH: quita el bloque `operador.` del Caddyfile, publica `127.0.0.1:8081:8081` en el servicio `operador` y entra con `ssh -L 8081:127.0.0.1:8081 usuario@vps` → `http://127.0.0.1:8081` (con `OPERADOR_HOST=0.0.0.0` la cookie lleva `Secure`: sobre http local no se guarda; usa un túnel con `OPERADOR_HOST=127.0.0.1` o el dominio).
- Clave de la consola: mínimo 12 caracteres; 5 fallos por IP bloquean esa IP 5 minutos.
- Pausar/Activar y las altas escriben `clientes.yaml` y `alojar` lo recarga solo (sin reiniciar). Cambios de variables de un cliente ya abierto sí piden `docker compose restart alojar`. Restaurar una copia de su `datos.sqlite` o recrear su carpeta no: `alojar` lo detecta y reabre su BD en la siguiente petición.
- Variables del panel: `PANEL_PUBLICO=1` (o `PANEL_PUBLICO_<SLUG>=1`) permite `/panel` por dominio (siempre con PIN); `ALOJAR_HOST=0.0.0.0` solo dentro de Docker. Ya van fijadas en `docker-compose.yml` para `alojar`.

## 3. Firewall
Hetzner Firewall (o `ufw`): solo 22 (mejor limitado a tu IP), 80 y 443. El 80 hace falta para emitir certificados. Los puertos 8080/8081 no se publican.

## 4. DNS
1. Tu dominio: registro A `operador` hacia la IP del VPS.
2. Cada cliente crea en su dominio un A hacia la IP (o un CNAME a un nombre tuyo que apunte al VPS). Pídeselo por escrito.
3. Comprobar: `dig +short su-dominio.com` devuelve la IP. Solo entonces dar de alta.

## 5. Alta de cliente nuevo
Todo desde la consola, sin tocar el servidor ni el `.env`:
1. Consola → *Nuevo cliente*: nombre, slug, dominio y, opcionales, PIN del dueño (6 dígitos; vacío = la consola genera uno), color de marca (`#rrggbb`) y días de prueba (7, 15 o 30).
2. La consola copia `servicios/_plantilla-restaurante` (de la imagen) a `/datos/servicios/<slug>`, crea al usuario `Dueno` en la BD de ese cliente (solo el hash del PIN) y lo registra en `/datos/clientes.yaml`; `alojar` lo carga solo.
3. Apunta el PIN: se muestra UNA vez y no queda guardado en claro en ningún sitio. Si se pierde, el dueño (u otro dueño) lo cambia en el panel, Equipo.
4. Abrir `https://su-dominio.com`: el primer acceso emite el certificado (segundos). Si falla: `docker compose logs caddy`. Panel: `https://su-dominio.com/panel`.
5. El restaurante completa desde su panel el menú, horario, datos de pago y teléfono (vienen de ejemplo). El aviso de privacidad (`/datos/servicios/<slug>/aviso.md`) queda PENDIENTE: rellenar lo que va entre [corchetes] y poner la fecha en `web.aviso_version` antes de abrir al público.
6. Solo si ese cliente lleva variables propias (`REPARTIDORES_<SLUG>`, `TURSO_*_<SLUG>`...): `.env` y `docker compose up -d`. `PANEL_PIN_<SLUG>` ya no hace falta para clientes nuevos.

## 6. Actualizar (llega a todos los clientes)
```
cd ~/bot && git pull && cd despliegue/vps && docker compose up -d --build
```
Los datos viven en el volumen `datos` y no se tocan. Si falla: `git checkout <commit-anterior>` y repetir.
- Endurecimiento (2026-11-25): `alojar` y `operador` corren sin root (uid 10001), sin capacidades, `no-new-privileges` y con el sistema de archivos de solo lectura (escriben solo en `/datos` y `/tmp`). El servicio `permisos` hace `chown -R 10001 /datos` en cada arranque, así que un VPS creado antes (archivos de root) sigue funcionando sin pasos a mano. Si algo no arranca: `docker compose logs permisos alojar`.
- Antes del primer `up` con esta versión: poner `DATOS_CLAVE` y `AGE_DESTINATARIO` en `.env` (ver `.env.ejemplo`). Al arrancar con `DATOS_CLAVE`, los teléfonos de contacto que estaban en claro se cifran solos.

## 7. Copias de seguridad
- A `HORA_RESPALDO` el servicio `respaldo` copia cada `.sqlite` en caliente, `clientes.yaml` y `/datos/logs` al volumen `respaldos` (`/respaldos/AAAA-MM-DD/`, guarda `DIAS_RESPALDO` días). Ese volumen solo lo monta `respaldo`: `alojar` y `operador` (lo expuesto a internet) no pueden cifrarlo ni borrarlo.
- Cifrado con age: en TU equipo, `age-keygen -o respaldo-clave.txt` (guárdala fuera del VPS, p. ej. gestor de contraseñas + copia offline) y pon la línea `# public key: age1...` en `AGE_DESTINATARIO`. Cada archivo queda como `*.age`; sin la clave privada no se lee. Sin `AGE_DESTINATARIO` se copian sin cifrar y el registro avisa.
- `RESPALDO_DESTINO`: rsync/SSH **sin `--delete` y con `--ignore-existing`**: lo ya subido no se borra ni se sobrescribe desde el VPS. En el destino, limpia lo viejo allí (o usa instantáneas de la Storage Box). Mejor aún: limita la clave SSH del VPS a solo escribir (`command="rrsync -wo /backups/bot"` en el `authorized_keys` del destino) para que un VPS comprometido no pueda borrar las copias remotas.
- Destino: otro servidor o Hetzner Storage Box. Clave: `ssh-keygen -t ed25519 -f ~/.ssh/respaldo_ed25519 -N ""` y añadir la `.pub` al destino.
- Ver: `docker compose logs respaldo` y `docker compose exec respaldo ls /respaldos`. Las copias viejas de `/datos/backups/` (versiones anteriores) se pueden borrar a mano: `docker compose exec respaldo rm -rf /datos/backups/20*`.

## 8. Restaurar una copia
1. `docker compose stop alojar`
2. Traer la copia a tu equipo (`docker compose cp respaldo:/respaldos/AAAA-MM-DD ./copia` o desde el destino remoto) y descifrarla allí: `age -d -i respaldo-clave.txt -o <slug>_<archivo>.sqlite <slug>_<archivo>.sqlite.age`.
3. Subir el `.sqlite` descifrado al VPS y copiarlo sobre el original (el archivo se llama como su ruta con `_`):
   `docker compose cp <slug>_<archivo>.sqlite respaldo:/tmp/r.sqlite && docker compose exec respaldo sh -c "cp /tmp/r.sqlite /datos/servicios/<slug>/<archivo>.sqlite && rm -f /tmp/r.sqlite /datos/servicios/<slug>/*.sqlite-wal /datos/servicios/<slug>/*.sqlite-shm"`. Borra la copia descifrada de tu equipo al terminar.
4. `docker compose up -d` (el servicio `permisos` deja el archivo para el uid 10001) y comprobar el panel del cliente.
5. Servidor perdido: repetir el apartado 1 (con la MISMA `DATOS_CLAVE`: sin ella los teléfonos y correos cifrados no se leen), traer las copias desde el destino, restaurar y arrancar.

## 9b. Registro de seguridad
- `alojar` escribe `/datos/logs/seguridad.log` y `operador` `/datos/logs/seguridad-operador.log`: una línea JSON por evento (entradas al panel/repartidor/consola correctas y fallidas, IP bloqueada, acceso saturado, firma de webhook falsa). Sin PIN, claves, tokens, teléfonos ni correos. Rota a medianoche y guarda `SEGURIDAD_DIAS` días (180 por defecto, nunca menos de 90). Va también en la copia diaria (`logs.tgz`).
- Alertas: con `ALERTA_URL` (p. ej. `https://ntfy.sh/<tema-largo-y-secreto>`), cada evento de nivel `alerta` se manda por POST al momento. Revisar: `docker compose exec respaldo tail -n 50 /datos/logs/seguridad.log`.
- Los `docker compose logs` rotan a 10 × 10 MB por contenedor.

## 10. Entrar con Google (panel y repartidor)
Opcional, además del PIN. Solo entra un correo que el dueño haya autorizado antes en su panel (Equipo → «Google» en la fila de la persona) y si esa persona está activa.
1. https://console.cloud.google.com → crear (o elegir) un proyecto.
2. *APIs y servicios → Pantalla de consentimiento de OAuth*: tipo **Externo**, nombre de la app, correo de soporte; ámbitos solo `openid` y `email`. Publicarla («En producción»; no pide verificación con esos ámbitos).
3. *APIs y servicios → Credenciales → Crear credenciales → ID de cliente de OAuth* → tipo **Aplicación web**.
4. *Orígenes de JavaScript autorizados*: `https://<dominio de cada cliente>` (uno por línea; sin ruta ni barra final). Sin URI de redirección (el botón no las usa). Cada cliente nuevo = añadir su dominio aquí (tarda unos minutos en valer).
5. Copiar el ID (`….apps.googleusercontent.com`) en `.env` como `GOOGLE_CLIENT_ID=` (o `GOOGLE_CLIENT_ID_<SLUG>=` para un cliente con ID propio) y `docker compose up -d`. No hay secreto de cliente: el servidor solo verifica el token en `oauth2.googleapis.com/tokeninfo` (aud = este ID, `email_verified`, caducidad).
6. Sin `GOOGLE_CLIENT_ID` el botón no aparece y `POST /panel/google` da 404.

## 9. Costes (aprox., 20 clientes)
| Concepto | Coste |
|---|---|
| VPS Hetzner CX22 | ~4-5 EUR/mes |
| Copias del proveedor | ~1 EUR/mes |
| Storage Box para copias (opcional) | ~4 EUR/mes |
| HTTPS, Caddy, Docker | 0 |
| Dominio propio (operador) | ~12 USD/año |
| IA (DeepSeek) y WhatsApp (Meta) | por uso, ver `comercial` |

Fijo: ~10 EUR/mes, menos de 1 EUR por cliente. Verificar precios vigentes en hetzner.com.

## Límites
- Un solo proceso `alojar`: si cae, caen todos (`restart: unless-stopped` lo levanta). Vigilar `/salud`.
- Un VPS es un punto de fallo: las copias remotas no son opcionales en producción.
- Let's Encrypt limita certificados nuevos por dominio y semana: dar de alta en tandas.
