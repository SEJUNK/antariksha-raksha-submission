"""Generates narration per sentence with a neural en-IN voice and records exact durations.

Each sentence is synthesised separately so visual cuts can be aligned to sentence starts.
Output: audio/<section>_<n>.mp3 and timing.json.
"""
import asyncio, json, os, subprocess, sys

import edge_tts

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "audio")
os.makedirs(OUT, exist_ok=True)
script = json.load(open(os.path.join(HERE, "script.json"), encoding="utf-8"))
voice, rate = script["voice"], script["rate"]


def duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


async def main():
    timing = []
    for sec in script["sections"]:
        items = []
        for i, text in enumerate(sec["sentences"]):
            f = os.path.join(OUT, f"{sec['id']}_{i:02d}.mp3")
            if not os.path.exists(f) or "--force" in sys.argv:
                await edge_tts.Communicate(text, voice, rate=rate).save(f)
            items.append({"file": f, "text": text, "dur": round(duration(f), 3)})
        timing.append({"id": sec["id"], "title": sec["title"], "sentences": items})
        print(sec["id"], round(sum(x["dur"] for x in items), 1), "s")
    json.dump(timing, open(os.path.join(HERE, "timing.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


asyncio.run(main())
