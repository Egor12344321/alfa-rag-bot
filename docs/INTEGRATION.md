# Подключение к текущему боту

## Какие файлы добавить

Добавьте содержимое `src/alfa_bot/knowledge/` и `src/alfa_bot/assistant/`.
Если там остались заглушки старого starter, замените их файлами из архива.
Этот вариант работает через ваш текущий `AssistantPort.handle`, а не через
старый контракт `handle_turn(UserTurn)`.

Добавьте `infra/llm.py` и `infra/embeddings.py`. Обновите зависимости по
`pyproject.toml`. Код использует Python 3.12 и выше.

## Какие существующие файлы обновлены

| Файл | Изменение |
| --- | --- |
| `src/alfa_bot/main.py` | `AssistantService` вместо `FakeAssistant`, загрузка индекса, создание таблицы памяти, закрытие LLM-клиента |
| `src/alfa_bot/infra/config.py` | Ключ и модель DeepSeek, путь индекса, исправлен корень `.env` |
| `src/alfa_bot/infra/db.py` | Совместимое с asyncpg создание engine и sessionmaker |
| `src/alfa_bot/infra/models.py` | Общая ORM-база текущего reminders и новой таблицы памяти |
| `src/alfa_bot/adapters/telegram.py` | Настоящая справка, `/new`, отмена незавершённого уточнения через `/cancel` |

Ваши текущие `contracts.py` и файлы `reminders/` включены как основа совместимости
без изменений логики. `FakeAssistant` больше не импортируется при запуске.
Основной обработчик сообщений продолжает работать с прежним `Reply`.

Если у вас уже есть дополнительные ORM-модели в `infra/models.py`, сохраните их
при объединении: новая модель `AssistantDialog` должна наследовать тот же `Base`,
что и `Reminder`. Для создания своей таблицы assistant импортирует только её модель.

Таблица памяти называется `assistant_dialogs` и не требует `users`,
`conversations` или таблиц старого каркаса. Схему reminders она не меняет.

## Подключение в main.py

Ключевые строки (полный файл находится в архиве):

```python
index = KnowledgeRepository.load(config.knowledge_index)
knowledge = KnowledgeService(index)
repository = AssistantRepository(database.sessions)
await repository.init_db()
assistant = AssistantService(repository, knowledge, llm)

dispatcher = Dispatcher(
    storage=MemoryStorage(),
    events_isolation=SimpleEventIsolation(),
    reminders=reminders,
    assistant=assistant,
)
```

## Ручная приёмка после настройки ключей

| Проверка | Ожидаемое поведение |
| --- | --- |
| «Как привлечь клиентов?» → «Кофейня, уже открылись» | Один уточняющий вопрос, затем ответ про исходный запрос с источниками |
| Перезапуск между этими сообщениями | Уточнение и профиль сохранены |
| «Что такое NPS?» | Поиск и ответ по доступным материалам, без обязательного сбора профиля |
| «Подбери бесплатные курсы по продажам» | Только бесплатные курсы из каталога; без каталога — честное сообщение об отсутствии |
| «Напомни прочитать статью» → дата, время и пояс | Уточнение недостающих полей, затем карточка подтверждения |
| Не нажимать «Подтвердить» | Нового напоминания в БД нет |
| Нажать «Подтвердить» | Создание существующим ReminderService |
| «Напиши стих про космос» | Подсказка о тематике помощника |
| `/cancel` во время уточнения | Текущий запрос отменён, профиль сохранён |
| `/new` | История, профиль и незавершённый запрос очищены |

Предлагаемое название коммита:

```text
feat: implement knowledge retrieval and stateful assistant with DeepSeek
```
