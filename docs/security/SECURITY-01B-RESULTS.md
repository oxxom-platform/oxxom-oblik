# SECURITY-01B — прикладний аудит і межі перевірки

Дата: 18.11.2026. Вихідна версія: `608a6739fc668a12a0a24753ce3e7dbf65891f00`.
Це протокол перевірених сценаріїв, а не сертифікація ASVS L2 або завершений pentest.

## Підтверджений дефект SEC-B01

**Середній ризик, залежить від обмежень reverse proxy:** зайвий multipart-файл
розбирався Django до перевірки входу. `DATA_UPLOAD_MAX_MEMORY_SIZE` не враховує
вміст файлів. CSRF middleware читає POST до переходу до перевірки токена з header.
Анонімний клієнт може отримати CSRF cookie на сторінці входу.

В ізольованій SQLite-копії запит з одним синтетичним файлом 3 MiB до login API
створив `TemporaryUploadedFile`, після чого повернув 400. Паролі та дані production
не використовувалися. Це доводить зайву обробку на прикладному рівні; виснаження
ресурсів production і можливість пройти фактичний NPM body limit не перевірялися.

Виправлення: middleware **до CSRF**, 8 KiB для auth API, 128 KiB для інших
мутуючих запитів, 1 MiB + 16 KiB framing лише для чотирьох точних CSV preview
маршрутів. Власний розмір CSV залишається до 1 MiB. Максимум один файл.
Невідомий розмір тіла з Content-Type/Transfer-Encoding відхиляється; порожній
POST без тіла залишається сумісним із logout/setup. Перевірки проводяться без
читання request.body/POST/FILES. Публічний fixture-only demo має окремий
суворіший ліміт 4 KiB та попередній контракт помилок.

Регресійні тести підміняють parser так, щоб будь-яке передчасне читання
завершувало тест помилкою. Перевіряються також відсутність throttle/session writes,
CSRF, MFA та сумісність імпорту. Захист стосується WSGI, який використовується
Gunicorn. Він не замінює edge body/time limits і не захищає ASGI pre-buffering.

Статус: виправлено; 18.11.2026 власник передав `SECURITY-01B-REQUEST OK`,
який підтверджує установку й service checks на Ubuntu. Browser MFA smoke
після цього оновлення окремо не підтверджений.

## Доступ, MFA і сесії

- [Інвентар маршрутів](ROUTE-INVENTORY-01B.json): 118 декларацій, 78 tenant API.
- Новий негативний sweep бере маршрути з фактичного Django resolver: GET/POST,
  анонімний клієнт, парольна сесія без MFA, п’ять ролей чужої організації,
  відкликане членство, вимкнена організація, global superuser без членства.
- 200 від capability/options endpoint допускається лише для явного deny/порожнього
  набору прав і публічних констант імпорту; це не доступ до записів.
- До/після заборонених запитів порівнюється вміст прикладних таблиць, без друку
  значень. Django session bookkeeping виключено. Облікових змін не допускається.
- Цей sweep використовує випадкові UUID записів; тести конкретних існуючих чужих
  об’єктів, field redaction і дозволених дій залишаються в тестах кожного модуля.
- Перевірено повторне використання cookie після logout. Попередні тести MFA
  охоплюють replay TOTP/recovery, завершення challenge, зміну пароля, блокування
  користувача, відкликання членства, CSRF і фіксовані сім днів без idle timeout.
- Конкурентні сценарії перевіряються на ізольованому PostgreSQL у CI, не на SQLite
  і не на робочих акаунтах.

## Залежності, історія й GitHub

Новий workflow `Security verification`: повний набір **фактично resolved** runtime
пакетів у чистому Python 3.12 venv, окремий venv для pip-audit 2.10.1, strict
перевірка без автоматичного fix. Зберігаються точні версії та JSON результату.
Це не інвентар встановленої VM: її transitive dependencies ще потрібно зібрати
без виводу секретів. Повний hash lock поки не додано.

Окремий checksum-pinned Gitleaks 8.30.1 перевіряє доступну Git history з
`fetch-depth: 0`. Raw logs/report залишаються лише в тимчасовому runner;
артефакт містить rule/path/line/commit/fingerprint, **без значень збігів**.
Відсутність знаходжень не доводить відсутність усіх можливих секретів.
Обидві перевірки також запускаються щопонеділка та вручну; prod secrets не потрібні.

Перший dependency audit: 12 resolved runtime пакетів, 0 відомих advisory findings
на момент CI. Це не гарантія відсутності невідомих вразливостей. Перший history
scan знайшов один `generic-api-key` у printing test: перевірено контекст і
історичну версію — це UUID `idempotency_key` синтетичного тестового документа,
не credential. У `.gitleaksignore` додано лише точний fingerprint цього commit/
path/rule/line; цілі тести, директорії або правила зі сканування не виключені.

Репозиторій підтверджено як private. API branch protection і rulesets повертає
403 із вимогою змінити тариф або видимість. **SEC-B02, середній:** примусовий merge
gate недоступний на поточному тарифі; без зміни видимості/оплати це не виправлено.
Код усе одно проходить PR і CI; claims про технічну заборону прямого push немає.
Дозволені Actions зараз `all`, platform-wide SHA pinning не вимагається; Actions
у доданому workflow прив’язані до commit SHA.

## ASVS і відкриті питання

[Реєстр ASVS 5.0.0 L1/L2](ASVS-5.0.0-L1-L2.json): усі 253 вимоги цих рівнів
із точними versioned IDs, відповідальним і статусом. Описи залишені в первинному
OWASP джерелі. N/A пояснює відсутні функції поточного релізу й переглядається
при появі OAuth/OIDC, JWT, GraphQL, WebSocket або WebRTC.

Початковий підсумок: 5 PASS, 1 FAIL, 1 BLOCKED, 58 N/A, 188 NOT_TESTED.
PASS стосується перевірених прикладних контролів і вимагає успішного CI цього
релізу; це не результат live-аудиту інфраструктури.

**SEC-B03, середній:** немає користувацької дії завершити інші активні сесії
після зміни факторів. Зміна пароля вже інвалідує попередні session hashes;
цього недостатньо для повного v5.0.0-7.4.3. Потрібні UX і свіжий MFA для
чутливих дій, без скорочення погоджених семи днів звичайної сесії.

Доповнення 18.11.2026: цей дефект виправлено на прикладному рівні в
[блоці сесій](SECURITY-01B-SESSIONS.md): користувацька дія, свіжий TOTP,
відкликання при заміні recovery-кодів, повторна перевірка hash під row lock.
330 тестів пройшли на ізольованому PostgreSQL, включно з concurrency.
Приймання випуску потребує зеленого CI; установку на Ubuntu ще не підтверджено.

Залишаються: live TLS/headers/CSP, SSH/ACL, installed dependency inventory,
full hash lock, lifecycle очищення throttle, журнал auth/security events,
операційне відновлення після reboot, backup schedule/alerts, незалежна незмінна
копія та решта 188 не повністю перевірених ASVS контролів. Попередні receipts
першого backup й ізольованого restore не є доказом наявності розкладу.

## Установка

`deploy/ubuntu/update-request-limits.sh FULL_COMMIT_SHA` завантажує точний реліз.
Updater змінює лише settings і новий middleware; звіряє baseline, SHA256,
maintenance helpers та відсутність symlink. Backup попередніх файлів приватний.
Перевірка імпорту й Django check виконується від `oxxom`, після чого перезапускається
лише `oxxom-oblik` і виконуються read-only health/page checks. Помилка повертає
попередні файли й перезапускає попередню версію. Невідомі локальні зміни зупиняють
оновлення до запису. Міграцій, оновлень пакетів, змін env/NPM/backup немає.

## Валідація

- Локально: нові boundary/parser/API тести й тести атомарного updater.
- Обов’язково перед merge: повний Django suite на PostgreSQL; старі тести demo,
  security updater і реальний Nginx; dependency audit; metadata-only history scan.
- Остаточні CI результати та reviewed release фіксуються в PR. До успіху всіх
  перевірок реліз не вважається прийнятим. Установка та browser MFA smoke на VM
  потребують окремого підтвердження власника.

Первинні джерела: [Django upload settings](https://docs.djangoproject.com/en/5.2/ref/settings/#data-upload-max-memory-size),
[pip-audit](https://github.com/pypa/pip-audit/tree/v2.10.1),
[Gitleaks](https://github.com/gitleaks/gitleaks/tree/v8.30.1),
[OWASP ASVS 5.0.0](https://github.com/OWASP/ASVS/tree/v5.0.0).
