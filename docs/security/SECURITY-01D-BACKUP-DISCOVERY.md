# SECURITY-01D — read-only перевірка чинних бекапів

18.11.2026. Підготовчий підблок SEC-R01/R03/R04/L03 з [SECURITY-01](../SECURITY-01.md).
Початковий коміт: `529b4329a8c98cea783ac2068d46aaf3b6309c11`.

## Відоме до перевірки

Власник підтвердив першу зашифровану копію даних і ключів та ізольоване
відновлення з перевіркою БД, сум, залишків і MFA. Попередній збирач побачив
локальні `first-success.json` і `restore-success.json`, але не виявив timer
із маркером `oxxom-oblik`. Це не виключає cron, інші назви або зовнішній scheduler.
Скриптів ручного backup/restore у чинному GitHub tree немає; наявність локальних
чернеток не є підтвердженням встановленого production-коду або розкладу.

## Підготовлено

`deploy/ubuntu/audit-backups.sh FULL_COMMIT_SHA` отримує один закріплений
collector, перевіряє SHA256 і запускає його від root. Тимчасова папка приватна
й прибирається після завершення. Collector використовує тільки Python stdlib.

| Об’єкт | Що збирається |
|---|---|
| Живе монтування | Kernel metadata через findmnt без canonicalize: наявність CIFS, SMB 3.1.1, seal, rw; джерело й повні options не друкуються |
| Постійне монтування | Відповідні записи fstab: булеві прапорці шифрування, automount/noauto, credentials-file/inline-credentials; native mount/automount unit states |
| systemd | Кандидати timer за назвами, стан enable/active, наявність last/next trigger; last service result і наявність OnFailure. ExecStart/Environment не запитуються |
| cron | Лічильники прочитаних і пропущених файлів та кандидатів з маркерами OXXOM/backup. Команди, імена файлів і значення не друкуються |
| Файли доступу | Лише lstat для SMB credentials і двох Restic password files: regular/root-owned/0600/nonempty; вміст не читається |
| Локальні receipts | Дати й вік конкретних first/restore receipts, прапорці перевірок та збіг записаних job/database/data/keys. Самі значення job/database/snapshot IDs не друкуються |

Невідомі unit names приховані за коротким SHA256 fingerprint; відомі публічні
назви проєкту можуть відображатися. OnFailure означає наявність hook, а не
доставлене повідомлення. Скан cron/timer за маркерами є пошуком кандидатів;
він не доводить відсутності непрямих або зовнішніх завдань.

Collector не запускає backup, restore, restic, mount, prune, cron jobs чи
systemd units; не змінює fstab, timer-и, служби, БД, конфігурацію або файли копій.
Не звертається до SMB-сховища: його доступність і вміст залишаються NOT TESTED.

`OBSERVED`/exit 0 означає завершення збору метаданих, не приймання бекапів.
Недоступність окремого джерела явно позначається; root/загальна помилка дає
`BLOCKED`/exit 2 без stderr чи exception values.

## Приймання й наступні залежності

Перед використанням — unit tests конфіденційності й відмов та виконання
повного launcher на ізольованому Ubuntu CI. Production-звіт ще не отримано.

Після звіту потрібно визначити чинне джерело backup-коду, точний target,
розклад/mount, політику matching data/keys pairs та канал перевіреної доставки.
Ціль ТЗ — 7 щоденних і 4 щотижневі копії, періодичне відновлення та контроль
після reboot. Snapshot-и не видаляються цим етапом. Без звіту й перевірки
часткової відмови не додається новий timer і не активується retention.

Незалежна незмінна/offline-копія та виміряні RPO/RTO залишаються відкритими.

Первинні джерела: [systemd.timer](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html),
[findmnt](https://man7.org/linux/man-pages/man8/findmnt.8.html),
[Restic retention](https://restic.readthedocs.io/en/stable/060_forget.html).
