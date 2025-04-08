# https://web.mit.edu/marleigh/www/portfolio/Files/zork/transcript.html
from dotenv import load_dotenv
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Generic, List, Optional, TypeVar, Union, Literal
from browser_use.agent.service import Context
from browser_use.browser.browser import Browser, BrowserConfig
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from browser_use.browser.views import BrowserError
from browser_use.controller.service import Controller
from browser_use.dom.views import DOMElementNode
from playwright.async_api import Page, ElementHandle, JSHandle, TimeoutError
import asyncio
import os, json, inspect, argparse, inspect, re


load_dotenv()

url = "https://eblong.com/infocom/visi-zork1/"
config = BrowserContextConfig(
    cookies_file=os.getenv("cookies", "./.save/cookies.json"),
    wait_for_network_idle_page_load_time=3.0,
    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/85.0.4183.102 Safari/537.36',
    highlight_elements=False,
)
browser_instance_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
browser_config = BrowserConfig(headless=False, chrome_instance_path=browser_instance_path)

help_info = """Useful commands:
The 'QUIT' or 'Q' command prints your score and asks whether you wish to continue playing. 
The 'INVENTORY' or 'I' command lists the objects in your possession.
The 'LOOK' or 'L' command prints a description of your surroundings.

The Actions: Among the more obvious of these, such as 'TAKE', 'PUT', 'DROP', etc.
The Directions: 'NORTH', 'SOUTH', 'EAST', 'WEST', 'UP', 'DOWN', etc. and their various abbreviations. Other more obscure directions (LAND, CROSS) are appropriate in only certain situations.
Objects: Most objects have names and can be referenced by them.
Adjectives: Some adjectives are understood and required when there are  two objects which can be referenced with the same 'name' (e.g., DOORs, BUTTONs).
Prepositions: It may be necessary in some cases to include prepositions, but the parser attempts to handle cases which aren't ambiguous without.  Thus 'GIVE CAR TO DEMON' will work, as will 'GIVE DEMON CAR'.  'GIVE CAR DEMON' probably won't do anything interesting. When a preposition is used, it should be appropriate;  'GIVE CAR WITH DEMON' won't parse.
"""

background = """Welcome to ZORK!

You are near a large dungeon, which is reputed to contain vast quantities of treasure.   Naturally, you wish to acquire some of it. In order to do so, you must of course remove it from the dungeon.  To receive full credit for it, you must deposit it safely in the trophy case in the living room of the house.

In addition to valuables, the dungeon contains various objects which may or may not be useful in your attempt to get rich.  You may need sources of light, since dungeons are often dark, and weapons, since dungeons often have unfriendly things wandering about.  Reading material is scattered around the dungeon as well;  some of it is rumored to be useful.

To determine how successful you have been, a score is kept. When you find a valuable object and pick it up, you receive a certain number of points, which depends on the difficulty of finding the object.  You receive extra points for transporting the treasure safely to the living room and placing it in the trophy case.  In addition, some particularly interesting rooms have a value associated with visiting them.  The only penalty is for getting yourself killed, which you may do only twice.

Of special note is a thief (always carrying a large bag) who likes to wander around in the dungeon (he has never been seen by the light of day).  He likes to take things.  Since he steals for pleasure rather than profit and is somewhat sadistic, he only takes things which you have seen.  Although he prefers valuables, sometimes in his haste he may take something which is worthless.  From time to time, he examines his take and discards objects which he doesn't like.  He may occasionally stop in a room you are visiting, but more often he just wanders through and rips you off (he is a skilled pickpocket).
"""

class Client:

    def chat(self, content: List) -> str:
        raise NotImplementedError()
    
class MaunalClient(Client):

    def chat(self, content: List) -> str:
        cmd = input("[q to exit]> ")
        return cmd

class OpenAiClient(Client):
    system_prompt = """You are a player, You need to play a Text Game named zork.
Everytime you need to output a command based on chat history.

## Background
```
{background}
```

## Help Info:
```
{help_info}
```

Note:
- Your output MUST be concise command based on Help Info.
- If you get lost, you need to go back based on historical information.
"""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.message = []

        self.message.append({"role": "system", "content": self.system_prompt.format(help_info=help_info, background=background)})

    def chat(self, content: List) -> str:
        # cmd = input("AI>")
        # if cmd.lower() == 'q':
        #     return cmd

        self.message.append({"role": "user", "content": "\n".join(content)})
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=self.message,
            temperature=0.0
        )

        cmd = resp.choices[0].message.content
        self.message.append({"role": "assistant", "content": cmd})
        print(f">{cmd}")
        return cmd


class Player:
    header = "=== {place}, Score: {score}, moves: {moves} ==="

    def __init__(self, context: BrowserContext, client: Client = MaunalClient(), step_limit: int = 50) -> None:
        self.context = context
        self.client = client

        self.step_limit = step_limit

        self.message = []
        self.moves = 0
        self.score = 0
        self.place = ""
        self.playground_content = []
        self.input_handle: ElementHandle = None

    async def _init(self):
        assert((playground_handler := await self.page.query_selector("#window1")) != None)
        assert((place_handler := await self.page.query_selector("#window2")) != None)
        self.playground_handler = playground_handler
        self.place_handler = place_handler

        await self._get_place()
        self.message = await self._get_playground_content()
        
        print(self.header.format(place=self.place, score=self.score, moves=self.moves))
        for line in self.playground_content:
            print(line)

    async def _get_playground_content(self) -> List[str]:
        """Get lastest content in playground and Update history and input handle."""
        content_list: List[ElementHandle] = await self.playground_handler.query_selector_all(".BufferLine")
        
        start = len(self.playground_content)
        ret = []
        for content in content_list[start: -1]:
            ret.append(await content.inner_text())
        
        self.playground_content.extend(ret)
        self.input_handle = await content_list[-1].query_selector("input")
        assert(self.input_handle)
        return ret
    
    async def _get_place(self) -> str:
        """Get current place and Update score and moves."""
        header = await self.place_handler.inner_text()
        try:
            pattern = r"(.*)Score:([ 0-9]*)Moves:([ 0-9]*)"
            res = re.search(pattern, header, re.DOTALL)
            assert(res)
            self.place = res.group(1).strip()
            self.score = int(res.group(2).strip())
            self.moves = int(res.group(3).strip())
        except Exception as e:
            print(e)
        return self.place

    async def _input_cmd(self, cmd: str):
        await self.input_handle.fill(cmd)
        await self.page.keyboard.press("Enter")
        # await self.context._wait_for_page_and_frames_load()

    async def play(self):
        self.page = await self.context.get_current_page()
        await self.page.goto(url)
        await self.context._wait_for_page_and_frames_load()

        await self._init()

        while (await self.step()) and self.step_limit >= self.moves:
            pass
        
    async def step(self) -> bool:
        cmd = self.client.chat(self.message)
        if cmd.lower() == "q" or cmd.lower() == "quit":
            return False
        
        try:
            self.playground_content.append(f">{cmd}")
            await self._input_cmd(cmd)
            await self._get_place()
            self.message = await self._get_playground_content()
        except Exception as e:
            print(e)
            return False

        print(self.header.format(place=self.place, score=self.score, moves=self.moves))
        for line in self.message:
            print(line)
        return True


async def run():
    browser = Browser(browser_config)
    context = BrowserContext(browser=browser, config=config)

    await context.refresh_page()
    await context._wait_for_page_and_frames_load()

    api_key = os.getenv("OPENAI_API_KEY", "")
    base_url = os.getenv("OPENAI_BASE_URL", "")
    client = OpenAiClient(api_key, base_url, "gpt-4o-mini")

    player = Player(context, client)
    await player.play()

    await context.close()
    await browser.close()

asyncio.run(run())