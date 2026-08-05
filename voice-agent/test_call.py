import asyncio
import os
import secrets
import sys
import time
import wave
from datetime import timedelta

from dotenv import load_dotenv

if os.path.exists("../.env"):
    load_dotenv("../.env")
else:
    load_dotenv()

from livekit import rtc
from livekit.api import AccessToken, VideoGrants

URL = os.environ["LIVEKIT_URL"].rstrip("/")
ROOM = sys.argv[1] if len(sys.argv) > 1 else f"sparkle-test-{secrets.token_hex(3)}"
WAV = r"C:\Users\HP\AppData\Local\Temp\opencode\test_utterance.wav"


def build_token() -> str:
    return (
        AccessToken(os.environ["LIVEKIT_API_KEY"], os.environ["LIVEKIT_API_SECRET"])
        .with_identity("e2e-tester")
        .with_ttl(timedelta(minutes=10))
        .with_grants(VideoGrants(room_join=True, room=ROOM))
        .to_jwt()
    )

async def main():
    wf = wave.open(WAV, "rb")
    sr, ch, spc = wf.getframerate(), wf.getnchannels(), wf.getnframes()
    pcm = wf.readframes(spc)
    wf.close()
    print(f"WAV: {sr}Hz {ch}ch {spc} samples", flush=True)

    room = rtc.Room()
    agent_audio = {"count": 0}
    participants = {}

    @room.on("participant_connected")
    def on_pc(p: rtc.RemoteParticipant):
        print(f"[evt] participant connected: {p.identity} kind={p.kind}", flush=True)
        participants[p.identity] = p

    @room.on("participant_disconnected")
    def on_pd(p: rtc.RemoteParticipant):
        print(f"[evt] participant disconnected: {p.identity}", flush=True)

    @room.on("track_subscribed")
    def on_ts(track: rtc.Track, pub: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        print(f"[evt] track subscribed from {participant.identity}: {track.kind}", flush=True)
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            stream = rtc.AudioStream(track)

            async def drain():
                async for _ in stream:
                    agent_audio["count"] += 1
                    if agent_audio["count"] <= 5 or agent_audio["count"] % 50 == 0:
                        print(f"[agent-audio] frames received: {agent_audio['count']}", flush=True)

            asyncio.get_event_loop().create_task(drain())

    token = build_token()
    await room.connect(URL, token)
    print(f"connected as {room.local_participant.identity}", flush=True)

    source = rtc.AudioSource(sample_rate=sr, num_channels=ch)
    track = rtc.LocalAudioTrack.create_audio_track("mic", source)
    opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
    await room.local_participant.publish_track(track, opts)
    print("mic published", flush=True)

    frame_len = sr // 100  # 10ms frames
    for off in range(0, len(pcm), frame_len * 2):
        chunk = pcm[off:off + frame_len * 2]
        if len(chunk) < frame_len * 2:
            chunk += b"\x00" * (frame_len * 2 - len(chunk))
        await source.capture_frame(
            rtc.AudioFrame(data=chunk, sample_rate=sr, num_channels=ch, samples_per_channel=frame_len)
        )
    print(f"pushed {spc} samples; waiting for agent to answer...", flush=True)

    deadline = time.time() + 45
    while time.time() < deadline and agent_audio["count"] < 20:
        await asyncio.sleep(1)

    print("=" * 50)
    print("RESULT")
    print("=" * 50)
    print("remote participants:", list(participants.keys()))
    print("agent audio frames received:", agent_audio["count"])
    await asyncio.sleep(2)
    await room.disconnect()


asyncio.run(main())
