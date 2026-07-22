#!/usr/bin/env python3
"""kb_pipeline.py — media pipeline for the /kb-video skill.

Turns a silent screencast (recorded by record-walkthrough.mjs) into a narrated
MP4 for the onbo knowledge base: cloned-voice TTS via ElevenLabs IVC, freeze-frame
video stretch under the longer narration, optional background music, final mux.

Usage:
    kb_pipeline.py init <outdir> <master.mp4>       # copy recording in, report duration
    kb_pipeline.py dryrun <outdir>                  # overflow warnings (read-only)
    kb_pipeline.py synth-tts <outdir> [--mode posegment]   # cloned-voice TTS per segment
    kb_pipeline.py build <outdir> [bgm|none] [--duck N] [--speed S]  # stretch, mix, mux -> final.mp4
    kb_pipeline.py cleanup <outdir>                 # drop heavy rebuildable work files
    kb_pipeline.py list-bgm                         # list available BGM tracks

Run with the skill venv that has the elevenlabs SDK installed:
    .claude/skills/kb-video/.venv/bin/python .claude/skills/kb-video/scripts/kb_pipeline.py ...

Transcript is authored by Claude (no transcription step): after `init`, write
transcript.md with one `## [NNN] start --> end` block per scene (see `init` output).

No ELEVENLABS_API_KEY? `synth-tts` still writes the narration text to
work/narration.txt so the video can be voiced later — it does not crash.
"""
import sys, os, re, json, subprocess, shutil
from pathlib import Path

# scripts/ lives under the skill root; refs/ and bgm/ are siblings of scripts/.
SKILL_DIR = Path(__file__).resolve().parent.parent
REFS_DIR = SKILL_DIR / "refs"
BGM_DIR = SKILL_DIR / "bgm"
VOICE_REF = REFS_DIR / "voice-ref.wav"

SR = 48000
MIN_GAP = 1.3
MIN_INSERT = 0.15
FADE_IN = 1.0
FADE_OUT = 1.5
DEFAULT_DUCK = 4.0
DEFAULT_SPEED = 1.0
BGM_LOOP_CROSSFADE = 2.0

SEG_RE = re.compile(
    r"^## \[(?P<idx>\d+)\] "
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3}) --> "
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})\s*(?:\r?\n)"
    r"original:\s*(?P<orig>.*?)(?:\r?\n)"
    r"(?:edited:\s*(?P<edit>.*?)(?:\r?\n|\Z))?",
    re.MULTILINE,
)


def ts(s: str) -> float:
    h, m, r = s.split(":"); sec, ms = r.split(",")
    return int(h)*3600 + int(m)*60 + int(sec) + int(ms)/1000


def sec_to_srt(sec: float) -> str:
    h = int(sec // 3600); sec -= h*3600
    m = int(sec // 60);   sec -= m*60
    s = int(sec);         ms = int(round((sec - s) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_transcript_md(path: Path):
    """Parse authored transcript.md to a list of segment dicts."""
    if not path.exists():
        raise FileNotFoundError(
            f"transcript.md not found at {path}. Author it after `init` "
            f"(one `## [NNN] start --> end` block per scene).")
    segs = []
    for m in SEG_RE.finditer(path.read_text(encoding="utf-8")):
        segs.append({
            "idx": int(m["idx"]),
            "start": ts(m["start"]),
            "end": ts(m["end"]),
            "orig": m["orig"].strip(),
            "text": (m["edit"] or m["orig"]).strip(),
        })
    if not segs:
        raise RuntimeError(
            f"no segments parsed from {path}. Check the `## [NNN] start --> end / "
            f"original: / edited:` block format.")
    return segs


def _try_env_key(name: str):
    """Return an env key from the process env first, then ~/.claude/.env; None if absent."""
    val = os.environ.get(name)
    if val and val.strip():
        return val.strip()
    env = Path.home() / ".claude" / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if v:
                    return v
    return None


def get_env_key(name: str) -> str:
    val = _try_env_key(name)
    if val is None:
        raise RuntimeError(f"{name} не найден ни в окружении, ни в ~/.claude/.env")
    return val


def _require(tool: str):
    """Ensure a CLI tool is on PATH; Russian error with Linux install hints."""
    if shutil.which(tool) is None:
        raise RuntimeError(
            f"не найден `{tool}` в PATH. Установите его — "
            f"Arch: sudo pacman -S {tool} · Debian/Ubuntu: sudo apt install {tool}")


def _require_ffmpeg():
    _require("ffmpeg")
    _require("ffprobe")


def ffprobe_duration(p: Path) -> float:
    r = subprocess.run(
        ["ffprobe","-v","0","-show_entries","format=duration","-of","csv=p=0",str(p)],
        capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def ffprobe_video_meta(p: Path):
    r = subprocess.run(
        ["ffprobe","-v","0","-select_streams","v:0","-show_entries",
         "stream=width,height,avg_frame_rate","-of","csv=p=0",str(p)],
        capture_output=True, text=True, check=True)
    parts = r.stdout.strip().split(",")
    width, height, rate = parts[0], parts[1], parts[2]
    num, den = rate.split("/")
    fps = float(num) / float(den)
    return {"fps": fps, "width": width, "height": height}


def measure_lufs(wav: Path) -> float:
    r = subprocess.run(
        ["ffmpeg","-i",str(wav),"-af","ebur128=framelog=quiet","-f","null","-"],
        capture_output=True, text=True)
    m = re.search(r"Integrated loudness:\s*\n\s*I:\s*([-\d.]+)", r.stderr)
    return float(m.group(1)) if m else None


# ----------------------------------------------------------------------
# init: pull the recording into a self-contained project folder
# ----------------------------------------------------------------------

def cmd_init(outdir: str, master_path: str):
    """Copy the recorded master.mp4 into <outdir>/source.mp4 and report its duration
    so Claude can author transcript.md segments against real scene boundaries."""
    _require_ffmpeg()
    out = Path(outdir).expanduser().resolve()
    src = Path(master_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"recording not found: {src}")

    workdir = out / "work"
    out.mkdir(parents=True, exist_ok=True)
    workdir.mkdir(exist_ok=True)

    source_copy = out / "source.mp4"
    shutil.copy2(str(src), str(source_copy))

    dur = round(ffprobe_duration(source_copy), 2)
    template = (
        "# transcript — video narration (one block per scene)\n\n"
        "## [001] 00:00:00,000 --> "
        f"{sec_to_srt(dur)}\n"
        "original: <краткий текст озвучки этой сцены>\n"
        "edited:   <тот же текст; правь здесь>\n"
    )
    print(json.dumps({
        "outdir": str(out),
        "source": str(source_copy),
        "video_duration_s": dur,
        "next": "Напиши transcript.md: по блоку на сцену, тайминги из ffprobe.",
        "transcript_template": template,
    }, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------
# dryrun: overflow check (read-only)
# ----------------------------------------------------------------------

def cmd_dryrun(outdir: str):
    out = Path(outdir).resolve()
    segs = parse_transcript_md(out / "transcript.md")

    warnings = []
    for s in segs:
        slot = s["end"] - s["start"]
        lo, le = len(s["orig"]), len(s["text"])
        # Empty original but non-empty edited text = full overflow (not ratio 1.0).
        if lo == 0 and le > 0:
            ratio = 5.0
        elif lo == 0:
            ratio = 1.0
        else:
            ratio = le / lo
        est = slot * ratio
        delta_pct = (est - slot) / slot * 100 if slot > 0 else 0
        if abs(delta_pct) > 30:
            warnings.append({
                "idx": s["idx"], "slot": round(slot, 2),
                "est_dur": round(est, 2), "delta_pct": round(delta_pct, 1),
                "orig_chars": lo, "edit_chars": le,
                "text_preview": s["text"][:80] + ("…" if len(s["text"])>80 else ""),
            })
    print(json.dumps({"warnings": warnings}, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------
# synth-tts: cloned-voice narration via ElevenLabs IVC (posegment)
# ----------------------------------------------------------------------

def cmd_synth_tts(outdir: str, mode: str = "posegment"):
    """Per-segment synthesis: one ElevenLabs request per segment. Fast, fully
    cacheable, 100% precise slicing (each segment's mp3 = exactly that text).

    Without an ElevenLabs key the narration text is written to work/narration.txt
    and the command returns cleanly — the recording can be voiced later."""
    if mode != "posegment":
        raise ValueError(f"Unknown synth mode: {mode!r}. Only 'posegment' is supported.")

    out = Path(outdir).resolve()
    workdir = out / "work"
    segs_dir = workdir / "segments"
    workdir.mkdir(exist_ok=True)

    segs = parse_transcript_md(out / "transcript.md")
    dropped = [s["idx"] for s in segs if not s["text"].strip()]
    segs = [s for s in segs if s["text"].strip()]
    if dropped:
        print(f"[!] WARNING: {len(dropped)} segments have empty text; skipped: {dropped}")
    if not segs:
        raise RuntimeError("No segments with text to synthesize. Check transcript.md.")

    key = _try_env_key("ELEVENLABS_API_KEY")
    if key is None:
        # No key -> save narration text for later voicing instead of failing.
        narration = workdir / "narration.txt"
        lines = [f"[{s['idx']:03d}] {s['text']}" for s in segs]
        narration.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": "no_api_key",
            "narration_saved": str(narration),
            "segments": len(segs),
            "hint": "Добавь ELEVENLABS_API_KEY в окружение или ~/.claude/.env и "
                    "перезапусти synth-tts, чтобы озвучить клонированным голосом.",
        }, ensure_ascii=False, indent=2))
        return

    if not VOICE_REF.exists():
        raise FileNotFoundError(
            f"voice reference not found: {VOICE_REF}. Положи 30-60 сек WAV своего "
            f"голоса в {REFS_DIR}/voice-ref.wav (см. refs/README.md).")

    segs_dir.mkdir(exist_ok=True)
    from elevenlabs.client import ElevenLabs
    client = ElevenLabs(api_key=key)

    vid_file = segs_dir / "voice_id.txt"
    voice_id = _ensure_ivc_voice(client, vid_file)
    _synth_posegment(client, voice_id, segs, segs_dir)


def _ensure_ivc_voice(client, vid_file: Path) -> str:
    """Return a usable ElevenLabs voice_id. Reuse cached one if still alive; else clone."""
    voice_id = None
    if vid_file.exists():
        cand = vid_file.read_text().strip()
        try:
            client.voices.get(cand)
            voice_id = cand
        except Exception:
            voice_id = None
            vid_file.unlink(missing_ok=True)
    if voice_id is None:
        with open(VOICE_REF, "rb") as f:
            voice = client.voices.ivc.create(
                name="onbo_kb_video_session",
                description="Session-scoped IVC for /kb-video",
                files=[f],
            )
        voice_id = voice.voice_id
        vid_file.write_text(voice_id)
    return voice_id


def _synth_posegment(client, voice_id, segs, segs_dir):
    """One request per segment. Cacheable; voice may drift slightly between segments."""
    manifest = []
    for s in segs:
        mp3 = segs_dir / f"{s['idx']:04d}.mp3"
        if mp3.exists() and mp3.stat().st_size > 1000:
            manifest.append({**s, "file": str(mp3)})
            continue
        with client.text_to_speech.with_raw_response.convert(
            voice_id=voice_id,
            text=s["text"],
            model_id="eleven_v3",
            output_format="mp3_44100_128",
            voice_settings={"stability": 0.7, "similarity_boost": 0.85, "style": 0.0},
        ) as resp:
            with open(mp3, "wb") as f:
                for chunk in resp.data:
                    f.write(chunk)
        manifest.append({**s, "file": str(mp3)})

    (segs_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"[+] synthesized {len(manifest)} segments (posegment) -> {segs_dir}")


# ----------------------------------------------------------------------
# build: stretch video, build voice track, mix BGM, mux -> final.mp4
# ----------------------------------------------------------------------

def cmd_build(outdir: str, bgm_filename: str = "none",
              duck_lu: float = DEFAULT_DUCK, speed: float = DEFAULT_SPEED):
    _require_ffmpeg()
    bgm_filename = "none" if bgm_filename is None else str(bgm_filename)

    out = Path(outdir).resolve()
    workdir = out / "work"
    segs_dir = workdir / "segments"
    source = out / "source.mp4"
    manifest_path = segs_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"manifest.json not found at {manifest_path}. "
            f"Run `synth-tts {outdir}` (with an ElevenLabs key) before `build`.")
    if not source.exists():
        raise FileNotFoundError(
            f"source.mp4 not found at {source}. Run `init` first.")
    manifest = json.loads(manifest_path.read_text())
    if not manifest:
        raise RuntimeError(f"manifest.json is empty ({manifest_path}). No segments to build.")

    build_ok = False
    try:
        _cmd_build_core(out, workdir, segs_dir, source, manifest,
                        bgm_filename, duck_lu, speed)
        build_ok = True
    finally:
        # Always try to release the session IVC voice, even on a failed build.
        _cleanup_ivc_voice(segs_dir, fail_silently=not build_ok)


def _cleanup_ivc_voice(segs_dir: Path, fail_silently: bool = False):
    key = _try_env_key("ELEVENLABS_API_KEY")
    vid_file = segs_dir / "voice_id.txt"
    if key is None or not vid_file.exists():
        return
    voice_id = vid_file.read_text().strip()
    r = subprocess.run(
        ["curl","-s","-w","%{http_code}","-o","/dev/null",
         "-X","DELETE","-H",f"xi-api-key: {key}",
         f"https://api.elevenlabs.io/v1/voices/{voice_id}"],
        capture_output=True, text=True)
    code = r.stdout.strip()
    if code == "200":
        vid_file.unlink()
    elif not fail_silently:
        print(f"[!] WARNING: IVC voice cleanup returned HTTP {code}; "
              f"voice_id kept at {vid_file}")


def _cmd_build_core(out, workdir, segs_dir, source, manifest,
                    bgm_filename, duck_lu, speed):
    import numpy as np
    import soundfile as sf

    # Decode TTS mp3s to wav, measure durations
    for s in manifest:
        wav = Path(s["file"]).with_suffix(".wav")
        if not wav.exists():
            subprocess.run(
                ["ffmpeg","-y","-i",s["file"],"-ar",str(SR),"-ac","1",str(wav)],
                capture_output=True, check=True)
        s["wav"] = str(wav)
        info = sf.info(str(wav))
        s["tts_dur"] = info.frames / info.samplerate

    # Plan freeze-frame insertions where narration overflows its slot+gap.
    insertions = []
    accum = 0.0
    for i, s in enumerate(manifest):
        if i + 1 < len(manifest):
            nxt = manifest[i+1]["start"]
            gap = nxt - s["end"]
            slot = s["end"] - s["start"]
            shortfall = (s["tts_dur"] + MIN_GAP) - (slot + gap)
            if shortfall >= MIN_INSERT:
                insertions.append({"at_orig_time": nxt, "duration": round(shortfall, 2)})
                accum += round(shortfall, 2)

    meta = ffprobe_video_meta(source)
    fps = meta["fps"]
    vid_total = ffprobe_duration(source)

    # Build video fragments with tpad (freeze-frame) at each insertion point.
    vfrags = workdir / "vfrags"
    if vfrags.exists():
        shutil.rmtree(vfrags)
    vfrags.mkdir()

    cuts = [0.0] + [ins["at_orig_time"] for ins in insertions] + [vid_total]
    frags = []
    for j in range(len(cuts) - 1):
        a, b = cuts[j], cuts[j+1]
        frag = vfrags / f"f{j:02d}.mp4"
        vf_parts = []
        if j < len(insertions):
            pad_dur = insertions[j]["duration"]
            vf_parts.append(f"tpad=stop_mode=clone:stop_duration={pad_dur:.3f}")
        cmd = ["ffmpeg","-y","-ss",f"{a:.3f}","-to",f"{b:.3f}","-i",str(source)]
        if vf_parts:
            cmd += ["-vf", ",".join(vf_parts)]
        cmd += ["-c:v","libx264","-preset","slow","-crf","16","-pix_fmt","yuv420p",
                "-r",f"{fps:.4f}","-an",str(frag)]
        subprocess.run(cmd, capture_output=True, check=True)
        frags.append(frag)

    # Concat via demuxer + copy
    list_file = vfrags / "list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in frags))
    video_stretched = workdir / "video_stretched.mp4"
    subprocess.run(
        ["ffmpeg","-y","-f","concat","-safe","0","-i",str(list_file),
         "-c:v","copy",str(video_stretched)],
        capture_output=True, check=True)

    vstr = ffprobe_duration(video_stretched)
    if abs(vstr - (vid_total + accum)) > 0.5:
        # Fallback to filter concat (re-encode)
        n = len(frags); inputs = []
        for p in frags:
            inputs.extend(["-i", str(p)])
        filt = "".join(f"[{k}:v]" for k in range(n)) + f"concat=n={n}:v=1[outv]"
        subprocess.run(
            ["ffmpeg","-y",*inputs,"-filter_complex",filt,"-map","[outv]",
             "-c:v","libx264","-preset","slow","-crf","16","-pix_fmt","yuv420p",
             "-r",f"{fps:.4f}",str(video_stretched)],
            capture_output=True, check=True)
        vstr = ffprobe_duration(video_stretched)

    # Build voice track at the new (stretched) timestamps.
    def new_start_for(t: float) -> float:
        shift = 0.0
        for ins in insertions:
            if ins["at_orig_time"] <= t:
                shift += ins["duration"]
        return t + shift

    total_samples = int((vstr + 1.0) * SR)
    voice = np.zeros(total_samples, dtype=np.float32)
    for s in manifest:
        audio, _ = sf.read(s["wav"])
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        start_sec = new_start_for(s["start"])
        i0 = int(start_sec * SR); i1 = min(i0 + len(audio), total_samples)
        voice[i0:i1] += audio[:i1-i0].astype(np.float32)

    peak = float(np.max(np.abs(voice)))
    if peak > 0.01:
        voice = voice / peak * 0.95
    voice_only = workdir / "voice_only.wav"
    sf.write(str(voice_only), voice, SR)

    # Final audio: voice-only, or voice + ducked/looped BGM.
    final_audio = workdir / "mixed_audio.wav"
    if bgm_filename and bgm_filename.lower() != "none":
        bgm_src = BGM_DIR / bgm_filename
        if not bgm_src.exists():
            raise FileNotFoundError(f"BGM not found: {bgm_src}")

        voice_lufs = measure_lufs(voice_only)
        if voice_lufs is None:
            voice_lufs = -18.0
            print(f"[!] WARNING: could not measure voice LUFS, falling back to {voice_lufs}")
        target_lufs = voice_lufs - duck_lu

        bgm_prepared = _prepare_bgm(bgm_src, vstr, workdir)
        bgm_track = workdir / "bgm_track.wav"
        subprocess.run(
            ["ffmpeg","-y","-i",str(bgm_prepared),"-t",f"{vstr:.3f}",
             "-ar",str(SR),"-ac","1",
             "-af", (f"loudnorm=I={target_lufs:.2f}:TP=-1:LRA=11,"
                     f"afade=t=in:st=0:d={FADE_IN},"
                     f"afade=t=out:st={vstr-FADE_OUT:.3f}:d={FADE_OUT}"),
             str(bgm_track)],
            capture_output=True, check=True)

        bgm, _ = sf.read(bgm_track)
        if bgm.ndim > 1:
            bgm = bgm.mean(axis=1)
        if len(bgm) < len(voice):
            bgm = np.pad(bgm, (0, len(voice)-len(bgm)))
        else:
            bgm = bgm[:len(voice)]

        mixed = voice + bgm.astype(np.float32)
        peak = float(np.max(np.abs(mixed)))
        if peak > 0.99:
            mixed = mixed / peak * 0.99
        sf.write(str(final_audio), mixed, SR)
    else:
        shutil.copy2(voice_only, final_audio)

    # Mux (H.264 CRF 18, scale=1440:-2, 30fps, faststart). speed defaults to 1.0
    # (no change); pass --speed to time-scale video (setpts) + audio (atempo).
    out_mp4 = out / "final.mp4"
    subprocess.run(
        ["ffmpeg","-y","-i",str(video_stretched),"-i",str(final_audio),
         "-filter_complex",f"[0:v]scale=1440:-2,fps=30,setpts=PTS/{speed}[v];[1:a]atempo={speed}[a]",
         "-map","[v]","-map","[a]",
         "-c:v","libx264","-preset","slow","-crf","18",
         "-profile:v","high","-level","4.0","-pix_fmt","yuv420p",
         "-movflags","+faststart",
         "-c:a","aac","-b:a","128k",
         "-shortest",str(out_mp4)],
        capture_output=True, check=True)

    cwd = Path.cwd()
    try:
        display_mp4 = str(out_mp4.relative_to(cwd))
    except ValueError:
        display_mp4 = str(out_mp4)
    print(json.dumps({
        "final_video": display_mp4,
        "final_duration_s": round(ffprobe_duration(out_mp4), 2),
        "insertions": insertions,
        "bgm": bgm_filename,
        "duck_lu": duck_lu,
        "speed": speed,
    }, ensure_ascii=False, indent=2))


def _prepare_bgm(bgm_src: Path, needed_duration: float, workdir: Path) -> Path:
    """Loop BGM with a 2s crossfade until it's at least `needed_duration` seconds long."""
    import numpy as np, soundfile as sf
    src_dur = ffprobe_duration(bgm_src)
    if src_dur >= needed_duration:
        return bgm_src

    base = workdir / "bgm_base.wav"
    subprocess.run(
        ["ffmpeg","-y","-i",str(bgm_src),"-ar",str(SR),"-ac","1",str(base)],
        capture_output=True, check=True)
    audio, _ = sf.read(base)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)

    xf = int(BGM_LOOP_CROSSFADE * SR)
    if xf * 2 >= len(audio):
        xf = max(1, len(audio) // 4)

    result = audio.copy()
    while len(result) / SR < needed_duration:
        head = result[:-xf]
        tail = result[-xf:]
        fade_out = np.linspace(1.0, 0.0, xf, dtype=np.float32)
        fade_in = np.linspace(0.0, 1.0, xf, dtype=np.float32)
        mixed = tail * fade_out + audio[:xf] * fade_in
        result = np.concatenate([head, mixed, audio[xf:]])

    out = workdir / "bgm_looped.wav"
    sf.write(str(out), result, SR)
    return out


# ----------------------------------------------------------------------
# cleanup / list-bgm
# ----------------------------------------------------------------------

def cmd_cleanup(outdir: str):
    """Remove heavy, easily-recreatable files (work/vfrags, video_stretched.mp4,
    decoded segment WAVs). Keep segments/*.mp3, voice_only.wav, mixed_audio.wav so
    the project stays editable: tweak transcript.md and rerun `build`."""
    out = Path(outdir).resolve()
    workdir = out / "work"
    if not workdir.exists():
        print(f"[+] nothing to clean at {workdir}")
        return

    cwd = Path.cwd()
    def show(p: Path) -> str:
        try: return str(p.relative_to(cwd))
        except ValueError: return str(p)

    removed = []
    vfrags = workdir / "vfrags"
    if vfrags.exists():
        shutil.rmtree(vfrags)
        removed.append(show(vfrags))
    stretched = workdir / "video_stretched.mp4"
    if stretched.exists():
        stretched.unlink()
        removed.append(show(stretched))

    segs_dir = workdir / "segments"
    wav_count = 0
    if segs_dir.exists():
        for wav in segs_dir.glob("*.wav"):
            wav.unlink()
            wav_count += 1
    if wav_count:
        removed.append(f"{wav_count} segment WAVs")

    if removed:
        print(f"[+] removed: {', '.join(removed)}")
    else:
        print(f"[+] nothing to remove (vfrags/, video_stretched.mp4, segment WAVs already gone)")
    print(f"[i] kept: segments/*.mp3, voice_only.wav, mixed_audio.wav (project remains editable)")


def cmd_list_bgm():
    tracks = sorted(BGM_DIR.glob("*.mp3")) + sorted(BGM_DIR.glob("*.wav"))
    print(json.dumps({"bgm": [t.name for t in tracks]}, indent=2))


# ----------------------------------------------------------------------

def _need_arg(cmd: str, position: int, description: str):
    if len(sys.argv) <= position:
        print(f"error: `{cmd}` requires {description} as argument {position}", file=sys.stderr)
        print(__doc__)
        sys.exit(2)
    return sys.argv[position]


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    cmd = sys.argv[1]
    if cmd == "init":
        cmd_init(_need_arg(cmd, 2, "path to outdir"),
                 _need_arg(cmd, 3, "path to recorded master.mp4"))
    elif cmd == "dryrun":
        cmd_dryrun(_need_arg(cmd, 2, "path to outdir"))
    elif cmd == "synth-tts":
        outdir = _need_arg(cmd, 2, "path to outdir")
        mode = "posegment"
        for i, a in enumerate(sys.argv):
            if a == "--mode" and i + 1 < len(sys.argv):
                mode = sys.argv[i + 1]
        cmd_synth_tts(outdir, mode=mode)
    elif cmd == "build":
        outdir = _need_arg(cmd, 2, "path to outdir")
        # Positional BGM arg only if it's not a flag; default "none" (voice only).
        bgm = "none"
        if len(sys.argv) > 3 and not sys.argv[3].startswith("--"):
            bgm = sys.argv[3]
        duck = DEFAULT_DUCK
        speed = DEFAULT_SPEED
        for i, a in enumerate(sys.argv):
            if a == "--duck" and i+1 < len(sys.argv):
                duck = float(sys.argv[i+1])
            if a == "--speed" and i+1 < len(sys.argv):
                speed = float(sys.argv[i+1])
        cmd_build(outdir, bgm, duck, speed)
    elif cmd == "cleanup":
        cmd_cleanup(_need_arg(cmd, 2, "path to outdir"))
    elif cmd == "list-bgm":
        cmd_list_bgm()
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
