import getpass
import sys
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError
from accounts.bootstrap import create_owner


class Command(BaseCommand):
    help = "Створити власника та організацію. Пароль вводиться приховано в терміналі."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)
        parser.add_argument("--organization", required=True)

    def handle(self, *args, **options):
        if not sys.stdin.isatty():
            raise CommandError("Запустіть команду в інтерактивному терміналі.")
        try:
            password = getpass.getpass("Пароль (щонайменше 12 символів): ")
            confirmation = getpass.getpass("Повторіть пароль: ")
        except (EOFError, KeyboardInterrupt):
            raise CommandError("Створення скасовано.") from None
        if password != confirmation:
            raise CommandError("Паролі не збігаються.")
        try:
            create_owner(email=options["email"], password=password, organization_name=options["organization"])
        except ValidationError as exc:
            raise CommandError(" ".join(exc.messages)) from None
        except IntegrityError:
            raise CommandError("Запис уже існує. Дані не змінено.") from None
        self.stdout.write(self.style.SUCCESS("Власника та організацію створено. При першому вході налаштуйте TOTP на сторінці /login/."))
