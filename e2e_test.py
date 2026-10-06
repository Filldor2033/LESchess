"""E2E: два игрока, комната, ходы, чат, мат."""
import asyncio, json, sys
sys.path.insert(0, '/root/chess')
import websockets

async def rx(ws, want_type, timeout=5):
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout)
        m = json.loads(raw)
        if m.get('type') == want_type:
            return m

async def main():
    rid = 'e2e-' + str(id(object()))[-5:]
    a = await websockets.connect('ws://127.0.0.1:8091')
    b = await websockets.connect('ws://127.0.0.1:8091')
    await a.send(json.dumps({'type': 'join', 'name': 'Alice', 'room': rid, 'roomName': 'E2E'}))
    wa = await rx(a, 'welcome')
    assert wa['color'] == 'w', wa
    print('Alice joined as white')
    await b.send(json.dumps({'type': 'join', 'name': 'Bob', 'room': rid}))
    wb = await rx(b, 'welcome')
    assert wb['color'] == 'b', wb
    await rx(a, 'system')  # Bob joined
    print('Bob joined as black')

    # чат
    await b.send(json.dumps({'type': 'chat', 'text': 'привет, удачи!'}))
    ca = await rx(a, 'chat')
    assert ca['name'] == 'Bob' and ca['text'] == 'привет, удачи!', ca
    cb = await rx(b, 'chat')
    assert cb['text'] == 'привет, удачи!'
    print('chat OK')

    # подсказки ходов
    await a.send(json.dumps({'type': 'moves', 'x': 4, 'y': 6}))
    mv = await rx(a, 'moves')
    assert (4, 4) in [tuple(m) for m in mv['moves']], mv
    print('move hints OK:', mv['moves'])

    # мат (scholar's mate): e4 e5 Bc4 a6 Qh5 Nf6 Qxf7#
    seq = [
        (a, (4,6), (4,4)),  # e4
        (b, (4,1), (4,3)),  # e5
        (a, (5,7), (2,4)),  # Bc4
        (b, (0,1), (0,2)),  # a6
        (a, (3,7), (7,3)),  # Qh5
        (b, (6,0), (5,2)),  # Nf6??
    ]
    for sock, f, t in seq:
        await sock.send(json.dumps({'type': 'move', 'from': list(f), 'to': list(t)}))
        ma = await rx(a, 'move')
        mb = await rx(b, 'move')
        assert ma['state'] == mb['state'], 'states diverged'
    print('4 moves synced both sides')
    assert ma['state']['turn'] == 'w'

    # мат: Qxf7#  — ферзь на h5 (7,3) берёт f7 (5,1)
    await a.send(json.dumps({'type': 'move', 'from': [7,3], 'to': [5,1]}))
    ma = await rx(a, 'move')
    mb = await rx(b, 'move')
    st = ma['state']
    assert st['status'] == 'checkmate', st
    assert st['result'] == 'w', st
    print('CHECKMATE detected, white wins')

    # сброс
    await a.send(json.dumps({'type': 'reset'}))
    ra = await rx(a, 'system')
    assert ra['state']['status'] == 'active' and ra['state']['turn'] == 'w'
    rb = await rx(b, 'system')
    assert rb['state']['board'][6][4] == 'wP'
    print('reset OK')

    # третий игрок — отказ (комната заполнена)
    c = await websockets.connect('ws://127.0.0.1:8091')
    await c.send(json.dumps({'type': 'join', 'name': 'Mallory', 'room': rid}))
    err = await rx(c, 'error')
    assert 'заполнена' in err['text'], err
    print('third player rejected:', err['text'])

    await a.close(); await b.close(); await c.close()
    print('\nALL E2E TESTS PASSED')

asyncio.run(main())
