# Переезд OXXOM OBLIK: Mac → GitHub → Ubuntu

Подготовлено 16.11.2026. Команды выполняет владелец. На сервере ещё ничего
не установлено этим обновлением, репозиторий ещё не опубликован.

Схема: **Nginx HTTPS → Gunicorn/systemd → PostgreSQL 16 на Ubuntu 24.04 LTS**.
Это первая установка на отдельную VM. Скрипт восстановления отказывается
заменять существующие базу или роль. На общем сервере сначала выполнить аудит
и согласовать порты/пакеты; не выполнять раздел установки вслепую.

Пароли, `.env`, `.secrets`, дампы и приватные SSH-ключи не отправлять в GitHub
или чат. Программа переноса сохраняет текущие Django/MFA-ключи и создаёт новый
пароль серверной роли PostgreSQL. Существующие пользователи, документы и TOTP
переносятся с дампом. На новом домене потребуется обычный вход с MFA.

## 1. Mac: GitHub

Каждый блок выполнять отдельно. Если команда завершилась ошибкой, дальше
не продолжать. В текущем проекте Git уже инициализирован, коммитов и remote нет.

```zsh
cd "/Users/vaceslavkozub/Documents/ChatGPT/OXXOM Облік/oxxom-oblik"
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status
```

Если `gh: command not found`, использовать установленный CLI:

```zsh
export PATH="$HOME/Documents/Codex/tools/gh_2.103.3_macOS_arm64/bin:$PATH"
```

Проверить автора коммита:

```zsh
git var GIT_AUTHOR_IDENT
```

Если Git просит имя/email, задать их **только для этого проекта**:

```zsh
read "OXXOM_GIT_NAME?Имя автора коммитов: "
read "OXXOM_GIT_EMAIL?Email GitHub или GitHub noreply: "
git config user.name "$OXXOM_GIT_NAME"
git config user.email "$OXXOM_GIT_EMAIL"
```

Подготовить исходники и проверить, что локальные секреты не попали в коммит:

```zsh
git add .gitignore .env.example README.md requirements.txt requirements-production.txt compose.yaml backend scripts deploy docs tests app server.py OXXOM-Oblik-Prototype.html start-core.command start.command
"$HOME/OXXOM-Dev/venv/bin/python" scripts/check-git-content.py
git diff --cached --stat
```

После успешной проверки:

```zsh
git commit -m "Initial OXXOM OBLIK application and Ubuntu deployment profile"
gh repo create oxxom-platform/oxxom-oblik --private --source=. --remote=origin --push
git remote -v
gh repo view --json nameWithOwner,isPrivate,url
```

Требуется право создания репозиториев в `oxxom-platform`. При ошибке доступа
не менять владельца молча: выдать право или явно выбрать личный репозиторий.
Если репозиторий уже существует, не повторять `repo create`, а проверить его
содержимое и подключить существующий remote. Не использовать force push.

Проверка секретов ищет запрещённые пути, приватные ключи и действующие локальные
пароли/ключи в staged-файлах. Она не гарантирует поиск всех неизвестных секретов
и не сканирует историю. Перед публикацией проверить список файлов.

## 2. Ubuntu: только аудит

Подключиться по SSH своим административным пользователем. Выполнить:

```bash
cat /etc/os-release
uname -m
nproc
free -h
df -h /
timedatectl status
sudo -v
sudo ss -lntp
sudo systemctl --no-pager status nginx postgresql oxxom-oblik 2>/dev/null || true
command -v docker || true
sudo ufw status verbose
```

Проверить Ubuntu 24.04 LTS, свободные 80/443/4181 и отсутствие конфликтующей
PostgreSQL на 5432. Для старта: 2 vCPU, 4 GB RAM, 40 GB SSD; для пілота —
4 vCPU, 8 GB RAM, 80 GB SSD. Если уже есть сервисы HUB, другой Nginx или базы,
сначала адаптировать план. Firewall автоматически не менять; сохранить SSH.

## 3. Ubuntu: пакеты и ключ чтения GitHub

Далее команды **для отдельной VM**, после аудита. Запустить Bash; при любой
ошибке он остановится. Глобальное обновление ОС не выполняется.

```bash
bash
set -euo pipefail
sudo apt-get update
sudo apt-get install -y git python3-venv postgresql-16 postgresql-client-16 nginx certbot age
sudo systemctl enable --now postgresql nginx
install -d -m 700 "$HOME/.ssh"
test ! -e "$HOME/.ssh/oxxom-oblik-deploy"
ssh-keygen -t ed25519 -C "oxxom-oblik Ubuntu read-only deploy" -f "$HOME/.ssh/oxxom-oblik-deploy" -N ""
cat "$HOME/.ssh/oxxom-oblik-deploy.pub"
```

На Mac скопировать показанный **публичный** ключ в буфер и выполнить:

```zsh
pbpaste > /tmp/oxxom-oblik-deploy.pub
ssh-keygen -lf /tmp/oxxom-oblik-deploy.pub
gh repo deploy-key add /tmp/oxxom-oblik-deploy.pub --repo oxxom-platform/oxxom-oblik --title "Ubuntu OBLIK read-only"
```

Флаг `--allow-write` не добавлять. Серверу достаточно чтения одного репозитория.
Приватный ключ остаётся на Ubuntu. При отзыве авторизации GitHub CLI такой
deploy key может быть отозван — проверить его перед следующим обновлением.

На Ubuntu:

```bash
sudo install -d -m 755 -o "$USER" -g "$(id -gn)" /srv/oxxom-oblik
test ! -e /srv/oxxom-oblik/app
export GIT_SSH_COMMAND="ssh -i $HOME/.ssh/oxxom-oblik-deploy -o IdentitiesOnly=yes"
git clone git@github.com:oxxom-platform/oxxom-oblik.git /srv/oxxom-oblik/app
cd /srv/oxxom-oblik/app
git config core.sshCommand "ssh -i $HOME/.ssh/oxxom-oblik-deploy -o IdentitiesOnly=yes"
python3 -m venv /srv/oxxom-oblik/venv
/srv/oxxom-oblik/venv/bin/python -m pip install -r requirements-production.txt
sudo useradd --system --user-group --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin oxxom
sudo install -d -m 750 -o root -g oxxom /etc/oxxom-oblik
sudo -u oxxom test -r /srv/oxxom-oblik/app/backend/manage.py
sudo -u oxxom test -x /srv/oxxom-oblik/venv/bin/python
```

При первом SSH-подключении к GitHub сравнить fingerprint с официальным списком
GitHub. Не отключать `StrictHostKeyChecking`. При уже существующем пользователе
`oxxom` остановиться и проверить его назначение, не менять его права автоматически.

## 4. Mac: финальный дамп и ключи

До финального дампа остановить локальный Django через Ctrl+C. Docker с базой
оставить включённым. После дампа не менять документы на Mac до завершения
переезда: новые записи в этот дамп не попадут. Это окно переноса данных.

Нужен выбранный DNS-домен с A-записью на сервер. Если есть AAAA, IPv6 тоже
должен вести на этот сервер. Внешние 80/443 должны проходить до Nginx.
Для LAN/VPN без публичного домена нужен отдельный вариант с доверенным TLS;
не включать production с `DEBUG=true` и не отключать Secure cookies.

```zsh
cd "/Users/vaceslavkozub/Documents/ChatGPT/OXXOM Облік/oxxom-oblik"
read "OXXOM_DOMAIN?Домен сервиса без https://: "
read "OXXOM_SSH?SSH-адрес user@ip-сервера: "
OXXOM_TRANSFER="$(mktemp -d "$HOME/OXXOM-transfer.XXXXXX")"
rmdir "$OXXOM_TRANSFER"
"$HOME/OXXOM-Dev/venv/bin/python" scripts/prepare-ubuntu-transfer.py --domain "$OXXOM_DOMAIN" --output "$OXXOM_TRANSFER"
```

Отдельным блоком, после успешного экспорта:

```zsh
ssh "$OXXOM_SSH" 'umask 077; mkdir -m 700 "$HOME/oxxom-transfer"'
scp "$OXXOM_TRANSFER/database.dump" "$OXXOM_TRANSFER/app.env" "${OXXOM_SSH}:oxxom-transfer/"
```

Исходная база на Mac сохраняется. Пакет переноса содержит реальные данные и
MFA-ключ: хранить отдельно от GitHub, с ограниченными правами. Сохранить ключ
в отдельном защищённом хранилище восстановления до удаления временных копий.

## 5. Ubuntu: восстановление и проверки

На Ubuntu, в том же Bash с `set -euo pipefail`:

```bash
cd /srv/oxxom-oblik/app
sudo install -m 640 -o root -g oxxom "$HOME/oxxom-transfer/app.env" /etc/oxxom-oblik/app.env
sudo /srv/oxxom-oblik/venv/bin/python deploy/ubuntu/restore-first-install.py --archive "$HOME/oxxom-transfer/database.dump"
sudo -u oxxom /srv/oxxom-oblik/venv/bin/python deploy/ubuntu/manage.py check --deploy
sudo -u oxxom /srv/oxxom-oblik/venv/bin/python deploy/ubuntu/manage.py migrate --noinput
sudo -u oxxom /srv/oxxom-oblik/venv/bin/python deploy/ubuntu/manage.py migrate --check
sudo install -m 644 -o root -g root deploy/ubuntu/oxxom-oblik.service /etc/systemd/system/oxxom-oblik.service
sudo systemctl daemon-reload
sudo systemctl enable --now oxxom-oblik
sudo systemctl --no-pager --full status oxxom-oblik
```

Текущая политика HSTS действует 3600 секунд только для этого hostname, без
`includeSubDomains` и preload. Поэтому `check --deploy` показывает W005/W021;
это известные ограничения текущего профиля. Не включать их автоматически для
всех поддоменов. Другие предупреждения разобрать до публичного запуска.

Скрипт восстановления создаёт отдельную роль без superuser/CREATEDB/CREATEROLE
и новую базу; импорт идёт одной транзакцией. При ошибке база/роль могут остаться
созданными: повторный запуск остановится. Не удалять их вслепую — сначала
проверить причину. Не запускать `create_owner`: владелец уже есть в дампе.

Здесь не нужен `collectstatic`: текущие assets обслуживаются приложением
через явный разрешённый список URL, включая FileResponse под Gunicorn.

## 6. Ubuntu: домен, HTTPS и Nginx

На Ubuntu ввести тот же домен и email для сертификата. Убедиться, что данный
server_name ещё не используется другим vhost; не удалять чужие конфигурации.

```bash
read -r -p "Домен сервиса: " OXXOM_DOMAIN
read -r -p "Email для сертификата: " OXXOM_TLS_EMAIL
cd /srv/oxxom-oblik/app
test ! -e /etc/nginx/sites-available/oxxom-oblik
test ! -e /etc/nginx/sites-enabled/oxxom-oblik
sudo install -d -m 755 /var/www/letsencrypt
python3 deploy/ubuntu/render-nginx.py --domain "$OXXOM_DOMAIN" --bootstrap | sudo tee /etc/nginx/sites-available/oxxom-oblik >/dev/null
sudo ln -s /etc/nginx/sites-available/oxxom-oblik /etc/nginx/sites-enabled/oxxom-oblik
sudo nginx -t
sudo systemctl reload nginx
sudo certbot certonly --webroot -w /var/www/letsencrypt --cert-name "$OXXOM_DOMAIN" -d "$OXXOM_DOMAIN" --email "$OXXOM_TLS_EMAIL" --agree-tos --non-interactive
sudo cp /etc/nginx/sites-available/oxxom-oblik /etc/nginx/sites-available/oxxom-oblik.bootstrap
python3 deploy/ubuntu/render-nginx.py --domain "$OXXOM_DOMAIN" | sudo tee /etc/nginx/sites-available/oxxom-oblik >/dev/null
sudo nginx -t
sudo systemctl reload nginx
```

Gunicorn слушает только 127.0.0.1:4181, PostgreSQL — loopback/Unix socket.
Внешние 4181 и 5432 не открывать. Nginx заменяет forwarded-заголовки;
`TRUST_PROXY=true` применяется только за этим доверенным прокси.
Начальный HTTP-vhost отдаёт 503 до установки сертификата.

Продление сертификата с reload Nginx:

```bash
printf '%s\n' '#!/bin/sh' 'nginx -t && systemctl reload nginx' | sudo tee /etc/letsencrypt/renewal-hooks/deploy/oxxom-nginx >/dev/null
sudo chmod 755 /etc/letsencrypt/renewal-hooks/deploy/oxxom-nginx
sudo systemctl enable --now certbot.timer
sudo certbot renew --dry-run
```

При ошибке Nginx восстановить только созданный vhost из `.bootstrap`, проверить
`nginx -t`, затем reload. Нельзя заменять глобальный `/etc/nginx/nginx.conf`.

## 7. Проверка переезда

```bash
curl --fail --silent --show-error "https://$OXXOM_DOMAIN/api/v1/health"
curl --fail --silent --show-error "https://$OXXOM_DOMAIN/api/v1/ready"
curl --fail --silent --show-error --output /dev/null "https://$OXXOM_DOMAIN/login/"
curl --fail --silent --show-error --output /dev/null "https://$OXXOM_DOMAIN/assets/ui/translations.js"
sudo journalctl -u oxxom-oblik -n 80 --no-pager
sudo ss -lntp
```

В браузере: действующий владелец → пароль → прежний MFA → язык → организация
→ прежние документы → сверить остатки, суммы, оплаты и права пользователя.
Убедиться, что 5432/4181 не слушают публичные адреса. `ready` проверяет
доступность БД и отсутствие неприменённых миграций.

После окна обслуживания и проверенного резервного копирования выполнить
`sudo reboot`, подключиться заново и повторить health/ready и вход.
Исходную базу Mac не удалять; дальнейшую работу вести только на Ubuntu.

## 8. Резервная копия перед дальнейшими изменениями

Это **ручная зашифрованная копия**, ещё не ежедневная система бекапов.
Выполнять в Bash с `set -euo pipefail`; age запрашивает пароль интерактивно.
Пароли шифрования хранить отдельно от файлов резервных копий.

```bash
umask 077
OXXOM_BACKUP="$HOME/oxxom-backup-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -m 700 "$OXXOM_BACKUP"
sudo -u postgres pg_dump --format=custom --no-owner --no-acl oxxom_oblik | age -p -o "$OXXOM_BACKUP/database.dump.age"
sudo tar -C / -czf - etc/oxxom-oblik/app.env | age -p -o "$OXXOM_BACKUP/keys.tar.gz.age"
git -C /srv/oxxom-oblik/app rev-parse HEAD > "$OXXOM_BACKUP/commit.txt"
```

Скопировать эту папку за пределы VM по SSH. Ключ MFA и Django входят в
зашифрованный `keys.tar.gz.age`; без них восстановление авторизации неполное.
Перед пілотом ещё настроить ежедневные копии вне VM, 7 ежедневных/4 недельных,
уведомления об ошибках и выполнить изолированное восстановление с проверкой
входа, документов, остатков и времени восстановления. Сейчас это не закрыто.

Не использовать первоначальный restore-скрипт для отката рабочей базы.
Обновления делать после бекапа в отдельном релизе; миграции оценивать на
совместимость. Возврат кода не откатывает данные и не гарантирует совместимость.

## Источники

- GitHub CLI: https://cli.github.com/manual/gh_auth_login
- Создание репозитория: https://cli.github.com/manual/gh_repo_create
- Deploy keys: https://cli.github.com/manual/gh_repo_deploy-key_add
- Django deployment: https://docs.djangoproject.com/en/5.2/howto/deployment/
- Ubuntu PostgreSQL: https://ubuntu.com/server/docs/how-to/databases/install-postgresql/
- Ubuntu Nginx: https://ubuntu.com/server/docs/how-to/web-services/install-nginx/
- Сертификаты: https://ubuntu.com/server/docs/how-to/security/obtain-tls-certificates/

Проверки локального комплекта не заменяют фактический аудит, тест установки,
перезагрузки и восстановления на целевой Ubuntu. Они будут зафиксированы после
выполнения команд на сервере.
