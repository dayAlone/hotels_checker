# Инструкции для AI агентов

## Обзор проекта

Скрипт для проверки диплинков отелей через TravelLine Integration API. Получает ссылку на бронирование и проверяет, что страница корректно отображает данные.

## Структура кода

```
check_deeplinks.py
├── TokenManager          # Управление OAuth токеном
├── PageAnalyzer          # Анализ содержимого страницы  
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

# Только сбор диплинков
python3 check_deeplinks.py collect --hotels hotels.json --output deeplinks.json

# Только проверка собранных
PLAYWRIGHT_BROWSERS_PATH=0 python3 check_deeplinks.py auto --deeplinks deeplinks.json --output results.csv
```

## Формат входных данных

**hotels_id_name.json:**
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
- `expected_price` — цена из API
- `error_details` — описание ошибок
- `deeplink` — URL для бронирования

## Критерии проверки (PageAnalyzer.analyze)

1. **page_loaded** — страница загрузилась (content > 100 символов)
2. **name_matches** — название отеля найдено на странице
3. **has_travelline** — есть iframe с виджетом TravelLine
4. **no_errors** — нет текста ошибок (404, не найден, etc.)
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
1. Март (2026-03-01)
2. Октябрь неделя (2026-10-01 — 2026-10-08)

### Playwright особенности
- Используется headless Chromium
- Маскировка webdriver для обхода детекции ботов
- TravelLine виджет в iframe — нужно ждать загрузки
- Контент iframe доступен через `frame.locator('body').inner_text()`
- Input values (даты, гости) доступны через `input.input_value()`

### Нормализация текста
TravelLine использует специальные пробелы:
- `\xa0` (NBSP)
- `\u2009` (thin space)
Все нормализуются в обычные пробелы перед поиском.

## Типичные задачи

### Добавить новую проверку
1. Добавить поле в `results` dict в `PageAnalyzer.analyze()`
2. Реализовать логику проверки
3. Добавить в `get_status()` если влияет на итоговый статус
4. Добавить колонку в `CSV_FIELDS` в `ResultsSaver`

### Изменить fallback даты
Редактировать `fallback_dates` в:
- `DeeplinkCollector.try_fallback_dates()`
- `CombinedChecker.get_deeplink()`

### Отладка проверки страницы
```python
# В check_deeplinks.py добавить отладочный вывод:
print(f"Content length: {len(page_content)}")
print(f"Looking for: {hotel_name}")
print(f"Found: {hotel_name.lower() in page_content.lower()}")
```

## Переменные окружения

Файл `.env`:
```
TL_AUTH_KEY=base64_encoded_credentials
```

Получить ключ: Base64 от `username:password` для TravelLine Partner API.
