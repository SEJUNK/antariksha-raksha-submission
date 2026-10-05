"""Trims each narration clip, assembles the narration track with controlled pauses,
and writes timeline.json (exact start/end of every sentence) plus subtitles (SRT)."""
import json, os, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
TRIM = os.path.join(HERE, "audio_trim")
os.makedirs(TRIM, exist_ok=True)
timing = json.load(open(os.path.join(HERE, "timing.json"), encoding="utf-8"))

LEAD_IN = 1.2          # seconds of silence before the first sentence
SENT_GAP = 0.30        # pause between sentences
SECTION_GAP = 0.75     # pause between sections
TAIL = 2.5             # silence after the last sentence (closing card)
SR = 48000


def run(*a):
    return subprocess.run(a, capture_output=True, text=True, check=True)


def dur(p):
    return float(run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p).stdout)


def silence(path, seconds):
    run("ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{seconds:.3f}", path)


trim_filter = ("silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.04,"
               "areverse,silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.08,areverse")

parts, timeline, t = [], [], 0.0
lead = os.path.join(TRIM, "_lead.wav"); silence(lead, LEAD_IN); parts.append(lead); t += LEAD_IN
for si, sec in enumerate(timing):
    s_start = t
    sents = []
    for i, s in enumerate(sec["sentences"]):
        out = os.path.join(TRIM, os.path.basename(s["file"]).replace(".mp3", ".wav"))
        run("ffmpeg", "-y", "-i", s["file"], "-af", trim_filter, "-ar", str(SR), "-ac", "1", out)
        d = dur(out)
        sents.append({"text": s["text"], "start": round(t, 3), "end": round(t + d, 3)})
        parts.append(out); t += d
        gap = SENT_GAP if i < len(sec["sentences"]) - 1 else (SECTION_GAP if si < len(timing) - 1 else TAIL)
        g = os.path.join(TRIM, f"_gap_{si}_{i}.wav"); silence(g, gap); parts.append(g); t += gap
    timeline.append({"id": sec["id"], "title": sec["title"], "start": round(s_start, 3), "end": round(t, 3), "sentences": sents})

lst = os.path.join(TRIM, "concat.txt")
with open(lst, "w", encoding="utf-8") as f:
    for p in parts:
        f.write(f"file '{p.replace(os.sep, '/')}'\n")
narr = os.path.join(HERE, "narration.wav")
run("ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-ar", str(SR), "-ac", "1", narr)
total = dur(narr)
json.dump({"total": round(total, 3), "sections": timeline}, open(os.path.join(HERE, "timeline.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)


def ts(x):
    h, r = divmod(x, 3600); m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s - int(s)) * 1000)):03d}"


with open(os.path.join(HERE, "subtitles.srt"), "w", encoding="utf-8") as f:
    n = 1
    for sec in timeline:
        for s in sec["sentences"]:
            f.write(f"{n}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n\n"); n += 1
print("narration", round(total, 1), "s =", f"{int(total // 60)}:{int(total % 60):02d}")
for sec in timeline:
    print(f"  {sec['id']:18s} {sec['start']:7.1f} -> {sec['end']:7.1f}")
