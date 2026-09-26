"""Scripted phone calls with synthesized speech (macOS `say` makes the user's lines).

    uv run uvicorn app.main:app --port 8765 &
    uv run python -m evals.calls [scenario ...]    # all if none given

Steps: text:<msg>  accept  decline  start  say:<line>  silence:<s>  hangup  drop  demo
wait:<s>. `drop` closes the audio without hanging up (a lost connection); `demo` connects
the demo inbox. Transcripts: evals/out/calls/<name>.txt.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import wave
import zlib

import websockets

PORT = 8765
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "calls")
WAV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "wav")
LINES = {
    "pick": "Honestly, you pick a name for yourself.",
    "yes": "Yeah, that works for me.",
    "siobhan": "I'm Siobhan.",
    "myname": "Wait, what's my name again?",
    "aoife": "My name is Aoife. That's A, O, I, F, E.",
    "real": "Are you a real person? Is this being recorded?",
    "didyouget": "Did you get my text?",
    "bye": "Okay, that's all for now. Bye!",
    "holdon": "Hold on one sec.",
    "bills": "Honestly, I keep forgetting to pay my bills on time.",
    "link": "Sure, text me the link.",
    "done": "Okay, I just connected it.",
}


def wav(line: str) -> str:
    """The user's line as 24 kHz mono PCM, made once with macOS `say`."""
    path = os.path.join(WAV, f"{line}.wav")
    if not os.path.exists(path):
        os.makedirs(WAV, exist_ok=True)
        subprocess.run(
            [
                "say",
                "-v",
                "Samantha",
                "-o",
                path,
                "--data-format=LEI16@24000",
                "--channels=1",
                LINES[line],
            ],
            check=True,
        )
    return path


FRAME = 960

SCENARIOS: dict[str, list[str]] = {
    "hangup_on_pickup": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "silence:3",
        "hangup",
        "wait:14",
    ],
    "hangup_then_callback": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:pick",
        "say:yes",
        "say:siobhan",
        "hangup",
        "wait:12",
        "start",
        "say:myname",
        "say:bye",
        "wait:12",
    ],
    "drop": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:pick",
        "say:yes",
        "drop",
        "wait:14",
    ],
    "dead_air": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:holdon",
        "silence:55",
        "wait:12",
    ],
    "text_during_call": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:pick",
        "say:yes",
        "text:btw my name is jordan",
        "say:didyouget",
        "say:bills",
        "say:bye",
        "wait:12",
    ],
    "spell_name": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:pick",
        "say:yes",
        "say:aoife",
        "say:bye",
        "wait:10",
    ],
    "real_person": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:real",
        "say:bye",
        "wait:10",
    ],
    "gmail_on_call": [
        "text:Hey, what's a Persona?",
        "text:sure call me",
        "accept",
        "say:pick",
        "say:yes",
        "say:siobhan",
        "say:bills",
        "say:link",
        "demo",
        "say:done",
        "say:bye",
        "wait:12",
    ],
    "ignore_ring": ["text:Hey, what's a Persona?", "text:sure call me", "wait:45"],
    "decline": ["text:Hey, what's a Persona?", "text:sure call me", "decline", "wait:14"],
}


async def run(name: str, steps: list[str]) -> str:
    phone = "5558" + str(zlib.crc32(name.encode()) % 10**6).zfill(6)
    log: list[str] = []
    t0 = time.time()
    ts = lambda: f"[{time.time() - t0:5.1f}]"  # noqa: E731
    state = {"ringing": False, "last": time.time(), "audio": None}
    async with websockets.connect(f"ws://localhost:{PORT}/ws?phone={phone}", max_size=None) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "reset"}))
        await ws.recv()

        async def watch() -> None:
            async for raw in ws:
                m = json.loads(raw)
                if m["type"] == "partial":
                    if m["speaker"] == "agent":
                        state["last"] = time.time()
                    if m["final"]:
                        log.append(f"{ts()}   {m['speaker'].upper()} (voice): {m['text']}")
                elif m["type"] == "call":
                    state["ringing"] = m["call"]["phase"] == "ringing"
                elif m["type"] == "event":
                    p = m["event"]["payload"]
                    k = p["kind"]
                    if k == "agent_message":
                        log.append(f"{ts()}   AGENT (text): {p['text']}")
                        state["last"] = time.time()
                    elif k == "tool_call":
                        log.append(f"{ts()}   · {p['name']} {json.dumps(p['args'])[:100]}")
                    elif k == "call":
                        log.append(f"{ts()}   · call {p['transition']} {p.get('reason') or ''}")
                    elif k == "slot_changed":
                        log.append(f"{ts()}   · {p['slot']} = {p['new']}")
                    elif k == "graduated":
                        log.append(f"{ts()}   · GRADUATED")

        watcher = asyncio.create_task(watch())
        silence = b"\0" * FRAME

        async def pump(seconds: float) -> None:
            audio = state["audio"]
            for _ in range(int(seconds / 0.02)):
                if audio is not None:
                    try:
                        await audio.send(silence)
                    except Exception:
                        return
                await asyncio.sleep(0.02)

        async def quiet(min_s: float = 2.5, max_s: float = 25) -> None:
            start = time.time()
            while time.time() - start < max_s:
                await pump(0.5)
                if time.time() - start > min_s and time.time() - state["last"] > 2.8:
                    return

        async def open_audio() -> None:
            await asyncio.sleep(0.3)
            state["audio"] = await websockets.connect(
                f"ws://localhost:{PORT}/ws/audio?phone={phone}", max_size=None
            )

            async def drain() -> None:
                try:
                    async for _ in state["audio"]:
                        state["last"] = time.time()
                except Exception:
                    pass

            asyncio.create_task(drain())
            await quiet(4)

        for step in steps:
            kind, _, arg = step.partition(":")
            if kind == "text":
                log.append(f"{ts()} USER (text): {arg}")
                await ws.send(json.dumps({"type": "message", "text": arg}))
                await quiet(3)
            elif kind in ("accept", "decline"):
                for _ in range(60):
                    if state["ringing"]:
                        break
                    await asyncio.sleep(0.25)
                log.append(f"{ts()} USER: {kind}s the call")
                await ws.send(json.dumps({"type": "call", "action": kind}))
                if kind == "accept":
                    await open_audio()
            elif kind == "start":
                log.append(f"{ts()} USER: calls the agent")
                await ws.send(json.dumps({"type": "call", "action": "start"}))
                await open_audio()
            elif kind == "say":
                pcm = wave.open(wav(arg)).readframes(10**9)
                log.append(f"{ts()} USER (says {arg})")
                for i in range(0, len(pcm), FRAME):
                    await state["audio"].send(pcm[i : i + FRAME])
                    await asyncio.sleep(0.02)
                await quiet()
            elif kind == "silence":
                log.append(f"{ts()} USER: silent {arg}s")
                await pump(float(arg))
            elif kind in ("hangup", "drop"):
                log.append(f"{ts()} USER: {'hangs up' if kind == 'hangup' else 'connection drops'}")
                if kind == "hangup":
                    await ws.send(json.dumps({"type": "call", "action": "hangup"}))
                if state["audio"] is not None:
                    await state["audio"].close()
                    state["audio"] = None
            elif kind == "demo":
                log.append(f"{ts()} USER: connects the demo inbox")
                demo = f"http://localhost:{PORT}/api/auth/google/demo?phone={phone}"
                urllib.request.urlopen(urllib.request.Request(demo, method="POST"))
                await quiet(2)
            elif kind == "wait":
                await asyncio.sleep(float(arg))
        if state["audio"] is not None:
            await state["audio"].close()
        await asyncio.sleep(2)
        watcher.cancel()
    out = f"=== {name}\n" + "\n".join(log)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.txt"), "w") as f:
        f.write(out + "\n")
    return out


async def main() -> None:
    names = sys.argv[1:] or list(SCENARIOS)
    sem = asyncio.Semaphore(4)

    async def one(n: str) -> str:
        async with sem:
            try:
                return await run(n, SCENARIOS[n])
            except Exception as exc:
                return f"=== {n}\nFAILED: {type(exc).__name__}: {exc}"

    for out in await asyncio.gather(*(one(n) for n in names)):
        print(out, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
