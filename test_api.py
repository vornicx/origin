import os, asyncio, httpx
from dotenv import load_dotenv
load_dotenv()

key = os.getenv("DEEPSEEK_API_KEY", "")
print(f"Key present: {bool(key)} ({len(key)} chars)")

async def test():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "Say hi"}], "max_tokens": 30},
        )
        print(f"Status: {r.status_code}")
        j = r.json()
        if "error" in j:
            print(f"ERROR: {j['error']}")
        else:
            print(f"OK: {j['choices'][0]['message']['content']}")
            print(f"Usage: {j.get('usage', {})}")

asyncio.run(test())
