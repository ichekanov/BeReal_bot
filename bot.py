import asyncio
import configparser
import json
from datetime import datetime
from random import randint

from telethon import TelegramClient
from telethon.events import ChatAction, NewMessage, StopPropagation

import messages as msg

BEGIN_HOUR = 10
END_HOUR = 21

OVERALL_TIME = 30
NOTIFY_TIME = 10

config = configparser.ConfigParser()
config.read('config.ini')
API_ID = config.get('default', 'API_ID')
API_HASH = config.get('default', 'API_HASH')
BOT_TOKEN = config.get('default', 'BOT_TOKEN')

client = TelegramClient('session_master', API_ID, API_HASH)

session = {"users": {}, "chats": [], "next_round": 0}
photos_are_accepted = False


def update_file() -> None:
    """
    Database update method.
    """
    with open("./session.json", "w", encoding="utf-8") as file:
        json.dump(session, file)


def calculate_time() -> int:
    """
    Calculates the time of the next reminder.
    The notification will come the next day.

    Returns
        Time delta in seconds until the next notification.
    """
    # return 30*60
    curr = datetime.now()
    hour = randint(BEGIN_HOUR, END_HOUR-1)
    minute = randint(0, 59)
    nxt = datetime(curr.year, curr.month, curr.day+1, hour, minute, 0)
    delta = nxt.timestamp() - curr.timestamp()
    return delta


async def notify() -> None:
    """
    Waits until next notification, then sends two notifications and finally sends photos to chats.
    """
    global photos_are_accepted
    global session
    while True:
        time_until_notification = calculate_time()
        await asyncio.sleep(time_until_notification)

        photos_are_accepted = True
        for m in session["users"].keys():
            session["users"][m]["posted_photo"] = False
        update_file()
        all_user_ids = [int(m) for m in session["users"].keys()]
        for user_id in all_user_ids:
            await client.send_message(user_id, msg.PHOTO_BEGIN, parse_mode="HTML")
        await asyncio.sleep((OVERALL_TIME-NOTIFY_TIME)*60)

        no_photos = [m for m in all_user_ids if not 
                     session["users"][str(m)]["posted_photo"]]
        for user_id in no_photos:
            await client.send_message(user_id, msg.PHOTO_RUNOUT, parse_mode="HTML")
        await asyncio.sleep(NOTIFY_TIME*60)

        for user_id in all_user_ids:
            await client.send_message(user_id, msg.PHOTO_END, parse_mode="HTML")
        photos_are_accepted = False
        await send_photos()
        for m in session["users"].keys():
            session["users"][m]["posted_photo"] = False
        update_file()


async def send_photos():
    """
    Sends photos to chats according to the participating users and the users who sent the photos.
    """
    all_user_ids = [int(m) for m in session["users"].keys()]
    for chat_id in session["chats"]:
        async for tg_user in client.iter_participants(chat_id):
            if tg_user.id in all_user_ids:
                if session["users"][str(tg_user.id)]["posted_photo"]:
                    photo_path = f"./pics/{tg_user.id}.jpg"
                    name = session["users"][str(tg_user.id)]["name"]
                    photo_msg = f"{name}, @{tg_user.username}"
                    await client.send_file(chat_id, photo_path, caption=photo_msg, parse_mode="HTML")


@client.on(NewMessage(pattern=r"(?i)^/start$", func=lambda e: e.is_private))
async def start(event):
    """
    Greeting message in private dialog.
    Function adds user from mailing list.
    """
    sender = await event.get_sender()
    print(f"New user: {sender.first_name} {sender.last_name}\n")
    if sender.last_name:
        name = f"{sender.first_name} {sender.last_name}"
    else:
        name = sender.first_name
    global session
    session["users"][str(sender.id)] = {"name": name, "posted_photo": False}
    update_file()
    await client.send_message(sender.id, msg.BEGIN, parse_mode="HTML")
    raise StopPropagation


@client.on(NewMessage(pattern=r"^/(?i)stop$", func=lambda e: e.is_private))
async def stop(event):
    """
    Farewell message in private dialog.
    Function deletes user from mailing list.
    """
    sender = await event.get_sender()
    global session
    if (session["users"].pop(str(sender.id), None)):
        print(f"User removed: {sender.first_name} {sender.last_name}\n")
    else:
        print(
            f"The user \"{sender.first_name} {sender.last_name}\" was deleted earlier\n")
    update_file()
    await client.send_message(sender.id, msg.END, parse_mode="HTML")
    raise StopPropagation


@client.on(NewMessage(func=lambda e: e.photo and e.is_private))
async def handle_image(event):
    """
    Checks time and, if it is correct, downloads photo and changes the user status.
    """
    sender = await event.get_sender()
    if photos_are_accepted:
        path = await client.download_media(event.photo, f"./pics/{sender.id}.jpg")
        global session
        session["users"][str(sender.id)]["posted_photo"] = True
        update_file()
        await client.send_message(sender.id, msg.PHOTO_OK, parse_mode="HTML")
    else:
        await client.send_message(sender.id, msg.PHOTO_BAD, parse_mode="HTML")
    raise StopPropagation


@client.on(NewMessage(func=lambda e: e.is_private))
async def default_text(event):
    """
    Default answer on direct messages.
    """
    sender = await event.get_sender()
    await client.send_message(sender.id, msg.DEFAULT, parse_mode="HTML")
    raise StopPropagation


@client.on(ChatAction(func=lambda e: e.user_added))
async def on_added(event):
    """
    Greeting message in a chat.
    Function adds chat to mailing list.
    """
    print("e.user_added")
    me = await client.get_me()
    if event.user_id != me.id:
        return
    global session
    if event.chat_id in session["chats"]:
        print(f"Chat \"{event.chat.title}\" is already added\n")
    else:
        session["chats"].append(event.chat_id)
        update_file()
        print(f"Chat \"{event.chat.title}\" was successfully added\n")
    await client.send_message(event.chat_id, msg.JOINED_CHAT, parse_mode="HTML")
    raise StopPropagation


@client.on(ChatAction(func=lambda e: e.user_kicked))
async def on_kicked(event):
    """
    Function removes chat from mailing list.
    """
    me = await client.get_me()
    print("e.user_kicked")
    if event.user_id != me.id:
        return
    global session
    if event.chat_id in session["chats"]:
        session["chats"].remove(event.chat_id)
        update_file()
        print(f"Chat \"{event.chat.title}\" was successfully removed\n")
    else:
        print(f"Chat \"{event.chat.title}\" is already removed\n")
    raise StopPropagation


if __name__ == '__main__':
    try:
        with open("./session.json", "r", encoding="utf-8") as file:
            session = json.load(file)
    except:
        update_file()
    client.start(bot_token=BOT_TOKEN)
    client.loop.create_task(notify())
    print("Bot started!\n")
    client.run_until_disconnected()
