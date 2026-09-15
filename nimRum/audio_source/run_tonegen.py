#!/usr/bin/env python3
"""
runNimRumToneGen — Entry point for the tone generator audio source.

Generates test tones and sends them as AudioSource UDP packets to nimRumTx.
"""

import argparse
import signal
import sys
import time

from nimRum.tonegen.nimRumToneGenSource import ToneGenSource


def main() -> None:
    """Parse arguments and run the tone generator."""
    parser = argparse.ArgumentParser(
        description="nimRum Tone Generator AudioSource",
    )
    parser.add_argument(
        "--mode",
        choices=["chirp", "chirp_loop", "sinus", "spikes"],
        default="sinus",
        help="Signal mode (default: sinus)",
    )
    parser.add_argument(
        "--freq",
        type=float,
        default=1000.0,
        help="Frequency in Hz for sinus/spikes mode (default: 1000)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=60.0,
        help="Duration in seconds for chirp mode (default: 60)",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        default=254,
        help="AudioSource ID (default: 254)",
    )
    parser.add_argument(
        "--target-host",
        default="127.0.0.1",
        help="Target host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--target-port",
        type=int,
        default=53473,
        help="Target UDP port (default: 53473)",
    )
    parser.add_argument(
        "--volume",
        type=float,
        default=1.0,
        help="Volume fraction 0.0-1.0 (default: 1.0)",
    )
    parser.add_argument(
        "--channels",
        type=int,
        choices=[1, 2],
        default=1,
        help="Number of output channels (default: 1)",
    )

    args = parser.parse_args()

    source = ToneGenSource(
        mode=args.mode,
        freq=args.freq,
        duration_s=args.duration,
        volume=args.volume,
        sample_rate=48000,
        num_channels=args.channels,
        source_id=args.source_id,
        source_name="tonegen",
        target_host=args.target_host,
        target_port=args.target_port,
    )

    # Handle Ctrl-C gracefully
    def _signal_handler(signum: int, frame: object) -> None:
        print("\nStopping tone generator...")
        source.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    print(
        f"ToneGen: mode={args.mode} freq={args.freq}Hz "
        f"vol={args.volume:.0%} -> {args.target_host}:{args.target_port}"
    )
    source.start()

    # Wait until source finishes (one-shot modes) or user interrupts
    while source.is_running():
        time.sleep(0.1)

    print("Done.")


if __name__ == "__main__":
    main()
