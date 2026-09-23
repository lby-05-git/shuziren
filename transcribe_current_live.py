from pathlib import Path

from pipeline import transcribe


audio = Path("/root/autodl-tmp/projects/DigitalHuman/outputs/current_live_first60.wav")
print(transcribe(audio, lambda message: print(message, flush=True)), flush=True)
