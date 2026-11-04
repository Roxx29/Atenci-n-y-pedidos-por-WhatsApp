# Imagen para Render / Fly.io. Guía: despliegue/nube.md
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt requirements-nube.txt ./
# tzdata: en Windows viene de requirements.txt; en slim hay que pedirlo
RUN pip install --no-cache-dir -r requirements.txt -r requirements-nube.txt tzdata
COPY motor/ motor/
COPY servicios/ servicios/
# Render y Fly dan PORT (Render 10000; en fly.toml 8080). Local: 8080.
ENV PORT=8080
CMD ["sh", "-c", "exec python -m motor web servicios/restaurante-pedidos ${PORT}"]
