# Импорт выгрузки «Альфа-Курса»

Сырые таблицы дампа остаются в отдельной локальной БД. База приложения хранит индекс, профиль, историю и напоминания.

## Восстановите исходную БД

Нужны работающий PostgreSQL и утилиты createdb и pg_restore версии 17 или новее. Пример использует роль postgres. При необходимости укажите полный путь к каталогу bin PostgreSQL.

    createdb -h 127.0.0.1 -U postgres alfa_course_source
    pg_restore -h 127.0.0.1 -U postgres --no-owner --no-privileges --exit-on-error -d alfa_course_source 'D:\alfa_course_hackathon.backup'

Импорт читает опубликованные записи и исключает удалённые материалы и закрытые статьи. Тексты уроков и данные активности остаются в исходной базе. Курс участвует через публичную карточку. В этой выгрузке Courses.Cost = NULL означает бесплатный курс.

## Подготовьте корпус для Docker

В корне проекта выполните uv sync --locked. В .env укажите подключение к восстановленной базе в SOURCE_DATABASE_URL, например postgresql+asyncpg://postgres:пароль@127.0.0.1:5432/alfa_course_source. Выполните:

    uv run python -m alfa_bot.knowledge.export_sources --output private_data/materials.jsonl

Затем очистите SOURCE_DATABASE_URL и задайте контейнерный путь в .env.

    SOURCE_DATABASE_URL=
    KB_MATERIALS_PATH=/app/private_data/materials.jsonl

    docker compose up --build

Bootstrap читает JSONL из read-only монтирования. Для обновления запущенного стека подготовьте новый файл и выполните docker compose run --rm bootstrap. Одинаковый корпус и модель повторно не кодируются. Предыдущий снимок остаётся доступен до commit нового.

После смены модели запустите bootstrap с ранее заданным исходным корпусом. Если демо уже было собрано и хотите пересобрать именно его, выполните:

    docker compose run --rm --entrypoint python bootstrap -m alfa_bot.knowledge.ingest --backend pgvector --materials /app/demo/materials.jsonl --force

Эта команда заменяет текущий индекс данными демо и сохраняет историю и напоминания. Для обновления весов под тем же именем при прямом импорте добавьте --force и укажите исходный корпус.

Если исходная БД доступна из контейнера, можно указать SOURCE_DATABASE_URL и оставить KB_MATERIALS_PATH пустым. Используйте роль с правом чтения нужных таблиц. В Docker Desktop адрес хоста обычно host.docker.internal.

## Формат JSONL

Одна строка на материал. Обязательные поля source_id, source_type, title, access. Для допуска нужны is_public: true и is_active: true. Свойство is_closed: true исключает запись. Для кликабельной карточки нужен HTTP(S) URL. Поля description, body и price опциональны. В body допустим HTML.

    {"source_id":"demo-1","source_type":"article","title":"Демо-статья","access":"free","is_public":true,"is_active":true,"body":"<p>Учебный текст.</p>","url":"https://example.org/"}

source_type принимает article, document, glossary, course. access принимает free, paid, unknown. Курсы и материалы с платным или неизвестным доступом участвуют в поиске по карточкам.

## Запуск с хоста и миграции

Задайте DATABASE_URL отдельной БД приложения с pgvector. Запустите локальный Ollama с OLLAMA_NO_CLOUD=1 и загрузите модели. Из корня проекта выполните:

    uv sync --locked --extra dev
    uv run alembic upgrade head
    uv run python -m alfa_bot.knowledge.ingest --backend pgvector --materials private_data/materials.jsonl
    uv run python -m alfa_bot.knowledge.search "Как проверить название бренда?"
    uv run python -m alfa_bot.assistant.chat --user-id 999000001

Для прямого импорта из PostgreSQL задайте SOURCE_DATABASE_URL и вызовите knowledge.ingest --backend pgvector без --materials. Для Telegram запустите uv run python -m alfa_bot.main с настроенным BOT_TOKEN. В консоли можно пройти диалог и увидеть черновик напоминания. Подтверждение и доставка идут в Telegram.

Alembic подхватывает таблицы версии с create_all и сохраняет существующие данные. Для старого каркаса с ревизией 0001_initial используйте отдельную целевую БД и перенесите нужные записи. При откате индекса его надо пересобрать из корпуса.

Для имеющегося article_chunks.jsonl доступен файловый поиск. Соберите индекс через uv run python -m alfa_bot.knowledge.ingest --chunks private_data/article_chunks.jsonl --backend bm25 и задайте KNOWLEDGE_BACKEND=legacy при запуске с хоста. Файловый --backend hybrid требует uv sync --locked --extra semantic.
