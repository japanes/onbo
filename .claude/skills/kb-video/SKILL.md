---
name: kb-video
description: >-
  Собрать короткое видео-объяснение для базы знаний onbo: экранная запись флоу в
  UI продукта + клонированная озвучка (ElevenLabs) → mp4, который прикрепляется к
  Q&A-паре или к стартовому ролику роли. Используй, когда просят «сними видео к
  вопросу», «сделай видео-инструкцию», «озвучь запись экрана», «welcome-ролик для
  отдела».
---

# kb-video — видео для базы знаний

Делает одно короткое (10–40 сек) видео: молчаливая запись экрана одного флоу в UI
продукта + озвучка голосом, склонированным с образца. Результат — `final.mp4`,
который прикрепляется либо к Q&A-паре (`video_url`), либо к welcome-ролику роли.

Два сценария в одном скилле:

- **A. Видео к Q&A-паре** — на вход пара «вопрос → ответ», выход `media/kb/qa-<id>.mp4`,
  прикрепление через `PATCH /admin/api/qa/<id>`.
- **B. Стартовый ролик роли** — на вход роль/отдел, диктор читает welcome-дайджест,
  выход `media/welcome/<department>.mp4`, прикрепление правкой `welcome:`-конфига.

Все команды выполняются из корня проекта. Скрипты скилла — в
`.claude/skills/kb-video/scripts/`. Рабочие файлы — только в `tmp/kb-video/…`
(глобальный `/tmp` использовать нельзя). Готовые видео — в `media/`.

---

## Предусловия (настроить один раз)

1. **ffmpeg + ffprobe** на PATH. Arch: `sudo pacman -S ffmpeg` · Debian/Ubuntu: `sudo apt install ffmpeg`.
2. **node + Playwright Chromium**: `npm i -D playwright && npx playwright install chromium`
   (запись экрана крутится на хосте, не в контейнере — нужен браузер).
3. **venv скилла** (только для TTS-запроса к ElevenLabs — лёгкое действие с ключом,
   не запуск проекта):
   ```bash
   python -m venv .claude/skills/kb-video/.venv
   .claude/skills/kb-video/.venv/bin/pip install -r .claude/skills/kb-video/requirements.txt
   ```
   Дальше зови интерпретатор как `.claude/skills/kb-video/.venv/bin/python`.
4. **Образец голоса** `refs/voice-ref.wav` — 30–60 сек, моно 48 kHz. См. `refs/README.md`.
5. **`ELEVENLABS_API_KEY`** — в окружении или в `~/.claude/.env`. Без ключа пайплайн
   не падает: сохраняет тексты озвучки в `work/narration.txt` (см. ниже).
6. **Запущенный веб-канал onbo** и **URL UI продукта** (тот интерфейс, который
   снимаем). Веб-канал onbo поднимается через `docker compose --profile app up app`
   (по умолчанию `http://localhost:18000`).

BGM (фоновая музыка) — опционально, по умолчанию видео без музыки. См. `bgm/README.md`.

---

## Сценарий A. Видео к Q&A-паре

### 1. Предусловия
Проверь список из блока выше. Определи `<onbo-web>` (адрес admin API, обычно
`http://localhost:18000`) и `<product-url>` (адрес UI, который снимаем).

### 2. Найти пару
```bash
curl -s <onbo-web>/admin/api/qa | python -m json.tool
```
Если задан `ONBO_ADMIN_TOKEN` — добавь `-H "X-Admin-Token: $ONBO_ADMIN_TOKEN"`.
Найди пару по `id` или тексту вопроса, запомни `<id>` и текст `answer` — из него
будет диктор.

### 3. Сценарий записи (2–5 сцен ровно одного флоу)
Напиши `tmp/kb-video/qa-<id>/scenes.json` — 2–5 сцен, показывающих **ровно тот
флоу**, про который вопрос. Селекторы бери **реальные** из разметки/шаблонов
продукта (открой страницу, посмотри DOM), не выдумывай. Схему и пример смотри в
`examples/scenes.json`. Держи сцены короткими: суммарно 10–40 сек.

### 4. Запись экрана
```bash
node .claude/skills/kb-video/scripts/record-walkthrough.mjs \
  --base-url=<product-url> \
  --out=tmp/kb-video/qa-<id>/rec \
  --config=tmp/kb-video/qa-<id>/scenes.json \
  --no-overlay
```
Получишь `rec/master.mp4`. **Открой и посмотри его глазами до озвучки** — курсор
в нужных местах, ничего не обрезано, флоу читается. Озвучка ElevenLabs стоит
символов/денег, переделывать после TTS дорого. Не устраивает — правь `scenes.json`
и перезаписывай.

### 5. init + transcript.md
```bash
.claude/skills/kb-video/.venv/bin/python .claude/skills/kb-video/scripts/kb_pipeline.py \
  init tmp/kb-video/qa-<id>/proj tmp/kb-video/qa-<id>/rec/master.mp4
```
Скопирует запись в `proj/source.mp4`, создаст `proj/work/` и напечатает JSON с
`video_duration_s` и шаблоном транскрипта.

Дальше **сам напиши** `tmp/kb-video/qa-<id>/proj/transcript.md`. Транскрипции нет —
текст диктора ты составляешь из `answer`, переписав его под устную речь (живо,
короткими фразами). Формат — по одному блоку на сцену:
```
## [001] 00:00:00,000 --> 00:00:06,000
original: <что показывает эта сцена, для тебя>
edited: <точный текст, который произнесёт диктор в этой сцене>
```
Тайминги `-->` бери из длительностей сцен (`ffprobe` по файлам в `rec/`, или из
`duration` в scenes.json). Текст диктора **может быть длиннее** слота сцены — на
сборке кадр «замораживается» и тянется под озвучку, это норма.

### 6. dryrun → synth-tts → build
```bash
PY=.claude/skills/kb-video/.venv/bin/python
SC=.claude/skills/kb-video/scripts/kb_pipeline.py
OUT=tmp/kb-video/qa-<id>/proj

$PY $SC dryrun $OUT                       # предупредит, если диктор сильно длиннее слота
$PY $SC synth-tts $OUT --mode posegment   # озвучка: 1 запрос на сцену
$PY $SC build $OUT none                   # сборка без музыки → $OUT/final.mp4
```
Для музыки: `list-bgm`, затем `build $OUT track.mp3 --duck 4` (`--duck N` — насколько
тише музыка относительно голоса в LU, «единицах громкости»; больше N = тише).

### 7. Прикрепление
```bash
mkdir -p media/kb
cp tmp/kb-video/qa-<id>/proj/final.mp4 media/kb/qa-<id>.mp4
curl -s -X PATCH <onbo-web>/admin/api/qa/<id> \
  -H "content-type: application/json" \
  -d '{"video_url":"/media/kb/qa-<id>.mp4"}'
```
(`-H "X-Admin-Token: $ONBO_ADMIN_TOKEN"`, если токен задан.)

### 8. Проверка
```bash
curl -sI <onbo-web>/media/kb/qa-<id>.mp4 | head -1      # ожидаем HTTP 200
```
Задай исходный вопрос в `/chat` (веб-канал) — в ответе должна появиться строка
`Видео-инструкция: /media/kb/qa-<id>.mp4`. Покажи `final.mp4` пользователю. Убери
рабочий мусор:
```bash
.claude/skills/kb-video/.venv/bin/python .claude/skills/kb-video/scripts/kb_pipeline.py cleanup tmp/kb-video/qa-<id>/proj
```

---

## Сценарий B. Стартовый ролик роли (welcome)

То же самое, с тремя отличиями:

- **Вход** — не Q&A-пара, а роль/отдел (например `accounting`). Пары в admin API
  искать не нужно.
- **Текст диктора** — welcome-дайджест этой роли: то, что новичок этого отдела
  видит в приветствии (порядок действий, ссылки, к кому обращаться). Составь его
  так же, под устную речь, и разбей по сценам записи.
- **Выход и прикрепление** — файл кладётся в `media/welcome/<department>.mp4`, а
  прикрепляется **правкой конфига**, не через PATCH:
  ```bash
  mkdir -p media/welcome
  cp tmp/kb-video/welcome-<dept>/proj/final.mp4 media/welcome/<dept>.mp4
  ```
  Затем в `config/settings.yaml` в блоке `welcome.video` добавь маппинг
  `отдел|роль → url`:
  ```yaml
  welcome:
    video:
      <dept>: /media/welcome/<dept>.mp4
  ```
  Проверка: `POST /welcome {"user_id": "<кто-то из этого отдела>"}` — в дайджесте
  появится строка `Видео-знакомство: /media/welcome/<dept>.mp4`.

Рабочая папка — `tmp/kb-video/welcome-<dept>/`, шаги записи/init/transcript/build
идентичны сценарию A.

---

## Если нет ключа ElevenLabs

`synth-tts` без `ELEVENLABS_API_KEY` **не падает**: он пишет тексты всех сцен в
`proj/work/narration.txt` (блоки `[NNN] текст`) и печатает `status: "no_api_key"`.
Отдай этот файл пользователю (или на другую озвучку/язык) — когда появится ключ,
повтори `synth-tts` и `build`. Так же добываются мультиязычные версии: переведи
`edited:`-тексты в transcript.md и пересобери.

---

## Что попадает в репозиторий

Коммитятся только `scripts/`, `requirements.txt`, `SKILL.md`, `examples/`,
`refs/README.md`, `bgm/README.md`. Личное — нет: `refs/*.wav`, `bgm/*.mp3`,
`.venv/`, `tmp/`, `media/` (кроме `.gitkeep`) в `.gitignore`. Готовые ролики в
`media/` — это ассеты конкретной инсталляции, не исходники скилла.
