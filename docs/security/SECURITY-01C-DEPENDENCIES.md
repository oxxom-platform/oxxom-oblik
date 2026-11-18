# SECURITY-01C-DEPS — відтворюваний runtime і безпечний інвентар

18.11.2026. Підблок SEC-C02/SEC-C03 з [SECURITY-01](../SECURITY-01.md).
Вихідний коміт: `dcd7872d0707a6c2fbd0fb6c11c1bc29461af55b`.
Це перевірка залежностей застосунку; аудит ОС, TLS, мережевих ACL, загальної
browser policy й усього ASVS не оголошується завершеним.

## Що змінено

`requirements-production.lock` закріплює всі 12 runtime-пакетів, включно з
транзитивними, і SHA256 дозволених upstream-артефактів. Власні опції lock-файла
вимагають перевірки хешів та binary wheels. Хеші source-архівів, які додав
pip-compile, не дозволяють їх встановлення всупереч `--only-binary=:all:`.
Прямі версії Django, psycopg, cryptography, PyOTP, QRCode й Gunicorn не змінені.
Ціль резолюції: CPython 3.12, Linux x86_64; macOS/інші Python використовують
наявні development inputs і не оголошуються перевіреними цим lock.

Генерація виконана `pip==26.3` і `pip-tools==7.7.0` у Linux amd64-контейнері
з pinned digest. Версії, libc, SHA256 двох inputs і результату збережені в
[RUNTIME-LOCK-BUILD.json](RUNTIME-LOCK-BUILD.json). Генератор — інструмент
розробки в окремому venv, до production runtime не входить.

CI завантажує wheels з перевіркою хешів, встановлює їх у порожній venv
offline через `--no-index --find-links`, виконує `pip check`, порівнює повний
очікуваний набір версій та запускає весь Django suite на PostgreSQL 16 під
цим runtime. `pip-audit==2.10.1` встановлюється в інший venv і перевіряє всі
фактично встановлені runtime-пакети, без автоматичного виправлення.

## Інвентар фактичної Ubuntu

`deploy/ubuntu/check-runtime.sh FULL_COMMIT_SHA` завантажує точні collector і lock
у приватну тимчасову папку, перевіряє їхні SHA256 й запускає read-only інвентар.
Папка видаляється після завершення. Символьні/короткі refs не приймаються.
Другий необов’язковий аргумент задає Python окремого перевірочного venv;
CI перевіряє весь launcher на своєму ізольованому runtime. Без цього аргументу
використовується фіксований шлях чинного Ubuntu venv.

`deploy/ubuntu/collect-runtime.py --lock FILE` запускає лише ізольоване читання
метаданих із `/srv/oxxom-oblik/venv/bin/python`. Аргумент `--python` призначений
також для перевірки окремого CI-оточення. Він не імпортує Django, не читає
`app.env`, не звертається до БД, не встановлює пакети й не перезапускає службу.

Виводяться відомі назви пакетів із lock і перевірені рядки версій; відсутні
пакети, розбіжності версій, дублікати та кількість неочікуваних distributions.
Невідомі назви, direct URL, локальні шляхи, metadata bodies, stderr процесу
та значення оточення не друкуються. Для direct URL виводиться лише кількість.
`pip`, `setuptools`, `wheel` явно виключені з порівняння runtime: це bootstrap
інструменти, їхній окремий аудит не оголошується виконаним.

| Статус | Значення | Exit |
|---|---|---|
| MATCH | Python/платформа/venv відповідають цілі; очікуваний набір версій збігається, немає невідомих distributions, дублікатів чи direct URL | 0 |
| DRIFT | Виявлена розбіжність версій/набору/метаданих або непідтримувана платформа | 1 |
| BLOCKED | Неможливо прочитати/перевірити lock або виконати інвентар | 2 |

MATCH стосується метаданих. Collector не перевіряє хеші вже встановлених
файлів і не підтверджує їхню автентичність. Інвентар не є окремим live advisory
scan VM; додаткові/невідомі пакети вимагають закритого розбору. На робочому
сервері спочатку отримати цей звіт, а оновлення/заміну venv робити окремим
пакетом з тестом повернення попереднього оточення.

## Докази та межі

| Перевірка | Результат |
|---|---|
| Чистий Linux amd64, CPython 3.12.16 | 12 пакетів завантажені з SHA256 та offline встановлені; `pip check` PASS; collector MATCH |
| Негативні перевірки | Строгий lock, ціль/venv/набір, зайві пакети/dedup/direct URL, приховування stderr, узгодженість input hashes, pinned launcher і відмова pip на підміненому wheel; остаточний набір у CI |
| Актуальний advisory scan resolved runtime | 12 пакетів, 0 відомих advisory findings на момент локальної перевірки; не гарантія відсутності невідомих вразливостей |
| Ubuntu PostgreSQL CI цього випуску | PR #12: 330 Django tests та 7 inventory/lock tests PASS; усі 3 checks успішні, включно з історією секретів та advisory scan |
| Фактичні пакети робочої VM | Власник передав MATCH: CPython 3.12.3, усі 12 runtime-версій збігаються, немає відсутніх/зайвих пакетів, дублікатів, некоректних метаданих або direct URL |
| Lock фактичної перевірки | SHA256 `a2d23db421dbb06a6cab080a694360954793a802261f464471f27d63a285ea2b` відповідає перевіреному випуску `4d007625c5f236a88e03b9bc7a51e5b936da73d7` |
| Файли вже встановлених пакетів | NOT TESTED: `installed_artifact_hashes_verified=false`; metadata MATCH не є доказом цілісності файлів |

Попередній блок сесій установлений: власник передав `SECURITY-01B-SESSIONS OK`.
Браузерний сценарій двох сесій відкладено й залишається NOT TESTED.
Пакети, сесії та конфігурація робочої VM в цьому підблоці не змінюються.

## Відтворення lock

Використати окремий Linux amd64/CPython 3.12 resolver venv, установити
`pip==26.3 pip-tools==7.7.0` і виконати команду з header lock-файла.
Додати дві policy-опції на початку: `--require-hashes`, `--only-binary=:all:`.
Оновити generation receipt лише після перегляду diff версій/хешів; пройти
повторну hash-установку, `pip check`, PostgreSQL suite й advisory scan.
Генерація не запускається на production і не є частиною звичайної установки.

Первинні джерела: [pip secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/),
[pip-tools hash generation](https://pip-tools.readthedocs.io/en/stable/#using-hashes).
