# Endurecimiento del servidor (VPS de HealthCore)

Scripts versionados para endurecer el VPS Ubuntu 22.04 donde corre el despliegue de HealthCore (`/opt/healthcore`, Docker Compose). Forman parte de la auditoría OWASP Top 10 ([`docs/security/owasp-top10-audit.md`](../../docs/security/owasp-top10-audit.md), controles A05 y A07 a nivel de host).

| Control | Antes (línea base) | Después |
| --- | --- | --- |
| Login SSH como root | `PermitRootLogin yes` | `PermitRootLogin no` |
| Contraseñas por SSH | `PasswordAuthentication yes` (192.555 contraseñas fallidas en `auth.log`, 99.255 contra root) | Solo clave pública (`AuthenticationMethods publickey`) |
| Usuario operativo | Solo `root` tiene shell | `deploy` (no root, `sudo` con contraseña, **fuera del grupo `docker`**, que equivale a root) |
| Quién puede entrar | Cualquier usuario | `AllowUsers deploy`, `MaxAuthTries 3` |
| Firewall | ufw inactivo, política INPUT ACCEPT | ufw: deny incoming; solo `22/tcp` con `limit` |
| Bloqueo de fuerza bruta | — | fail2ban (jail `sshd`: 5 fallos en 10 min → 1 h) |
| Permisos de carpetas | `/opt/healthcore` root:root, ficheros 644 (legibles por todos) | ver abajo |

## Permisos de carpetas

| Ruta | Qué contiene | Propietario | Modo |
| --- | --- | --- | --- |
| `/opt/healthcore` | Código del monorepo y `docker compose` | `deploy:healthcore` | directorios `750`, ficheros `640` (ejecutables `750`) |
| `/etc/healthcore` | Configuración sensible (ficheros env con secretos) | `root:healthcore` | `750`, ficheros `640` |
| `/opt/healthcore/**/.env` | Secretos locales | `deploy` | `600` |
| `/var/log/healthcore` | Logs de la aplicación (sin PHI) | `deploy:healthcore` | `750` |

Ningún fichero de la aplicación es legible por «otros». Los contenedores siguen leyendo su código porque Docker los ejecuta con su propio usuario. Las imágenes del repo ya corren como no-root: `app` (uid 10001) y `node`.

## Puertos

Solo SSH está expuesto a Internet. La aplicación publica sus puertos en `127.0.0.1` (`docker-compose.yml` / `compose.vps.yml`) y se usa a través de un túnel:

```bash
ssh -L 3001:127.0.0.1:3001 -L 8000:127.0.0.1:8000 deploy@VPS_HOST   # backoffice en http://localhost:3001
```

PostgreSQL, Redis, Qdrant, Keycloak, Flower y el gateway de modelos local (OpenClaw, `127.0.0.1:18789`) no son accesibles desde fuera. El túnel SSH cifra el tráfico. Si algún día se publica un dominio, el único puerto nuevo sería `443` (reverse proxy con TLS) y nunca la API ni la base de datos.

## Cómo se aplica (por etapas, sin riesgo de quedarse fuera)

```bash
scp infra/hardening/{harden.sh,verify.sh} ~/.ssh/<clave>.pub root@VPS_HOST:/root/
ssh root@VPS_HOST 'OPS_PASSWORD_FILE=/root/ops.pw bash /root/harden.sh user /root/<clave>.pub'
ssh deploy@VPS_HOST 'sudo -v'                                    # comprobar ANTES de cerrar root
ssh deploy@VPS_HOST 'sudo bash ~/harden.sh perms && sudo bash ~/harden.sh fail2ban && sudo bash ~/harden.sh firewall'
ssh deploy@VPS_HOST 'sudo bash ~/harden.sh ssh'                  # se niega si deploy no tiene clave
ssh deploy@VPS_HOST 'sudo bash ~/verify.sh'                      # PASS/FAIL por control
```

La etapa `ssh` valida la configuración (`sshd -t`) antes de recargar y no cierra las sesiones abiertas.

## Comprobación externa

```bash
ssh root@VPS_HOST true                                   # → Permission denied (publickey)
ssh -o PubkeyAuthentication=no deploy@VPS_HOST true      # → Permission denied (publickey): sin contraseñas
nc -z -w 3 VPS_HOST 22    # abierto
nc -z -w 3 VPS_HOST 9999  # cerrado, aunque haya un proceso escuchando en 0.0.0.0:9999
```

## Pendientes fuera del alcance de los scripts

- Hay un reinicio pendiente (`/var/run/reboot-required`) por actualizaciones del kernel. Conviene programarlo, porque reinicia también los otros servicios del VPS.
- OpenClaw (`127.0.0.1:18789`) corre como `root`. Solo escucha en loopback; conviene moverlo a un usuario propio.
- Activar también el Cloud Firewall de DigitalOcean con la misma regla (solo 22), como segunda capa.
