# Imagen para Render / Fly.io y el VPS. Guía: despliegue/nube.md
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt requirements-nube.txt ./
# tzdata: en Windows viene de requirements.txt; en slim hay que pedirlo
RUN pip install --no-cache-dir -r requirements.txt -r requirements-nube.txt tzdata
COPY motor/ motor/
COPY servicios/ servicios/
# Sin root (uid 10001). /datos nace suyo (un volumen nuevo hereda el dueño); en un VPS ya en marcha lo arregla el servicio `permisos` de docker-compose.
# servicios/ también: en Render/Fly el motor escribe ahí su BD y .web_secret.
RUN groupadd --system --gid 10001 motor && useradd --system --uid 10001 --gid 10001 --home-dir /app --shell /usr/sbin/nologin motor \
    && mkdir -p /datos && chown -R 10001:10001 /datos /app/servicios
USER 10001
# Render y Fly dan PORT (Render 10000; en fly.toml 8080). Local: 8080.
ENV PORT=8080
CMD ["sh", "-c", "exec python -m motor web servicios/restaurante-pedidos ${PORT}"]
