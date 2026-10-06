<div align="center">

<img src="logo.png" width="325px">

# ♟️ LESchess

**Локальные веб-шахматы с живым чатом и отдельными комнатами**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![WebSocket](https://img.shields.io/badge/WebSocket-Real--time-010101?style=for-the-badge&logo=socket.io&logoColor=white)](https://developer.mozilla.org/en-US/docs/Web/API/WebSocket)
[![Zero Dependencies](https://img.shields.io/badge/Backend-Zero--dependency-22c55e?style=for-the-badge)](https://pypi.org/project/websockets/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue?style=for-the-badge&logo=gnu&logoColor=white)](https://www.gnu.org/licenses/gpl-3.0)
[![Tests](https://img.shields.io/badge/Tests-pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)](https://pytest.org/)

</div>

---

## 📋 Содержание

- [📖 Описание проекта](#-описание-проекта)
- [✨ Возможности](#-возможности)
- [📸 Скриншоты](#-скриншоты)
- [🚀 Быстрый старт](#-быстрый-старт)
- [🔌 Протокол WebSocket](#-протокол-websocket)
- [🧪 Тестирование](#-тестирование)
- [📁 Структура проекта](#-структура-проекта)
- [🚢 Развёртывание](#-развёртывание)
- [📄 Лицензия](#-лицензия)

---

## 📖 Описание проекта

**LESchess** — локальные веб-шахматы для игры вдвоём в браузере. Один Python-файл бэкенда без внешних сервисов и базы данных: комнаты, полная валидация правил, чат в реальном времени и интерфейс в фирменном стиле LESogram.

### 🎯 Цели проекта

- **Простота** — вся игра в одном файле `server.py` + один `index.html`
- **Живое время** — WebSocket синхронизирует доску и чат мгновенно
- **Комнаты** — пары играют параллельно, не мешая друг другу
- **Удобство** — интерфейс как в LESogram: тёмная тема, стеклянные панели, адаптивность

---

## ✨ Возможности

- ♟️ **Полные правила шахмат**: валидация ходов на сервере, шах, мат, пат, превращение пешек
- 🏠 **Отдельные комнаты** — до 2 игроков в каждой, «Общая комната» + создание своих
- 💬 **Чат игроков** — пузыри в стиле LESogram, системные сообщения о входе/выходе
- 🎨 **Фирменный дизайн** — тёмная тема `#0f172a` + зелёный акцент `#22c55e`, Trebuchet MS, радиусы 22/16/12, стеклянные панели с blur
- 🔄 **Синхронизация в реальном времени** — ход, чат, подключение соперника
- 🤝 **Реванш** — кнопка «Новая партия» после мата
- 📱 **Адаптивность** — доска масштабируется под экран, мобильная раскладка
- 🔃 **Переподключение** — клиент автоматически восстанавливает WS-соединение
- 🖼️ **Поворот доски** — чёрные играют со своей стороны
- 💡 **Подсказки ходов** — точки на свободные поля, обводка на взятия, подсветка шаха

---

## 📸 Скриншоты

<details>
<summary>Нажмите, чтобы развернуть</summary>

- Экран входа <br>
![Auth](screenshots/screenshot_redesign_auth.png)

- Партия, мат и оверлей победы <br>
![Checkmate](screenshots/screenshot_checkmate.png)

- Комната с доской и чатом <br>
![Room](screenshots/screenshot_redesign_room.png)

</details>

---

## 🚀 Быстрый старт

### 📋 Предварительные требования

- **Python** `3.10` или выше
- **pip** (менеджер пакетов Python)

### 🔽 Установка

```bash
git clone https://github.com/Filldor2033/LESchess.git
cd LESchess

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install websockets
```

### ▶️ Запуск

```bash
python server.py
```

Приложение поднимается на двух портах:

- **HTTP (интерфейс):** `http://localhost:8090/`
- **WebSocket (игра и чат):** `ws://localhost:8091/`

Откройте `http://localhost:8090/` в **двух** вкладках или на двух устройствах, введите разные имена — и играйте. Второй игрок получает чёрные фигуры.

> 💡 В сети LAN/интернете укажите IP хоста — фронтенд строит WS-адрес из `location.host` автоматически.

---

## 🔌 Протокол WebSocket

Все сообщения — JSON.

### Клиент → Сервер

| Тип | Поля | Описание |
|---|---|---|
| `join` | `name`, `room`, `roomName` | Войти в комнату (создаётся при отсутствии) |
| `move` | `from`, `to` | Ход `[x, y] → [x, y]` |
| `moves` | `x`, `y` | Запросить допустимые ходы фигуры |
| `chat` | `text` | Сообщение в чат (до 500 символов) |
| `reset` | — | Новая партия |

### Сервер → Клиент

| Тип | Поля | Описание |
|---|---|---|
| `welcome` | `name`, `color`, `room`, `state`, `players`, `chat` | Ответ на вход |
| `move` | `from`, `to`, `state` | Ход + новое состояние |
| `state` | `state` | Состояние партии |
| `moves` | `x`, `y`, `moves` | Допустимые ходы |
| `players` | `players` | Белый/чёрный игроки |
| `chat` | `name`, `text`, `ts` | Сообщение чата |
| `system` | `text`, `state?` | Системные события |
| `error` | `text` | Ошибка («Не ваш ход», «Комната заполнена»…) |

`state` — состояние партии: `board` (8×8), `turn`, `status` (`active`/`checkmate`/`stalemate`), `result`, `inCheck`, `material`.

---

## 🧪 Тестирование

E2E-тест гоняет полный сценарий через реальный WebSocket-сервер: вход двух игроков, чат, подсказки ходов, детский мат (`e4 e5 Bc4 a6 Qh5 Nf6 Qxf7#`), определение победы, сброс, отказ третьему игроку.

```bash
python server.py &     # сервер должен быть запущен
python e2e_test.py
```

---

## 📁 Структура проекта

```
LESchess/
├── server.py           # Весь бэкенд: HTTP-статика + WS + правила шахмат
├── index.html          # Весь фронтенд: интерфейс, доска, чат
├── e2e_test.py         # E2E-тесты (pytest-совместимый сценарий)
├── requirements.txt    # websockets + pytest
├── logo.png            # Логотип (король и ёлки)
├── favicon-*.png       # Фавиконы 512/192/48/32/16
├── apple-touch-icon.png
├── screenshots/        # Скриншоты для README
├── .github/workflows/  # CI: тесты на push
└── LICENSE             # GPLv3
```

---

## 🚢 Развёртывание

### systemd (прод)

```ini
[Unit]
Description=LESchess
After=network.target

[Service]
WorkingDirectory=/opt/leschess
ExecStart=/opt/leschess/.venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

Порты 8090 (HTTP) и 8091 (WS) пробрасываются/открываются отдельно. Для HTTPS поставьте Caddy/nginx перед 8090 и проксируйте `/` и WS-апгрейд на 8091.

---

## 📄 Лицензия

Проект распространяется по лицензии [GPLv3](LICENSE).
