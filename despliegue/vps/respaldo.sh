#!/bin/sh
# Copia diaria de cada *.sqlite de /datos/servicios (backup en caliente, seguro con WAL), clientes.yaml y los registros de seguridad.
# Va a /respaldos (volumen que SOLO monta este contenedor: la app expuesta no puede cifrarlas ni borrarlas) y, con AGE_DESTINATARIO,
# cada archivo se cifra con age (clave publica; la privada vive fuera del VPS). rsync sube solo lo nuevo y nunca borra ni pisa en el destino.
set -u
while true; do
  ahora=$(date +%s)
  h=$(date -d "$(date +%F) ${HORA_RESPALDO:-03:30}" +%s)
  [ "$h" -le "$ahora" ] && h=$((h + 86400))
  sleep $((h - ahora))
  dir=/respaldos/$(date +%F); tmp=$(mktemp -d); mkdir -p "$dir"; chmod 700 /respaldos "$dir"
  find /datos/servicios -name '*.sqlite' | while read -r f; do
    n=$(echo "${f#/datos/servicios/}" | tr '/' '_')
    sqlite3 "$f" ".backup '$tmp/$n'" && echo "ok $n" || echo "FALLO $f"
  done
  cp /datos/clientes.yaml "$tmp/" 2>/dev/null
  [ -d /datos/logs ] && tar czf "$tmp/logs.tgz" -C /datos logs
  for f in "$tmp"/*; do
    [ -f "$f" ] || continue
    if [ -n "${AGE_DESTINATARIO:-}" ]; then
      age -r "$AGE_DESTINATARIO" -o "$dir/$(basename "$f").age" "$f" || echo "FALLO cifrado $(basename "$f")"
    else
      echo "AVISO: sin AGE_DESTINATARIO la copia queda sin cifrar"; cp "$f" "$dir/"
    fi
  done
  rm -rf "$tmp"
  chmod -R go-rwx "$dir"
  find /respaldos -mindepth 1 -maxdepth 1 -type d -mtime +"${DIAS_RESPALDO:-14}" -exec rm -rf {} +
  if [ -n "${RESPALDO_DESTINO:-}" ]; then
    # sin --delete y con --ignore-existing: una copia ya subida no se borra ni se sobrescribe desde aqui (ransomware en el VPS no la alcanza)
    rsync -a --ignore-existing -e "ssh -i /clave -o StrictHostKeyChecking=accept-new" /respaldos/ "$RESPALDO_DESTINO/" \
      && echo "rsync ok" || echo "FALLO rsync"
  fi
done
