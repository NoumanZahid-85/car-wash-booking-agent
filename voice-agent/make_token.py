"""Mint a LiveKit access token so a client can join a room and talk to the
Sparkle Car Wash voice agent.

The worker registers with no agent name, so LiveKit auto-dispatches it to every
NEW room. Because dispatch only fires on room creation, a unique room name is
generated on each run (override with --room for an existing room).

Usage:
    .\\venv\\Scripts\\python.exe make_token.py
    .\\venv\\Scripts\\python.exe make_token.py --ttl-seconds 3600
    .\\venv\\Scripts\\python.exe make_token.py --room my-room --identity ahmed
"""
import argparse
import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv

if os.path.exists("../.env"):
    load_dotenv("../.env")
else:
    load_dotenv()

from livekit.api import AccessToken, VideoGrants

LIVEKIT_URL = os.environ["LIVEKIT_URL"].rstrip("/")
API_KEY = os.environ["LIVEKIT_API_KEY"]
API_SECRET = os.environ["LIVEKIT_API_SECRET"]


def main():
    parser = argparse.ArgumentParser(description="Mint a LiveKit access token for the voice agent")
    parser.add_argument("--room", default=None, help="Room to join (default: a fresh unique room, required for auto-dispatch)")
    parser.add_argument("--identity", default=None, help="Client identity (default: sparkle-client-<random>)")
    parser.add_argument("--ttl-seconds", type=int, default=7200, help="Token lifetime in seconds (default: 7200, max 86400)")
    args = parser.parse_args()

    ttl = min(args.ttl_seconds, 86400)
    room = args.room or f"sparkle-{secrets.token_hex(3)}"
    identity = args.identity or f"sparkle-client-{secrets.token_hex(4)}"

    token = (
        AccessToken(API_KEY, API_SECRET)
        .with_identity(identity)
        .with_ttl(timedelta(seconds=ttl))
        .with_grants(VideoGrants(room_join=True, room=room))
    )
    jwt = token.to_jwt()

    print("=" * 70)
    print("SPARKLE CAR WASH - VOICE AGENT TEST TOKEN")
    print("=" * 70)
    print()
    print(f"Room:      {room}")
    print(f"Identity:  {identity}")
    print(f"Lifetime:  {ttl} seconds ({ttl / 3600:.1f}h)")
    print()
    print("Test in the LiveKit Agents Playground (Manual connect):")
    print("  1) Open  https://agents-playground.livekit.io/")
    print("  2) Choose the 'Manual' tab (bottom of the connect card)")
    print("  3) URL:   " + LIVEKIT_URL)
    print("  4) Token: paste the raw token below")
    print("  5) Click Connect, allow microphone access, then talk to the agent.")
    print()
    print("Raw token:")
    print(jwt)


if __name__ == "__main__":
    main()
