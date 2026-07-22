# Nuxt 3: чат-виджет за 5 файлов

Готовая интеграция в режиме A (запросы идут через ваш бэкенд — см.
[HOWTO-3](../../HOWTO-3-embed-chat.ru.md)). Браузер обращается только к вашему
домену, `user_id` подставляется на сервере из сессии.

## Что копировать

Из этой папки в корень вашего Nuxt-проекта, сохраняя структуру:

```bash
ONBO=~/projects/onbo            # где лежит клон onbo
APP=~/projects/my-nuxt-app      # ваш проект

cp -r $ONBO/docs/examples/nuxt/server  $APP/
cp -r $ONBO/docs/examples/nuxt/plugins $APP/
cp $ONBO/docs/examples/onbo-widget.js  $APP/public/
```

Получится:

```
server/utils/onbo.js                    ← адрес onbo + кто спрашивает
server/api/assistant/chat.post.js       ← вопрос
server/api/assistant/confirm.post.js    ← кнопки Ок/Отмена
server/api/assistant/welcome.post.js    ← приветствие
server/api/assistant/voice.post.js      ← голос (необязательно)
plugins/onbo.client.js                  ← показать окно чата
public/onbo-widget.js                   ← сам виджет
```

## Что поправить руками

**1. `server/utils/onbo.js`, функция `assistantUser(event)`** — единственное
место, где нужен ваш код. Верните id залогиненного человека или бросьте 401:

```js
export async function assistantUser(event) {
  const user = await requireAuth(event)        // ваша авторизация
  return String(user.id)
}
```

Этот id должен совпадать с тем, что заведён через `onbo users add` — по нему
onbo определяет отдел и роли, а значит и что человеку показывать.

**2. `plugins/onbo.client.js`, строка с `useAuthStore()`** — подставьте свой
признак «пользователь вошёл». Виджет не должен появляться на странице логина:
ручки всё равно ответят 401.

**3. Адрес onbo.** По умолчанию `http://localhost:18000`. Переопределяется
переменной `ONBO_URL` или ключом `onboUrl` в `runtimeConfig`:

```js
// nuxt.config.ts
runtimeConfig: {
  onboUrl: process.env.ONBO_URL || 'http://localhost:18000',
}
```

В Docker `localhost` — это сам контейнер Nuxt, а не хост. Варианты:

| где живут onbo и Nuxt | `ONBO_URL` |
|---|---|
| один `docker-compose.yml` | `http://app:18000` (имя сервиса) |
| разные compose-проекты | `http://host.docker.internal:18000` + `extra_hosts: ["host.docker.internal:host-gateway"]` у сервиса Nuxt |
| onbo на другой машине | `https://onbo.internal.example.com` |

## Проверить

```bash
# 1. onbo отвечает сам по себе
curl -s -X POST http://localhost:18000/chat -H 'Content-Type: application/json' \
  -d '{"user_id":"u_1042","text":"привет"}'

# 2. ваша ручка отвечает залогиненному (cookie из браузера) и 401 — всем остальным
curl -s -X POST http://localhost:3000/api/assistant/chat -H 'Content-Type: application/json' \
  -d '{"text":"привет"}'
```

Дальше — обновите страницу: в правом нижнем углу появится пузырь.

## Голос

Микрофон браузеры дают только в защищённом контексте — `localhost` считается,
удалённому хосту нужен HTTPS. Если голос не нужен, удалите `voice.post.js` и
строку `voiceEndpoint` в плагине: кнопки микрофона просто не будет.
