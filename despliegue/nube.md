# Llevar el sistema a la nube (Render o Fly.io)

Hoy corre en el PC con Cloudflare Tunnel (`cloudflare.md`). Esta guía es para el día que se decida pasar a un servidor. Los archivos ya están: `Dockerfile`, `.dockerignore`, `render.yaml`, `fly.toml`.

**Por qué Turso:** el disco del servidor gratis es efímero (se borra en cada reinicio o despliegue). La BD y los comprobantes viven en Turso (SQLite en la nube). Sin `TURSO_*` el motor usa SQLite local, que en la nube se perdería.

**Antes de empezar:** pedir a `legal-datos` que revise en qué región se guardan los datos (Ley 81 de Panamá, datos personales de clientes) y confirmar la región de Turso y del servidor.

## 1. Turso (BD)
1. Cuenta en https://turso.tech → crear BD `restaurante`. Elegir región (la más cercana que acepte legal; p. ej. `aws-us-east-1`).
2. Anotar la URL: `libsql://restaurante-<usuario>.turso.io` → será `TURSO_DATABASE_URL`.
3. Crear token de la BD (dashboard → *Create token*, o `turso db tokens create restaurante`) → será `TURSO_AUTH_TOKEN`. No pegarlo en chats ni en git.

## 2. Pasar los datos actuales
En el PC, con `pip install -r requirements-nube.txt` hecho:
```
set TURSO_DATABASE_URL=libsql://...
set TURSO_AUTH_TOKEN=...
python -m motor migrar servicios\restaurante-pedidos
```
Copia pedidos, ajustes, usuarios y comprobantes a Turso. Antes, guardar una copia del archivo `.sqlite` del servicio. Repetir justo antes del cambio final, con la web parada, para no perder pedidos.

## 3A. Render
1. Repo en GitHub → Render → *New → Blueprint* → elegir el repo. Lee `render.yaml`.
2. Render pide los secretos; pegar:
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - `DEEPSEEK_API_KEY`
   - `WEB_SECRET`: texto largo al azar (`python -c "import secrets;print(secrets.token_hex(32))"`). **Obligatorio**: sin él, cada reinicio invalida las sesiones.
   - `PANEL_PIN` (6 dígitos), `REPARTIDORES` (igual que en el `.env` actual)
   - `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` (los que ya uses)
3. Plan `free`: se duerme a los 15 min sin tráfico y tarda ~1 min en despertar. Para producción cambiar a `plan: starter` ($7/mes).
4. Comprobar `https://<app>.onrender.com/salud` → 200.

## 3B. Fly.io
```
fly auth login
fly launch --no-deploy --copy-config
fly secrets set TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... DEEPSEEK_API_KEY=... WEB_SECRET=... PANEL_PIN=... REPARTIDORES=... WHATSAPP_TOKEN=... WHATSAPP_PHONE_ID=... WHATSAPP_VERIFY_TOKEN=... WHATSAPP_APP_SECRET=...
fly deploy
fly status
curl https://<app>.fly.dev/salud
```
Región `mia` (Miami). Si `fly platform regions` lista `bog`, cambiar `primary_region`. La máquina no se apaga (`auto_stop_machines = "off"`).

## 4. Dominio restaurantes.nubiksoft.com
Hoy lo sirve el túnel de Cloudflare (CNAME a `<id>.cfargotunnel.com`).
1. Probar primero con la URL `onrender.com` / `fly.dev`.
2. Render: *Settings → Custom Domains* → añadir el dominio. Fly: `fly certs add restaurantes.nubiksoft.com`. Ambos indican qué registro crear.
3. Cloudflare → DNS: borrar el CNAME del túnel y crear el indicado (CNAME a `<app>.onrender.com` o `<app>.fly.dev`). Dejarlo en **DNS only** (gris) hasta que el certificado quede emitido; luego puede ser naranja con SSL *Full*.
4. Si WhatsApp usa webhook: es la misma ruta y dominio; comprobar en Meta que sigue verificado.
5. Cerrar la ventana del túnel (`publicar.cmd`).

## 5. Volver al PC
1. Cloudflare → DNS: quitar el CNAME de la nube y restaurar el del túnel (la ruta pública `restaurantes.nubiksoft.com` → `localhost:8080` sigue en Zero Trust si no se borró).
2. Si hubo pedidos nuevos en la nube, exportarlos antes (`turso db shell restaurante .dump`); no hay migración inversa.
3. Arrancar `Encender.cmd`.

## Pendientes (para agentedeprogramacion)
- **IP real detrás del proxy.** Resuelto con `PROXY=1` (ya puesto en `render.yaml` y `fly.toml`): en Fly vale `Fly-Client-IP` (solo si existe `FLY_APP_NAME`, porque fuera de Fly el cliente la puede escribir); en Render, el ÚLTIMO valor de `X-Forwarded-For` (el primero lo escribe el cliente). Si hay otro proxy de confianza delante (CDN), `PROXY_SALTOS=2`. Comprobar tras desplegar que dos visitantes distintos no comparten IP.
- **Un solo proceso escritor.** Con Turso no hay `BEGIN IMMEDIATE`: no escalar a más de 1 instancia (Fly `min_machines_running = 1`, sin autoescalado; Render 1 instancia) ni abrir el bot y el panel de escritorio a la vez contra la misma BD.
- El Dockerfile no se pudo construir aquí (no hay Docker). Probar con `docker build -t restaurante .` y `docker run -p 8080:8080 restaurante`.
- `requirements-nube.txt` (libsql), `migrar` y `/salud` los añade otro agente; el build falla hasta que estén en la rama.
