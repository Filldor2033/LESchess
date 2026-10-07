#!/usr/bin/env python3
"""E2E: контроль времени на ход — лимит, тик, просрочка, победа соперника, откат часов при undo."""
import asyncio, json, time, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=6, collect_errors=True):
    err = None
    for _ in range(80):
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
    async with websockets.connect(URL) as anna, websockets.connect(URL) as bob:
        # комната с лимитом 15 сек
        await anna.send(json.dumps({'type': 'create', 'name': 'Clock', 'moveTime': 15, 'playerName': 'Anna'}))
        w, _ = await recv_until(anna, 'welcome')
        assert w['room']['moveTime'] == 15, w['room']
        print('1. Комната с лимитом 15с:', w['room']['moveTime'])
        await bob.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': w['room']['id'], 'roomName': 'Clock'}))
        wb, _ = await recv_until(bob, 'welcome')
        await drain(anna)
        st = wb['state']
        assert st['moveTime'] == 15 and st['timeLeft'] is not None and st['timeLeft'] <= 15, st
        print('2. Bob вошёл, часы пошли: timeLeft =', st['timeLeft'], 'сек')

        # Anna ходит — часы перезапускаются
        t0 = time.time()
        await anna.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        mv, _ = await recv_until(anna, 'move')
        assert mv and mv['state']['timeLeft'] <= 15 and mv['state']['timeLeft'] >= 13, mv['state']['timeLeft']
        await drain(bob)
        print('3. После хода часы сброшены: timeLeft =', mv['state']['timeLeft'], 'сек')

        # Bob НЕ ходит — ждём просрочку (15 сек)
        print('4. Ждём просрочку (Bob молчит)...')
        sysm, _ = await recv_until(bob, 'system', timeout=25, collect_errors=False)
        assert sysm and sysm.get('text') == 'Время на ход истекло', sysm
        st = sysm['state']
        assert st['status'] == 'timeup', st
        assert st['result'] == 'w', st   # просрочил чёрный (Bob) — победа белых
        print(f'5. Просрочка через {time.time()-t0:.0f}с: статус timeup, победа белых')

        # после окончания партии ходы блокируются
        await bob.send(json.dumps({'type': 'move', 'from': [4, 1], 'to': [4, 3]}))
        _, err = await recv_until(bob, 'x', timeout=3)
        assert err == 'Партия уже окончена' or err == 'Не ваш ход', err
        print('6. Ходы в оконченной партии отклонены:', err)

        # новая партия — лимит сохраняется, часы заново
        await anna.send(json.dumps({'type': 'reset'}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm['state']['status'] == 'active', sysm
        assert sysm['state']['moveTime'] == 15 and sysm['state']['timeLeft'] is not None, sysm['state']
        print('7. Новая партия: лимит сохранён (15с), часы идут, timeLeft =', sysm['state']['timeLeft'])

    # отдельная комната: undo сбрасывает часы + комната без лимита
    async with websockets.connect(URL) as c, websockets.connect(URL) as d:
        await c.send(json.dumps({'type': 'create', 'name': 'Clock2', 'moveTime': 15, 'playerName': 'C'}))
        w, _ = await recv_until(c, 'welcome')
        await d.send(json.dumps({'type': 'join', 'name': 'D', 'room': w['room']['id'], 'roomName': 'Clock2'}))
        wd, _ = await recv_until(d, 'welcome')
        await drain(c)
        # ходы
        await c.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        await recv_until(c, 'move')
        await d.send(json.dumps({'type': 'move', 'from': [4, 1], 'to': [4, 3]}))
        await recv_until(d, 'move')
        await drain(c)
        # подождём 5 сек и сделаем undo — часы должны дать полный лимит заново
        await asyncio.sleep(5)
        await d.send(json.dumps({'type': 'undo'}))
        req, _ = await recv_until(c, 'request')
        assert req and req['kind'] == 'undo'
        await c.send(json.dumps({'type': 'answer', 'kind': 'undo', 'ok': True}))
        sysm, _ = await recv_until(d, 'system', collect_errors=False)
        assert sysm and sysm['state']['status'] == 'active', sysm
        tl = sysm['state']['timeLeft']
        assert tl >= 13, tl
        print('8. После undo часы перезапущены: timeLeft =', tl, 'сек')

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
