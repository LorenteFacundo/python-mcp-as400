"""
Genera la narracion del video con Piper TTS (voz neural, 100% local)
y calcula la linea de tiempo que usan las animaciones de escenas.html.

Salida (en build/):
  narration.wav   audio completo del video
  timeline.json   inicio/duracion de cada escena y de cada frase
  timeline.js     lo mismo, como script para escenas.html
  subtitulos.srt  subtitulos para subir junto al video

Uso:
  pip install piper-tts
  python build_audio.py --voice /ruta/es_AR-daniela-high.onnx
"""

import argparse
import hashlib
import io
import json
import wave
from pathlib import Path

from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).parent

LEAD = 0.6    # silencio al comienzo de cada escena (s)
GAP = 0.45    # pausa entre frases (s)
TAIL = 1.0    # silencio al final de cada escena (s)


def synth(voice: PiperVoice, text: str, cache: Path, length_scale: float) -> bytes:
    """Sintetiza una frase y devuelve los frames PCM (con cache en disco)."""
    key = hashlib.sha1(f"{length_scale}|{text}".encode()).hexdigest()[:16]
    cached = cache / f"{key}.wav"
    if not cached.exists():
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            voice.synthesize_wav(text, wav, SynthesisConfig(length_scale=length_scale))
        cached.write_bytes(buf.getvalue())
    with wave.open(str(cached), "rb") as wav:
        return wav.readframes(wav.getnframes())


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voice", required=True, help="Modelo .onnx de Piper (ej: es_AR-daniela-high.onnx)")
    parser.add_argument("--length-scale", type=float, default=1.1, help="> 1 habla mas lento")
    parser.add_argument("--out", default=str(HERE / "build"))
    args = parser.parse_args()

    out = Path(args.out)
    cache = out / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    guion = json.loads((HERE / "guion.json").read_text(encoding="utf-8"))
    voice = PiperVoice.load(args.voice)
    rate = voice.config.sample_rate
    bytes_per_sec = rate * 2  # mono, 16 bits

    def silence(seconds: float) -> bytes:
        return b"\x00\x00" * int(round(seconds * rate))

    pcm = bytearray()
    scenes = []
    srt = []

    for scene in guion["scenes"]:
        start = len(pcm) / bytes_per_sec
        pcm += silence(scene.get("lead", LEAD))
        cues = []
        for i, cue in enumerate(scene["cues"]):
            if i:
                pcm += silence(cue.get("pause", GAP))
            audio = synth(voice, cue.get("say", cue["sub"]), cache, args.length_scale)
            cue_start = len(pcm) / bytes_per_sec
            pcm += audio
            cue_end = len(pcm) / bytes_per_sec
            entry = {"start": round(cue_start - start, 3), "end": round(cue_end - start, 3), "sub": cue["sub"]}
            if cue.get("hide"):  # el texto ya esta en pantalla: sin subtitulo quemado
                entry["hide"] = True
            cues.append(entry)
            srt.append(f"{len(srt) + 1}\n{srt_time(cue_start)} --> {srt_time(cue_end)}\n{cue['sub']}\n")
        pcm += silence(scene.get("tail", TAIL))
        dur = len(pcm) / bytes_per_sec - start
        scenes.append({"id": scene["id"], "start": round(start, 3), "dur": round(dur, 3), "cues": cues})
        print(f"{scene['id']:16s} {start:7.2f}s  +{dur:5.2f}s")

    total = len(pcm) / bytes_per_sec
    with wave.open(str(out / "narration.wav"), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(bytes(pcm))

    timeline = {"total": round(total, 3), "scenes": scenes}
    (out / "timeline.json").write_text(json.dumps(timeline, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "timeline.js").write_text(
        "window.TIMELINE = " + json.dumps(timeline, ensure_ascii=False) + ";\n", encoding="utf-8"
    )
    (out / "subtitulos.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"Total: {total:.1f}s -> {out}")


if __name__ == "__main__":
    main()
