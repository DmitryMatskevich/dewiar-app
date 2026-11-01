"""Explicit DND on/off through macOS Shortcuts; never toggle or edit private preference stores."""
import subprocess
import threading

SHORTCUTS = {True: 'Dewiar — Не беспокоить — Вкл', False: 'Dewiar — Не беспокоить — Выкл'}
_lock = threading.Lock()


def set_dnd(enabled):
    if not isinstance(enabled, bool):
        raise ValueError('Укажите, включить или выключить «Не беспокоить»')
    name = SHORTCUTS[enabled]
    with _lock:
        try:
            found = subprocess.run(['/usr/bin/shortcuts', 'list'], capture_output=True, text=True, timeout=10)
            if found.returncode:
                raise ValueError('macOS не вернула список команд. Откройте приложение «Команды» и повторите.')
            names = found.stdout.splitlines()
            if names.count(name) != 1:
                raise ValueError('Нужна одна системная команда «' + name + '» в приложении «Команды»: действие «Настроить фокусирование / Set Focus» → «Не беспокоить» → ' + ('«Вкл» до выключения.' if enabled else '«Выкл».') + ' Инструкция: «Руки → Голосовые команды → Помощь».')
            result = subprocess.run(['/usr/bin/shortcuts', 'run', name], capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError('macOS не выполнила команду фокусировки. Откройте «' + name + '» в приложении «Команды» и проверьте её запуск.')
        except FileNotFoundError as error:
            raise ValueError('На этом Mac недоступна системная утилита «Команды»') from error
        except subprocess.TimeoutExpired as error:
            raise ValueError('macOS не подтвердила выполнение за отведённое время. Проверьте фокусировку в Пункте управления; автоматического повтора нет.') from error
    return {'message': 'Режим фокусировки «Не беспокоить» ' + ('включён до выключения.' if enabled else 'выключен.'), 'enabled': enabled}
