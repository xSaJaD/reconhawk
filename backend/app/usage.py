import asyncio
from .db import SessionLocal, Task, TokenUsage
from .pricing import estimate_cost

locks = {}

async def record(task_id, provider, model, tin, tout):
    async with locks.setdefault(task_id, asyncio.Lock()):
        cost = estimate_cost(model,tin,tout)
        async with SessionLocal() as s:
            t=await s.get(Task,task_id)
            if not t:
                return 0,0
            s.add(TokenUsage(task_id=task_id,provider=provider,model=model,input_tokens=tin,output_tokens=tout,cost_usd=cost))
            t.tokens_in+=tin
            t.tokens_out+=tout
            t.cost_usd+=cost or 0
            t.unknown_price=t.unknown_price or cost is None
            t.steps+=1
            await s.commit()
            return t.cost_usd,t.steps
