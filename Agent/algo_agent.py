import os
import requests
import json

API_KEY = os.getenv("OPENAI_API_KEY")

def chat(prompt):
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        },
        data=json.dumps({
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "user", "content": prompt}
            ]
        })
    )

    return resp.json()["choices"][0]["message"]["content"]


print(chat("hello"))
