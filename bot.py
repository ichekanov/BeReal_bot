import asyncio
import configparser
import json
from datetime import datetime, timedelta
from random import randint
import logging

from telethon import TelegramClient
from telethon.events import ChatAction, NewMessage, StopPropagation
from telethon.errors.rpcerrorlist import UserIsBlockedError

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

session = {"users": {}, "chats": {},
           "next_round": datetime.fromtimestamp(0).isoformat()}
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
    if datetime.fromisoformat(session["next_round"]) > datetime.now():
        return datetime.fromisoformat(session["next_round"]).timestamp() - curr.timestamp()
    hour = randint(BEGIN_HOUR, END_HOUR-1)
    minute = randint(0, 59)
    nxt = datetime(curr.year, curr.month, curr.day) + timedelta(days=1, hours=hour, minutes=minute)
    session["next_round"] = nxt.isoformat()
    update_file()
    delta = nxt.timestamp() - curr.timestamp()
    logging.info("Next notification will be sent at %s", nxt.strftime("%d-%b-%y %H:%M:%S"))
    return delta


async def safe_send_message(chat_id: int | str, message: str) -> None:
    """
    Sends message to chat and handles exceptions.

    Args:
        chat_id: Chat ID, int or str.
        message: Message text.
    """
    if isinstance(chat_id, str):
        chat_id = int(chat_id)
    try:
        await client.send_message(chat_id, message, parse_mode="HTML")
        logging.info("Sent message to %d: %s", chat_id, message)
    except UserIsBlockedError:
        logging.info("User %d is blocked, removing from database", chat_id)
        try:
            session["users"].pop(str(chat_id))
            update_file()
        except KeyError:
            logging.warning("Error while removing user: user %d is blocked, but not in database", chat_id)
    except Exception as exc:
        logging.exception("Error while sending message to %d: %s", chat_id, exc)


async def custom_message():
    """
    Sends custom messages to all bot users.
    E.g.: to inform about recent changes.
    """
    while True:
        message = await client.loop.run_in_executor(None, input)
        message = message.replace("\\n", "\n")
        print(f"Your message:\n{message}\n\nSend? [y/n]: ", end="")
        result = await client.loop.run_in_executor(None, input)
        if result.lower() not in ("y", "yes"):
            print("Cancelled\n")
            continue
        for user_id in session["users"].keys():
            await safe_send_message(user_id, message)
        print("Success\n")
        logging.info('Sent message "%s" to all %d users', message, len(session["users"]))


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
            session["users"][m]["posted_media"] = False
        update_file()
        all_user_ids = [int(m) for m in session["users"].keys()]
        for user_id in all_user_ids:
            await safe_send_message(user_id, msg.PHOTO_BEGIN)
        await asyncio.sleep((OVERALL_TIME-NOTIFY_TIME)*60)

        all_user_ids = [int(m) for m in session["users"].keys()]
        no_photos = [m for m in all_user_ids if not
                     session["users"][str(m)]["posted_media"]]
        for user_id in no_photos:
            await safe_send_message(user_id, msg.PHOTO_RUNOUT)
        await asyncio.sleep(NOTIFY_TIME*60)

        all_user_ids = [int(m) for m in session["users"].keys()]
        for user_id in all_user_ids:
            await safe_send_message(user_id, msg.PHOTO_END)
        photos_are_accepted = False
        await send_photos()
        for m in session["users"].keys():
            session["users"][m]["posted_media"] = False
        update_file()


async def send_photos():
    """
    Sends photos to chats according to the participating users and the users who sent the photos.
    """
    active_chats = set()
    all_user_ids = [int(m) for m in session["users"].keys()]
    for chat_id in session["chats"]:
        chat_id = int(chat_id)
        async for tg_user in client.iter_participants(chat_id):
            if not tg_user.id in all_user_ids:
                continue
            user = session["users"][str(tg_user.id)]
            posted_media = user["posted_media"]
            if not posted_media:
                continue
            media_type = user["media_type"]
            path = user["media_path"]
            name = user["name"]
            time = datetime.fromisoformat(user["timestamp"]).strftime("%H:%M")
            photo_msg = f"<b>{name}</b>, @{tg_user.username}\n{time}"
            try:
                if media_type == "photo":
                    await client.send_file(chat_id, path, caption=photo_msg, parse_mode="HTML")
                elif media_type == "video":
                    await client.send_file(chat_id, path)
                    await client.send_message(chat_id, photo_msg, parse_mode="HTML")
                logging.info("Sent %s to %d", path, chat_id)
                active_chats.add(chat_id)
            except Exception as exc:
                logging.exception("Error while sending media to chat %s for user %d: %s", chat_id, tg_user.id, exc)
    logging.info("Sent photos to %d chats", len(active_chats))
    for chat_id in active_chats:
        session["chats"][str(chat_id)]["last_activity"] = datetime.now().isoformat()
    update_file()


@client.on(NewMessage(pattern=r"(?i)^/start$", func=lambda e: e.is_private))
async def start(event):
    """
    Greeting message in private dialog.
    Function adds user from mailing list.
    """
    sender = await event.get_sender()
    logging.info("New user: %s %s", sender.first_name, sender.last_name)
    if sender.last_name:
        name = f"{sender.first_name} {sender.last_name}"
    else:
        name = sender.first_name
    global session
    session["users"][str(sender.id)] = {
        "name": name,
        "registered_at": datetime.now().isoformat(),
        "posted_media": False,
        "media_type": None,
        "media_path": "",
        "timestamp": datetime.fromtimestamp(0).isoformat()
    }
    update_file()
    await safe_send_message(sender.id, msg.BEGIN)
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
        logging.info("User removed: %s %s", sender.first_name, sender.last_name)
    else:
        logging.info("User removed earlier: %s %s", sender.first_name, sender.last_name)
    update_file()
    await safe_send_message(sender.id, msg.END)
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
        session["users"][str(sender.id)]["posted_media"] = True
        session["users"][str(sender.id)]["media_type"] = "photo"
        session["users"][str(sender.id)]["media_path"] = path
        session["users"][str(sender.id)]["timestamp"] = datetime.now().isoformat()
        update_file()
        await safe_send_message(sender.id, msg.PHOTO_OK)
    else:
        await safe_send_message(sender.id, msg.PHOTO_BAD)
    raise StopPropagation


@client.on(NewMessage(func=lambda e: e.video and e.is_private))
async def handle_video(event):
    """
    Checks time and, if it is correct, saves video id and changes the user status.
    """
    sender = await event.get_sender()
    if photos_are_accepted:
        global session
        session["users"][str(sender.id)]["posted_media"] = True
        session["users"][str(sender.id)]["media_type"] = "video"
        session["users"][str(sender.id)]["media_path"] = event.file.id
        session["users"][str(sender.id)]["timestamp"] = datetime.now().isoformat()
        update_file()
        await safe_send_message(sender.id, msg.CIRCLE_OK)
    else:
        await safe_send_message(sender.id, msg.PHOTO_BAD)
    raise StopPropagation


@client.on(NewMessage(func=lambda e: e.is_private))
async def default_text(event):
    """
    Default answer on direct messages.
    """
    sender = await event.get_sender()
    await safe_send_message(sender.id, msg.DEFAULT)
    raise StopPropagation


@client.on(ChatAction(func=lambda e: e.user_added))
async def on_added(event):
    """
    Greeting message in a chat.
    Function adds chat to mailing list.
    """
    me = await client.get_me()
    if event.user_id != me.id:
        return
    global session
    if event.chat_id in session["chats"]:
        logging.info("Chat \"%s\" is already added", event.chat.title)
    else:
        session["chats"][event.chat_id] = {
            "added_at": datetime.now().isoformat(),
            "last_activity": datetime.fromtimestamp(0).isoformat()
        }
        update_file()
        logging.info("Chat \"%s\" was successfully added", event.chat.title)
    await safe_send_message(event.chat_id, msg.JOINED_CHAT)
    raise StopPropagation


@client.on(ChatAction(func=lambda e: e.user_kicked))
async def on_kicked(event):
    """
    Function removes chat from mailing list.
    """
    me = await client.get_me()
    if event.user_id != me.id:
        return
    global session
    if event.chat_id in session["chats"]:
        session["chats"].remove(event.chat_id)
        update_file()
        logging.info("Chat \"%s\" was successfully removed", event.chat.title)
    else:
        logging.info("Chat \"%s\" is already removed", event.chat.title)
    raise StopPropagation


if __name__ == '__main__':
    try:
        with open("./session.json", "r", encoding="utf-8") as file:
            session = json.load(file)
    except Exception:
        update_file()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                        datefmt="%d-%b-%y %H:%M:%S",
                        handlers=[logging.FileHandler("log.txt"), logging.StreamHandler()])
    client.start(bot_token=BOT_TOKEN)
    client.loop.create_task(notify())
    client.loop.create_task(custom_message())
    logging.info("Bot started!")
    client.run_until_disconnected()
