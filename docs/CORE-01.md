# Серверне ядро CORE

## Що змінено

Додано окремий backend Django з PostgreSQL, конфігурацію середовища, UUID-користувача
з входом за email, першу міграцію, health/readiness та тести. Business API,
admin і login ще не відкриті: справжня 2FA реалізується наступним етапом.
Старий прототип на порту 4173 та його SQLite не змінено.

## Запуск

Потрібні Python 3.11+ і запущений Docker Desktop на Mac. Із кореня проекту:

```sh
sh scripts/start-core.sh
```

Або відкрити start-core.command. Скрипт створить .env, встановить залежності,
підніме окремий PostgreSQL, перевірить схему, застосує міграції, запустить
тести на окремій тестовій БД і лише після успіху відкриє сервер на
http://127.0.0.1:4180. Перший запуск потребує інтернету.

.env не перезаписується. Не міняйте пароль після створення Docker volume без
окремої зміни пароля PostgreSQL. Локальна роль контейнера призначена лише для
розробки та створення тестової БД, не для production.

Зупинити вебсервер: Ctrl+C. Зупинити PostgreSQL: docker compose stop db.
Не використовуйте down -v: це видалить базу.

## Перевірки

```sh
"$HOME/OXXOM-Dev/venv/bin/python" backend/manage.py check
"$HOME/OXXOM-Dev/venv/bin/python" backend/manage.py makemigrations --check --dry-run
"$HOME/OXXOM-Dev/venv/bin/python" backend/manage.py migrate --noinput
"$HOME/OXXOM-Dev/venv/bin/python" backend/manage.py test accounts core --noinput
```

GET /api/v1/health перевіряє процес без БД.
GET /api/v1/ready повертає 200 лише при доступній БД без незастосованих міграцій;
інакше 503 без деталей підключення.

## Межі приймання

У середовищі агента pip не має доступу до DNS/мережі, а Docker socket заборонений.
Користувач підтвердив успішний локальний запуск після міграцій та тестів; головна, health і readiness повернули HTTP 200. Локальний CORE прийнято за цим журналом.
Прямі залежності зафіксовані, повний lock із транзитивними залежностями та digest
образу PostgreSQL створюємо після успішного встановлення і тестування.

## Ubuntu

Цільова ОС 24.04 LTS amd64; Python 3.12 і PostgreSQL 16 для серверного профілю.
Після AUTH/ACCESS буде підготовлено окремий production-пакет Nginx/systemd.
Compose цього етапу — лише локальний PostgreSQL, не production deployment.
В production конфігурація надходить через середовище, .env не підхоплюється:
APP_ENV=production, сильний DJANGO_SECRET_KEY, явні hosts і HTTPS CSRF origins.
TLS reverse proxy, окрема непривілейована роль БД, бекап і restore-test обов’язкові.

## Джерела вибору

- https://www.djangoproject.com/download/ — Django 5.2 LTS.
- https://docs.djangoproject.com/en/5.2/ref/databases/ — PostgreSQL backend.
- https://pypi.org/project/psycopg/3.2.13/ — PostgreSQL adapter.

## Оновлення запуску

Оточення Python створюється у ~/OXXOM-Dev/venv поза Documents. У попередньому оточенні виявлено dataless-файл Django. Старе оточення не видаляється. Шлях можна задати через OXXOM_VENV. Перевірка фактичного підключення до PostgreSQL виконується перед міграціями; повторний запуск на Mac підтверджено. Для локального loopback PostgreSQL вимкнено GSS negotiation після успішної перевірки користувачем; production-політику не змінено.
