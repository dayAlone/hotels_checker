# TravelLine Deeplink Checker

Инструмент для автоматической проверки диплинков отелей TravelLine Integration API.

## Возможности

- Получение диплинков из TravelLine API
- Автоматическая проверка страниц через headless браузер (Playwright)
- Проверка: загрузка страницы, название отеля, виджет TravelLine, даты, гости, цена
- Проверка детей: возраст в URL, отображение на странице, возможность выбора
- Fallback даты (март, апрель, май, октябрь × 1 и 4 ночи) если нет комнат
- Fallback без детей: если с детьми комнат нет — пробует без детей
- Обработка rate limits с автоматическим ожиданием
- Перепроверка по существующим диплинкам из CSV (без API-запросов)
- Автоматическое удаление cookie-баннеров и оверлеев перед взаимодействием с виджетом
- Восстановление страницы браузера после ошибок навигации
- Замер времени загрузки виджета
- Блокировка тяжёлых ресурсов (изображения, видео, шрифты) для ускорения загрузки
- Умный поиск TL iframe по наличию дат в input-полях (обход вложенных/множественных iframe)
- Детекция пустого виджета «Здесь пока ничего нет» → статус `no_rooms`
- Защита от ложных срабатываний ошибок в JS/JSON-строках локализации

## Установка

```bash
# Создать виртуальное окружение
python3 -m venv venv
source venv/bin/activate

# Установить зависимости
pip install -r requirements.txt

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

Получает диплинк из API и сразу проверяет страницу в браузере:

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
- `--children` — возраста детей через запятую (например `7` или `5,10`)
- `--date` — дата заезда (по умолчанию 2026-08-01)
- `--nights` — количество ночей (по умолчанию 1)
- `--start` — пропустить первые N подходящих отелей
- `--limit` — ограничить количество проверяемых отелей
- `--gui` — показать окно браузера (по умолчанию headless)
- `--recheck` — перепроверить отели (см. ниже)
- `--from-csv` — использовать диплинки из CSV вместо API
- `--only-dates` — перепроверять только отели с корректными датами (`check_dates_correct=True`)

### Перепроверка (`--recheck`)

Позволяет перепроверить определённые отели без повторного сбора всех:

| Режим | Описание |
|-------|----------|
| `guests` | Отели с `check_guests_correct=False` |
| `price` | Отели с `check_price_correct=False` |
| `failed` | Отели со статусом `failed` или `partial` |
| `children` | Отели с неточным определением детей (sel=False при children_as_adults, или sel=True без возраста) |
| `all` | Все отели |
| `deeplinks` | Все отели с диплинками (без API) |

Примеры:

```bash
# Перепроверить гостей, используя существующие диплинки (без API-запросов)
python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7 \
  --recheck guests --from-csv

# Перепроверить цены с перезапросом из API, только с корректными датами
python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7 \
  --recheck price --only-dates

# Перепроверить определение детей по существующим диплинкам
python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7 \
  --recheck children --from-csv

# Перепроверить все неудачные через API
python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --recheck failed
```

> **Примечание:** отели со статусом `no_rooms` всегда перепроверяются автоматически.

### Команда `collect`

Только сбор диплинков без проверки в браузере:

```bash
python3 check_deeplinks.py collect \
  --hotels hotels_id_name.json \
  --output deeplinks.json \
  --adults 1 \
  --children 7
```

### Команда `auto`

Проверка уже собранных диплинков из JSON:

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

### Команда `report`

Показать статистику по результатам:

```bash
python3 check_deeplinks.py report --results results.csv
```

## Критерии проверки

| Критерий | Описание |
|----------|----------|
| `check_page_loaded` | Страница загрузилась |
| `check_name_matches` | Название отеля совпадает |
| `check_has_travelline` | Есть виджет TravelLine |
| `check_no_errors` | Нет сообщений об ошибках (в видимом тексте, без JS/JSON) |
| `check_dates_correct` | Даты в виджете совпадают |
| `check_guests_correct` | Гости отображаются корректно |
| `check_price_correct` | Цена совпадает с API |

### Дополнительные поля CSV

| Поле | Описание |
|------|----------|
| `guests_info` | Детали: `correct`, `mismatch`, `children_as_adults`, `not_available`, `no_children_in_deeplink` |
| `children_ages_in_url` | Возраст детей присутствует в URL диплинка |
| `children_ages_on_page` | Возраст / лимит возраста на странице (число) |
| `children_selectable` | Можно ли выбрать детей в виджете: `True` / `False` |
| `widget_load_time` | Время загрузки виджета TravelLine (секунды) |

## Статусы

- `success` — все проверки пройдены
- `partial` — часть проверок не пройдена
- `failed` — страница не загрузилась / ошибка навигации
- `no_access` — нет доступа к отелю (API 403)
- `no_rooms` — нет доступных комнат (API не вернул номеров, или виджет показывает «Здесь пока ничего нет»)

## Логика поиска комнат

При отсутствии комнат скрипт пробует в порядке:

1. С детьми + основная дата
2. С детьми + fallback-даты (март, апрель, май, октябрь × 1н/4н)
3. Без детей + основная дата
4. Без детей + fallback-даты

Итого до **18 API-запросов** на отель без комнат.

## Оптимизация загрузки

Для ускорения проверки страниц:
- Блокируются запросы на изображения, видео и шрифты (`image`, `media`, `font`)
- Стили (`stylesheet`) загружаются для корректного отображения виджетов
- Маскируется `navigator.webdriver` для обхода антибот-защит
- Cookie-баннеры и оверлеи удаляются автоматически

## Rate Limits

API имеет лимиты:
- ~1000 запросов/час
- ~200 запросов/минута
- ~50 запросов/секунду

При исчерпании часового лимита скрипт автоматически ждёт до сброса (читает `Retry-After` и `X-RateLimit-Remaining-Hour` из заголовков).

## Файлы

- `check_deeplinks.py` — основной скрипт
- `requirements.txt` — зависимости Python
- `.env` — ключ авторизации (не коммитить!)
- `.gitignore` — исключения для git
- `AGENTS.md` — инструкции для AI-агентов
