# Инструкции для AI агентов

## Обзор проекта

Скрипт для проверки диплинков отелей через TravelLine Integration API. Получает ссылку на бронирование и проверяет, что страница корректно отображает данные.

## Структура кода

```
check_deeplinks.py
├── TokenManager          # Управление OAuth токеном (Basic auth → Bearer)
├── PageAnalyzer          # Анализ содержимого страницы (7 критериев)
├── DeeplinkCollector     # Сбор диплинков из API
├── ResultsSaver          # Сохранение результатов в CSV
├── BrowserChecker        # Проверка через Playwright (для команды auto)
├── CombinedChecker       # Сбор + проверка в одном (для команды check)
└── cmd_* функции         # Обработчики команд CLI
```

## Основные команды

```bash
# Активация окружения (ОБЯЗАТЕЛЬНО перед запуском)
source venv/bin/activate

# Главная команда — сбор и проверка в одном шаге
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7

# Перепроверка цен с новыми диплинками, только с корректными датами
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7 \
  --recheck price --only-dates

# Перепроверка по существующим диплинкам (без API)
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py check \
  --hotels hotels_id_name.json \
  --output results.csv \
  --adults 1 --children 7 \
  --recheck guests --from-csv

# Только сбор диплинков
python3 check_deeplinks.py collect --hotels hotels.json --output deeplinks.json

# Только проверка собранных
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py auto --deeplinks deeplinks.json --output results.csv

# Синхронизация результатов в Google Sheet
python3 check_deeplinks.py sync --results deeplinks_results.csv --sheet-url "URL"
# Или напрямую (URL из .env GOOGLE_SHEET_URL):
python3 sync_google_sheet.py
```

## Формат входных данных

**hotels_id_name.json / hotels_id_name_whitelist.json:**
```json
[
  {"id": "12345", "name": "Название отеля"},
  {"id": "67890", "name": "Другой отель"}
]
```

## Формат выходных данных

**CSV колонки:**
- `hotel_id`, `hotel_name` — идентификация
- `status` — success/partial/failed/no_access/no_rooms
- `check_*` — результаты отдельных проверок (True/False)
- `guests_info` — детали проверки гостей: `correct`, `mismatch`, `children_as_adults`, `not_available`, `no_children_in_deeplink`
- `children_selectable` — `True`/`False` (определяется по тексту гостевого дропдауна)
- `children_ages_on_page` — числовой возраст или лимит
- `expected_price` — цена из API
- `widget_load_time` — время загрузки виджета (секунды)
- `error_details` — описание ошибок
- `deeplink` — URL для бронирования

## Критерии проверки (PageAnalyzer.analyze)

1. **page_loaded** — страница загрузилась (content > 100 символов)
2. **name_matches** — название отеля найдено на странице
3. **has_travelline** — есть iframe с виджетом TravelLine
4. **no_errors** — нет текста ошибок (проверяется только видимый текст, `<script>`/`<style>` теги исключаются)
5. **dates_correct** — дата в виджете совпадает с запросом
6. **guests_correct** — количество гостей отображается верно
7. **price_correct** — цена на странице совпадает с API

## Важные особенности

### Rate Limits
- API лимит: ~1000 запросов/час
- При исчерпании скрипт автоматически ждёт до сброса
- Заголовки: `X-RateLimit-Remaining-Hour`, `Retry-After`

### Fallback даты
Если нет комнат на основную дату, пробует:
1. С детьми: март, апрель, май, октябрь × 1н/4н
2. Без детей: основная дата + те же fallback-даты
Итого до 18 API-запросов на отель.

### Playwright особенности
- Используется headless Chromium
- Маскировка `navigator.webdriver` для обхода детекции ботов
- Блокировка тяжёлых ресурсов (`image`, `media`, `font`) — стили (`stylesheet`) оставлены
- TravelLine виджет в iframe — нужно ждать загрузки
- Контент iframe доступен через `frame.locator('body').inner_text()`
- Input values (даты, гости) доступны через `input.input_value()`
- Cookie-баннеры и оверлеи удаляются через `_dismiss_overlays()`

### Поиск TL iframe (`_get_tl_frame`)
Двухэтапный поиск правильного виджета бронирования:
1. **page.frames** — итерация по фреймам, пропуск main_frame и `reputation`, поиск по наличию даты в input
2. **frame_locator** — fallback для cross-origin iframe, приоритет `booking` виджетам

Критерий: input-поле содержит название месяца (января, февраля, ...). Если ни один фрейм не содержит дату, берётся первый с input-полями.

### Детекция «Здесь пока ничего нет»
Метод `_check_widget_no_rooms()` проверяет все TL-фреймы (включая без input-полей) на текст «Здесь пока ничего нет» или «нет доступных». При обнаружении — статус `no_rooms`.

### Определение `children_selectable`
Анализируется **только текст гостевого дропдауна** (`guests_dropdown_text`), а не весь snapshot. Это исключает ложные срабатывания от слов «дети» в описании отеля. Если дропдаун не открылся — `False`.

### Нормализация текста
TravelLine использует специальные пробелы:
- `\xa0` (NBSP), `\u2009` (thin space), `\u202f` (narrow NBSP)
Все нормализуются в обычные пробелы перед поиском.

### Проверка ошибок (no_errors)
Ошибки ищутся только в **видимом тексте** — `<script>` и `<style>` теги удаляются перед проверкой. Это предотвращает ложные срабатывания на строки локализации в JS/JSON (например, `"errors":{"404":"страница не найдена"}`).

## Типичные задачи

### Добавить новую проверку
1. Добавить поле в `results` dict в `PageAnalyzer.analyze()`
2. Реализовать логику проверки
3. Добавить в `get_status()` если влияет на итоговый статус
4. Добавить колонку в `CSV_FIELDS` в `ResultsSaver` и `CombinedChecker.save_result()`

### Изменить fallback даты
Редактировать `fallback_dates` в:
- `DeeplinkCollector.try_fallback_dates()`
- `CombinedChecker.get_deeplink()`

### Добавить режим --recheck
1. Добавить в `choices` в argparse (`check_parser`)
2. Добавить условие в `CombinedChecker.load_checked()` (блок `if self.recheck`)

### Отладка проверки страницы
```bash
# Запуск с GUI для визуальной отладки
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py check \
  --hotels /tmp/test.json --output /tmp/test.csv \
  --adults 1 --children 7 --gui --recheck all
```

### Синхронизация с Google Sheet
Скрипт `sync_google_sheet.py` загружает CSV в Google Sheet через OAuth2.
- Очищает только значения (`worksheet.clear()`), форматирование и условное форматирование сохраняются
- При первом запуске открывает браузер для авторизации, сохраняет токен в `token.json`
- Лист определяется по `gid` из URL

## Переменные окружения

Файл `.env`:
```
TL_AUTH_KEY=base64_encoded_credentials
GOOGLE_SHEET_URL=https://docs.google.com/spreadsheets/d/...
```

- `TL_AUTH_KEY` — Base64 от `username:password` для TravelLine Partner API
- `GOOGLE_SHEET_URL` — URL таблицы для синхронизации (опционально, можно передать через `--sheet-url`)
- `credentials.json` — OAuth2 Client ID (Desktop) из Google Cloud Console (не коммитить!)
- `token.json` — сохранённый токен авторизации (не коммитить!)

**PLAYWRIGHT_BROWSERS_PATH=0** — обязательно при запуске Playwright (указывает на локальные браузеры в venv).
