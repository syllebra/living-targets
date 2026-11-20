#!/usr/bin/env python3
"""
ARCADE SFX KIT v2 — pure-stdlib synthesis + algorithmic reverb.

New in v2
  * real reverb: 8 parallel damped comb filters + 4 series allpasses
    (Schroeder / Freeverb topology), true stereo (23-sample tuning offset)
  * per-sound reverb send: tight clicks stay dry-ish, rewards/ambience go wet
  * 41 one-shots instead of 11, grouped by role
  * writes manifest.json (used by the audition page) + clicksprite

Everything is 48 kHz. Mono files stay mono when dry; anything with a
reverb tail is stereo (L/R decorrelated). Peak <= -1 dBFS, loudness
RMS-trimmed so the whole kit sits at a consistent level.

Usage:
  gen-arcade-clicks.py [outdir] [--no-encode] [--only substr]
"""

import json
import math
import os
import random
import struct
import subprocess
import sys
import wave

SR = 48000
PEAK_CEIL = 0.95
TARGET_LOUD = 0.42   # target for the loudest 50 ms window (~-7.5 dBFS)
random.seed(7)


# =========================================================== basic blocks
def n_samples(dur):
    return int(round(dur * SR))


def env_ad(n, attack=0.0015, tau=0.05, fade=0.004):
    """attack ramp, exponential decay, micro fade-out (kills end clicks)."""
    out = []
    fn = max(1, int(fade * SR))
    for i in range(n):
        t = i / SR
        a = min(1.0, t / attack) if attack > 0 else 1.0
        e = a * math.exp(-t / tau)
        if i > n - fn:
            e *= (n - i) / fn
        out.append(e)
    return out


def env_ar(n, attack=0.01, release=0.1, hold=0.0):
    """attack / hold / release envelope, for pads and risers."""
    out = []
    a_n = max(1, int(attack * SR))
    h_n = int(hold * SR)
    r_n = max(1, int(release * SR))
    for i in range(n):
        if i < a_n:
            e = i / a_n
        elif i < a_n + h_n:
            e = 1.0
        else:
            k = (i - a_n - h_n) / r_n
            e = max(0.0, 1.0 - k)
        out.append(e * e * (3 - 2 * e))      # smoothstep
    return out


def osc_sine(freq_fn, dur, phase=0.0):
    n = n_samples(dur)
    ph, out = phase, []
    for i in range(n):
        f = freq_fn(i / SR) if callable(freq_fn) else freq_fn
        ph += 2 * math.pi * f / SR
        out.append(math.sin(ph))
    return out


def osc_saw(freq_fn, dur, harmonics=14):
    n = n_samples(dur)
    ph, out = 0.0, []
    for i in range(n):
        f = freq_fn(i / SR) if callable(freq_fn) else freq_fn
        ph += 2 * math.pi * f / SR
        v = 0.0
        for k in range(1, harmonics + 1):
            if k * f > SR * 0.45:
                break
            v += math.sin(ph * k) / k
        out.append(v * 0.62)
    return out


def osc_square(freq_fn, dur, harmonics=9):
    n = n_samples(dur)
    ph, out = 0.0, []
    for i in range(n):
        f = freq_fn(i / SR) if callable(freq_fn) else freq_fn
        ph += 2 * math.pi * f / SR
        v = 0.0
        for k in range(1, harmonics * 2, 2):
            if k * f > SR * 0.45:
                break
            v += math.sin(ph * k) / k
        out.append(v * 0.8)
    return out


def osc_fm(fc_fn, ratio, idx_fn, dur):
    n = n_samples(dur)
    phc = phm = 0.0
    out = []
    for i in range(n):
        t = i / SR
        fc = fc_fn(t) if callable(fc_fn) else fc_fn
        idx = idx_fn(t) if callable(idx_fn) else idx_fn
        phm += 2 * math.pi * fc * ratio / SR
        phc += 2 * math.pi * fc / SR
        out.append(math.sin(phc + idx * math.sin(phm)))
    return out


def noise(dur):
    return [random.uniform(-1.0, 1.0) for _ in range(n_samples(dur))]


def lp(x, fc):
    a = 1.0 - math.exp(-2 * math.pi * fc / SR)
    y, out = 0.0, []
    for s in x:
        y += a * (s - y)
        out.append(y)
    return out


def hp(x, fc):
    low = lp(x, fc)
    return [a - b for a, b in zip(x, low)]


def bandpass(x, lo, hi):
    return lp(hp(x, lo), hi)


def lp_sweep(x, f0, f1):
    """time-varying one-pole lowpass, coefficient updated per block."""
    blk = 32
    n = len(x)
    y, out = 0.0, []
    for i in range(n):
        if i % blk == 0:
            t = i / n
            fc = f0 * (f1 / f0) ** t if f0 > 0 and f1 > 0 else f1
            a = 1.0 - math.exp(-2 * math.pi * min(fc, SR * 0.45) / SR)
        y += a * (x[i] - y)
        out.append(y)
    return out


def bp_sweep(x, f0, f1, width=2.0):
    low = lp_sweep(x, f0, f1)
    return [a - b for a, b in zip(low, lp_sweep(low, f0 * width, f1 * width))]


def mix(*layers):
    norm = [(l, 1.0) if not isinstance(l, tuple) else l for l in layers]
    n = max(len(l) for l, _ in norm)
    out = [0.0] * n
    for sig, g in norm:
        for i, v in enumerate(sig):
            out[i] += v * g
    return out


def apply_env(sig, e):
    return [s * e[i] for i, s in enumerate(sig)]


def crush(x, hold=3):
    out, cur = [], 0.0
    for i, v in enumerate(x):
        if i % hold == 0:
            cur = v
        out.append(cur)
    return out


def quantize(x, bits=8):
    q = 2 ** (bits - 1)
    return [round(v * q) / q for v in x]


def soft_clip(x, drive=1.0, ceiling=0.98):
    return [math.tanh(v * drive) / math.tanh(drive) * ceiling for v in x]


def fade_tail(x, ms=5.0):
    fn = int(ms * SR / 1000)
    for i in range(min(fn, len(x))):
        x[len(x) - 1 - i] *= i / fn
    return x


def compress(x, thresh_db=-12.0, ratio=4.0, attack=0.004, release=0.12):
    """feed-forward compressor on a one-pole envelope follower.

    Shortens the crest factor so sparse sounds (impacts, ambience) sit at the
    same perceived level as dense ones, without a limiter's distortion.
    """
    thr = 10 ** (thresh_db / 20.0)
    a_att = math.exp(-1.0 / (attack * SR))
    a_rel = math.exp(-1.0 / (release * SR))
    env = 0.0
    out = []
    for v in x:
        rect = v if v >= 0 else -v
        a = a_att if rect > env else a_rel
        env = a * env + (1 - a) * rect
        if env > thr and env > 1e-7:
            gain = (thr / env) ** (1.0 - 1.0 / ratio)
        else:
            gain = 1.0
        out.append(v * gain)
    return out


def max_win_rms(x, ms=50.0):
    """loudest short window = perceptual loudness of a percussive sound."""
    n = len(x)
    if n == 0:
        return 0.0
    w = min(max(1, int(ms * SR / 1000)), n)
    acc = sum(v * v for v in x[:w])
    best = acc                              # max energy over a w-sample window
    for i in range(w, n):
        acc += x[i] * x[i] - x[i - w] * x[i - w]
        if acc > best:
            best = acc
    return math.sqrt(best / w)


def rms(x):
    return math.sqrt(sum(v * v for v in x) / max(1, len(x)))


def peak(x):
    return max(abs(v) for v in x) if x else 0.0


def add_at(dst, src, offset):
    """paste src into dst at sample offset (extends dst if needed)."""
    end = offset + len(src)
    if len(dst) < end:
        dst.extend([0.0] * (end - len(dst)))
    for i, v in enumerate(src):
        dst[offset + i] += v
    return dst


def silence(dur):
    return [0.0] * n_samples(dur)


# ================================================================ reverb
# Freeverb tunings, scaled from 44.1 kHz to 48 kHz (x 1.0884)
COMBS = [1215, 1293, 1390, 1476, 1548, 1622, 1695, 1760]
APS = [605, 480, 371, 245]


def _comb_channel(inp, spread, room, damp):
    total = len(inp)
    acc = [0.0] * total
    for d in COMBS:
        ln = d + spread
        buf = [0.0] * ln
        idx = 0
        y = 0.0
        one_minus = 1.0 - damp
        for i in range(total):
            v = buf[idx]
            y = v * one_minus + y * damp          # damping lowpass
            buf[idx] = inp[i] + y * room
            idx += 1
            if idx >= ln:
                idx = 0
            acc[i] += v
    # series allpasses (diffusion)
    for d in APS:
        ln = d + spread
        buf = [0.0] * ln
        idx = 0
        for i in range(total):
            bo = buf[idx]
            v = acc[i]
            acc[i] = bo - v
            buf[idx] = v + bo * 0.5
            idx += 1
            if idx >= ln:
                idx = 0
    return acc


def reverb(x, wet=0.2, room=0.84, damp=0.28, tail=0.9, stereo=True,
           pre_delay=0.004):
    """dry + algorithmic reverb. Returns (L, R) or (mono, None) if wet==0."""
    if wet <= 0:
        return list(x), None

    dry = list(x)
    n = len(dry)
    tail_n = int(tail * SR)
    pd = int(pre_delay * SR)
    inp = [0.0] * pd + dry + [0.0] * tail_n
    total = len(inp)

    L = _comb_channel(inp, 0, room, damp)
    R = _comb_channel(inp, 23, room, damp) if stereo else L

    # match reverb energy to the dry signal so the wet/dry ratio is predictable
    ref = rms(dry) or 1.0
    for ch in ((L, R) if stereo else (L,)):
        r = rms(ch) or 1.0
        g = ref / r
        for i in range(len(ch)):
            ch[i] *= g

    # pad dry to the tail length, then mix
    dry_p = dry + [0.0] * (total - n)
    outL = [dry_p[i] + wet * L[i] for i in range(total)]
    outR = [dry_p[i] + wet * R[i] for i in range(total)] if stereo else None
    return outL, outR


# ======================================================== sound generators
# ---- clicks (short, mostly dry) -------------------------------------
def click_pop():
    d = 0.10
    body = osc_sine(lambda t: 380 + 1250 * math.exp(-t / 0.016), d)
    body = apply_env(body, env_ad(n_samples(d), 0.0012, 0.021))
    tick = apply_env(bandpass(noise(0.006), 3000, 12000),
                     env_ad(n_samples(0.006), 0.0002, 0.0012))
    return fade_tail(soft_clip(mix((body, 1.0), (tick, 0.42)), 1.5))


def click_tick():
    d = 0.045
    tick = apply_env(bandpass(noise(d), 3500, 14000),
                     env_ad(n_samples(d), 0.00015, 0.0055))
    ping = apply_env(osc_sine(lambda t: 5400 - 1800 * min(1, t / 0.012), d),
                     env_ad(n_samples(d), 0.0003, 0.006))
    return fade_tail(soft_clip(mix((tick, 1.0), (ping, 0.55)), 1.3))


def click_coin():
    d0, d1 = 0.058, 0.17
    def voice(f0, f1, detune):
        a = apply_env(osc_square(lambda t: f0 * detune, d0),
                      env_ad(n_samples(d0), 0.0008, 0.09))
        b = apply_env(osc_square(lambda t: f1 * detune, d1),
                      env_ad(n_samples(d1), 0.0008, 0.052))
        return a + b
    x = mix((voice(1046.5, 1568.0, 1.0), 1.0), (voice(1046.5, 1568.0, 1.006), 0.34))
    return fade_tail(soft_clip(x, 1.35))


def click_zap():
    d = 0.135
    s = apply_env(osc_saw(lambda t: 240 + 2350 * math.exp(-t / 0.042), d),
                  env_ad(n_samples(d), 0.001, 0.048))
    s = crush(s, 3)
    air = apply_env(bandpass(noise(d), 2000, 9000),
                    env_ad(n_samples(d), 0.0005, 0.012))
    return fade_tail(soft_clip(mix((s, 1.0), (air, 0.22)), 1.7))


def click_snap():
    d = 0.15
    body = apply_env(bandpass(noise(d), 700, 5200),
                     env_ad(n_samples(d), 0.0008, 0.017))
    sub = apply_env(osc_sine(lambda t: 62 + 150 * math.exp(-t / 0.018), d),
                    env_ad(n_samples(d), 0.002, 0.045))
    return fade_tail(soft_clip(mix((body, 1.0), (sub, 0.85)), 2.0))


def click_blip():
    d = 0.125
    x = osc_fm(lambda t: 660 + 200 * math.exp(-t / 0.03), 2.72,
               lambda t: 4.6 * math.exp(-t / 0.018), d)
    return fade_tail(soft_clip(apply_env(x, env_ad(n_samples(d), 0.001, 0.042)), 1.2))


def click_thud():
    d = 0.22
    sub = apply_env(osc_sine(lambda t: 48 + 105 * math.exp(-t / 0.035), d),
                    env_ad(n_samples(d), 0.0025, 0.062))
    tick = apply_env(bandpass(noise(0.01), 1200, 7000),
                     env_ad(n_samples(0.01), 0.0004, 0.0022))
    return fade_tail(soft_clip(mix((sub, 1.0), (tick, 0.5)), 1.8))


def click_chime():
    d = 0.6
    base = 660.0
    x = [0.0] * n_samples(d)
    for ratio, amp, tau in ((1.0, 1.0, 0.19), (2.76, .52, .13), (5.40, .3, .085),
                            (8.93, .17, .055), (13.3, .09, .035)):
        f = base * ratio
        if f > SR * 0.45:
            continue
        v = apply_env(osc_sine(f, d), env_ad(n_samples(d), 0.0006, tau))
        for i, s in enumerate(v):
            x[i] += s * amp
    strike = apply_env(bandpass(noise(0.008), 4000, 15000),
                       env_ad(n_samples(0.008), 0.0002, 0.0016))
    return fade_tail(soft_clip(mix((x, 1.0), (strike, 0.35)), 1.15), 8)


def click_glitch():
    d = 0.11
    s = osc_square(lambda t: 880 * (1.35 if t < 0.03 else 1.0), d)
    s = crush(s, 5)
    n = n_samples(d)
    win = n_samples(0.009)
    for k in range(4):
        start = int(k * win * 1.7)
        if k % 2:
            for i in range(start, min(start + win, n)):
                s[i] *= 0.18
    return fade_tail(soft_clip(apply_env(s, env_ad(n, 0.0008, 0.055)), 1.6))


def click_volley():
    seg = 0.055
    parts = []
    for f0 in (420, 540, 700):
        v = osc_sine(lambda t, f=f0: f * 0.35 + f * 2.2 * math.exp(-t / 0.013), seg)
        v = apply_env(v, env_ad(n_samples(seg), 0.001, 0.018))
        tick = apply_env(bandpass(noise(0.005), 3200, 13000),
                         env_ad(n_samples(0.005), 0.0002, 0.0011))
        parts.append(soft_clip(mix((v, 1.0), (tick, 0.4)), 1.4) + silence(0.012))
    x = [0.0]
    for p in parts:
        x = x + p
    return fade_tail(x)


def click_tock():
    """pendulum / wood block — very tight."""
    d = 0.08
    tone = apply_env(osc_sine(lambda t: 1650 - 300 * t / d, d),
                     env_ad(n_samples(d), 0.0004, 0.011))
    body = apply_env(bandpass(noise(d), 900, 6000),
                     env_ad(n_samples(d), 0.0003, 0.006))
    return fade_tail(soft_clip(mix((tone, 1.0), (body, 0.55)), 1.4))


def click_clack():
    """mechanical key: down-stroke + up-stroke 22 ms later."""
    down = apply_env(bandpass(noise(0.022), 1500, 9000),
                     env_ad(n_samples(0.022), 0.0002, 0.004))
    up = apply_env(bandpass(noise(0.018), 2200, 11000),
                   env_ad(n_samples(0.018), 0.0002, 0.003))
    x = silence(0.1)
    add_at(x, down, 0)
    add_at(x, up, n_samples(0.022))
    sub = apply_env(osc_sine(lambda t: 150 + 60 * math.exp(-t / 0.01), 0.05),
                    env_ad(n_samples(0.05), 0.001, 0.012))
    add_at(x, [v * 0.5 for v in sub], 0)
    return fade_tail(soft_clip(x, 1.6))


def click_marimba():
    """wooden mallet: sine + quickly decaying 4th partial."""
    d = 0.3
    f0 = 620.0
    x = [0.0] * n_samples(d)
    for ratio, amp, tau in ((1.0, 1.0, 0.085), (3.93, 0.34, 0.028), (9.2, 0.1, 0.012)):
        if f0 * ratio > SR * 0.45:
            continue
        v = apply_env(osc_sine(f0 * ratio, d), env_ad(n_samples(d), 0.0006, tau))
        for i, s in enumerate(v):
            x[i] += s * amp
    mallet = apply_env(bandpass(noise(0.006), 2000, 9000),
                       env_ad(n_samples(0.006), 0.0002, 0.0014))
    return fade_tail(soft_clip(mix((x, 1.0), (mallet, 0.3)), 1.2), 6)


def click_bubble():
    """rising water droplet (bloop)."""
    d = 0.13
    x = osc_sine(lambda t: 320 + 1150 * (t / d) ** 0.6, d)
    x = apply_env(x, env_ad(n_samples(d), 0.0015, 0.042))
    return fade_tail(soft_clip(x, 1.2))


def click_digi():
    """two-step digital confirm, bright and clean."""
    d0, d1 = 0.05, 0.09
    a = apply_env(osc_square(1318.5, d0), env_ad(n_samples(d0), 0.0006, 0.03))
    b = apply_env(osc_square(1975.5, d1), env_ad(n_samples(d1), 0.0006, 0.034))
    x = a + b
    x = quantize(x, 6)
    return fade_tail(soft_clip(x, 1.1))


def click_steel():
    """short metallic ping (inharmonic)."""
    d = 0.25
    x = [0.0] * n_samples(d)
    for ratio, amp, tau in ((1.0, 1.0, 0.075), (2.41, .6, .045), (3.86, .4, .03),
                            (6.13, .22, .018)):
        v = apply_env(osc_sine(1180 * ratio, d), env_ad(n_samples(d), 0.0003, tau))
        for i, s in enumerate(v):
            x[i] += s * amp
    return fade_tail(soft_clip(x, 1.3), 6)


# ---- UI (soft, small room) ------------------------------------------
def ui_soft_press():
    d = 0.14
    puff = apply_env(bandpass(noise(d), 400, 2600),
                     env_ad(n_samples(d), 0.0015, 0.02))
    thump = apply_env(osc_sine(lambda t: 120 + 90 * math.exp(-t / 0.02), d),
                      env_ad(n_samples(d), 0.002, 0.04))
    return fade_tail(soft_clip(mix((puff, 0.8), (thump, 1.0)), 1.3))


def ui_hover():
    d = 0.06
    x = apply_env(osc_sine(lambda t: 2100 + 500 * (t / d), d),
                  env_ad(n_samples(d), 0.0015, 0.014))
    return fade_tail(x)


def ui_toggle_on():
    d0, d1 = 0.06, 0.12
    a = apply_env(osc_sine(784, d0), env_ad(n_samples(d0), 0.001, 0.05))
    b = apply_env(osc_sine(1175, d1), env_ad(n_samples(d1), 0.001, 0.06))
    return fade_tail(soft_clip(a + b, 1.15))


def ui_toggle_off():
    d0, d1 = 0.06, 0.12
    a = apply_env(osc_sine(1175, d0), env_ad(n_samples(d0), 0.001, 0.05))
    b = apply_env(osc_sine(784, d1), env_ad(n_samples(d1), 0.001, 0.06))
    return fade_tail(soft_clip(a + b, 1.15))


def ui_confirm():
    x = silence(0.26)
    for i, f in enumerate((659.3, 987.8)):
        v = apply_env(osc_sine(f, 0.16), env_ad(n_samples(0.16), 0.0015, 0.06))
        add_at(x, v, n_samples(i * 0.075))
    return fade_tail(soft_clip(x, 1.1))


def ui_cancel():
    x = silence(0.26)
    for i, f in enumerate((523.3, 493.9)):
        v = apply_env(osc_square(f, 0.13), env_ad(n_samples(0.13), 0.0015, 0.05))
        v = lp(v, 2400)
        add_at(x, [s * 0.8 for s in v], n_samples(i * 0.08))
    return fade_tail(soft_clip(x, 1.2))


# ---- rewards ---------------------------------------------------------
def reward_powerup():
    x = silence(0.62)
    for i, f in enumerate((523.3, 659.3, 784.0, 1046.5)):
        d = 0.22
        v = apply_env(osc_square(f, d), env_ad(n_samples(d), 0.0008, 0.06))
        v = lp(v, 5200)
        add_at(x, [s * 0.7 for s in v], n_samples(i * 0.055))
    return fade_tail(soft_clip(x, 1.3))


def reward_combo():
    x = silence(0.5)
    for i, f in enumerate((880, 1108.7, 1318.5)):
        d = 0.28
        v = [0.0] * n_samples(d)
        for r, a, tau in ((1.0, 1, 0.06), (2.7, .4, .03)):
            s = apply_env(osc_sine(f * r, d), env_ad(n_samples(d), 0.0005, tau))
            for k in range(len(s)):
                v[k] += s[k] * a
        add_at(x, v, n_samples(i * 0.075))
    return fade_tail(soft_clip(x, 1.1))


def reward_sparkle():
    """shimmer of high partials — deliberately wet."""
    d = 0.75
    x = [0.0] * n_samples(d)
    freqs = [2350, 3130, 4180, 5270, 6640, 7950]
    for k, f in enumerate(freqs):
        if f > SR * 0.45:
            continue
        start = int(k * 0.028 * SR)
        seg = apply_env(osc_sine(f, 0.4),
                        env_ad(n_samples(0.4), 0.001, 0.13 - k * 0.014))
        add_at(x, [s * (0.9 - k * 0.1) for s in seg], start)
    air = apply_env(bandpass(noise(d), 5000, 16000), env_ad(n_samples(d), 0.01, 0.2))
    return fade_tail(soft_clip(mix((x, 1.0), (air, 0.12)), 1.05), 10)


def reward_jackpot():
    x = silence(1.0)
    notes = (523.3, 659.3, 784.0, 1046.5, 1318.5)
    for i, f in enumerate(notes):
        d = 0.5
        v = mix((apply_env(osc_square(f, d), env_ad(n_samples(d), 0.0008, 0.07)), 0.6),
                (apply_env(osc_sine(f * 2, d), env_ad(n_samples(d), 0.001, 0.05)), 0.5))
        v = lp(v, 6000)
        add_at(x, v, n_samples(i * 0.062))
    # final bell
    bell = apply_env(osc_sine(1568, 0.7), env_ad(n_samples(0.7), 0.001, 0.22))
    add_at(x, [s * 0.55 for s in bell], n_samples(0.33))
    return fade_tail(soft_clip(x, 1.2), 10)


def reward_coin_big():
    """the coin, but with a room around it."""
    x = click_coin()
    big = apply_env(osc_square(lambda t: 1568 * 2, 0.35),
                    env_ad(n_samples(0.35), 0.001, 0.1))
    add_at(x, [s * 0.28 for s in big], n_samples(0.1))
    return fade_tail(soft_clip(x, 1.2))


# ---- impacts ---------------------------------------------------------
def reward_arpeggio_up():
    """5-note major arpeggio on filtered squares — fast, bright, clean."""
    x = silence(0.8)
    for i, f in enumerate((523.3, 659.3, 784.0, 1046.5, 1318.5)):
        d = 0.30
        n = n_samples(d)
        v = mix((apply_env(osc_square(f, d), env_ad(n, 0.0008, 0.075)), 0.7),
                (apply_env(osc_sine(f * 2, d), env_ad(n, 0.001, 0.05)), 0.4))
        v = lp(v, 6500)
        add_at(x, v, n_samples(i * 0.048))
    return fade_tail(soft_clip(x, 1.2))


def reward_level_up():
    """three rising steps, detuned saws — the classic 'level up'."""
    x = silence(1.0)
    for i, (f, d, start) in enumerate(((392.0, 0.13, 0.0), (523.3, 0.13, 0.10),
                                       (783.99, 0.44, 0.21))):
        n = n_samples(d)
        v = mix((apply_env(osc_saw(f, d, 10), env_ar(n, 0.004, d * 0.7, 0.01)), 0.8),
                (apply_env(osc_saw(f * 1.007, d, 10), env_ar(n, 0.006, d * 0.7, 0.01)), 0.5))
        v = lp(v, 4200)
        add_at(x, v, n_samples(start))
    return fade_tail(soft_clip(x, 1.25), 8)


def reward_fanfare():
    """short triumphant I-IV-I fanfare; brass-ish filter swell."""
    x = silence(1.25)
    chords = (((523.3, 659.3, 783.99), 0.00, 0.22),
              ((587.3, 739.99, 880.0), 0.20, 0.22),
              ((523.3, 659.3, 783.99, 1046.5), 0.40, 0.58))
    for freqs, start, dur in chords:
        n = n_samples(dur)
        v = [0.0] * n
        for f in freqs:
            for det, amp in ((1.0, 1.0), (1.005, 0.55)):
                s = apply_env(osc_saw(f * det, dur, 9), env_ar(n, 0.010, dur * 0.6, 0.02))
                for k in range(n):
                    v[k] += s[k] * amp * 0.26
        v = lp_sweep(v, 1300, 5200)
        add_at(x, v, n_samples(start))
    return fade_tail(soft_clip(x, 1.3), 12)


def reward_gem():
    """tiny bright ping + rising blip — 'collect a gem'."""
    x = silence(0.4)
    ping = [0.0] * n_samples(0.3)
    for ratio, amp, tau in ((1.0, 1, .055), (2.98, .45, .028), (6.1, .18, .014)):
        v = apply_env(osc_sine(1760 * ratio, 0.3), env_ad(n_samples(0.3), 0.0003, tau))
        for i, sv in enumerate(v):
            ping[i] += sv * amp
    add_at(x, [s * 0.7 for s in ping], 0)
    blip = osc_sine(lambda t: 1200 + 1500 * (t / 0.14), 0.14)
    blip = apply_env(blip, env_ad(n_samples(0.14), 0.002, 0.035))
    add_at(x, [s * 0.5 for s in blip], n_samples(0.02))
    return fade_tail(soft_clip(x, 1.2), 6)


def reward_streak():
    """6 tiny ascending ticks — combo counter climbing."""
    x = silence(0.6)
    for i in range(6):
        f = 880 * (2 ** (i * 2 / 12))          # whole-tone ladder
        d = 0.07
        v = mix((apply_env(osc_sine(f, d), env_ad(n_samples(d), 0.0006, 0.022)), 1.0),
                (apply_env(bandpass(noise(0.004), 4000, 13000),
                           env_ad(n_samples(0.004), 0.0002, 0.0012)), 0.4))
        add_at(x, [s * 0.8 for s in v], n_samples(i * 0.055))
    return fade_tail(soft_clip(x, 1.3), 6)


def reward_unlock():
    """mechanical click, then a rising chord — a door opening."""
    x = silence(0.9)
    add_at(x, click_clack(), 0)
    for i, f in enumerate((523.3, 783.99, 1046.5)):
        d = 0.55
        n = n_samples(d)
        v = apply_env(osc_sine(f, d), env_ad(n, 0.004, 0.16))
        add_at(x, [s * 0.42 for s in v], n_samples(0.06 + i * 0.02))
    shimmer = apply_env(bandpass(noise(0.5), 5000, 15000),
                        env_ad(n_samples(0.5), 0.02, 0.14))
    add_at(x, [s * 0.12 for s in shimmer], n_samples(0.06))
    return fade_tail(soft_clip(x, 1.15), 10)


def reward_star_get():
    """fast twinkle arpeggio with a high sparkle trail."""
    x = silence(0.9)
    for i, f in enumerate((1046.5, 1318.5, 1568.0, 2093.0)):
        d = 0.35
        v = [0.0] * n_samples(d)
        for r, a, tau in ((1.0, 1, 0.07), (2.0, 0.35, 0.04)):
            sv = apply_env(osc_sine(f * r, d), env_ad(n_samples(d), 0.0005, tau))
            for k in range(len(sv)):
                v[k] += sv[k] * a
        add_at(x, [s * 0.55 for s in v], n_samples(i * 0.055))
    air = apply_env(bandpass(noise(0.7), 6000, 16000), env_ad(n_samples(0.7), 0.03, 0.2))
    add_at(x, [s * 0.1 for s in air], n_samples(0.1))
    return fade_tail(soft_clip(x, 1.1), 12)


def reward_achievement():
    """pad swell + bell hit — the 'achievement unlocked' feel."""
    d = 1.6
    n = n_samples(d)
    pad = [0.0] * n
    for f, det in ((196.0, 0.0), (293.7, 0.5), (392.0, -0.4)):
        v = apply_env(osc_saw(f + det, d, 8), env_ar(n, 0.28, 1.1, 0.12))
        v = lp(v, 2600)
        for i, sv in enumerate(v):
            pad[i] += sv * 0.3
    x = list(pad)
    bell = amb_bell()
    add_at(x, [s * 0.55 for s in bell[:n_samples(0.9)]], n_samples(0.22))
    return fade_tail(soft_clip(x, 1.15), 16)


def reward_coin_rain():
    """six coins falling in quickly, slightly detuned — a burst of loot."""
    x = silence(1.0)
    for i in range(6):
        c = click_coin()
        rate = 1.5 - i * 0.07          # falling pitch
        c2 = [0.0] * int(len(c) / rate)
        pos = 0.0
        for k in range(len(c2)):       # linear-interpolated resample (pitch shift)
            pos += rate
            a = int(pos)
            frac = pos - a
            v0 = c[a] if a < len(c) else 0.0
            v1 = c[a + 1] if a + 1 < len(c) else 0.0
            c2[k] = v0 + (v1 - v0) * frac
        add_at(x, [s * (0.85 - i * 0.08) for s in c2], n_samples(0.03 + i * 0.075))
    return fade_tail(soft_clip(x, 1.25), 10)


def reward_chime_cluster():
    """major triad of bells, long tail — rich, ceremonial."""
    d = 1.5
    x = [0.0] * n_samples(d)
    for f in (659.3, 830.6, 987.8):
        inv = [0.0] * n_samples(d)
        for ratio, amp, tau in ((1.0, 1, .34), (2.76, .4, .17), (5.4, .2, .09)):
            v = apply_env(osc_sine(f * ratio, d), env_ad(n_samples(d), 0.0008, tau, 0.02))
            for i, sv in enumerate(v):
                inv[i] += sv * amp
        for i, sv in enumerate(inv):
            x[i] += sv * 0.36
    strike = apply_env(bandpass(noise(0.02), 3000, 15000),
                       env_ad(n_samples(0.02), 0.0002, 0.004))
    for i, sv in enumerate(strike):
        x[i] += sv * 0.3
    return fade_tail(soft_clip(x, 1.1), 16)


def reward_magic_swirl():
    """FM sweep upward with shimmer — sparkly, magical."""
    d = 0.9
    n = n_samples(d)
    v = osc_fm(lambda t: 300 * (1 + 5.2 * (t / d) ** 1.3), 1.618,
               lambda t: 3.4 * (1 - t / d) + 0.6, d)
    v = apply_env(v, env_ad(n, 0.02, 0.34))
    shimmer = apply_env(bandpass(noise(d), 5000, 14000), env_ar(n, 0.25, 0.55, 0.1))
    return fade_tail(soft_clip(mix((v, 0.8), (shimmer, 0.18)), 1.15), 10)


def reward_perfect():
    """perfect fifth, then a bell — clean, satisfying, not cheesy."""
    x = silence(0.9)
    for f, start, d in ((659.3, 0.0, 0.30), (987.8, 0.075, 0.45)):
        n = n_samples(d)
        v = apply_env(osc_sine(f, d), env_ad(n, 0.002, 0.10))
        add_at(x, [s * 0.7 for s in v], n_samples(start))
    bell = [0.0] * n_samples(0.6)
    for ratio, amp, tau in ((1.0, 1, .16), (2.4, .4, .07), (4.9, .16, .035)):
        sv = apply_env(osc_sine(1318.5 * ratio, 0.6), env_ad(n_samples(0.6), 0.0005, tau))
        for i, t in enumerate(sv):
            bell[i] += t * amp
    add_at(x, [s * 0.4 for s in bell], n_samples(0.14))
    return fade_tail(soft_clip(x, 1.1), 12)


def reward_tally():
    """counting ticks rising, then a confirm — bonus score tallying up."""
    x = silence(1.0)
    for i in range(5):
        f = 1200 + i * 120
        d = 0.045
        v = mix((apply_env(osc_square(f, d), env_ad(n_samples(d), 0.0005, 0.016)), 0.7),
                (apply_env(bandpass(noise(0.004), 4000, 12000),
                           env_ad(n_samples(0.004), 0.0002, 0.001)), 0.3))
        add_at(x, v, n_samples(i * 0.075))
    tail = apply_env(osc_sine(lambda t: 900 + 500 * math.exp(-t / 0.05), 0.5),
                     env_ad(n_samples(0.5), 0.002, 0.14))
    add_at(x, [s * 0.6 for s in tail], n_samples(0.40))
    return fade_tail(soft_clip(x, 1.2), 10)


def reward_supernova():
    """bright bloom: sub thump + rising noise + bell cluster — big reward."""
    d = 1.4
    n = n_samples(d)
    sub = apply_env(osc_sine(lambda t: 55 + 120 * math.exp(-t / 0.06), d),
                    env_ad(n, 0.003, 0.2))
    bloom = bp_sweep(noise(d), 400, 11000, 2.2)
    bloom = apply_env(bloom, env_ar(n, 0.05, 0.75, 0.05))
    x = mix((sub, 0.9), (bloom, 0.5))
    bell = reward_chime_cluster()
    add_at(x, [s * 0.5 for s in bell[:n_samples(1.1)]], n_samples(0.05))
    return fade_tail(soft_clip(x, 1.3), 16)


def hit_impact():
    d = 0.42
    body = crush(apply_env(lp(noise(d), 3200), env_ad(n_samples(d), 0.0006, 0.06)), 2)
    sub = apply_env(osc_sine(lambda t: 40 + 150 * math.exp(-t / 0.04), d),
                    env_ad(n_samples(d), 0.002, 0.11))
    zap = apply_env(osc_saw(lambda t: 90 + 1900 * math.exp(-t / 0.03), d),
                    env_ad(n_samples(d), 0.0008, 0.035))
    return fade_tail(soft_clip(mix((body, 0.9), (sub, 1.0), (zap, 0.45)), 2.2), 10)


def hit_deep_boom():
    d = 0.75
    sub = apply_env(osc_sine(lambda t: 34 + 92 * math.exp(-t / 0.09), d),
                    env_ad(n_samples(d), 0.004, 0.22))
    rumble = apply_env(lp(noise(d), 220), env_ad(n_samples(d), 0.006, 0.18))
    crack = apply_env(bandpass(noise(0.05), 900, 7000),
                      env_ad(n_samples(0.05), 0.0004, 0.012))
    x = mix((sub, 1.0), (rumble, 0.7))
    add_at(x, [s * 0.5 for s in crack], 0)
    return fade_tail(soft_clip(x, 1.6), 12)


def hit_metal_clank():
    d = 0.55
    x = [0.0] * n_samples(d)
    for ratio, amp, tau in ((1.0, 1, .16), (2.76, .7, .1), (5.4, .45, .07),
                            (8.9, .28, .04), (13.3, .15, .025)):
        v = apply_env(osc_sine(430 * ratio, d), env_ad(n_samples(d), 0.0004, tau))
        for i, s in enumerate(v):
            x[i] += s * amp
    strike = apply_env(bandpass(noise(0.02), 2000, 14000),
                       env_ad(n_samples(0.02), 0.0002, 0.005))
    return fade_tail(soft_clip(mix((x, 1.0), (strike, 0.55)), 1.5), 8)


def hit_glitch_break():
    d = 0.36
    x = osc_square(lambda t: 320 * (1 + 0.6 * math.sin(2 * math.pi * 18 * t)), d)
    x = quantize(crush(x, 7), 5)
    x = apply_env(x, env_ad(n_samples(d), 0.0008, 0.09))
    n = n_samples(d)
    for k in range(7):                      # dropouts
        st = int(k * n / 7 * 0.9)
        if k % 2:
            for i in range(st, min(st + n_samples(0.012), n)):
                x[i] *= 0.08
    burst = apply_env(bandpass(noise(d), 1500, 12000),
                      env_ad(n_samples(d), 0.0005, 0.03))
    return fade_tail(soft_clip(mix((x, 1.0), (burst, 0.35)), 1.7), 8)


def hit_shield_deflect():
    d = 0.5
    ting = [0.0] * n_samples(d)
    for ratio, amp, tau in ((1.0, 1, .12), (3.1, .5, .06), (7.4, .25, .03)):
        v = apply_env(osc_sine(1720 * ratio, d), env_ad(n_samples(d), 0.0003, tau))
        for i, s in enumerate(v):
            ting[i] += s * amp
    sweep = apply_env(osc_saw(lambda t: 2400 - 1900 * (t / d), d),
                      env_ad(n_samples(d), 0.001, 0.05))
    sweep = lp(sweep, 4000)
    return fade_tail(soft_clip(mix((ting, 1.0), (sweep, 0.35)), 1.4), 8)


# ---- zaps ------------------------------------------------------------
def zap_pew():
    d = 0.19
    x = osc_saw(lambda t: 240 + 2300 * math.exp(-t / 0.022), d)
    x = apply_env(x, env_ad(n_samples(d), 0.0006, 0.045))
    x = quantize(x, 8)
    air = apply_env(bandpass(noise(d), 3000, 12000), env_ad(n_samples(d), 0.0004, 0.008))
    return fade_tail(soft_clip(mix((x, 1.0), (air, 0.3)), 1.6))


def zap_laser_sweep():
    d = 0.34
    x = osc_saw(lambda t: 320 * (1 + 5.5 * (t / d) ** 1.4)
                * (1 + 0.03 * math.sin(2 * math.pi * 22 * t)), d)
    x = apply_env(x, env_ad(n_samples(d), 0.002, 0.09))
    x = lp(x, 6000)
    return fade_tail(soft_clip(x, 1.5))


def zap_crackle():
    d = 0.42
    n = n_samples(d)
    x = [0.0] * n
    for k in range(48):
        st = int(random.uniform(0, d - 0.02) * SR)
        ln = n_samples(random.uniform(0.003, 0.010))
        b = apply_env(bandpass(noise(ln / SR), 1500, 15000),
                      env_ad(ln, 0.0002, 0.0024))
        g = random.uniform(0.5, 1.0) * (1 - st / n * 0.45)
        add_at(x, [s * g for s in b], st)
    # arc body so the sound has a tone under the crackle, not just dust
    arc = apply_env(osc_saw(lambda t: 90 + 60 * math.sin(math.pi * t / d), d, 8),
                    env_ar(n, 0.004, 0.32, 0.04))
    arc = lp(arc, 1800)
    return fade_tail(soft_clip(mix((x, 1.0), (arc, 0.75)), 1.8))


def zap_warp_down():
    d = 0.45
    x = osc_saw(lambda t: 1600 * math.exp(-t / 0.14) + 70, d)
    x = apply_env(x, env_ad(n_samples(d), 0.002, 0.12))
    x = crush(x, 4)
    x = lp(x, 5000)
    return fade_tail(soft_clip(x, 1.5))


# ---- ambience / tails (reverb-forward) ------------------------------
def amb_riser():
    d = 1.2
    n = n_samples(d)
    nz = bp_sweep(noise(d), 300, 9000, 2.2)
    nz = apply_env(nz, env_ar(n, 0.6, 0.5, 0.0))
    saw = osc_saw(lambda t: 110 * (1 + 5.0 * (t / d) ** 2), d, harmonics=10)
    saw = apply_env(saw, env_ar(n, 0.9, 0.28, 0.0))
    saw = lp_sweep(saw, 600, 8000)
    return fade_tail(soft_clip(mix((nz, 0.55), (saw, 0.7)), 1.3))


def amb_fall():
    d = 1.1
    n = n_samples(d)
    saw = osc_saw(lambda t: 90 * (1 + 4.0 * (1 - t / d) ** 2), d, harmonics=10)
    saw = apply_env(saw, env_ar(n, 0.02, 0.9, 0.2))
    saw = lp_sweep(saw, 5000, 400)
    nz = bp_sweep(noise(d), 7000, 400, 2.0)
    nz = apply_env(nz, env_ar(n, 0.05, 0.85, 0.15))
    return fade_tail(soft_clip(mix((saw, 0.8), (nz, 0.4)), 1.3))


def amb_woosh():
    d = 0.7
    n = n_samples(d)
    nz = bp_sweep(noise(d), 350, 5200, 2.6)
    nz = apply_env(nz, env_ar(n, 0.28, 0.35, 0.05))
    return fade_tail(soft_clip(nz, 1.4))


def amb_bell():
    """big inharmonic bell — long tail, this one wants a lot of room."""
    d = 2.0
    f0 = 392.0
    x = [0.0] * n_samples(d)
    partials = ((0.5, .5, 1.4), (1.0, 1.0, 1.1), (1.19, .45, .8),
                (2.03, .55, .6), (2.74, .35, .42), (3.76, .25, .3),
                (5.1, .15, .2), (7.4, .1, .12))
    for ratio, amp, tau in partials:
        f = f0 * ratio
        if f > SR * 0.45:
            continue
        v = apply_env(osc_sine(f, d), env_ad(n_samples(d), 0.004, tau, 0.02))
        for i, s in enumerate(v):
            x[i] += s * amp
    strike = apply_env(bandpass(noise(0.03), 1500, 11000),
                       env_ad(n_samples(0.03), 0.0004, 0.006))
    return fade_tail(soft_clip(mix((x, 1.0), (strike, 0.4)), 1.05), 20)


def amb_pad():
    """detuned shimmer chord, soft attack — rewards get a halo."""
    d = 1.4
    n = n_samples(d)
    x = [0.0] * n
    for f, det in ((196.0, 0.0), (246.9, 0.6), (293.7, -0.5), (392.0, 0.9)):
        v = apply_env(osc_saw(f + det, d, harmonics=8), env_ar(n, 0.35, 0.9, 0.15))
        v = lp(v, 3000)
        for i, s in enumerate(v):
            x[i] += s * 0.35
    air = apply_env(bandpass(noise(d), 4000, 12000), env_ar(n, 0.5, 0.8, 0.1))
    return fade_tail(soft_clip(mix((x, 1.0), (air, 0.08)), 1.1), 14)


# =========================================================== the kit list
# name, generator, category, reverb send, reverb tail s, description
KIT = [
    # ---- clicks ----
    ("click-01-pop", click_pop, "click", 0.16, 0.35, "clic UI universel — corps brillant, chute de pitch"),
    ("click-02-tick", click_tick, "click", 0.00, 0.00, "ultra court, très aigu — taps rapides"),
    ("click-03-coin", click_coin, "click", 0.20, 0.40, "deux marches carrées doublées — coin arcade"),
    ("click-04-zap", click_zap, "click", 0.18, 0.35, "saw descendant bitcrushé — laser court"),
    ("click-05-snap", click_snap, "click", 0.22, 0.40, "attaque boisée + sub — impact physique"),
    ("click-06-blip", click_blip, "click", 0.20, 0.40, "FM digital verre — UI de jeu"),
    ("click-07-thud", click_thud, "click", 0.18, 0.40, "grave et lourd — grosses cibles"),
    ("click-08-chime", click_chime, "click", 0.34, 0.70, "partiels inharmoniques — récompense"),
    ("click-09-glitch", click_glitch, "click", 0.12, 0.30, "carré haché — broken-arcade"),
    ("click-10-volley", click_volley, "click", 0.17, 0.40, "3 pops ascendantes — « hit all »"),
    ("click-11-tock", click_tock, "click", 0.13, 0.30, "balancier / bloc de bois — très serré"),
    ("click-12-clack", click_clack, "click", 0.10, 0.28, "clavier mécanique : descente + remontée"),
    ("click-13-marimba", click_marimba, "click", 0.28, 0.55, "mailloche bois — chaleureux, organique"),
    ("click-14-bubble", click_bubble, "click", 0.24, 0.45, "goutte d'eau montante (bloop)"),
    ("click-15-digi", click_digi, "click", 0.13, 0.30, "confirmation numérique 2 tons, 6-bit"),
    ("click-16-steel", click_steel, "click", 0.26, 0.55, "ping métallique court"),
    # ---- UI ----
    ("ui-01-soft-press", ui_soft_press, "ui", 0.16, 0.38, "pression douce — sourdine + souffle filtré"),
    ("ui-02-hover", ui_hover, "ui", 0.00, 0.00, "survol léger, à peine audible"),
    ("ui-03-toggle-on", ui_toggle_on, "ui", 0.18, 0.40, "bascule ON — deux notes montantes"),
    ("ui-04-toggle-off", ui_toggle_off, "ui", 0.18, 0.40, "bascule OFF — deux notes descendantes"),
    ("ui-05-confirm", ui_confirm, "ui", 0.22, 0.50, "confirmation — tierce majeure"),
    ("ui-06-cancel", ui_cancel, "ui", 0.16, 0.40, "annulation — seconde mineure, mate"),
    # ---- rewards ----
    ("reward-01-powerup", reward_powerup, "reward", 0.26, 0.55, "arpège carré montant — power-up"),
    ("reward-02-combo", reward_combo, "reward", 0.26, 0.55, "3 cloches rapides ascendantes — combo"),
    ("reward-03-sparkle", reward_sparkle, "reward", 0.55, 1.00, "scintillement de partiels aigus — très aérien"),
    ("reward-04-jackpot", reward_jackpot, "reward", 0.40, 0.85, "arpège 5 notes + cloche — jackpot"),
    ("reward-05-coin-big", reward_coin_big, "reward", 0.32, 0.65, "le coin, avec de la place autour"),
    ("reward-06-arpeggio", reward_arpeggio_up, "reward", 0.28, 0.60, "arpège majeur 5 notes, carrés filtrés — montée rapide"),
    ("reward-07-level-up", reward_level_up, "reward", 0.30, 0.65, "level up — 3 marches montantes en saw désaccordé"),
    ("reward-08-fanfare", reward_fanfare, "reward", 0.34, 0.70, "fanfare I-IV-I, cuivres filtrés"),
    ("reward-09-gem", reward_gem, "reward", 0.24, 0.50, "gemme ramassée — ping clair + blip montant"),
    ("reward-10-streak", reward_streak, "reward", 0.22, 0.45, "combo qui monte — 6 ticks ascendants"),
    ("reward-11-unlock", reward_unlock, "reward", 0.32, 0.70, "déverrouillage — clic mécanique + accord montant"),
    ("reward-12-star-get", reward_star_get, "reward", 0.45, 0.90, "étoile obtenue — arpège scintillant + traînée aiguë"),
    ("reward-13-achievement", reward_achievement, "reward", 0.48, 1.10, "succès débloqué — nappe + cloche"),
    ("reward-14-coin-rain", reward_coin_rain, "reward", 0.30, 0.70, "pluie de pièces — 6 coins qui tombent"),
    ("reward-15-chime-cluster", reward_chime_cluster, "reward", 0.45, 0.95, "cluster de cloches (triade) — cérémonieux"),
    ("reward-16-magic-swirl", reward_magic_swirl, "reward", 0.42, 0.85, "tourbillon FM montant — magique"),
    ("reward-17-perfect", reward_perfect, "reward", 0.38, 0.80, "quinte parfaite + cloche — net et satisfaisant"),
    ("reward-18-tally", reward_tally, "reward", 0.20, 0.45, "comptage de bonus — ticks puis confirmation"),
    ("reward-19-supernova", reward_supernova, "reward", 0.52, 1.20, "supernova — sub + bloom bruité + cloches"),
    # ---- impacts ----
    ("hit-01-impact", hit_impact, "impact", 0.22, 0.50, "gros impact : bruit + sub + zap"),
    ("hit-02-deep-boom", hit_deep_boom, "impact", 0.28, 0.90, "boom grave avec grondement — destruction"),
    ("hit-03-metal-clank", hit_metal_clank, "impact", 0.30, 0.70, "clang métallique inharmonique"),
    ("hit-04-glitch-break", hit_glitch_break, "impact", 0.18, 0.45, "cassure digitale, dropouts"),
    ("hit-05-shield", hit_shield_deflect, "impact", 0.35, 0.75, "déflexion de bouclier — ting + sweep"),
    # ---- zaps ----
    ("zap-01-pew", zap_pew, "zap", 0.14, 0.35, "pew classique descendant"),
    ("zap-02-laser-sweep", zap_laser_sweep, "zap", 0.22, 0.50, "laser montant avec vibrato"),
    ("zap-03-crackle", zap_crackle, "zap", 0.25, 0.55, "crépitements électriques aléatoires"),
    ("zap-04-warp-down", zap_warp_down, "zap", 0.20, 0.50, "warp descendant bitcrushé"),
    # ---- ambience ----
    ("amb-01-riser", amb_riser, "amb", 0.45, 1.10, "montée bruit + saw — tension avant l'action"),
    ("amb-02-fall", amb_fall, "amb", 0.45, 1.10, "descente — échec, chute"),
    ("amb-03-woosh", amb_woosh, "amb", 0.40, 0.90, "whoosh filtré — transition"),
    ("amb-04-bell", amb_bell, "amb", 0.60, 1.50, "grande cloche inharmonique — beaucoup d'espace"),
    ("amb-05-pad", amb_pad, "amb", 0.52, 1.30, "accord de pad désaccordé — halo de récompense"),
]


# =============================================================== output
def write_wav(path, left, right=None):
    ch = 2 if right is not None else 1
    with wave.open(path, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        if ch == 1:
            for v in left:
                frames += struct.pack("<h", int(round(max(-1, min(1, v)) * 32767)))
        else:
            for a, b in zip(left, right):
                frames += struct.pack("<hh",
                                      int(round(max(-1, min(1, a)) * 32767)),
                                      int(round(max(-1, min(1, b)) * 32767)))
        w.writeframes(bytes(frames))


def finalize(left, right):
    """peak to -1 dBFS, bounded RMS trim, tiny fade-out."""
    p = max(peak(left), peak(right) if right else 0) or 1.0
    g = 0.89 / p
    left = [v * g for v in left]
    if right:
        right = [v * g for v in right]
    ref = left if not right else [(a + b) * 0.5 for a, b in zip(left, right)]
    loud = max_win_rms(ref)
    g2 = max(0.5, min(6.0, TARGET_LOUD / loud)) if loud > 1e-6 else 1.0
    left = [v * g2 for v in left]
    if right:
        right = [v * g2 for v in right]
    p = max(peak(left), peak(right) if right else 0)
    if p > PEAK_CEIL:
        k = PEAK_CEIL / p
        left = [v * k for v in left]
        if right:
            right = [v * k for v in right]
    fade_tail(left, 6)
    if right:
        fade_tail(right, 6)
    return left, right


def build_sprite(entries, outdir):
    """all sounds back to back, stereo, + offsets. one fetch for games."""
    buf_l, buf_r = [], []
    index = []
    for e in entries:
        with wave.open(os.path.join(outdir, e["file"]), "rb") as w:
            n = w.getnframes()
            ch = w.getnchannels()
            raw = w.readframes(n)
        off = len(buf_l)
        if ch == 1:
            for i in range(n):
                v = struct.unpack_from("<h", raw, i * 2)[0] / 32768.0
                buf_l.append(v)
                buf_r.append(v)
        else:
            for i in range(n):
                a, b = struct.unpack_from("<hh", raw, i * 4)
                buf_l.append(a / 32768.0)
                buf_r.append(b / 32768.0)
        index.append({"name": e["name"], "category": e["category"],
                      "offsetFrames": off, "duration": e["duration"]})
    write_wav(os.path.join(outdir, "clicksprite.wav"), buf_l, buf_r)
    with open(os.path.join(outdir, "clicksprite.json"), "w") as f:
        json.dump({"sampleRate": SR, "channels": 2, "sounds": index}, f, indent=2)
    return len(buf_l)


def encode(outdir, names):
    for n in names:
        src = os.path.join(outdir, n + ".wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                        "-codec:a", "libmp3lame", "-b:a", "192k",
                        os.path.join(outdir, n + ".mp3")], check=False)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src,
                        "-codec:a", "libvorbis", "-q:a", "6",
                        os.path.join(outdir, n + ".ogg")], check=False)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    outdir = args[0] if args else "."
    only = None
    for f in flags:
        if f.startswith("--only="):
            only = f.split("=", 1)[1]
    do_encode = "--no-encode" not in flags
    force_dry = "--dry" in flags          # same kit, reverb bypassed (A/B)
    os.makedirs(outdir, exist_ok=True)

    entries = []
    print(f"{'name':<22}{'cat':<8}{'dur':>6}{'wet':>6}{'pk':>6}{'rms':>6}{'ch':>4}")
    for name, fn, cat, wet_spec, tail, desc in KIT:
        if only and only not in name:
            continue
        wet = wet_spec
        mono = compress(fn())
        if force_dry:
            wet = 0.0
        if wet > 0:
            L, R = reverb(mono, wet=wet, tail=tail)
            L, R = finalize(L, R)
            write_wav(os.path.join(outdir, name + ".wav"), L, R)
            p, r, ch, dur = max(peak(L), peak(R)), rms([(a + b) / 2 for a, b in zip(L, R)]), 2, len(L) / SR
        else:
            M, _ = finalize(mono, None)
            write_wav(os.path.join(outdir, name + ".wav"), M)
            p, r, ch, dur = peak(M), rms(M), 1, len(M) / SR
        entries.append({"name": name, "file": name + ".wav", "category": cat,
                        "description": desc, "duration": round(dur, 3),
                        "reverb": wet_spec, "dry": force_dry, "channels": ch})
        print(f"{name:<22}{cat:<8}{dur:>6.2f}{wet:>6.2f}{p:>6.2f}{r:>6.2f}{ch:>4}")

    if only:
        # single-sound run: never touch the shared manifest
        print(f"\n{len(entries)} son(s) · {outdir}")
        return
    if force_dry:
        with open(os.path.join(outdir, "manifest.json"), "w") as f:
            json.dump({"sampleRate": SR, "count": len(entries), "dry": True,
                       "sounds": entries}, f, indent=2)
        print(f"\n{len(entries)} sons (dry) · {outdir}")
        return

    total = build_sprite(entries, outdir)
    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump({"sampleRate": SR, "count": len(entries),
                   "sprite": {"file": "clicksprite.wav", "frames": total,
                              "duration": round(total / SR, 3)},
                   "sounds": entries}, f, indent=2)

    if do_encode:
        print("\nencodage mp3/ogg…")
        encode(outdir, [e["name"] for e in entries])
        encode(outdir, ["clicksprite"])
    print(f"\n{len(entries)} sons · sprite {total / SR:.2f}s · {outdir}")


if __name__ == "__main__":
    main()
