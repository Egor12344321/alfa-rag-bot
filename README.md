# Альфа Курс Telegram RAG бот

Стартовый каркас модульного монолита для трека 2. Три бизнес-модуля: знания и
каталог (`knowledge`), диалог (`assistant`), напоминания (`reminders`). Telegram —
адаптер; API, worker и ingest — точки запуска одного проекта.

Сейчас реализованы настройки, сборка зависимостей, API с проверками состояния
и начальная миграция. Поиск, генерация, обработка Telegram и напоминания
помечены `NotImplementedError` с указанием владельца. Пустой результат поиска
не подменяет отсутствие реализации.

Реализация Data-модуля Python 1 и результаты её отдельной проверки находятся в
[contrib/data-module](contrib/data-module/README.md). Она пока запускается в
изолированном окружении и не подключена к точкам входа этого каркаса.

## Запуск инфраструктуры

Нужен Docker с Compose. Скопировать `.env.example` в `.env`:

```bash
cp .env.example .env
docker compose up --build
```

В PowerShell первая команда: `Copy-Item .env.example .env`.

Compose поднимает PostgreSQL с pgvector, применяет миграцию и запускает API.
Порты опубликованы на localhost: API 8000, PostgreSQL 5432; значения меняются
через `API_PORT` и `POSTGRES_PORT`. Для простоты локальный каркас использует одну
демонстрационную роль БД; роли ingest/runtime/migrations нужно разделить до
развёртывания по архитектуре.

- `http://localhost:8000/healthz` — процесс работает, ответ 200, stage=scaffold.
- `http://localhost:8000/readyz` — ответ 503, пока нет рабочего индекса и реализации.
- `http://localhost:8000/docs` — техническая документация API.

503 в каркасе ожидаем: приложение не заявляет готовность отвечать пользователю.
Настоящий webhook не подключать до реализации durable inbox. Сейчас он
проверяет секрет и возвращает 501 без подтверждения принятия update.

Демонстрационные логин/пароль из env.example предназначены для локальной БД.
Рабочие ключи Telegram/LLM вписываются только в `.env` или секреты окружения.
Они не должны попадать в git, Docker image и логи.

## Разработка без Docker

Python 3.12 и uv:

```bash
uv sync --frozen
uv run alfa-api
```

Для применения миграции на уже работающем PostgreSQL с установленным pgvector:

```bash
uv run alembic upgrade head
```

DATABASE_URL на хосте использует `localhost`; Compose сам задаёт `db` внутри
контейнеров. Исходный дамп восстанавливается отдельно; runtime-контейнеры
SOURCE_DATABASE_URL не получают. Файлы подготовки размещаются в private_data.

Остальные точки запуска уже имеют CLI и `--help`, но бизнес-работа ещё не реализована:

```bash
uv run alfa-ingest --help
uv run alfa-ingest --check-config
uv run alfa-worker --help
uv run alfa-polling --help
```

`--check-config` проверяет наличие source URL, без подключения к источнику.
Worker и polling при обычном запуске завершаются с кодом 2 и сообщением о нужной
реализации. Профиль Compose `workers` включать после реализации scheduler/sender.
Ingest запускается через профиль `tools`; приватный каталог монтируется только
ему, в режиме чтения. Адрес source в Docker должен быть достижим из контейнера.

## Файлы и владельцы

| Путь | Назначение | Владелец |
|---|---|---|
| src/alfa_bot/knowledge | Импорт, правила допуска, очистка, чанки, поиск, каталог | Python 1 |
| src/alfa_bot/assistant | Контекст, уточнение, RAG, проверка ответа и источников | Python 2 |
| src/alfa_bot/reminders | Черновик, подтверждение, расписание, изменение, отмена | Python 3 |
| src/alfa_bot/adapters/telegram.py | Команды, кнопки, форматирование | Python 3 |
| src/alfa_bot/entrypoints | API, polling, ingest, worker | Владельцы модулей + DevOps |
| src/alfa_bot/contracts.py | DTO и Protocol между модулями | Согласует команда |
| src/alfa_bot/bootstrap.py | Сборка зависимостей | Команда |
| src/alfa_bot/infra | БД, LLM, embeddings, inbox/outbox, конфигурация | DevOps + владельцы интеграций |
| migrations | Версионирование рабочей БД | DevOps |
| docs | Архитектура и задачи | Продакт + команда |
| tests и eval | Технические проверки и качество RAG | Команда + продакт |

Внутри модуля достаточно service, repository и нескольких файлов по назначению.
Имена SDK, ORM и Telegram-типы не входят в общие DTO.

## Первые задачи

1. Python 1 проверяет DDL дампа и заполняет mapping.yml; готовит 10–20 разрешённых
   материалов, retrieve и публичные карточки. Пока mapping непроверен, доступ закрыт.
2. Python 2 реализует handle_turn через Knowledge/Reminders Protocol, начинает
   с fake-реализаций; сохраняет вопрос при уточнении и проверяет цитаты.
3. Python 3 реализует durable приём Telegram, подготовку и подтверждение reminder,
   календарные once/daily/weekly и кнопки изменения/отмены.
4. DevOps с Python 3 реализуют inbox/outbox, lease, повтор, восстановление,
   проверку revision и реальные роли БД. Сетевой вызов не держит SQL-транзакцию.
5. Продакт размечает вопросы и проводит общую проверку сквозного сценария.

## Зависимости и проверки

LangChain-компоненты для текста включены. Адаптеры генератора и embeddings пока
пустые. SDK конкретной LLM и локальной embedding-модели добавляет владелец после
выбора; точные сервис, модель и подключение ключа документируются здесь.

`uv.lock` фиксирует разрешённые зависимости. `requirements.txt` экспортирован
из него для Docker. После изменения зависимостей обновлять оба файла:

```bash
uv lock
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv run pytest
uv run ruff check .
```

Начальная миграция содержит ранее спроектированные 15 таблиц. Прямое применение
docs/schema.sql и Alembic к одной БД одновременно не использовать: выбрать Alembic.
Схема пока SQL-first; autogenerate станет доступен после появления полной ORM metadata.
Миграции не затрагивают alfa_source. downgrade удаляет таблицы приложения;
использовать только осознанно, не как команду запуска.

Подробная архитектура: docs/architecture.md. Текущие проверки и ограничения:
docs/VALIDATION.md. Исходные данные и рабочие секреты в этот проект не включены.
