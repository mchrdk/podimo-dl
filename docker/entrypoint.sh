#!/bin/sh
set -eu

: "${CRON_SCHEDULE:=0 3 * * *}"
: "${PODIMO_ARGS:=--config --download -o /downloads}"
: "${RUN_ON_STARTUP:=false}"

cat > /etc/cron.d/podimo <<EOF
SHELL=/bin/sh
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
$CRON_SCHEDULE root cd /app && python podimo-dl.py $PODIMO_ARGS >> /proc/1/fd/1 2>&1
EOF

chmod 0644 /etc/cron.d/podimo
crontab /etc/cron.d/podimo

if [ "$RUN_ON_STARTUP" = "true" ]; then
  echo "[startup] Running initial download..."
  sh -c "cd /app && python podimo-dl.py $PODIMO_ARGS"
fi

echo "[cron] Schedule: $CRON_SCHEDULE"
echo "[cron] Args: $PODIMO_ARGS"
exec cron -f
