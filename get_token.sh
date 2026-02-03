#!/bin/bash

# Скрипт для получения авторизационного токена TL Integration API

# Загружаем переменные из .env
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
  export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

if [ -z "$TL_AUTH_KEY" ]; then
  echo "Ошибка: TL_AUTH_KEY не найден. Создайте .env файл с переменной TL_AUTH_KEY"
  exit 1
fi

TOKEN_RESPONSE=$(curl -s 'https://partner.tlintegration.com/auth/token' \
  -X 'POST' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -H 'Accept: application/json' \
  -H "Authorization: Basic $TL_AUTH_KEY" \
  --data 'grant_type=client_credentials')

# Вывод полного ответа
echo "Response: $TOKEN_RESPONSE"

# Извлечение токена с помощью jq (если установлен)
if command -v jq &> /dev/null; then
  ACCESS_TOKEN=$(echo "$TOKEN_RESPONSE" | jq -r '.access_token')
  TOKEN_TYPE=$(echo "$TOKEN_RESPONSE" | jq -r '.token_type')
  EXPIRES_IN=$(echo "$TOKEN_RESPONSE" | jq -r '.expires_in')
  
  echo ""
  echo "Access Token: $ACCESS_TOKEN"
  echo "Token Type: $TOKEN_TYPE"
  echo "Expires In: $EXPIRES_IN seconds"
fi
