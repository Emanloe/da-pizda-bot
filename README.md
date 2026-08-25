# Telegram Bot Da-Pizda

Telegram-бот с модульной архитектурой.

## Функционал
* Ежедневная игра «Пидор дня» по расписанию
* Поддержка текстовых триггеров и реакций на сообщения
* Учёт дней рождения пользователей
* Команды:
top - Посмотреть топ-10 «Пидоров дня» чата
weather - Узнать погоду, можно писать /weather Город
duel - Вызвать игрока на дуэль
duel_stats - Посмотреть статистику дуэлей
duel_top - Топ-10 дуэлянтов чата
force_pidor - Принудительно запустить выбор Пидора дня (админ)
setbday - Задать дату рождения пользователю (админ)
duel_delete - Удалить игрока из дуэльной базы чата (админ)
toggle_forward - Вкл/выкл реакцию на форварды (админ)
toggle_autodelete- Вкл/выкл автоудаление команд пользователей (админ)
* Отложенные ответы «Пизда» на старые сообщения «да» (джоба раз в 2–7 дней, 10:00–22:00 МСК)

## Отложенные «Пизда» (past_pizda)

Пул сообщений лежит в таблице `pizda_candidates`. Живые «да» бот дописывает сам. Стартовый пул заливается скриптом.

**Залить пул на проде из seed** (после `python handlers/past_pizda.py --export-seed` у себя):

```bash
# файл data/pizda_candidates_seed.json должен лежать рядом с кодом
python handlers/past_pizda.py
```

В Docker:

```bash
docker cp data/pizda_candidates_seed.json da-pizda-bot:/app/data/
docker exec -w /app da-pizda-bot python handlers/past_pizda.py
```

**Залить из полного экспорта Telegram** (`data/result.json`, в git не коммитить):

```bash
python handlers/past_pizda.py --from-export
```

Скрипт пишет в тот же `bot_database.db`, с которым крутится бот. Дубли игнорируются. После деплоя кода перезапусти бота — джоба сама поставит ближайший слот.

## Установка и запуск

1. Клонируйте репозиторий:
   ```bash
   git clone https://github.com/Emanloe/da-pizda-bot.git
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
