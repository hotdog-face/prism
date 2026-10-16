"""Prism UI sound set synthesizer (numpy only).

Two directions:
  A "Glass"  - crystalline sine bells in an F major 9 palette (F A C E G), tiny airy halo.
  B "Breath" - warm, muted low-register tones wrapped in soft filtered air, more room.

Writes 44.1 kHz 16-bit mono WAVs with the theme's file names, plus a demo sequence.
"""
import sys, os, wave
import numpy as np

SR = 44100
rng = np.random.default_rng(7)

# F major 9 palette (equal temperament)
def hz(n):  # midi note -> Hz
    return 440.0 * 2 ** ((n - 69) / 12)
F4, A4, C5, E5, G5 = 65, 69, 72, 76, 79
F5, A5, C6, E6, G6, A6, C7 = 77, 81, 84, 88, 91, 93, 96
F3, C4 = 53, 60


def t_axis(dur):
    return np.arange(int(dur * SR)) / SR


def env(n, attack, tau, hold=0.0):
    """Raised-cosine attack then exponential decay (tau seconds)."""
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    a = 0.5 - 0.5 * np.cos(np.pi * a)
    d = np.exp(-np.clip(t - attack - hold, 0, None) / tau)
    r = min(int(0.012 * SR), n // 4)  # release so notes never end on a cut
    if r > 1:
        d[-r:] *= np.cos(np.linspace(0, np.pi / 2, r)) ** 2
    return a * d


def fft_filter(x, lo=None, hi=None, slope=2):
    """Smooth band shaping in the frequency domain (butterworth-like magnitude)."""
    n = len(x)
    N = 1 << int(np.ceil(np.log2(n * 2)))
    X = np.fft.rfft(x, N)
    f = np.fft.rfftfreq(N, 1 / SR) + 1e-9
    g = np.ones_like(f)
    if hi:
        g *= 1 / np.sqrt(1 + (f / hi) ** (2 * slope))
    if lo:
        g *= 1 / np.sqrt(1 + (lo / f) ** (2 * slope))
    return np.fft.irfft(X * g, N)[:n]


def bell(note, dur, amp=1.0, attack=0.004, tau=0.08, bright=0.25, detune=0.0, glide=0.0):
    """Glassy sine bell: fundamental + soft octave + faint inharmonic shimmer."""
    t = t_axis(dur)
    f0 = hz(note) * (1 + detune)
    # optional tiny upward glide into pitch (rounded onset)
    f = f0 * (1 - glide * np.exp(-t / 0.012))
    ph = 2 * np.pi * np.cumsum(f) / SR
    y = np.sin(ph)
    y += bright * 0.45 * np.sin(2 * ph) * np.exp(-t / (tau * 0.5))
    y += bright * 0.18 * np.sin(3.01 * ph) * np.exp(-t / (tau * 0.3))
    y += bright * 0.08 * np.sin(4.17 * ph) * np.exp(-t / (tau * 0.2))
    return amp * y * env(len(t), attack, tau)


def soft_tone(note, dur, amp=1.0, attack=0.008, tau=0.12, warmth=0.3):
    """Muted, rounded tone (sine + a little 2nd harmonic, lowpassed)."""
    t = t_axis(dur)
    ph = 2 * np.pi * hz(note) * t
    y = np.sin(ph) + warmth * 0.3 * np.sin(2 * ph) * np.exp(-t / (tau * 0.4))
    return amp * y * env(len(t), attack, tau)


def air(dur, lo, hi, amp=1.0, attack=0.01, tau=0.05, sweep=None):
    """Band-limited noise puff. sweep=(hi_start, hi_end) makes a filtered rise/fall."""
    n = int(dur * SR)
    x = rng.standard_normal(n)
    if sweep:
        # crossfade between two filtered copies for a cheap swept filter
        a = fft_filter(x, lo, sweep[0])
        b = fft_filter(x, lo, sweep[1])
        k = np.linspace(0, 1, n) ** 1.5
        y = a * (1 - k) + b * k
    else:
        y = fft_filter(x, lo, hi)
    y /= np.sqrt(np.mean(y ** 2)) + 1e-9
    return amp * y * env(n, attack, tau)


def place(buf, sig, at):
    i = int(at * SR)
    end = min(len(buf), i + len(sig))
    buf[i:end] += sig[: end - i]
    return buf


def room(x, size=0.35, mix=0.18, damp=5000, predelay=0.006):
    """Tiny synthetic room: decaying, lowpassed noise IR convolved in."""
    n = int(size * SR)
    t = np.arange(n) / SR
    ir = rng.standard_normal(n) * np.exp(-t / (size / 5.5))
    ir = fft_filter(ir, 200, damp)
    ir[: int(predelay * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2))
    N = 1 << int(np.ceil(np.log2(len(x) + n)))
    wet = np.fft.irfft(np.fft.rfft(x, N) * np.fft.rfft(ir, N), N)[: len(x)]
    return x * (1 - mix * 0.5) + wet * mix * 1.6


def finish(x, dur, peak_db):
    """Trim/pad to dur, remove DC + rumble, fade tail, normalize to a peak level."""
    n = int(dur * SR)
    x = np.pad(x, (0, max(0, n - len(x))))[:n]
    x = fft_filter(x, lo=120, slope=2)  # no DC, nothing the Deck speakers can't play
    x -= x.mean()
    fade = int(min(0.012, dur * 0.25) * SR)
    x[-fade:] *= np.cos(np.linspace(0, np.pi / 2, fade)) ** 2
    x[: 16] *= np.linspace(0, 1, 16)
    x *= 10 ** (peak_db / 20) / (np.abs(x).max() + 1e-12)
    return x


# Levels (dBFS peak). ES-DE plays nav sounds at SoundVolumeNavigation=70 (-3 dB);
# deck_cc plays cc_* with pw-play at full scale, so cc_* sit 3 dB lower to match.
# Old placeholder set peaked at -9..-12 dBFS; this set sits roughly 6 dB under it.
LEVEL = dict(scroll=-31, systembrowse=-21, cc_move=-24, quicksysselect=-20, select=-17, back=-19,
             favorite=-17, launch=-16, cc_open=-20, cc_close=-22, cc_choose=-20)
DUR = dict(scroll=0.035, systembrowse=0.07, quicksysselect=0.10, select=0.16, back=0.14, favorite=0.30,
           launch=0.62, cc_open=0.55, cc_close=0.45, cc_move=0.07, cc_choose=0.30)


# ---------------------------------------------------------------- A: Glass
def glass():
    S = {}
    d = DUR
    # scroll: a feather-light glass tick, almost subliminal
    x = bell(C7, d["scroll"], tau=0.007, attack=0.0015, bright=0.0)
    x += air(d["scroll"], 3000, 9000, amp=0.15, attack=0.001, tau=0.004)
    S["scroll"] = x
    # home-row move: one soft A5 droplet
    x = bell(A5, d["systembrowse"], tau=0.022, attack=0.003, bright=0.2, glide=0.015)
    x += air(d["systembrowse"], 2500, 8000, amp=0.06, attack=0.002, tau=0.01)
    S["systembrowse"] = room(x, 0.12, 0.10)
    # quick system select: brighter C6+E6 dyad
    x = bell(C6, d["quicksysselect"], amp=0.7, tau=0.03, bright=0.2)
    x = place(x, bell(E6, 0.08, amp=0.55, tau=0.028, bright=0.15), 0.018)
    S["quicksysselect"] = room(x, 0.15, 0.12)
    # select: rising F5 -> C6
    x = bell(F5, d["select"], amp=0.75, tau=0.04, bright=0.3)
    x = place(x, bell(C6, 0.13, amp=0.85, tau=0.05, bright=0.3), 0.045)
    x += air(d["select"], 1500, 7000, amp=0.05, attack=0.004, tau=0.03)
    S["select"] = room(x, 0.2, 0.15)
    # back: falling C6 -> F5, darker and softer
    x = bell(C6, d["back"], amp=0.6, tau=0.035, bright=0.1)
    x = place(x, bell(F5, 0.11, amp=0.75, tau=0.045, bright=0.1), 0.045)
    S["back"] = room(fft_filter(x, hi=3500), 0.2, 0.15)
    # favorite: quick sparkle arpeggio F5 A5 C6 E6 G6 landing with the star burst
    x = np.zeros(int(d["favorite"] * SR))
    for i, (nn, a) in enumerate([(F5, .55), (A5, .55), (C6, .6), (E6, .65), (G6, .7)]):
        place(x, bell(nn, 0.25, amp=a, tau=0.06 + 0.015 * i, bright=0.25), 0.028 * i)
    x += air(d["favorite"], 4000, 12000, amp=0.05, attack=0.06, tau=0.06)
    S["favorite"] = room(x, 0.35, 0.25)
    # launch: airy rise into a soft Fmaj9 bloom
    n = int(d["launch"] * SR)
    x = np.zeros(n)
    place(x, air(0.3, 300, None, amp=0.09, attack=0.22, tau=0.03, sweep=(900, 4500)), 0.0)
    for i, nn in enumerate([F4, C5, E5, G5, A5, C6]):
        place(x, bell(nn, 0.45, amp=0.42 - 0.03 * i, attack=0.012, tau=0.16, bright=0.18), 0.2 + 0.012 * i)
    S["launch"] = room(x, 0.5, 0.3)
    # cc_open: gentle upward arpeggio on a breath
    n = int(d["cc_open"] * SR)
    x = np.zeros(n)
    place(x, air(0.32, 400, None, amp=0.07, attack=0.18, tau=0.06, sweep=(1200, 4000)), 0.0)
    for i, nn in enumerate([F5, A5, C6, E6]):
        place(x, bell(nn, 0.35, amp=0.5, attack=0.006, tau=0.09, bright=0.2), 0.05 + 0.045 * i)
    S["cc_open"] = room(x, 0.45, 0.28)
    # cc_close: the mirror, descending and softer
    n = int(d["cc_close"] * SR)
    x = np.zeros(n)
    place(x, air(0.25, 400, None, amp=0.06, attack=0.03, tau=0.08, sweep=(4000, 1000)), 0.0)
    for i, nn in enumerate([E6, C6, A5, F5]):
        place(x, bell(nn, 0.3, amp=0.45, attack=0.006, tau=0.07, bright=0.12), 0.04 * i)
    S["cc_close"] = room(fft_filter(x, hi=5000), 0.4, 0.25)
    # cc_move: like systembrowse, a fifth up (E6) and shorter
    x = bell(E6, d["cc_move"], tau=0.018, attack=0.003, bright=0.15)
    S["cc_move"] = room(x, 0.1, 0.08)
    # cc_choose: rounded A5+E6 dyad confirm
    x = bell(A5, d["cc_choose"], amp=0.7, tau=0.06, bright=0.25)
    x = place(x, bell(E6, 0.25, amp=0.6, tau=0.07, bright=0.2), 0.03)
    S["cc_choose"] = room(x, 0.3, 0.2)
    return S


# ---------------------------------------------------------------- B: Breath
def breath():
    S = {}
    d = DUR
    S["scroll"] = air(d["scroll"], 1800, 5000, attack=0.002, tau=0.006) \
        + soft_tone(G6, d["scroll"], amp=0.35, attack=0.002, tau=0.006)
    x = soft_tone(C5, d["systembrowse"], tau=0.03, attack=0.006, warmth=0.5)
    x += air(d["systembrowse"], 800, 3500, amp=0.25, attack=0.004, tau=0.015)
    S["systembrowse"] = room(fft_filter(x, hi=2800), 0.18, 0.18)
    x = soft_tone(E5, d["quicksysselect"], amp=0.7, tau=0.04, warmth=0.4)
    x = place(x, soft_tone(A5, 0.08, amp=0.5, tau=0.035), 0.02)
    x += air(d["quicksysselect"], 900, 4000, amp=0.2, attack=0.005, tau=0.02)
    S["quicksysselect"] = room(fft_filter(x, hi=3200), 0.2, 0.2)
    x = soft_tone(A4, d["select"], amp=0.7, tau=0.05, warmth=0.5)
    x = place(x, soft_tone(E5, 0.13, amp=0.8, tau=0.06, warmth=0.4), 0.05)
    x += air(d["select"], 700, 4500, amp=0.22, attack=0.01, tau=0.04)
    S["select"] = room(fft_filter(x, hi=3200), 0.3, 0.25)
    x = soft_tone(E5, d["back"], amp=0.6, tau=0.04, warmth=0.4)
    x = place(x, soft_tone(A4, 0.11, amp=0.75, tau=0.05, warmth=0.5), 0.045)
    x += air(d["back"], 500, 2500, amp=0.2, attack=0.004, tau=0.04, sweep=(3000, 1200))
    S["back"] = room(fft_filter(x, hi=2500), 0.3, 0.22)
    x = np.zeros(int(d["favorite"] * SR))
    for i, nn in enumerate([A4, C5, E5, G5, C6]):
        place(x, soft_tone(nn, 0.25, amp=0.5, tau=0.08 + 0.02 * i, warmth=0.3), 0.032 * i)
    x += air(d["favorite"], 2500, 9000, amp=0.12, attack=0.08, tau=0.07)
    S["favorite"] = room(fft_filter(x, hi=4500), 0.45, 0.32)
    n = int(d["launch"] * SR)
    x = np.zeros(n)
    place(x, air(0.34, 200, None, amp=0.35, attack=0.25, tau=0.04, sweep=(600, 4000)), 0.0)
    for i, nn in enumerate([F3, C4, A4, E5, G5]):
        place(x, soft_tone(nn, 0.42, amp=0.45 - 0.04 * i, attack=0.02, tau=0.18, warmth=0.4), 0.22 + 0.01 * i)
    S["launch"] = room(fft_filter(x, hi=4000), 0.6, 0.35)
    n = int(d["cc_open"] * SR)
    x = np.zeros(n)
    place(x, air(0.36, 300, None, amp=0.3, attack=0.2, tau=0.08, sweep=(800, 4500)), 0.0)
    for i, nn in enumerate([A4, C5, E5, A5]):
        place(x, soft_tone(nn, 0.32, amp=0.45, attack=0.01, tau=0.1, warmth=0.35), 0.08 + 0.05 * i)
    S["cc_open"] = room(fft_filter(x, hi=3800), 0.5, 0.32)
    n = int(d["cc_close"] * SR)
    x = np.zeros(n)
    place(x, air(0.3, 300, None, amp=0.28, attack=0.03, tau=0.09, sweep=(4000, 700)), 0.0)
    for i, nn in enumerate([A5, E5, C5, A4]):
        place(x, soft_tone(nn, 0.28, amp=0.4, attack=0.01, tau=0.08, warmth=0.35), 0.045 * i)
    S["cc_close"] = room(fft_filter(x, hi=3000), 0.45, 0.3)
    x = soft_tone(G5, d["cc_move"], tau=0.022, attack=0.005, warmth=0.4)
    x += air(d["cc_move"], 900, 4000, amp=0.22, attack=0.003, tau=0.012)
    S["cc_move"] = room(fft_filter(x, hi=3200), 0.15, 0.15)
    x = soft_tone(C5, d["cc_choose"], amp=0.7, tau=0.07, warmth=0.5)
    x = place(x, soft_tone(G5, 0.25, amp=0.6, tau=0.08, warmth=0.4), 0.035)
    x += air(d["cc_choose"], 700, 4000, amp=0.2, attack=0.01, tau=0.05)
    S["cc_choose"] = room(fft_filter(x, hi=3500), 0.35, 0.25)
    return S


def write_wav(path, x):
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm.tobytes())


# Demo: a typical navigation (time, sound). ES-DE nav sounds are at 70 % volume, cc at 100 %.
DEMO = [(0.3, "systembrowse"), (0.75, "systembrowse"), (1.35, "select")] + \
       [(2.0 + 0.11 * i, "scroll") for i in range(6)] + \
       [(3.0, "favorite"), (3.9, "back"), (4.5, "systembrowse"), (5.0, "launch"),
        (6.6, "cc_open"), (7.4, "cc_move"), (7.75, "cc_move"), (8.3, "cc_choose"),
        (9.2, "cc_open"), (9.9, "cc_close")]


def build(name, out):
    S = (glass if name == "glass" else breath)()
    os.makedirs(out, exist_ok=True)
    final = {}
    for k, x in S.items():
        final[k] = finish(x, DUR[k], LEVEL[k])
        write_wav(f"{out}/{k}.wav", final[k])
    demo = np.zeros(int(11.0 * SR))
    for at, k in DEMO:
        g = 1.0 if k.startswith("cc_") else 0.7
        place(demo, final[k] * g, at)
    write_wav(f"{out}/../demo_{name}.wav", demo)
    return final


if __name__ == "__main__":
    out = sys.argv[1]
    for name in ("glass", "breath"):
        f = build(name, f"{out}/{name}")
        for k, x in f.items():
            print(name, k, f"{len(x)/SR:.3f}s peak {20*np.log10(np.abs(x).max()):.1f} dBFS "
                  f"rms {20*np.log10(np.sqrt(np.mean(x**2))):.1f} dc {x.mean():.1e}")
