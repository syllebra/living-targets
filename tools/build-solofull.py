#!/usr/bin/env python3
"""
Build a single-file, fully offline page from solo.html.

Inlines:
  * fx.css            -> <style>
  * fx.js             -> <script>
  * all 55 sounds     -> base64 (transcoded to a chosen bitrate) in window.__SFX_EMBED

The result is ONE .html with no network access at all: it works from file://,
from a USB stick, from a projector laptop with no wifi. Audio is decoded from
the inlined base64, so there is no fetch and no CORS problem.

Usage:
  build-solofull.py [--kbps 96] [--out solofull.html] [--src solo.html]

Typical sizes (55 sounds):
    64 kbps -> ~1.5 MB     96 kbps -> ~2.2 MB     128 kbps -> ~2.9 MB
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.dirname(HERE)      # repo root: tools/ lives inside it


def die(msg):
    print('erreur: ' + msg, file=sys.stderr)
    sys.exit(1)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


def encode_audio(src_dir, kbps, tmp):
    """Transcode every wav to mp3 and return {name: base64}."""
    wavs = sorted(f for f in os.listdir(src_dir)
                  if f.endswith('.wav') and f != 'clicksprite.wav')
    if not wavs:
        die(f'aucun .wav dans {src_dir}')

    embed = {}
    total = 0
    for wav in wavs:
        name = wav[:-4]
        mp3 = os.path.join(tmp, name + '.mp3')
        subprocess.run(
            ['ffmpeg', '-v', 'error', '-y', '-i', os.path.join(src_dir, wav),
             '-codec:a', 'libmp3lame', '-b:a', f'{kbps}k', mp3],
            check=True)
        with open(mp3, 'rb') as f:
            raw = f.read()
        embed[name] = base64.b64encode(raw).decode('ascii')
        total += len(raw)
    return embed, len(wavs), total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kbps', type=int, default=96,
                    help='MP3 bitrate for the inlined sounds (default 96)')
    ap.add_argument('--out', default=None, help='output file')
    ap.add_argument('--src', default=None, help='source page (default solo.html)')
    ap.add_argument('--dir', default=DEFAULT_DIR, help='project directory')
    args = ap.parse_args()

    root = os.path.abspath(args.dir)
    src = os.path.join(root, args.src or 'solo.html')
    out = os.path.abspath(args.out or os.path.join(root, 'solofull.html'))

    for p in (src, os.path.join(root, 'fx.css'), os.path.join(root, 'fx.js')):
        if not os.path.exists(p):
            die(f'introuvable: {p}')

    html = read(src)
    css = read(os.path.join(root, 'fx.css'))
    js = read(os.path.join(root, 'fx.js'))

    # a stray "</script" inside the inlined JS would close the tag early
    if '</script' in js.lower():
        die('fx.js contient "</script" : impossible à inliner tel quel')

    tmp = tempfile.mkdtemp(prefix='solofull-')
    try:
        print(f'encodage de {args.kbps} kbps…')
        embed, count, raw_total = encode_audio(
            os.path.join(root, 'audio'), args.kbps, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f'  {count} sons · {raw_total / 1e6:.2f} Mo mp3')

    # ---- inline the stylesheet ----
    link_re = re.compile(r'<link[^>]+href="fx\.css"[^>]*>')
    if not link_re.search(html):
        die('balise <link href="fx.css"> introuvable dans ' + src)
    html = link_re.sub(lambda m: '<style>\n' + css + '\n</style>', html, count=1)

    # ---- inline the engine ----
    script_re = re.compile(r'<script src="fx\.js"></script>')
    if not script_re.search(html):
        die('balise <script src="fx.js"> introuvable dans ' + src)

    payload = json.dumps(embed, separators=(',', ':'))
    embed_tag = ('<script>\n'
                 "/* audio embarqué : 55 sons en base64, aucun accès réseau */\n"
                 'window.__SFX_EMBED = ' + payload + ';\n'
                 '</script>\n')
    html = script_re.sub(
        lambda m: embed_tag + '<script>\n' + js + '\n</script>', html, count=1)

    # ---- make the page use the embedded audio ----
    old_boot = """SFX.setDir('audio/');
SFX.preload(SOUNDS.map(s => s[0]));   // the solo page wants every sound"""
    new_boot = """/* offline build: every sound is inlined, nothing is fetched */
if (window.__SFX_EMBED) SFX.setEmbedded(window.__SFX_EMBED);
else SFX.setDir('audio/');
SFX.preload(SOUNDS.map(s => s[0]));   // the solo page wants every sound"""
    if old_boot not in html:
        die('bloc de démarrage audio introuvable — la page a-t-elle changé ?')
    html = html.replace(old_boot, new_boot, 1)

    # ---- offline marker ----
    html = html.replace('<title>LIVING TARGET — solo</title>',
                        '<title>LIVING TARGET — solo (offline)</title>', 1)
    html = html.replace('<h1>SOLO <span>TARGET</span></h1>',
                        '<h1>SOLO <span>TARGET</span>'
                        '<em style="font-style:normal;font:700 8px/1 ui-monospace,'
                        'Menlo,monospace;letter-spacing:.16em;color:#39ff88;'
                        'border:1px solid #1d5c3a;border-radius:4px;padding:2px 4px;'
                        'margin-left:6px;vertical-align:middle">OFFLINE</em></h1>', 1)

    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)

    size = os.path.getsize(out)
    print(f'écrit {out}')
    print(f'  {size / 1e6:.2f} Mo — un seul fichier, aucune dépendance')


if __name__ == '__main__':
    main()
