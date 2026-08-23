# Telegram Bot Da-Pizda

Telegram-бот с модульной архитектурой.

## Функционал
* Ежедневная игра «Пидор дня» по расписанию
* Поддержка текстовых триггеров и реакций на сообщения
* Учёт дней рождения пользователей
* Команда `/top` для вывода статистики
* Команда `/force_pidor` для принудительного выбора пидора дня
* 
## Установка и запуск

1. Клонируйте репозиторий:
   ```bash
   git clone [https://github.com/Emanloe/da-pizda-bot](https://github.com/Emanloe/da-pizda-bot)
   cd da-pizda-bot
   ```

2. Скопируйте файл конфигурации и укажите ваш токен и телеграм айди:
   ```bash
   cp .env.example .env
   ```

3. Запустите через Docker:
   ```bash
   docker build -t da-pizda-bot .
   docker run -d \
     --name da-pizda-bot \
     --restart always \
     --env-file .env \
     -v $(pwd)/bot_database.db:/app/bot_database.db \
     da-pizda-bot
   ```
