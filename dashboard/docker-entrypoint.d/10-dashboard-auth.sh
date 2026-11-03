#!/bin/sh
# Enable HTTP basic auth on the dashboard when DASHBOARD_PASSWORD is set.
# nginx:alpine runs every script in /docker-entrypoint.d before starting nginx.
set -eu

AUTH_CONF=/etc/nginx/auth.conf
HTPASSWD=/etc/nginx/.htpasswd

if [ -n "${DASHBOARD_PASSWORD:-}" ]; then
  USER="${DASHBOARD_USER:-owner}"
  htpasswd -bBc "$HTPASSWD" "$USER" "$DASHBOARD_PASSWORD" >/dev/null 2>&1
  cat > "$AUTH_CONF" <<EOF
auth_basic "FraudStream";
auth_basic_user_file $HTPASSWD;
EOF
  echo "dashboard: basic auth enabled for user '$USER'"
else
  : > "$AUTH_CONF"
  echo "dashboard: no DASHBOARD_PASSWORD set, serving without authentication"
fi
