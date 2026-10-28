Положите сюда сертификат `robot.crt` и ключ `robot.key` для HTTPS.
Самоподписанный сертификат для проверки:

```bash
openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout robot.key -out robot.crt -subj "/CN=robot-op.local"
```
