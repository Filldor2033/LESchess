#!/usr/bin/env python3
"""E2E: кастомное время, смена лимита в комнате (settime), смена сторон после reset, чистка мёртвых ws."""
import asyncio, json, time, websockets, socket, struct

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=5, collect_errors=True):
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
        # 1. кастомный лимит 45 сек
        await anna.send(json.dumps({'type': 'create', 'name': 'Custom45', 'moveTime': 45, 'playerName': 'Anna'}))
        w, _ = await recv_until(anna, 'welcome')
        assert w['room']['moveTime'] == 45, w['room']
        print('1. Кастомный лимит 45с принят')
        await bob.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': w['room']['id'], 'roomName': 'Custom45'}))
        wb, _ = await recv_until(bob, 'welcome')
        await drain(anna)
        print('2. timeLeft:', wb['state']['timeLeft'], 'сек')

        # 2. смена лимита в комнате: 45 → 10
        await anna.send(json.dumps({'type': 'settime', 'moveTime': 10}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and '10' in sysm['text'], sysm
        assert sysm['state']['moveTime'] == 10, sysm['state']
        assert sysm['state']['timeLeft'] <= 10, sysm['state']
        print('3. settime 10с: часы перезапущены, timeLeft =', sysm['state']['timeLeft'])

        # 3. снятие лимита
        await anna.send(json.dumps({'type': 'settime', 'moveTime': 0}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and sysm['state']['moveTime'] is None and sysm['state']['timeLeft'] is None, sysm
        print('4. Лимит снят: moveTime = None')

        # 4. невалидные значения отклоняются
        await drain(anna)
        await anna.send(json.dumps({'type': 'settime', 'moveTime': 3}))
        _, err = await recv_until(anna, 'x')
        assert err == 'Лимит: 0 или 5–600 секунд', err
        await anna.send(json.dumps({'type': 'settime', 'moveTime': 10000}))
        _, err = await recv_until(anna, 'x')
        assert err == 'Лимит: 0 или 5–600 секунд', err
        print('5. Невалидные лимиты отклонены')

        # 5. ход + reset со сменой сторон
        await anna.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        await recv_until(anna, 'move')
        await drain(bob)
        await anna.send(json.dumps({'type': 'reset'}))
        sysm, _ = await recv_until(bob, 'system', collect_errors=False)
        assert sysm and 'поменялись' in sysm['text'], sysm
        # цвета: Anna была w, Bob b → после swap: Anna b, Bob w
        pl, _ = await recv_until(bob, 'players', collect_errors=False)
        # дождёмся нужное (может прийти раньше system)
        while pl and not (pl['players'].get('w') == 'Bob' and pl['players'].get('b') == 'Anna'):
            pl, _ = await recv_until(bob, 'players', collect_errors=False, timeout=2)
            if pl is None: break
        assert pl and pl['players']['w'] == 'Bob' and pl['players']['b'] == 'Anna', pl
        print('6. Reset: стороны поменялись — Anna теперь чёрные, Bob белые')
        # ход теперь белый = Bob
        await bob.send(json.dumps({'type': 'move', 'from': [4, 6], 'to': [4, 4]}))
        mv, err = await recv_until(bob, 'move')
        assert mv is not None, err
        print('7. Bob (белые) ходит первым после смены — ок')

        # 6. явный leave
        await drain(anna)
        await bob.send(json.dumps({'type': 'leave'}))
        sysm, _ = await recv_until(anna, 'system', collect_errors=False)
        assert sysm and 'покинул' in sysm['text'], sysm
        print('8. Явный leave: Bob удалён из комнаты мгновенно')

    # 7. чистка мёртвых ws тикером
    a = await websockets.connect(URL)
    await a.send(json.dumps({'type': 'create', 'name': 'DeadWs', 'playerName': 'A'}))
    rid = None
    while rid is None:
        m = json.loads(await asyncio.wait_for(a.recv(), 2))
        if m['type'] == 'welcome': rid = m['room']['id']
    b = await websockets.connect(URL)
    await b.send(json.dumps({'type': 'join', 'name': 'B', 'room': rid, 'roomName': 'DeadWs'}))
    await drain(b)
    # A умирает «молча»: закрываем транспорт так, чтобы close-фрейм НЕ дошёл
    sock = a.transport.get_extra_info('socket')
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
    a.transport.abort()
    # но вебсокеты-объект ещё «открыт» с точки зрения сервера? Проверим уборку тикером
    t0 = time.time()
    ok = False
    while time.time() - t0 < 30:
        await asyncio.sleep(2)
        w = await websockets.connect(URL)
        await w.send(json.dumps({'type': 'list'}))
        rooms = json.loads(await asyncio.wait_for(w.recv(), 1))['rooms']
        await w.close()
        z = [r for r in rooms if r['name'] == 'DeadWs']
        if z and z[0]['players'] == 1:
            ok = True  # A вычищен тикером, B ещё в комнате
            print(f'9. Мёртвый ws вычищен тикером за {time.time()-t0:.0f}с (осталось игроков: 1)')
            break
        if not z:
            break
    assert ok, 'мёртвый ws не вычищен'
    await b.close()

    print()
    print('ALL E2E TESTS PASSED')

asyncio.run(main())
