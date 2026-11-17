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

## 7. Copias de seguridad
- A `HORA_RESPALDO` el servicio `respaldo` copia cada `.sqlite` en caliente a `/datos/backups/AAAA-MM-DD/` (guarda `DIAS_RESPALDO` días) y, si hay `RESPALDO_DESTINO`, lo sube por rsync/SSH.
- `rsync --delete`: lo que se borra del VPS (a los `DIAS_RESPALDO` días) también desaparece del destino.
- Las copias contienen datos personales de clientes finales: cifrar el destino (disco/Storage Box cifrado). Revisión de `legal-datos` pendiente.
- Destino: otro servidor o Hetzner Storage Box. Clave: `ssh-keygen -t ed25519 -f ~/.ssh/respaldo_ed25519 -N ""` y añadir la `.pub` al destino.
- Ver: `docker compose logs respaldo` y `docker compose exec respaldo ls /datos/backups`.

## 8. Restaurar una copia
1. `docker compose stop alojar`
2. Listar copias: `docker compose exec respaldo ls /datos/backups` (si es de otro servidor, traerla con rsync a `/datos/backups`).
3. Copiar sobre el original (el archivo se llama como su ruta con `_`):
   `docker compose exec respaldo sh -c "cp /datos/backups/AAAA-MM-DD/<slug>_<archivo>.sqlite /datos/servicios/<slug>/<archivo>.sqlite && rm -f /datos/servicios/<slug>/*.sqlite-wal /datos/servicios/<slug>/*.sqlite-shm"`
4. `docker compose start alojar` y comprobar el panel del cliente.
5. Servidor perdido: repetir el apartado 1, traer `/datos/backups` desde el destino, restaurar y arrancar.

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
