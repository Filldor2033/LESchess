#!/usr/bin/env python3
"""E2E: уровни сложности бота — приглашение с уровнем, ходы, смена, лобби-флаг, solo-reset."""
import asyncio, json, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=6, collect_errors=True):
    err = None
    for _ in range(100):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        except asyncio.TimeoutError:
            break
        if m.get('type') == want:
            return m, err
        if m.get('type') == 'error' and collect_errors:
            err = m.get('text')
    return None, err

async def drain(ws, quiet=0.3):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), quiet)
        except asyncio.TimeoutError:
            return

async def main():
    async with websockets.connect(URL) as a:
        await a.send(json.dumps({'type': 'create', 'name': 'Levels', 'playerName': 'A'}))
        w, _ = await recv_until(a, 'welcome')
        rid = w['room']['id']
        assert w['room']['botLevel'] == 'normal'
        print('1. Дефолтный уровень в room_public: normal')

        # позвать easy
        await a.send(json.dumps({'type': 'bot', 'level': 'easy'}))
        pl = None
        for _ in range(15):
            m, _ = await recv_until(a, 'players', timeout=3)
            if m and 'Компьютер' in str(m['players']):
                pl = m
                break
        assert pl and 'Лёгкий' in (pl['players'].get('b') or ''), pl
        print('2. Easy: игроки =', pl['players'])
        # ход → бот отвечает
        await a.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        mv = None
        for _ in range(15):
            m, _ = await recv_until(a, 'move', timeout=4, collect_errors=False)
            if m and m.get('bot'):
                mv = m
                break
        assert mv, 'easy не ответил'
        print('3. Easy сходил:', mv['from'], '→', mv['to'])

        # лобби с уровнем
        async with websockets.connect(URL) as watcher:
            await watcher.send(json.dumps({'type': 'list'}))
            rooms, _ = await recv_until(watcher, 'rooms')
            lr = [r for r in rooms['rooms'] if r['id'] == rid]
            assert lr and lr[0]['bot'] is True and lr[0]['botLevel'] == 'easy', lr
            print('4. Лобби: bot=True, botLevel=easy, players=', lr[0]['players'])
            # попытка входа отклонена
            await watcher.send(json.dumps({'type': 'join', 'name': 'W', 'room': rid, 'roomName': 'Levels'}))
            _, err = await recv_until(watcher, 'x', timeout=3)
            assert err == 'В комнате играет компьютер — комната закрыта', err

        # невалидный уровень → normal
        await a.send(json.dumps({'type': 'bot', 'kick': True}))
        await drain(a)
        await a.send(json.dumps({'type': 'bot', 'level': 'turbo'}))
        pl = None
        for _ in range(15):
            m, _ = await recv_until(a, 'players', timeout=3)
            if m and 'Компьютер' in str(m['players']):
                pl = m
                break
        assert pl and 'Средний' in (pl['players'].get('b') or ''), pl
        print('5. Невалидный уровень → normal:', pl['players'])

        # кик + solo reset (один человек) — работает
        await a.send(json.dumps({'type': 'bot', 'kick': True}))
        await drain(a)
        await a.send(json.dumps({'type': 'reset'}))
        sysm, _ = await recv_until(a, 'system', timeout=4)
        assert sysm and sysm['text'] == 'Новая партия', sysm
        assert sysm['state']['status'] == 'active'
        print('6. Solo-reset после кика бота: партия пересоздана (стороны не менялись)')

        # смена уровня: выгнать easy, позвать master — работает ход
        await a.send(json.dumps({'type': 'bot', 'level': 'master'}))
        pl = None
        for _ in range(15):
            m, _ = await recv_until(a, 'players', timeout=3)
            if m and 'Компьютер' in str(m['players']):
                pl = m
                break
        assert pl and 'Мастер' in (pl['players'].get('b') or ''), pl
        print('7. Master позван:', pl['players'])
        await a.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        mv = None
        for _ in range(15):
            m, _ = await recv_until(a, 'move', timeout=6, collect_errors=False)
            if m and m.get('bot'):
                mv = m
                break
        assert mv, 'master не ответил'
        print('8. Master сходил:', mv['from'], '→', mv['to'])

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
