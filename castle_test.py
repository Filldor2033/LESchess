import asyncio, json, websockets

async def t():
    async with websockets.connect('ws://127.0.0.1:8091') as w1:
        await w1.send(json.dumps({'type':'join','name':'W','room':'castle-test3'}))
        await w1.recv()
        async with websockets.connect('ws://127.0.0.1:8091') as w2:
            await w2.send(json.dumps({'type':'join','name':'B','room':'castle-test3'}))
            await w2.recv()
            b1 = []
            async def drain():
                try:
                    while True:
                        b1.append(json.loads(await w1.recv()))
                except Exception:
                    pass
            asyncio.get_event_loop().create_task(drain())
            await asyncio.sleep(0.3)
            # O-O белых: очистить f1, g1 (Nf3, Be2), между королём и ладьёй ничего нет
            plan = [
                (w1, [6,7],[5,5]),   # Nf3
                (w2, [6,0],[5,2]),   # Nf6
                (w1, [4,6],[4,5]),   # e3
                (w2, [4,1],[4,2]),   # e6
                (w1, [5,7],[2,4]),   # Bf1-e2
                (w2, [5,0],[4,1]),   # Be7
            ]
            for ws, f, t in plan:
                await ws.send(json.dumps({'type':'move','from':f,'to':t}))
                await asyncio.sleep(0.5)
            # O-O: король e1=(4,7) -> g1=(6,7)
            await w1.send(json.dumps({'type':'move','from':[4,7],'to':[6,7]}))
            await asyncio.sleep(1.0)
            state = None
            for m in b1:
                if m.get('type') == 'move' and m.get('state'):
                    state = m['state']
            if state:
                kpos = [(x,y) for y,row in enumerate(state['board']) for x,p in enumerate(row) if p=='wK']
                rpos = [(x,y) for y,row in enumerate(state['board']) for x,p in enumerate(row) if p=='wR']
                print('wK at', kpos, '| wR at', rpos)
                print('CASTLE OK' if (6,7) in kpos and (5,7) in rpos else 'CASTLE FAILED')
            errs = [m['text'] for m in b1 if m.get('type')=='error']
            print('errors:', errs)

asyncio.run(t())
