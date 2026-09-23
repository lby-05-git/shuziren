from pathlib import Path

from pipeline import animate_portrait, render_avatar


project = Path("/root/autodl-tmp/projects/DigitalHuman")
job = project / "outputs" / "realtime_acceptance"
job.mkdir(parents=True, exist_ok=True)
portrait = project / "outputs" / "v1_sessions" / "c80ffc615bca4d48b597b8fb" / "host.png"
audio = project / "outputs" / "live_voice_acceptance.wav"


def status(message: str) -> None:
    print(message, flush=True)


motion = job / "liveportrait" / "portrait_motion.mp4"
if not motion.exists():
    motion = animate_portrait(portrait, audio, job, "normal", status)
video = render_avatar(motion, audio, job, status)
print(video)
