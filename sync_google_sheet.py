#!/usr/bin/env python3
"""
Синхронизация deeplinks_results.csv → Google Sheet.

Использует OAuth2 для авторизации (при первом запуске откроется браузер).
Перезаписывает значения на листе, сохраняя форматирование и условное форматирование.

Требует файл credentials.json (OAuth2 Client ID, тип Desktop) из Google Cloud Console.
Скопы: https://www.googleapis.com/auth/spreadsheets

Использование:
    python3 sync_google_sheet.py --csv deeplinks_results.csv --sheet-url "https://docs.google.com/spreadsheets/d/..."
    python3 sync_google_sheet.py  # берёт значения из .env
"""

import argparse
import csv
import os
import sys
import re
from pathlib import Path

import gspread
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Скопы для Google Sheets API
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# Дефолтные значения
DEFAULT_CSV = 'deeplinks_results.csv'
DEFAULT_CREDENTIALS = 'credentials.json'
DEFAULT_TOKEN = 'token.json'


def load_env():
    """Загрузить переменные из .env файла."""
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, _, value = line.partition('=')
                    os.environ.setdefault(key.strip(), value.strip())


def parse_sheet_url(url: str) -> tuple[str, int | None]:
    """Извлечь spreadsheet ID и gid из URL Google Sheet.
    
    Returns:
        (spreadsheet_id, gid) — gid может быть None если не указан
    """
    # ID: между /d/ и /
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', url)
    if not match:
        raise ValueError(f'Не удалось извлечь ID из URL: {url}')
    spreadsheet_id = match.group(1)
    
    # gid из параметра
    gid_match = re.search(r'[?&]gid=(\d+)', url)
    gid = int(gid_match.group(1)) if gid_match else None
    
    return spreadsheet_id, gid


def authorize(credentials_path: str, token_path: str) -> Credentials:
    """Авторизация через OAuth2.
    
    При первом запуске откроет браузер для входа в Google-аккаунт.
    Сохраняет токен в token_path для последующих запусков.
    """
    creds = None
    
    # Пробуем загрузить сохранённый токен
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # Если токен невалиден — обновляем или запрашиваем новый
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print('🔄 Обновление токена...')
            creds.refresh(Request())
        else:
            if not os.path.exists(credentials_path):
                print(f'❌ Файл {credentials_path} не найден!')
                print()
                print('Для работы нужен OAuth2 Client ID (тип Desktop):')
                print('1. Откройте https://console.cloud.google.com/apis/credentials')
                print('2. Создайте OAuth 2.0 Client ID (Desktop application)')
                print('3. Скачайте JSON и сохраните как credentials.json')
                print('4. Включите Google Sheets API: https://console.cloud.google.com/apis/library/sheets.googleapis.com')
                sys.exit(1)
            
            print('🌐 Открываю браузер для авторизации...')
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        
        # Сохраняем токен
        with open(token_path, 'w') as f:
            f.write(creds.to_json())
        print(f'💾 Токен сохранён в {token_path}')
    
    return creds


def read_csv(csv_path: str) -> list[list[str]]:
    """Прочитать CSV и вернуть список строк (включая заголовок)."""
    rows = []
    with open(csv_path, 'r', encoding='utf-8', errors='replace') as f:
        reader = csv.reader(f)
        for row in reader:
            rows.append(row)
    
    if not rows:
        print(f'❌ Файл {csv_path} пуст')
        sys.exit(1)
    
    return rows


def sync(csv_path: str, sheet_url: str, credentials_path: str, token_path: str):
    """Синхронизировать CSV в Google Sheet."""
    # 1. Парсим URL
    spreadsheet_id, gid = parse_sheet_url(sheet_url)
    print(f'📊 Spreadsheet ID: {spreadsheet_id}')
    if gid is not None:
        print(f'📄 Sheet GID: {gid}')
    
    # 2. Читаем CSV
    rows = read_csv(csv_path)
    header = rows[0]
    data_rows = rows[1:]
    print(f'📋 CSV: {len(data_rows)} строк, {len(header)} колонок')
    
    # 3. Авторизация
    creds = authorize(credentials_path, token_path)
    gc = gspread.authorize(creds)
    
    # 4. Открываем таблицу
    print('🔗 Подключаюсь к Google Sheet...')
    spreadsheet = gc.open_by_key(spreadsheet_id)
    
    # Находим нужный лист по gid
    if gid is not None:
        worksheet = None
        for ws in spreadsheet.worksheets():
            if ws.id == gid:
                worksheet = ws
                break
        if worksheet is None:
            print(f'❌ Лист с gid={gid} не найден. Доступные листы:')
            for ws in spreadsheet.worksheets():
                print(f'   - "{ws.title}" (gid={ws.id})')
            sys.exit(1)
    else:
        worksheet = spreadsheet.sheet1
    
    print(f'📄 Лист: "{worksheet.title}"')
    
    # 5. Очищаем только значения (форматирование сохраняется)
    print('🧹 Очистка значений...')
    worksheet.clear()
    
    # 6. Загружаем данные
    all_rows = [header] + data_rows
    print(f'📤 Загрузка {len(all_rows)} строк...')
    
    # gspread batch_update для больших объёмов
    worksheet.update(range_name='A1', values=all_rows, value_input_option='RAW')
    
    print(f'✅ Готово! Загружено {len(data_rows)} строк в лист "{worksheet.title}"')
    print(f'🔗 {sheet_url}')


def main():
    load_env()
    
    parser = argparse.ArgumentParser(description='Синхронизация CSV → Google Sheet')
    parser.add_argument('--csv', type=str, 
                        default=os.environ.get('GOOGLE_SHEET_CSV', DEFAULT_CSV),
                        help='Путь к CSV файлу')
    parser.add_argument('--sheet-url', type=str,
                        default=os.environ.get('GOOGLE_SHEET_URL', ''),
                        help='URL Google Sheet')
    parser.add_argument('--credentials', type=str,
                        default=os.environ.get('GOOGLE_CREDENTIALS', DEFAULT_CREDENTIALS),
                        help='Путь к credentials.json')
    parser.add_argument('--token', type=str,
                        default=os.environ.get('GOOGLE_TOKEN', DEFAULT_TOKEN),
                        help='Путь к token.json')
    
    args = parser.parse_args()
    
    if not args.sheet_url:
        print('❌ Укажите URL Google Sheet через --sheet-url или GOOGLE_SHEET_URL в .env')
        sys.exit(1)
    
    if not os.path.exists(args.csv):
        print(f'❌ CSV файл не найден: {args.csv}')
        sys.exit(1)
    
    sync(args.csv, args.sheet_url, args.credentials, args.token)


if __name__ == '__main__':
    main()
