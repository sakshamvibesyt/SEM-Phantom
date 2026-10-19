import os
import asyncio
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pyrogram import Client, idle
from convopyro import Conversation
from pytgcalls import PyTgCalls
from config import *
# The voice-call callbacks and their filters live in tools, not in the music
# plugin: pyrogram loads everything under userbot/ itself, so importing a plugin
# module here would load a second copy of it under a different name and split
# its module-level state in two.
from tools import (
    CALL_GONE_FILTER,
    STREAM_END_FILTER,
    call_gone_handler,
    stream_end_handler,
)
from plugin_loader import load_extra_plugins
from userbot.game_solver import warm_solver

# Strong references to fire-and-forget tasks; asyncio only holds weak ones.
background_tasks = set()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
)

logger = logging.getLogger("userbot")

print("Starting Userbot...")


# Render Web Service health server.
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"SEM PHANTOM is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        # Keep routine health checks from filling the logs.
        return


def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="render-health-server",
        daemon=True,
    )
    thread.start()
    print(f"SEM PHANTOM health server listening on 0.0.0.0:{port}")
    return server


async def main():
    # Get session string from environment, or prompt when a human is attached.
    # `input()` unconditionally was a trap under Docker or a service manager:
    # stdin is not a tty there, and the os.execv restarts in userbot/update.py
    # and bot/botcmds.py re-enter this line, where a prompt either raises
    # EOFError or blocks forever on a pipe. Say what is missing instead.
    if SESSION_STR:
        session_string = SESSION_STR
    elif sys.stdin.isatty():
        session_string = input("Enter your Pyrogram session string: ")
    else:
        print("SESSION_STR is not set and stdin is not a terminal -- set it in .env.")
        raise SystemExit(1)

    # Initialize bot client with bot-specific plugins only. The bot is optional —
    # it only powers inline/special-group features. Skip it entirely when no
    # BOT_TOKEN is configured so nothing registers a dead client in apps["app"].
    app = None
    if BOT_TOKEN:
        app = Client(
            "main_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True,
            sleep_threshold=30,
            plugins=dict(root="bot")
        )

        # Initialize conversation for the bot
        Conversation(app)

    # Initialize userbot client with userbot-specific plugins
    userbot = Client(
        "userbot_session",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=session_string,
        plugins=dict(root="userbot")
    )

    # Initialize conversation for userbot
    Conversation(userbot)

    try:
        # Start bot client if it was created. A bot failure (e.g. FLOOD_WAIT
        # on auth.ImportBotAuthorization) must NOT take down the userbot.
        if app is not None:
            try:
                await app.start()
                apps["app"] = app
                print("Bot started successfully!")
                print(f"Bot logged in as: {app.me.first_name} (@{app.me.username})")
            except Exception as e:
                print(f"Bot client failed to start (continuing without it): {e}")

        # Start userbot client.
        try:
            await userbot.start()
        except Exception as e:
            print(f"Userbot client failed to start: {e}")
            raise SystemExit(1)

        print("Userbot started successfully!")
        print(f"Userbot logged in as: {userbot.me.first_name} (@{userbot.me.username})")

        # Add to clients dict for compatibility
        clients[userbot.me.id] = userbot

        # Load sudo users from database
        user_data = user_sessions.find_one({"user_id": userbot.me.id})
        if user_data and "sudoers" in user_data:
            SUDO[userbot.me.id] = user_data["sudoers"]

        # Start the voice-call client alongside the Pyrogram client.
        try:
            call_py = PyTgCalls(userbot)
            call_py.add_handler(stream_end_handler, STREAM_END_FILTER)
            call_py.add_handler(call_gone_handler, CALL_GONE_FILTER)
            await call_py.start()
            songs_client[userbot.me.id] = call_py
            queues[f"dic_{userbot.me.id}"] = {}
            active[userbot.me.id] = []
            print("Voice call client started successfully!")
        except Exception as e:
            print(f"Voice call client failed to start (music commands will be unavailable): {e}")

        # Load external community plugins (no repo fork needed)
        loaded_extra_plugins.extend(load_extra_plugins(userbot, EXTRA_PLUGINS_DIR))
        if loaded_extra_plugins:
            print(f"Loaded {len(loaded_extra_plugins)} extra plugin(s): {', '.join(loaded_extra_plugins)}")

        # Build the WordSeek solver in a worker thread now.
        warmup_task = asyncio.create_task(warm_solver())
        background_tasks.add(warmup_task)
        warmup_task.add_done_callback(background_tasks.discard)

    except Exception as e:
        # Keep existing post-start error handling.
        print(f"Error after starting clients: {e}")

    await idle()


if __name__ == "__main__":
    # Start the HTTP listener before starting the Telegram clients.
    start_health_server()
    asyncio.run(main())
