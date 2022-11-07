from datetime import datetime
import json
from os import system

DB_NAME = "session.json"

with open(DB_NAME, "r", encoding="utf-8") as file:
    old = json.load(file)


session = {"users": {}, "chats": {},
           "next_round": datetime.fromtimestamp(0).isoformat()}

for usr in old["users"]:
    session["users"][usr] = {
        "name": old["users"][usr]["name"],
        "registered_at": datetime.now().isoformat(),
        "posted_media": False,
        "media_type": None,
        "media_path": "",
        "timestamp": datetime.fromtimestamp(0).isoformat()
    }

for cht in old["chats"]:
    session["chats"][cht] = {
        "added_at": datetime.now().isoformat(),
        "last_activity": datetime.fromtimestamp(0).isoformat()
    }

system(f"cp {DB_NAME} db_old.json")

with open(DB_NAME, "w", encoding="utf-8") as file:
    json.dump(session, file)
