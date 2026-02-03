#!/usr/bin/env python3
"""
Скрипт для проверки диплинков отелей TL Integration API.
Автоматически анализирует страницы через MCP браузер.

Режимы работы:
1. collect - собрать диплинки из API и сохранить в JSON
2. analyze - проанализировать страницу (вызывается из Cursor MCP)
3. report - сгенерировать отчёт из результатов
"""

import json
import csv
import time
import argparse
import re
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

# Загружаем .env файл
def load_dotenv():
    env_path = Path(__file__).parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_dotenv()

# Playwright импортируется только для команды auto
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


class TokenManager:
    """Управление авторизационным токеном с автообновлением."""
    
    AUTH_URL = 'https://partner.tlintegration.com/auth/token'
    
    @property
    def AUTH_HEADER(self):
        auth_key = os.environ.get('TL_AUTH_KEY')
        if not auth_key:
            raise ValueError("TL_AUTH_KEY не найден. Создайте .env файл с переменной TL_AUTH_KEY")
        return f'Basic {auth_key}'
    
    def __init__(self, buffer_seconds=60):
        self.token = None
        self.expires_at = 0
        self.buffer = buffer_seconds
    
    def get_token(self):
        """Получить актуальный токен, обновив при необходимости."""
        if self._is_expired():
            self._refresh_token()
        return self.token
    
    def _is_expired(self):
        """Проверить, истёк ли токен."""
        return time.time() >= (self.expires_at - self.buffer)
    
    def _refresh_token(self):
        """Обновить токен."""
        print("🔄 Получение нового токена...")
        response = requests.post(
            self.AUTH_URL,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'Accept': 'application/json',
                'Authorization': self.AUTH_HEADER
            },
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        data = response.json()
        
        self.token = data['access_token']
        self.expires_at = time.time() + data['expires_in']
        print(f"✅ Токен получен, истекает через {data['expires_in']} сек")


class PageAnalyzer:
    """Анализатор страницы по 5 критериям."""
    
    # Паттерны ошибок - очень строгие, только явные сообщения об ошибках
    ERROR_PATTERNS = [
        'страница не найдена', 'page not found', 'ошибка 404', 'error 404',
        '404 not found', 'не удалось загрузить страницу', 'сайт недоступен',
        'сервер не отвечает', 'service unavailable', 'internal server error'
    ]
    
    @staticmethod
    def analyze(snapshot: str, page_title: str, hotel_name: str, expected_date: str,
                adults: int = 1, children_count: int = 0, deeplink: str = '',
                expected_price: float = None) -> dict:
        """
        Анализ страницы по 6 критериям.
        
        Args:
            snapshot: YAML snapshot страницы от MCP browser
            page_title: Заголовок страницы
            hotel_name: Ожидаемое название отеля
            expected_date: Ожидаемая дата (YYYY-MM-DD)
            adults: Ожидаемое количество взрослых
            children_count: Ожидаемое количество детей
            deeplink: URL диплинка для проверки параметров
        
        Returns:
            dict с результатами проверки каждого критерия
        """
        # Нормализуем пробелы (TravelLine использует разные типы пробелов)
        # \xa0 = NBSP, \u2009 = thin space, \u202f = narrow NBSP
        snapshot_lower = snapshot.lower()
        for space_char in ['\xa0', '\u00a0', '\u2009', '\u202f', '\u2007', '\u2008']:
            snapshot_lower = snapshot_lower.replace(space_char, ' ')
        title_lower = page_title.lower()
        hotel_name_lower = hotel_name.lower()
        
        # Подготовим варианты названия отеля для поиска
        # Убираем кавычки и спецсимволы для более гибкого поиска
        hotel_name_clean = re.sub(r'[«»""\'"]', '', hotel_name_lower)
        hotel_name_words = hotel_name_clean.split()
        
        results = {
            'page_loaded': False,
            'name_matches': False,
            'has_travelline': False,
            'no_errors': True,
            'dates_correct': False,
            'guests_correct': False,
            'price_correct': False,
            'error_details': ''
        }
        
        errors = []
        
        # 1. Страница загрузилась - есть контент
        results['page_loaded'] = len(snapshot) > 200 and len(page_title) > 0
        if not results['page_loaded']:
            errors.append('Страница не загрузилась')
        
        # 2. Название отеля совпадает
        # Приоритет: сначала в iframe (виджет TravelLine), потом в заголовке/контенте
        iframe_content = PageAnalyzer._extract_iframe_content(snapshot)
        iframe_lower = iframe_content.lower() if iframe_content else ''
        
        # Ищем название в виджете TravelLine (более надёжно)
        name_in_widget = hotel_name_clean in iframe_lower or any(w in iframe_lower for w in hotel_name_words if len(w) > 3)
        # Запасной вариант - в заголовке или контенте страницы
        name_in_title = hotel_name_clean in title_lower or any(w in title_lower for w in hotel_name_words if len(w) > 3)
        name_in_content = hotel_name_clean in snapshot_lower or any(w in snapshot_lower for w in hotel_name_words if len(w) > 3)
        
        results['name_matches'] = name_in_widget or name_in_title or name_in_content
        if not results['name_matches']:
            errors.append(f'Название отеля не найдено: {hotel_name}')
        
        # 3. Есть виджет TravelLine (iframe с booking или travelline)
        has_iframe = 'iframe' in snapshot_lower
        has_booking_context = 'booking' in snapshot_lower or 'бронирован' in snapshot_lower
        has_travelline = 'travelline' in snapshot_lower or 'tl-' in snapshot_lower
        results['has_travelline'] = has_iframe and (has_booking_context or has_travelline)
        if not results['has_travelline']:
            errors.append('Виджет TravelLine не найден')
        
        # 4. Нет сообщений об ошибках (проверяем строгие паттерны)
        found_errors = [err for err in PageAnalyzer.ERROR_PATTERNS if err in snapshot_lower or err in title_lower]
        if found_errors:
            results['no_errors'] = False
            errors.append(f'Найдены ошибки: {", ".join(found_errors)}')
        
        # 5. Даты корректны - проверяем дату в странице
        date_parts = expected_date.split('-')  # ['2026', '08', '01']
        year, month, day = date_parts[0], date_parts[1], date_parts[2]
        
        # Форматы даты:
        # DD.MM.YYYY (01.08.2026)
        date_dot_format = f"{day}.{month}.{year}"
        # D.MM.YYYY (1.08.2026)
        day_int = str(int(day))  # убираем ведущий ноль: 01 -> 1
        date_dot_short = f"{day_int}.{month}.{year}"
        
        # D месяца (1 августа) - русский формат в виджете TravelLine
        months_ru = {
            '01': 'январ', '02': 'феврал', '03': 'март', '04': 'апрел',
            '05': 'ма', '06': 'июн', '07': 'июл', '08': 'август',
            '09': 'сентябр', '10': 'октябр', '11': 'ноябр', '12': 'декабр'
        }
        month_ru = months_ru.get(month, '')
        date_ru_text = f"{day_int} {month_ru}"  # "1 август"
        
        # Для HTML контента ищем и в iframe и в основном контенте
        iframe_content = PageAnalyzer._extract_iframe_content(snapshot)
        search_content = (iframe_content.lower() if iframe_content else '') + ' ' + snapshot_lower
        
        # Ищем дату (любой из форматов)
        date_found = (
            date_dot_format in search_content or 
            date_dot_short in search_content or
            date_ru_text in search_content or
            expected_date in search_content or
            # Также ищем в URL параметрах (tl-date=2026-08-01)
            f"tl-date={expected_date}" in snapshot_lower
        )
        
        results['dates_correct'] = date_found
        if not results['dates_correct']:
            # Найдём какая дата отображается
            found_dates = re.findall(r'\d{1,2}\.\d{2}\.\d{4}', search_content)
            found_ru_dates = re.findall(r'\d{1,2}\s+(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*', search_content)
            
            if found_dates:
                errors.append(f'Дата в виджете: {found_dates[0]}, ожидалась: {date_dot_format}')
            elif found_ru_dates:
                errors.append(f'Дата в виджете: {found_ru_dates[0]}, ожидалась: {day_int} {month_ru}...')
            else:
                errors.append(f'Дата не найдена')
        
        # 6. Гости корректны - ищем текст в виджете TravelLine
        # Формат в виджете: "1 взрослый, 1 ребёнок" или "2 взрослых"
        # Ищем в snapshot_lower напрямую (работает и для HTML и для YAML)
        
        # Паттерны для взрослых
        if adults == 1:
            adults_pattern = '1 взрослый'
        elif adults == 2:
            adults_pattern = '2 взрослых'
        else:
            adults_pattern = f'{adults} взрослых'
        
        adults_found = adults_pattern in snapshot_lower
        
        # Паттерны для детей
        children_found = True  # по умолчанию если детей нет
        if children_count > 0:
            if children_count == 1:
                children_patterns = ['1 ребёнок', '1 ребенок']
            else:
                children_patterns = [f'{children_count} ребёнка', f'{children_count} ребенка', 
                                     f'{children_count} детей']
            
            children_found = any(p in snapshot_lower for p in children_patterns)
            
            if not children_found:
                errors.append(f'Дети ({children_count}) не найдены в виджете')
        
        guests_found = adults_found and children_found
        results['guests_correct'] = guests_found
        
        if not adults_found:
            errors.append(f'Взрослые ({adults}) не найдены в виджете')
        
        # 7. Проверка цены
        if expected_price is not None:
            # Форматируем цену для поиска (8994.0 -> "8 994" или "8994")
            price_int = int(expected_price)
            # Формат с пробелом как разделителем тысяч
            price_formatted = f"{price_int:,}".replace(",", " ")  # "8 994"
            price_no_space = str(price_int)  # "8994"
            
            # Ищем цену на странице
            price_found = price_formatted in snapshot_lower or price_no_space in snapshot_lower
            results['price_correct'] = price_found
            
            if not price_found:
                errors.append(f'Цена {price_formatted} ₽ не найдена')
        else:
            # Если цена не указана, считаем проверку пройденной
            results['price_correct'] = True
        
        results['error_details'] = '; '.join(errors) if errors else ''
        
        return results
    
    @staticmethod
    def _extract_iframe_content(snapshot: str) -> str:
        """Извлечь содержимое всех iframe из snapshot."""
        lines = snapshot.split('\n')
        iframe_content = []
        in_iframe = False
        iframe_indent = 0
        
        for line in lines:
            # Определяем уровень отступа
            stripped = line.lstrip()
            current_indent = len(line) - len(stripped)
            
            if 'iframe' in stripped.lower() and '[ref=' in stripped:
                in_iframe = True
                iframe_indent = current_indent
                continue
            
            if in_iframe:
                # Если отступ меньше или равен iframe - вышли из iframe
                if stripped and current_indent <= iframe_indent:
                    in_iframe = False
                else:
                    # Добавляем содержимое iframe
                    iframe_content.append(stripped)
        
        return '\n'.join(iframe_content)
    
    @staticmethod
    def get_status(results: dict) -> str:
        """Определить итоговый статус на основе результатов проверки."""
        if all([results['page_loaded'], results['name_matches'], 
                results['has_travelline'], results['no_errors'], 
                results['dates_correct'], results['guests_correct'],
                results['price_correct']]):
            return 'success'
        elif not results['page_loaded']:
            return 'failed'
        else:
            return 'partial'


class DeeplinkCollector:
    """Сбор диплинков из API."""
    
    API_BASE = 'https://partner.tlintegration.com/api/search'
    
    def __init__(self, hotels_file: str, output_file: str, arrival_date: str, departure_date: str,
                 adults: int = 1, children_ages: list = None):
        self.hotels_file = Path(hotels_file)
        self.output_file = Path(output_file)
        self.arrival_date = arrival_date
        self.departure_date = departure_date
        self.adults = adults
        self.children_ages = children_ages or []
        self.token_manager = TokenManager()
        self.hotels = []
    
    def load_hotels(self):
        """Загрузить список отелей из JSON файла."""
        with open(self.hotels_file, 'r', encoding='utf-8') as f:
            self.hotels = json.load(f)
        print(f"📋 Загружено {len(self.hotels)} отелей")
    
    def get_room_stays(self, property_id: str, max_retries: int = 3) -> dict:
        """Получить информацию о номерах отеля с retry при 429."""
        token = self.token_manager.get_token()
        url = f"{self.API_BASE}/v1/properties/{property_id}/room-stays"
        
        params = {
            'arrivalDate': self.arrival_date,
            'departureDate': self.departure_date,
            'adults': self.adults
        }
        
        # Добавляем детей если есть (параметр childAges согласно API документации)
        if self.children_ages:
            for age in self.children_ages:
                params.setdefault('childAges', []).append(age)
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        
        for attempt in range(max_retries):
            response = requests.get(url, params=params, headers=headers)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                remaining_hour = int(response.headers.get('X-RateLimit-Remaining-Hour', 1))
                retry_after = int(response.headers.get('Retry-After', 5))
                
                if remaining_hour == 0:
                    wait_time = retry_after + 5
                    mins = wait_time // 60
                    secs = wait_time % 60
                    print(f"\n⏳ Часовой лимит исчерпан, жду {mins}м {secs}с до сброса...", end=' ')
                    time.sleep(wait_time)
                    print("✅ продолжаю")
                else:
                    wait_time = min(max(retry_after, 2 ** attempt), 30)
                    print(f"⏳ rate limit, жду {wait_time}с...", end=' ')
                    time.sleep(wait_time)
                continue
            elif response.status_code == 403:
                return {'error': response.status_code, 'message': 'No access to hotel'}
            else:
                return {'error': response.status_code, 'message': response.text[:200]}
        
        return {'error': 429, 'message': 'Rate limit exceeded after retries'}
    
    def extract_deeplink(self, room_stays_data: dict) -> tuple[Optional[str], Optional[float], Optional[str]]:
        """Извлечь диплинк первой комнаты, цену и валюту."""
        if 'error' in room_stays_data:
            return None, None, None
        
        rooms = room_stays_data.get('roomStays', [])
        
        if rooms and len(rooms) > 0:
            first_room = rooms[0]
            deeplink = first_room.get('bookingFormLink')
            
            # Извлекаем цену
            total = first_room.get('total', {})
            price = total.get('priceBeforeTax')
            currency = first_room.get('currencyCode', 'RUB')
            
            return deeplink, price, currency
        
        return None, None, None
    
    def get_room_stays_with_dates(self, property_id: str, arrival: str, departure: str, max_retries: int = 3) -> dict:
        """Получить информацию о номерах отеля для конкретных дат."""
        token = self.token_manager.get_token()
        url = f"{self.API_BASE}/v1/properties/{property_id}/room-stays"
        
        params = {
            'arrivalDate': arrival,
            'departureDate': departure,
            'adults': self.adults
        }
        
        if self.children_ages:
            for age in self.children_ages:
                params.setdefault('childAges', []).append(age)
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/json'
        }
        
        for attempt in range(max_retries):
            response = requests.get(url, params=params, headers=headers)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                remaining_hour = int(response.headers.get('X-RateLimit-Remaining-Hour', 1))
                retry_after = int(response.headers.get('Retry-After', 5))
                
                if remaining_hour == 0:
                    wait_time = retry_after + 5
                    mins = wait_time // 60
                    secs = wait_time % 60
                    print(f"\n⏳ Часовой лимит, жду {mins}м {secs}с...", end=' ')
                    time.sleep(wait_time)
                    print("✅")
                else:
                    wait_time = min(max(retry_after, 2 ** attempt), 30)
                    time.sleep(wait_time)
                continue
            elif response.status_code == 403:
                return {'error': 403}
            elif response.status_code == 404:
                return {'error': 404}
            else:
                return {'error': response.status_code}
        
        return {'error': 429}
    
    def try_fallback_dates(self, hotel_id: str) -> tuple[Optional[dict], str, str]:
        """Попробовать альтернативные даты если нет комнат."""
        # Fallback даты: март и октябрь следующего года
        fallback_dates = [
            ('2026-03-01', '2026-03-02', 'март'),
            ('2026-04-01', '2026-04-02', 'апрель'),
            ('2026-05-01', '2026-05-02', 'май'),
            ('2026-10-01', '2026-10-08', 'октябрь (неделя)'),
        ]
        
        for arrival, departure, period_name in fallback_dates:
            try:
                room_data = self.get_room_stays_with_dates(hotel_id, arrival, departure)
                if 'error' not in room_data:
                    deeplink, price, currency = self.extract_deeplink(room_data)
                    if deeplink:
                        return room_data, arrival, departure
            except:
                pass
        
        return None, None, None
    
    def collect(self, start_index: int = 0, limit: Optional[int] = None):
        """Собрать диплинки и сохранить в JSON файл."""
        self.load_hotels()
        
        if start_index >= len(self.hotels):
            print(f"❌ Начальный индекс {start_index} больше количества отелей ({len(self.hotels)})")
            return
        
        end_index = len(self.hotels) if limit is None else min(start_index + limit, len(self.hotels))
        
        print(f"\n🚀 Сбор диплинков: отели {start_index + 1} - {end_index}")
        print(f"📅 Даты: {self.arrival_date} - {self.departure_date}")
        
        # Загружаем существующие результаты если есть
        existing_results = {}
        if self.output_file.exists():
            with open(self.output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                existing_results = {r['hotel_id']: r for r in data.get('hotels', [])}
        
        results = []
        
        try:
            for i in range(start_index, end_index):
                hotel = self.hotels[i]
                hotel_id = hotel['id']
                hotel_name = hotel['name']
                
                print(f"[{i + 1}/{end_index}] {hotel_name} (ID: {hotel_id})...", end=' ')
                
                # Пропускаем если уже обработан
                if hotel_id in existing_results and existing_results[hotel_id].get('deeplink'):
                    print("⏭️  уже есть")
                    results.append(existing_results[hotel_id])
                    continue
                
                try:
                    room_data = self.get_room_stays(hotel_id)
                    time.sleep(0.5)  # задержка между запросами для избежания rate limit
                except requests.RequestException as e:
                    print(f"❌ ошибка API")
                    results.append({
                        'hotel_id': hotel_id,
                        'hotel_name': hotel_name,
                        'deeplink': None,
                        'api_status': 'api_error',
                        'collected_at': datetime.now().isoformat()
                    })
                    continue
                
                if 'error' in room_data:
                    error_code = room_data.get('error')
                    if error_code == 403:
                        print("🚫 нет доступа")
                        api_status = 'no_access'
                    else:
                        print(f"❌ ошибка {error_code}")
                        api_status = 'api_error'
                    
                    results.append({
                        'hotel_id': hotel_id,
                        'hotel_name': hotel_name,
                        'deeplink': None,
                        'api_status': api_status,
                        'collected_at': datetime.now().isoformat()
                    })
                    continue
                
                deeplink, price, currency = self.extract_deeplink(room_data)
                used_arrival = self.arrival_date
                used_departure = self.departure_date
                
                # Если нет комнат - пробуем альтернативные даты
                if not deeplink:
                    print("📭 нет комнат, пробуем другие даты...", end=' ')
                    fallback_data, fallback_arrival, fallback_departure = self.try_fallback_dates(hotel_id)
                    if fallback_data:
                        deeplink, price, currency = self.extract_deeplink(fallback_data)
                        used_arrival = fallback_arrival
                        used_departure = fallback_departure
                
                if not deeplink:
                    print("📭 нет комнат")
                    results.append({
                        'hotel_id': hotel_id,
                        'hotel_name': hotel_name,
                        'deeplink': None,
                        'price': None,
                        'currency': None,
                        'api_status': 'no_rooms',
                        'collected_at': datetime.now().isoformat()
                    })
                else:
                    date_info = f" [{used_arrival}]" if used_arrival != self.arrival_date else ""
                    print(f"✅ диплинк получен ({price} {currency}){date_info}")
                    results.append({
                        'hotel_id': hotel_id,
                        'hotel_name': hotel_name,
                        'deeplink': deeplink,
                        'price': price,
                        'currency': currency,
                        'arrival_date': used_arrival,
                        'departure_date': used_departure,
                        'api_status': 'ok',
                        'collected_at': datetime.now().isoformat()
                    })
        except KeyboardInterrupt:
            print("\n\n⚠️  Прервано пользователем")
        
        # Сохраняем результаты
        output_data = {
            'arrival_date': self.arrival_date,
            'departure_date': self.departure_date,
            'adults': self.adults,
            'children_ages': self.children_ages,
            'collected_at': datetime.now().isoformat(),
            'hotels': results
        }
        
        with open(self.output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        # Статистика
        stats = {
            'total': len(results),
            'with_deeplink': len([r for r in results if r.get('deeplink')]),
            'no_access': len([r for r in results if r.get('api_status') == 'no_access']),
            'no_rooms': len([r for r in results if r.get('api_status') == 'no_rooms']),
            'errors': len([r for r in results if r.get('api_status') == 'api_error'])
        }
        
        print(f"\n📊 Статистика:")
        print(f"   Всего: {stats['total']}")
        print(f"   С диплинком: {stats['with_deeplink']}")
        print(f"   Нет доступа: {stats['no_access']}")
        print(f"   Нет комнат: {stats['no_rooms']}")
        print(f"   Ошибки: {stats['errors']}")
        print(f"\n💾 Сохранено в: {self.output_file}")


class ResultsSaver:
    """Сохранение результатов анализа в CSV."""
    
    CSV_FIELDS = [
        'hotel_id', 'hotel_name', 'status',
        'check_page_loaded', 'check_name_matches', 'check_has_travelline',
        'check_no_errors', 'check_dates_correct', 'check_guests_correct',
        'check_price_correct', 'expected_price',
        'page_title', 'error_details', 'timestamp', 'deeplink'
    ]
    
    def __init__(self, output_file: str):
        self.output_file = Path(output_file)
    
    def save(self, hotel_id: str, hotel_name: str, deeplink: str, 
             page_title: str, analysis_results: dict, expected_price: float = None):
        """Сохранить результат анализа."""
        status = PageAnalyzer.get_status(analysis_results)
        
        result = {
            'hotel_id': hotel_id,
            'hotel_name': hotel_name,
            'deeplink': deeplink,
            'status': status,
            'check_page_loaded': analysis_results['page_loaded'],
            'check_name_matches': analysis_results['name_matches'],
            'check_has_travelline': analysis_results['has_travelline'],
            'check_no_errors': analysis_results['no_errors'],
            'check_dates_correct': analysis_results['dates_correct'],
            'check_guests_correct': analysis_results['guests_correct'],
            'check_price_correct': analysis_results['price_correct'],
            'expected_price': expected_price,
            'page_title': page_title,
            'error_details': analysis_results['error_details'],
            'timestamp': datetime.now().isoformat()
        }
        
        file_exists = self.output_file.exists()
        with open(self.output_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(result)
        
        return result
    
    def save_skipped(self, hotel_id: str, hotel_name: str, status: str, error_details: str = '', deeplink: str = ''):
        """Сохранить пропущенный отель (no_access, no_rooms, api_error)."""
        result = {
            'hotel_id': hotel_id,
            'hotel_name': hotel_name,
            'deeplink': deeplink,
            'status': status,
            'check_page_loaded': False,
            'check_name_matches': False,
            'check_has_travelline': False,
            'check_no_errors': False,
            'check_dates_correct': False,
            'check_guests_correct': False,
            'check_price_correct': False,
            'expected_price': None,
            'page_title': '',
            'error_details': error_details,
            'timestamp': datetime.now().isoformat()
        }
        
        file_exists = self.output_file.exists()
        with open(self.output_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=self.CSV_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(result)
        
        return result


class BrowserChecker:
    """Автоматическая проверка диплинков через Playwright."""
    
    WIDGET_MARKERS = ['выбрать', 'выберите номер', 'найти номер']
    MAX_WAIT_TIME = 8  # секунд на загрузку виджета с правильными датами
    POLL_INTERVAL = 0.3  # интервал проверки в секундах
    
    def __init__(self, deeplinks_file: str, results_file: str, headless: bool = True):
        self.deeplinks_file = Path(deeplinks_file)
        self.results_file = Path(results_file)
        self.headless = headless
        self.saver = ResultsSaver(results_file)
        self.browser = None
        self.context = None
        self.page = None
    
    def load_data(self):
        """Загрузить диплинки и уже проверенные отели."""
        if not self.deeplinks_file.exists():
            raise FileNotFoundError(f"Файл {self.deeplinks_file} не найден")
        
        with open(self.deeplinks_file, 'r', encoding='utf-8') as f:
            self.data = json.load(f)
        
        self.checked_ids = set()
        if self.results_file.exists():
            with open(self.results_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                self.checked_ids = {row['hotel_id'] for row in reader}
        
        # Отели с диплинками, которые ещё не проверены
        self.pending = [
            h for h in self.data.get('hotels', [])
            if h.get('deeplink') and h['hotel_id'] not in self.checked_ids
        ]
        
        return len(self.pending)
    
    def start_browser(self):
        """Запустить браузер."""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        # Маскируем webdriver чтобы TravelLine виджет загружался
        self.context.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined});')
        self.page = self.context.new_page()
    
    def stop_browser(self):
        """Остановить браузер."""
        if self.page:
            self.page.close()
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if hasattr(self, 'playwright'):
            self.playwright.stop()
    
    def wait_for_widget(self, expected_date: str = None) -> bool:
        """Подождать загрузки виджета TravelLine с правильными датами."""
        start_time = time.time()
        widget_found = False
        
        # Подготавливаем паттерны для поиска даты
        date_patterns = []
        if expected_date:
            parts = expected_date.split('-')
            if len(parts) == 3:
                year, month, day = parts
                day_int = str(int(day))
                # Разные форматы даты
                date_patterns = [
                    f"{day}.{month}.{year}",  # 01.08.2026
                    f"{day_int}.{month}.{year}",  # 1.08.2026
                    f"{day_int} август",  # 1 август (для августа)
                ]
                # Русские месяцы
                months_ru = {
                    '01': 'январ', '02': 'феврал', '03': 'март', '04': 'апрел',
                    '05': 'ма', '06': 'июн', '07': 'июл', '08': 'август',
                    '09': 'сентябр', '10': 'октябр', '11': 'ноябр', '12': 'декабр'
                }
                if month in months_ru:
                    date_patterns.append(f"{day_int} {months_ru[month]}")
        
        while time.time() - start_time < self.MAX_WAIT_TIME:
            try:
                content = self.page.content().lower()
                
                # Сначала проверяем маркеры виджета
                for marker in self.WIDGET_MARKERS:
                    if marker in content:
                        widget_found = True
                        break
                
                # Если виджет найден, проверяем дату
                if widget_found:
                    if not date_patterns:
                        return True
                    for pattern in date_patterns:
                        if pattern.lower() in content:
                            return True
            except:
                pass
            time.sleep(self.POLL_INTERVAL)
        
        return widget_found  # Возвращаем хотя бы факт нахождения виджета
    
    def get_page_text(self) -> str:
        """Получить текстовое содержимое страницы для анализа."""
        try:
            parts = []
            
            # Получаем HTML главной страницы
            parts.append(self.page.content())
            
            # Ищем TravelLine iframe и получаем его контент
            for frame in self.page.frames:
                if 'tlintegration' in frame.url or 'travelline' in frame.url:
                    try:
                        # Получаем текст iframe
                        text = frame.locator('body').inner_text(timeout=3000)
                        parts.append(text)
                        
                        # Получаем значения input полей (там даты и гости)
                        inputs = frame.query_selector_all('input')
                        for inp in inputs:
                            val = inp.input_value()
                            if val:
                                parts.append(val)
                    except:
                        pass
            
            return '\n'.join(parts)
        except:
            return ''
    
    def check_hotel(self, hotel: dict) -> dict:
        """Проверить один отель."""
        hotel_id = hotel['hotel_id']
        hotel_name = hotel['hotel_name']
        deeplink = hotel['deeplink']
        expected_price = hotel.get('price')  # Цена из API
        # Берём дату из отеля (может быть fallback), иначе из глобальных данных
        arrival_date = hotel.get('arrival_date') or self.data.get('arrival_date', '2026-08-01')
        adults = self.data.get('adults', 1)
        children_ages = self.data.get('children_ages', [])
        children_count = len(children_ages)
        
        result = {
            'hotel_id': hotel_id,
            'hotel_name': hotel_name,
            'deeplink': deeplink,
            'status': 'failed',
            'error': ''
        }
        
        try:
            # Переходим на страницу
            self.page.goto(deeplink, timeout=15000, wait_until='domcontentloaded')
            
            # Ждём загрузки виджета с правильными датами
            widget_loaded = self.wait_for_widget(expected_date=arrival_date)
            
            # Получаем данные страницы
            page_title = self.page.title()
            page_content = self.get_page_text()
            
            # Анализируем страницу
            analysis = PageAnalyzer.analyze(
                snapshot=page_content,
                page_title=page_title,
                hotel_name=hotel_name,
                expected_date=arrival_date,
                adults=adults,
                children_count=children_count,
                deeplink=deeplink,
                expected_price=expected_price
            )
            
            # Сохраняем результат
            self.saver.save(
                hotel_id=hotel_id,
                hotel_name=hotel_name,
                deeplink=deeplink,
                page_title=page_title,
                analysis_results=analysis,
                expected_price=expected_price
            )
            
            status = PageAnalyzer.get_status(analysis)
            result['status'] = status
            result['analysis'] = analysis
            
        except PlaywrightTimeout:
            result['error'] = 'Timeout загрузки страницы'
            self.saver.save_skipped(hotel_id, hotel_name, 'failed', 'Timeout', deeplink=deeplink)
        except Exception as e:
            result['error'] = str(e)[:100]
            self.saver.save_skipped(hotel_id, hotel_name, 'failed', str(e)[:100], deeplink=deeplink)
        
        return result
    
    def run(self, start: int = 0, limit: Optional[int] = None):
        """Запустить автоматическую проверку."""
        pending_count = self.load_data()
        
        if pending_count == 0:
            print("✅ Все отели уже проверены!")
            return
        
        end_idx = len(self.pending) if limit is None else min(start + limit, len(self.pending))
        hotels_to_check = self.pending[start:end_idx]
        
        adults = self.data.get('adults', 1)
        children_ages = self.data.get('children_ages', [])
        
        print(f"\n🚀 Автоматическая проверка диплинков")
        print(f"📋 Отелей к проверке: {len(hotels_to_check)}")
        print(f"📅 Даты: {self.data.get('arrival_date')} - {self.data.get('departure_date')}")
        print(f"👥 Гости: {adults} взр." + (f" + дети {children_ages}" if children_ages else ""))
        print(f"🌐 Режим: {'headless' if self.headless else 'с GUI'}")
        print("-" * 50)
        
        stats = {'success': 0, 'partial': 0, 'failed': 0}
        
        try:
            self.start_browser()
            
            for i, hotel in enumerate(hotels_to_check):
                print(f"[{i+1}/{len(hotels_to_check)}] {hotel['hotel_name'][:40]}...", end=' ', flush=True)
                
                result = self.check_hotel(hotel)
                stats[result['status']] = stats.get(result['status'], 0) + 1
                
                if result['status'] == 'success':
                    print("✅")
                elif result['status'] == 'partial':
                    print(f"⚠️  {result.get('analysis', {}).get('error_details', '')[:40]}")
                else:
                    print(f"❌ {result.get('error', '')[:40]}")
                
                # Небольшая пауза между запросами
                time.sleep(0.5)
        
        except KeyboardInterrupt:
            print("\n\n⚠️  Прервано пользователем")
        finally:
            self.stop_browser()
        
        print("\n" + "=" * 50)
        print(f"📊 Результаты:")
        print(f"   ✅ Успешно: {stats['success']}")
        print(f"   ⚠️  Частично: {stats['partial']}")
        print(f"   ❌ Ошибки: {stats['failed']}")
        print(f"\n💾 Результаты сохранены в: {self.results_file}")


class CombinedChecker:
    """Сбор диплинка + проверка в одном шаге."""
    
    def __init__(self, hotels_file: str, results_file: str, arrival_date: str, departure_date: str,
                 adults: int = 1, children_ages: list = None, headless: bool = True):
        self.hotels_file = Path(hotels_file)
        self.results_file = Path(results_file)
        self.arrival_date = arrival_date
        self.departure_date = departure_date
        self.adults = adults
        self.children_ages = children_ages or []
        self.headless = headless
        
        self.token_manager = TokenManager()
        self.hotels = []
        self.checked_ids = set()
        
        # Playwright
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
    
    def load_hotels(self):
        """Загрузить список отелей."""
        with open(self.hotels_file, 'r', encoding='utf-8') as f:
            self.hotels = json.load(f)
        print(f"📋 Загружено {len(self.hotels)} отелей")
    
    def load_checked(self):
        """Загрузить уже проверенные отели из CSV (кроме no_rooms - их перепроверяем)."""
        no_rooms_count = 0
        if self.results_file.exists():
            with open(self.results_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('status') == 'no_rooms':
                        no_rooms_count += 1
                        continue  # Не добавляем - будем перепроверять
                    self.checked_ids.add(row['hotel_id'])
            print(f"📊 Уже проверено: {len(self.checked_ids)} отелей" + 
                  (f" (+ {no_rooms_count} no_rooms для перепроверки)" if no_rooms_count else ""))
    
    def start_browser(self):
        """Запустить браузер."""
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self.context = self.browser.new_context(
            viewport={'width': 1280, 'height': 720},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        self.context.add_init_script('Object.defineProperty(navigator, "webdriver", {get: () => undefined});')
        self.page = self.context.new_page()
    
    def stop_browser(self):
        """Остановить браузер."""
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
    
    def get_deeplink(self, hotel_id: str) -> tuple[Optional[str], Optional[float], str, str]:
        """Получить диплинк из API с fallback датами."""
        API_BASE = 'https://partner.tlintegration.com/api/search'
        
        # Основной запрос
        result = self._api_request(API_BASE, hotel_id, self.arrival_date, self.departure_date)
        if result[0]:  # deeplink found
            return result
        
        # Fallback даты
        fallback_dates = [
            ('2026-03-01', '2026-03-02'),
            ('2026-04-01', '2026-04-02'),
            ('2026-05-01', '2026-05-02'),
            ('2026-10-01', '2026-10-08'),
        ]
        
        for arrival, departure in fallback_dates:
            result = self._api_request(API_BASE, hotel_id, arrival, departure)
            if result[0]:
                return result
        
        return None, None, self.arrival_date, self.departure_date
    
    def _api_request(self, api_base: str, hotel_id: str, arrival: str, departure: str, 
                     max_retries: int = 3) -> tuple[Optional[str], Optional[float], str, str]:
        """Запрос к API с retry."""
        token = self.token_manager.get_token()
        url = f"{api_base}/v1/properties/{hotel_id}/room-stays"
        
        params = {
            'arrivalDate': arrival,
            'departureDate': departure,
            'adults': self.adults
        }
        if self.children_ages:
            for age in self.children_ages:
                params.setdefault('childAges', []).append(age)
        
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    rooms = data.get('roomStays', [])
                    if rooms:
                        room = rooms[0]
                        deeplink = room.get('bookingFormLink')
                        price = room.get('total', {}).get('priceBeforeTax')
                        return deeplink, price, arrival, departure
                    return None, None, arrival, departure
                
                elif response.status_code == 429:
                    remaining_hour = int(response.headers.get('X-RateLimit-Remaining-Hour', 1))
                    retry_after = int(response.headers.get('Retry-After', 5))
                    
                    if remaining_hour == 0:
                        # Часовой лимит исчерпан — ждём до полного сброса
                        wait_time = retry_after + 5  # + небольшой буфер
                        mins = wait_time // 60
                        secs = wait_time % 60
                        print(f"\n⏳ Часовой лимит исчерпан, жду {mins}м {secs}с до сброса...", end=' ')
                        time.sleep(wait_time)
                        print("✅ продолжаю")
                    else:
                        wait_time = min(max(retry_after, 2 ** attempt), 30)
                        print(f"⏳ rate limit, жду {wait_time}с...", end=' ')
                        time.sleep(wait_time)
                    continue
                
                elif response.status_code == 403:
                    return 'no_access', None, arrival, departure
                
                else:
                    return None, None, arrival, departure
                    
            except requests.RequestException:
                return None, None, arrival, departure
        
        return None, None, arrival, departure
    
    def wait_for_widget(self, expected_date: str) -> bool:
        """Ждать загрузки виджета TravelLine."""
        MAX_WAIT = 8
        POLL_INTERVAL = 0.3
        
        # Парсим дату для поиска
        try:
            date_obj = datetime.strptime(expected_date, '%Y-%m-%d')
            day = date_obj.day
            months_ru = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
                         'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']
            date_pattern = f"{day} {months_ru[date_obj.month - 1]}"
        except:
            date_pattern = expected_date
        
        start_time = time.time()
        while time.time() - start_time < MAX_WAIT:
            try:
                content = self.get_page_text().lower()
                if 'выбрать' in content and date_pattern.lower() in content:
                    return True
            except:
                pass
            time.sleep(POLL_INTERVAL)
        
        return False
    
    def get_page_text(self) -> str:
        """Получить текст страницы включая iframe."""
        try:
            parts = [self.page.content()]
            
            for frame in self.page.frames:
                if 'tlintegration' in frame.url or 'travelline' in frame.url:
                    try:
                        text = frame.locator('body').inner_text(timeout=3000)
                        parts.append(text)
                        
                        inputs = frame.query_selector_all('input')
                        for inp in inputs:
                            val = inp.input_value()
                            if val:
                                parts.append(val)
                    except:
                        pass
            
            return '\n'.join(parts)
        except:
            return ''
    
    def check_hotel(self, hotel_id: str, hotel_name: str, deeplink: str, 
                    price: float, arrival_date: str) -> dict:
        """Проверить страницу отеля."""
        children_count = len(self.children_ages)
        
        try:
            self.page.goto(deeplink, timeout=15000, wait_until='domcontentloaded')
            self.wait_for_widget(expected_date=arrival_date)
            
            page_title = self.page.title()
            page_content = self.get_page_text()
            
            analysis = PageAnalyzer.analyze(
                snapshot=page_content,
                page_title=page_title,
                hotel_name=hotel_name,
                expected_date=arrival_date,
                adults=self.adults,
                children_count=children_count,
                deeplink=deeplink,
                expected_price=price
            )
            
            status = PageAnalyzer.get_status(analysis)
            return {
                'status': status,
                'analysis': analysis,
                'page_title': page_title,
                'error': ''
            }
            
        except PlaywrightTimeout:
            return {'status': 'failed', 'analysis': None, 'page_title': '', 'error': 'Timeout'}
        except Exception as e:
            return {'status': 'failed', 'analysis': None, 'page_title': '', 'error': str(e)[:100]}
    
    def save_result(self, hotel_id: str, hotel_name: str, deeplink: str, 
                    price: float, status: str, analysis: dict, page_title: str, error: str):
        """Сохранить результат в CSV."""
        CSV_FIELDS = [
            'hotel_id', 'hotel_name', 'status',
            'check_page_loaded', 'check_name_matches', 'check_has_travelline',
            'check_no_errors', 'check_dates_correct', 'check_guests_correct',
            'check_price_correct', 'expected_price',
            'page_title', 'error_details', 'timestamp', 'deeplink'
        ]
        
        result = {
            'hotel_id': hotel_id,
            'hotel_name': hotel_name,
            'status': status,
            'check_page_loaded': analysis['page_loaded'] if analysis else False,
            'check_name_matches': analysis['name_matches'] if analysis else False,
            'check_has_travelline': analysis['has_travelline'] if analysis else False,
            'check_no_errors': analysis['no_errors'] if analysis else False,
            'check_dates_correct': analysis['dates_correct'] if analysis else False,
            'check_guests_correct': analysis['guests_correct'] if analysis else False,
            'check_price_correct': analysis['price_correct'] if analysis else False,
            'expected_price': price,
            'page_title': page_title,
            'error_details': analysis['error_details'] if analysis else error,
            'timestamp': datetime.now().isoformat(),
            'deeplink': deeplink or ''
        }
        
        file_exists = self.results_file.exists()
        with open(self.results_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(result)
    
    def run(self, start: int = 0, limit: Optional[int] = None):
        """Запустить проверку."""
        self.load_hotels()
        self.load_checked()
        
        # Собираем непроверенные отели: пропускаем start штук, берём limit штук
        to_check = []
        skipped_checked = 0
        skipped_start = 0
        
        for i, hotel in enumerate(self.hotels):
            if hotel['id'] in self.checked_ids:
                skipped_checked += 1
                continue
            
            # Пропускаем первые start непроверенных
            if skipped_start < start:
                skipped_start += 1
                continue
            
            to_check.append((i, hotel))
            if limit is not None and len(to_check) >= limit:
                break
        
        if not to_check:
            print("✅ Все отели уже проверены")
            return
        
        total_unchecked = len(self.hotels) - len(self.checked_ids)
        print(f"\n🚀 Проверка: {len(to_check)} отелей (всего непроверенных: {total_unchecked}, пропущено по --start: {skipped_start})")
        print(f"📅 Даты: {self.arrival_date} - {self.departure_date}")
        print(f"👥 Гости: {self.adults} взр." + (f" + дети {self.children_ages}" if self.children_ages else ""))
        print("-" * 50)
        
        self.start_browser()
        
        stats = {'success': 0, 'partial': 0, 'failed': 0, 'no_access': 0, 'no_rooms': 0}
        
        try:
            for idx, (i, hotel) in enumerate(to_check):
                hotel_id = hotel['id']
                hotel_name = hotel['name']
                
                print(f"[{idx + 1}/{len(to_check)}] {hotel_name[:30]}...", end=' ')
                
                # 1. Получаем диплинк
                deeplink, price, arrival, departure = self.get_deeplink(hotel_id)
                time.sleep(0.5)  # Rate limit prevention
                
                if deeplink == 'no_access':
                    print("🚫 нет доступа")
                    self.save_result(hotel_id, hotel_name, '', None, 'no_access', None, '', 'API access denied')
                    stats['no_access'] += 1
                    continue
                
                if not deeplink:
                    print("📭 нет комнат")
                    self.save_result(hotel_id, hotel_name, '', None, 'no_rooms', None, '', 'No rooms available')
                    stats['no_rooms'] += 1
                    continue
                
                # 2. Проверяем страницу
                result = self.check_hotel(hotel_id, hotel_name, deeplink, price, arrival)
                
                # 3. Сохраняем результат
                self.save_result(
                    hotel_id, hotel_name, deeplink, price,
                    result['status'], result['analysis'],
                    result['page_title'], result['error']
                )
                
                status = result['status']
                stats[status] = stats.get(status, 0) + 1
                
                if status == 'success':
                    print("✅")
                elif status == 'partial':
                    errors = result['analysis']['error_details'] if result['analysis'] else result['error']
                    print(f"⚠️  {errors[:50]}")
                else:
                    print(f"❌ {result['error'][:30]}")
                    
        except KeyboardInterrupt:
            print("\n\n⚠️  Прервано пользователем")
        finally:
            self.stop_browser()
        
        print("\n" + "=" * 50)
        print("📊 Результаты:")
        print(f"   ✅ Успешно: {stats['success']}")
        print(f"   ⚠️  Частично: {stats['partial']}")
        print(f"   ❌ Ошибки: {stats['failed']}")
        print(f"   🚫 Нет доступа: {stats['no_access']}")
        print(f"   📭 Нет комнат: {stats['no_rooms']}")
        print(f"\n💾 Результаты: {self.results_file}")


def cmd_check(args):
    """Команда объединённой проверки (collect + auto)."""
    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright не установлен. Выполните:")
        print("   pip install playwright")
        print("   playwright install chromium")
        sys.exit(1)
    
    arrival = datetime.strptime(args.date, '%Y-%m-%d')
    departure = arrival + timedelta(days=args.nights)
    
    children_ages = []
    if args.children:
        children_ages = [int(age.strip()) for age in args.children.split(',')]
    
    checker = CombinedChecker(
        hotels_file=args.hotels,
        results_file=args.output,
        arrival_date=arrival.strftime('%Y-%m-%d'),
        departure_date=departure.strftime('%Y-%m-%d'),
        adults=args.adults,
        children_ages=children_ages,
        headless=not args.gui
    )
    
    checker.run(start=args.start, limit=args.limit)


def cmd_auto(args):
    """Команда автоматической проверки через Playwright."""
    if not PLAYWRIGHT_AVAILABLE:
        print("❌ Playwright не установлен. Выполните:")
        print("   pip install playwright")
        print("   playwright install chromium")
        sys.exit(1)
    
    # Автоматическое имя файла на основе диапазона
    output_file = args.output
    if args.auto_name and args.limit:
        base = Path(args.output).stem
        ext = Path(args.output).suffix or '.csv'
        end = args.start + args.limit
        output_file = f"{base}_{args.start}_{end}{ext}"
    
    checker = BrowserChecker(
        deeplinks_file=args.deeplinks,
        results_file=output_file,
        headless=not args.gui
    )
    
    checker.run(start=args.start, limit=args.limit)


def cmd_collect(args):
    """Команда сбора диплинков."""
    arrival = datetime.strptime(args.date, '%Y-%m-%d')
    departure = arrival + timedelta(days=args.nights)
    
    # Парсим возраста детей
    children_ages = []
    if args.children:
        children_ages = [int(age.strip()) for age in args.children.split(',')]
    
    collector = DeeplinkCollector(
        hotels_file=args.hotels,
        output_file=args.output,
        arrival_date=arrival.strftime('%Y-%m-%d'),
        departure_date=departure.strftime('%Y-%m-%d'),
        adults=args.adults,
        children_ages=children_ages
    )
    
    print(f"👥 Гости: {args.adults} взрослых" + (f", дети: {children_ages}" if children_ages else ""))
    collector.collect(start_index=args.start, limit=args.limit)


def cmd_analyze(args):
    """Команда анализа страницы (для вызова из Cursor)."""
    results = PageAnalyzer.analyze(
        snapshot=args.snapshot,
        page_title=args.title,
        hotel_name=args.hotel_name,
        expected_date=args.date
    )
    
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return results


def cmd_next(args):
    """Получить следующий отель для проверки."""
    # Загружаем собранные диплинки
    deeplinks_file = Path(args.deeplinks)
    if not deeplinks_file.exists():
        print("❌ Файл с диплинками не найден. Сначала выполните: python check_deeplinks.py collect")
        return
    
    with open(deeplinks_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Загружаем уже проверенные
    results_file = Path(args.results)
    checked_ids = set()
    if results_file.exists():
        with open(results_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            checked_ids = {row['hotel_id'] for row in reader}
    
    # Находим следующий непроверенный отель с диплинком
    for hotel in data.get('hotels', []):
        if hotel['hotel_id'] not in checked_ids and hotel.get('deeplink'):
            result = {
                'hotel_id': hotel['hotel_id'],
                'hotel_name': hotel['hotel_name'],
                'deeplink': hotel['deeplink'],
                'arrival_date': data.get('arrival_date', ''),
                'index': data['hotels'].index(hotel)
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return result
    
    print('{"status": "done", "message": "Все отели проверены"}')
    return None


def cmd_save(args):
    """Сохранить результат анализа."""
    saver = ResultsSaver(args.output)
    
    if args.status in ('no_access', 'no_rooms', 'api_error'):
        result = saver.save_skipped(
            hotel_id=args.hotel_id,
            hotel_name=args.hotel_name,
            status=args.status,
            error_details=args.error or ''
        )
    else:
        # Парсим результаты анализа
        analysis = json.loads(args.analysis) if args.analysis else {}
        result = saver.save(
            hotel_id=args.hotel_id,
            hotel_name=args.hotel_name,
            deeplink=args.deeplink or '',
            page_title=args.title or '',
            analysis_results=analysis
        )
    
    print(f"✅ Сохранено: {args.hotel_name} -> {result['status']}")


def cmd_merge(args):
    """Объединить несколько CSV файлов результатов."""
    import glob
    
    pattern = args.pattern
    output_file = Path(args.output)
    
    # Находим все файлы по паттерну
    files = sorted(glob.glob(pattern))
    
    if not files:
        print(f"❌ Файлы не найдены по паттерну: {pattern}")
        return
    
    print(f"📁 Найдено файлов: {len(files)}")
    for f in files:
        print(f"   - {f}")
    
    all_rows = []
    header = None
    
    for filepath in files:
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if header is None:
                header = reader.fieldnames
            for row in reader:
                all_rows.append(row)
    
    # Убираем дубликаты по hotel_id
    seen = set()
    unique_rows = []
    for row in all_rows:
        if row['hotel_id'] not in seen:
            seen.add(row['hotel_id'])
            unique_rows.append(row)
    
    # Сохраняем объединённый файл
    with open(output_file, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(unique_rows)
    
    print(f"\n✅ Объединено записей: {len(unique_rows)}")
    print(f"💾 Сохранено в: {output_file}")


def cmd_report(args):
    """Сгенерировать отчёт."""
    results_file = Path(args.results)
    if not results_file.exists():
        print("❌ Файл результатов не найден")
        return
    
    with open(results_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    stats = {
        'total': len(rows),
        'success': len([r for r in rows if r['status'] == 'success']),
        'partial': len([r for r in rows if r['status'] == 'partial']),
        'failed': len([r for r in rows if r['status'] == 'failed']),
        'no_access': len([r for r in rows if r['status'] == 'no_access']),
        'no_rooms': len([r for r in rows if r['status'] == 'no_rooms']),
        'api_error': len([r for r in rows if r['status'] == 'api_error'])
    }
    
    print("\n📊 ОТЧЁТ ПО ПРОВЕРКЕ ДИПЛИНКОВ")
    print("=" * 40)
    print(f"Всего проверено: {stats['total']}")
    print(f"✅ Успешно (все критерии): {stats['success']}")
    print(f"⚠️  Частично (есть проблемы): {stats['partial']}")
    print(f"❌ Ошибка загрузки: {stats['failed']}")
    print(f"🚫 Нет доступа к API: {stats['no_access']}")
    print(f"📭 Нет комнат: {stats['no_rooms']}")
    print(f"💥 Ошибка API: {stats['api_error']}")
    
    # Показать проблемные
    problems = [r for r in rows if r['status'] in ('partial', 'failed') and r.get('error_details')]
    if problems:
        print(f"\n⚠️  Проблемные отели ({len(problems)}):")
        for r in problems[:10]:
            print(f"   - {r['hotel_name']}: {r['error_details'][:60]}")
        if len(problems) > 10:
            print(f"   ... и ещё {len(problems) - 10}")


def main():
    parser = argparse.ArgumentParser(description='Проверка диплинков отелей TL Integration')
    subparsers = parser.add_subparsers(dest='command', help='Команды')
    
    # Команда collect
    collect_parser = subparsers.add_parser('collect', help='Собрать диплинки из API')
    collect_parser.add_argument('--start', type=int, default=0, help='Начать с N-го отеля')
    collect_parser.add_argument('--limit', type=int, default=None, help='Ограничить количество')
    collect_parser.add_argument('--date', type=str, default='2026-08-01', help='Дата заезда')
    collect_parser.add_argument('--nights', type=int, default=1, help='Количество ночей')
    collect_parser.add_argument('--adults', type=int, default=1, help='Количество взрослых')
    collect_parser.add_argument('--children', type=str, default='', help='Возраста детей через запятую (напр. 5,10)')
    collect_parser.add_argument('--hotels', type=str, default='hotels_id_name.json', help='Файл с отелями')
    collect_parser.add_argument('--output', type=str, default='deeplinks.json', help='Выходной JSON')
    
    # Команда next
    next_parser = subparsers.add_parser('next', help='Получить следующий отель для проверки')
    next_parser.add_argument('--deeplinks', type=str, default='deeplinks.json', help='Файл с диплинками')
    next_parser.add_argument('--results', type=str, default='deeplink_results.csv', help='Файл результатов')
    
    # Команда save
    save_parser = subparsers.add_parser('save', help='Сохранить результат анализа')
    save_parser.add_argument('--hotel-id', type=str, required=True, help='ID отеля')
    save_parser.add_argument('--hotel-name', type=str, required=True, help='Название отеля')
    save_parser.add_argument('--deeplink', type=str, help='Диплинк')
    save_parser.add_argument('--title', type=str, help='Заголовок страницы')
    save_parser.add_argument('--status', type=str, help='Статус (no_access, no_rooms, api_error)')
    save_parser.add_argument('--analysis', type=str, help='JSON с результатами анализа')
    save_parser.add_argument('--error', type=str, help='Описание ошибки')
    save_parser.add_argument('--output', type=str, default='deeplink_results.csv', help='Выходной CSV')
    
    # Команда report
    report_parser = subparsers.add_parser('report', help='Показать отчёт')
    report_parser.add_argument('--results', type=str, default='deeplink_results.csv', help='Файл результатов')
    
    # Команда merge - объединить результаты
    merge_parser = subparsers.add_parser('merge', help='Объединить CSV файлы результатов')
    merge_parser.add_argument('--pattern', type=str, default='deeplink_results_*.csv', help='Паттерн файлов')
    merge_parser.add_argument('--output', type=str, default='deeplink_results_merged.csv', help='Выходной файл')
    
    # Команда analyze (для тестирования)
    analyze_parser = subparsers.add_parser('analyze', help='Анализировать страницу')
    analyze_parser.add_argument('--snapshot', type=str, required=True, help='Snapshot страницы')
    analyze_parser.add_argument('--title', type=str, required=True, help='Заголовок')
    analyze_parser.add_argument('--hotel-name', type=str, required=True, help='Название отеля')
    analyze_parser.add_argument('--date', type=str, required=True, help='Ожидаемая дата')
    
    # Команда auto - автоматическая проверка через Playwright
    auto_parser = subparsers.add_parser('auto', help='Автоматическая проверка через браузер')
    auto_parser.add_argument('--start', type=int, default=0, help='Начать с N-го отеля')
    auto_parser.add_argument('--limit', type=int, default=None, help='Ограничить количество')
    auto_parser.add_argument('--deeplinks', type=str, default='deeplinks.json', help='Файл с диплинками')
    auto_parser.add_argument('--output', type=str, default='deeplink_results.csv', help='Выходной CSV')
    auto_parser.add_argument('--auto-name', action='store_true', help='Автоимя файла: results_START_END.csv')
    auto_parser.add_argument('--gui', action='store_true', help='Показать окно браузера')
    
    # Команда check - объединённая проверка (collect + auto в одном)
    check_parser = subparsers.add_parser('check', help='Получить диплинк и сразу проверить')
    check_parser.add_argument('--start', type=int, default=0, help='Начать с N-го отеля')
    check_parser.add_argument('--limit', type=int, default=None, help='Ограничить количество')
    check_parser.add_argument('--date', type=str, default='2026-08-01', help='Дата заезда')
    check_parser.add_argument('--nights', type=int, default=1, help='Количество ночей')
    check_parser.add_argument('--adults', type=int, default=1, help='Количество взрослых')
    check_parser.add_argument('--children', type=str, default='', help='Возраста детей через запятую')
    check_parser.add_argument('--hotels', type=str, default='hotels_id_name.json', help='Файл с отелями')
    check_parser.add_argument('--output', type=str, default='deeplink_results.csv', help='Выходной CSV')
    check_parser.add_argument('--gui', action='store_true', help='Показать окно браузера')
    
    args = parser.parse_args()
    
    if args.command == 'collect':
        cmd_collect(args)
    elif args.command == 'next':
        cmd_next(args)
    elif args.command == 'save':
        cmd_save(args)
    elif args.command == 'report':
        cmd_report(args)
    elif args.command == 'analyze':
        cmd_analyze(args)
    elif args.command == 'auto':
        cmd_auto(args)
    elif args.command == 'merge':
        cmd_merge(args)
    elif args.command == 'check':
        cmd_check(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
