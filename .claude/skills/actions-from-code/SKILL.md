---
name: actions-from-code
description: Прочитать код целевого продукта и сгенерировать черновик config/actions.yaml (реестр действий над профилем + пайплайны) для onbo. Скилл-версия `onbo scan`. Использовать, когда нужно завести действия из исходников — юзер даёт путь к коду, на выходе — actions.draft.yaml под ревью, который проверяется через load_action_specs().
---

# /actions-from-code — черновик реестра действий из кода

Ты читаешь исходники целевого продукта и пишешь **черновик** `config/actions.yaml`
для onbo: действия над профилем пользователя (+ пайплайны). Результат — всегда
черновик под ревью; ты **не перезаписываешь** живой конфиг без подтверждения.
Скриптов нет — всю работу делаешь ты. Это рекомендованная замена CLI-команде
`onbo scan` (см. «Сосуществование с onbo scan»).

## Шаг 1. Найти мутирующие эндпоинты профиля/настроек

Ищи операции, которые **меняют** данные пользователя: `POST/PUT/PATCH/DELETE`,
формы настроек, страницы профиля. Игнорируй **админские и системные** ручки
(миграции, вебхуки, служебные крон-эндпоинты) — онбординг-ассистент их не трогает.

Заодно отмечай **кандидаты в пайплайны** — цепочки, которые в UI пользователь
делает серией шагов (оформить заказ → накладная → отправка).

## Шаг 2. Определить режим (mode)

Как в `onbo/generator/scan.py` и PLAN.md:

- пароль / платежи / персональные данные / удаление аккаунта → **`link` + `sensitive: true`**
  (в чате не выполняется, отдаётся ссылка);
- email / телефон / имя → **`confirm`** (переспросить Ok/Отмена);
- язык / тема / уведомления и прочее низкорисковое → **`chat`** (выполнить сразу).

Роли/гварды в коде (`@requires_role(...)`, `if user.is_staff`) → поля
`department`/`roles` у действия (пусто = доступно всем).

## Шаг 3. Точная схема

Черновик должен грузиться в эти Pydantic-модели (`onbo/handlers/actions/registry.py`).
Соблюдай их дословно:

```python
class ParamSpec:
    type: str = "string"            # string | email | enum | ...
    required: bool = False
    values: list[str] | None = None  # допустимые значения для enum

class ApiSpec:                       # как звать бэкенд продукта (url/path/body/
    method: str = "POST"             # query шаблонятся {user_id} и {param})
    url: str = ""                    # ПРЕДПОЧТИТЕЛЬНО: абсолютный адрес целиком
    path: str = ""                   # относительный, клеится с product.base_url
    body: dict = {}
    query: dict = {}
    success_message: str | None = None

class ActionSpec:
    name: str                        # ключ в actions:, name подставляется автоматически
    description: str = ""            # одна короткая строка, её ЧИТАЕТ человек
    keywords: list[str] = []         # в индекс, не в промпт: синонимы и сленг
    examples: list[str] = []         # в индекс, не в промпт: целые фразы
    mode: "chat" | "confirm" | "link" = "chat"
    sensitive: bool = False          # true всегда форсит mode: link
    link_url: str | None = None      # для link-действий
    confirm_prompt: str | None = None # для confirm-действий, шаблонится {param}
    params: dict[str, ParamSpec] = {}
    handler: str | None = None       # НЕ генерировать (см. ниже)
    api: ApiSpec | None = None       # для chat/confirm; для link НЕ писать
    department: str | None = None    # аудитория; пусто = всем
    roles: list[str] = []

class PipelineStep:
    action: str                      # имя существующего действия
    params: dict[str, str] = {}      # значения шаблонятся {param}

class PipelineSpec:
    name: str
    description: str = ""
    keywords: list[str] = []               # как у ActionSpec
    examples: list[str] = []
    mode: "chat" | "confirm" = "confirm"   # link запрещён
    confirm_prompt: str | None = None
    params: dict[str, ParamSpec] = {}
    steps: list[PipelineStep] = []
    on_error: "stop" | "continue" = "stop"
    department: str | None = None
    roles: list[str] = []
```

Правила генерации:

- **Пиши абсолютный `url:`, а не `path:`.** onbo ставят против произвольного,
  заранее неизвестного API — файл действий должен сам, без внешних переменных
  окружения, говорить, куда летит запрос. Хост бери из кода продукта (настройки,
  `.env`, документация); если хост определить не удалось — оставь плейсхолдер
  `https://CHANGEME/...` и скажи об этом юзеру явно в отчёте.
- **Заполняй `keywords:` и `examples:` у каждого действия.** Каталог команд не
  вставляется в промпт целиком: он лежит в Qdrant и ищется вектором на каждое
  сообщение, в промпт попадают только ~12 ближайших команд. Значит команду надо
  **находить**, а `description` из трёх слов ищется плохо. Давай 3–8 keywords
  (синонимы, сленг, английский и локальный вариант — «снести», «убрать»,
  «delete project») и 2–3 целых примера фразы («удали проект «телефон»»). Эти
  списки в промпт не уходят и на ответе не стоят ничего.
- **`handler:` не генерировать.** Декларативного блока `api:` достаточно — onbo
  исполняет его через `GenericHTTPHandler` без Python. Кастомный `handler:` юзер
  добавит сам, если нужна особая валидация.
- **Для `link`-действий `api:` не писать** — только `link_url` и `sensitive: true`.
- **Пайплайны** — top-level ключ `pipelines:`; шаг ссылается на существующее
  действие по имени и **не может** указывать на `link`/sensitive-действие.
- Шаблоны: `{user_id}` — из профиля, `{param}` — из entities действия.

## Шаг 4. Выход

Запиши в `tmp/actions-from-code/<проект>/actions.draft.yaml` (временные файлы —
только в `tmp/` проекта, никогда в `/tmp`). **Не перезаписывай** `config/actions.yaml`:
покажи **diff** против текущего конфига и обнови его только по подтверждению юзера.

## Шаг 5. Верификация

Прогони черновик через реальный загрузчик — он проверит схему и валидность
пайплайнов (несуществующий/sensitive-шаг → ошибка):

```bash
ONBO_CONFIG_DIR=tmp/actions-from-code/<проект> \
  python -c "from onbo.handlers.actions.registry import load_action_specs, load_pipeline_specs; \
a=load_action_specs(); print(len(a), 'actions'); print(len(load_pipeline_specs(a)), 'pipelines')"
```

(Загрузчик читает `actions.yaml` из `ONBO_CONFIG_DIR` — назови файл `actions.yaml`
в каталоге черновика или скопируй туда под этим именем перед проверкой.)

## Сосуществование с `onbo scan`

`onbo scan <project>` (CLI, `onbo/generator/scan.py`) остаётся для сред **без**
Claude Code — он просит LLM набросать реестр по вырезкам кода. Этот скилл —
**рекомендованный путь**: ты читаешь код целенаправленно, применяешь точную схему
и проверяешь результат загрузчиком.

## Проверка

`/actions-from-code onbo/demo` → черновик воспроизводит `set_language`
(chat, enum `ru|en`) и `change_email` (confirm) с путями `/api/users/{user_id}/...`;
проходит `load_action_specs()`.
