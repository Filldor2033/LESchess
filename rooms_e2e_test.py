#!/usr/bin/env python3
"""E2E: комнаты с паролями — создание, вход, запрет неверного пароля, две параллельные партии."""
import asyncio, json, websockets

URL = 'ws://127.0.0.1:8091'

async def recv_until(ws, want, timeout=3):
    """Принимать сообщения, пока не придёт тип want (или 'error')."""
    err = None
    for _ in range(50):
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        except asyncio.TimeoutError:
            break
        if m.get('type') == want:
            return m, err
        if m.get('type') == 'error':
            err = m.get('text')
    return None, err

async def drain(ws, quiet=0.4):
    """Вычитать всё, что накопилось (с коротким ожиданием хвоста)."""
    while True:
        try:
            await asyncio.wait_for(ws.recv(), quiet)
        except asyncio.TimeoutError:
            return


async def main():
    async with websockets.connect(URL) as anna, \
               websockets.connect(URL) as bob, \
               websockets.connect(URL) as carol, \
               websockets.connect(URL) as dave:
        # 1. Anna создаёт комнату с паролем
        await anna.send(json.dumps({'type': 'create', 'name': 'Турнир', 'password': '1234', 'playerName': 'Anna'}))
        w, _ = await recv_until(anna, 'welcome')
        assert w and w['room']['name'] == 'Турнир' and w['room']['locked'], 'create failed'
        room1 = w['room']['id']
        print('1. Комната создана:', w['room']['name'], 'id =', room1, 'locked =', w['room']['locked'])

        # 2. Bob подписывается на список комнат и видит её
        await bob.send(json.dumps({'type': 'list'}))
        rooms, _ = await recv_until(bob, 'rooms')
        names = [r['name'] for r in rooms['rooms']]
        assert 'Турнир' in names, names
        print('2. Лобби видит комнату:', names)

        # 3. Bob пытается войти с неверным паролем
        await bob.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': room1, 'roomName': 'Турнир', 'password': 'wrong'}))
        _, err = await recv_until(bob, 'x-error')
        assert err == 'Неверный пароль комнаты', err
        print('3. Неверный пароль отклонён:', err)

        # 4. Bob входит с правильным паролем
        await bob.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': room1, 'roomName': 'Турнир', 'password': '1234'}))
        w2, err = await recv_until(bob, 'welcome')
        assert w2 and w2['name'] == 'Bob' and w2['color'] == 'b', (w2, err)
        print('4. Bob вошёл с паролем, цвет =', w2['color'])

        # 5. Carol создаёт ВТОРУЮ комнату без пароля
        await carol.send(json.dumps({'type': 'create', 'name': 'Свободная', 'playerName': 'Carol'}))
        w3, _ = await recv_until(carol, 'welcome')
        room2 = w3['room']['id']
        await dave.send(json.dumps({'type': 'join', 'name': 'Dave', 'room': room2, 'roomName': 'Свободная'}))
        w4, _ = await recv_until(dave, 'welcome')
        assert w3['room']['name'] == 'Свободная' and w4['color'] == 'b'
        print('5. Вторая комната без пароля: Carol(w) vs Dave(b)')

        # 6. Две независимые партии параллельно
        async def play(ws, frm, to, tag):
            await ws.send(json.dumps({'type': 'move', 'from': frm, 'to': to}))
            m, err = await recv_until(ws, 'move', timeout=2)
            assert m is not None, (tag, err)
            return m
        l1 = await play(anna, [4,6], [4,4], 'r1-anna-e4')
        await play(bob, [4,1], [4,3], 'r1-bob-e5')
        l2 = await play(carol, [3,6], [3,4], 'r2-carol-d4')
        await play(dave, [3,1], [3,3], 'r2-dave-d5')
        # доска комнаты 1: пешка e4; комнаты 2: d4
        # board[y][x]: e4 = (x=4,y=4) -> b[4][4]; d4 = (x=3,y=4) -> b[4][3]
        b1 = l1['state']['board']
        assert b1[4][4] == 'wP' and b1[4][3] is None, (b1[4][4], b1[4][3])
        b2 = l2['state']['board']
        assert b2[4][3] == 'wP' and b2[4][4] is None, (b2[4][3], b2[4][4])
        print('6. Партии в комнатах независимы: r1 e-пешка, r2 d-пешка')

        # 7. Изоляция: Carol ходит в r2 — Bob (r1) НЕ должен получать move от r2.
        #    И второй ход Carol подряд (очередь чёрных) → «Не ваш ход»
        await drain(bob)
        await play(carol, [4,6], [4,4], 'r2-carol-e4')
        await asyncio.sleep(0.3)
        leaked = False
        for _ in range(3):
            try:
                m = json.loads(await asyncio.wait_for(bob.recv(), 0.3))
                if m.get('type') == 'move': leaked = True
            except asyncio.TimeoutError:
                break
        assert not leaked, 'ход r2 протёк в r1!'
        await carol.send(json.dumps({'type': 'move', 'from': [3,6], 'to': [3,4]}))  # очередь чёрных
        _, err = await recv_until(carol, 'x')
        assert err == 'Не ваш ход', err
        print('7. Изоляция ходов: r2 не протекает в r1; двойной ход отклонён:', err)

        # 8. Пятый игрок не влезает в заполненную комнату 1
        async with websockets.connect(URL) as eve:
            await eve.send(json.dumps({'type': 'join', 'name': 'Eve', 'room': room1, 'roomName': 'Турнир', 'password': '1234'}))
            _, err = await recv_until(eve, 'x')
            assert err == 'Комната заполнена (макс. 2 игрока)', err
            print('8. Заполненная комната отклоняет третьего:', err)

        # 9. Bob выходит → комната 1 жива (Anna внутри), лобби обновилось
        await bob.close()
        await asyncio.sleep(0.5)
        rooms2, _ = await recv_until(carol, 'rooms')
        # carol тоже в list? нет — подпишем отдельным соединением
        async with websockets.connect(URL) as watch:
            await watch.send(json.dumps({'type': 'list'}))
            rooms3, _ = await recv_until(watch, 'rooms')
            info = {r['name']: (r['players'], r['status']) for r in rooms3['rooms']}
            assert info['Турнир'][0] == 1, info
            print('9. Bob вышел, лобби видит игроков 1/2:', info)

        print()
        print('ALL E2E TESTS PASSED')

asyncio.run(main())
