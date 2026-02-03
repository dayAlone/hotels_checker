# TravelLine Deeplink Checker

Инструмент для автоматической проверки диплинков отелей TravelLine Integration API.

## Возможности

- Получение диплинков из TravelLine API
- Автоматическая проверка страниц через headless браузер
- Проверка: загрузка страницы, название отеля, виджет TravelLine, даты, гости, цена
- Fallback даты (март, октябрь) если нет комнат
- Обработка rate limits с автоматическим ожиданием

## Установка

```bash
# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install requests playwright

# Установить браузер для Playwright
PLAYWRIGHT_BROWSERS_PATH=0 playwright install chromium
```

## Настройка

Создайте файл `.env` с ключом авторизации:

```
TL_AUTH_KEY=ваш_ключ_base64
```

## Использование

### Команда `check` (рекомендуется)

Получает диплинк и сразу проверяет его:

```bash
python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 \
  --children 7
```

Параметры:
- `--hotels` — JSON файл с отелями `[{"id": "123", "name": "Hotel"}]`
- `--output` — CSV файл результатов
- `--adults` — количество взрослых (по умолчанию 1)
- `--children` — возраста детей через запятую (например `7,10`)
- `--date` — дата заезда (по умолчанию 2026-08-01)
- `--nights` — количество ночей (по умолчанию 1)
- `--start` — начать с N-го отеля
- `--limit` — ограничить количество
- `--gui` — показать окно браузера

### Команда `collect`

Только сбор диплинков без проверки:

```bash
python3 check_deeplinks.py collect \
  --hotels hotels_id_name.json \
  --output deeplinks.json \
  --adults 1 \
  --children 7
```

### Команда `auto`

Проверка уже собранных диплинков:

```bash
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py auto \
  --deeplinks deeplinks.json \
  --output results.csv
```

### Команда `merge`

Объединить несколько CSV файлов:

```bash
python3 check_deeplinks.py merge \
  --pattern "results_*.csv" \
  --output results_merged.csv
```

## Критерии проверки

| Критерий | Описание |
|----------|----------|
| `check_page_loaded` | Страница загрузилась |
| `check_name_matches` | Название отеля совпадает |
| `check_has_travelline` | Есть виджет TravelLine |
| `check_no_errors` | Нет сообщений об ошибках |
| `check_dates_correct` | Даты в виджете совпадают |
| `check_guests_correct` | Гости отображаются корректно |
| `check_price_correct` | Цена совпадает с API |

## Статусы

- `success` — все проверки пройдены
- `partial` — часть проверок не пройдена
- `failed` — страница не загрузилась
- `no_access` — нет доступа к отелю (403)
- `no_rooms` — нет доступных комнат

## Rate Limits

API имеет лимиты:
- ~1000 запросов/час
- ~200 запросов/минута
- ~50 запросов/секунда

При исчерпании часового лимита скрипт автоматически ждёт до сброса.

## Файлы

- `check_deeplinks.py` — основной скрипт
- `get_token.sh` — получение токена через curl
- `.env` — ключ авторизации (не коммитить!)
- `.gitignore` — исключения для git
