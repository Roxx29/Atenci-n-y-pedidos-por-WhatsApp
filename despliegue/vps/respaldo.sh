#!/bin/sh
# Copias con datos personales: cifrar el destino (ver vps.md). Copia diaria de cada *.sqlite de /datos/servicios (backup en caliente, seguro con WAL) y rsync opcional.
while true; do
  ahora=$(date +%s)
  h=$(date -d "$(date +%F) ${HORA_RESPALDO:-03:30}" +%s)
  [ "$h" -le "$ahora" ] && h=$((h + 86400))
  sleep $((h - ahora))
  dir=/datos/backups/$(date +%F); mkdir -p "$dir"
  find /datos/servicios -name '*.sqlite' | while read -r f; do
    n=$(echo "${f#/datos/servicios/}" | tr '/' '_')
    sqlite3 "$f" ".backup '$dir/$n'" && echo "ok $n" || echo "FALLO $f"
  done
  cp /datos/clientes.yaml "$dir/" 2>/dev/null
  find /datos/backups -mindepth 1 -maxdepth 1 -type d -mtime +"${DIAS_RESPALDO:-14}" -exec rm -rf {} +
  if [ -n "$RESPALDO_DESTINO" ]; then
    rsync -a --delete -e "ssh -i /clave -o StrictHostKeyChecking=accept-new" /datos/backups/ "$RESPALDO_DESTINO/" \
      && echo "rsync ok" || echo "FALLO rsync"
  fi
done
