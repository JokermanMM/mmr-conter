import urllib.request
import json
import os

token = ""
if os.path.exists(".env"):
    with open(".env") as f:
        for line in f:
            if line.startswith("STRATZ_TOKEN="):
                token = line.strip().split("=", 1)[1]

query2 = json.dumps({
    "query": """{ 
        __type(name: "MatchPlayerType") { 
            fields { 
                name 
                type { name kind } 
            } 
        } 
    }"""
}).encode("utf-8")

headers = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Authorization": f"Bearer {token}"
}

req2 = urllib.request.Request("https://api.stratz.com/graphql", data=query2, headers=headers)
try:
    with urllib.request.urlopen(req2, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        fields = data.get("data", {}).get("__type", {}).get("fields", [])
        print("All fields in MatchPlayerType:")
        for f in fields:
            print(f"  - {f['name']}")
except Exception as e:
    print(f"Error: {e}")
