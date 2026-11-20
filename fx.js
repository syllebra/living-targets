/* ============================================================
   LIVING TARGETS — shared target engine (JS)
   Exposes: FXKIT.TARGETS, FXKIT.create(), FXKIT.hit(),
            FXKIT.enter(), FXKIT.enterAll(), FXKIT.SFX
   No layout assumptions: index.html builds a grid, solo.html
   builds one draggable target. Both call create().
   ============================================================ */
(function (global) {
  'use strict';

  document.documentElement.classList.add('anim-ready');

  const SPARKS = 10;

const TARGETS = [
  ['Breath Core',   'circle',  'cyan',    'k-breathe',     'h-punch',   'fx-glowCore'],
  ['Heartbeat',     'circle',  'red',     'k-heartbeat',   'h-squash',  'fx-rings'],
  ['Radar Sweep',   'round',   'green',   'k-breathe',     'h-zap',     'fx-radar'],
  ['Barcode',       'rect',    'white',   'k-breathe',     'h-flash',   'fx-scan'],
  ['Orbital',       'circle',  'magenta', 'k-floaty',      'h-spin',    'fx-orbit'],
  ['Marching Ants', 'square',  'amber',   'k-wobble',      'h-shake',   'fx-dash'],
  ['Hazard Tape',   'rect',    'yellow',  'k-squeeze',     'h-blow',    'fx-stripes'],
  ['Neon Tube',     'round',   'cyan',    'k-flicker',     'h-glitch',  'fx-sheen'],
  ['Honeycomb',     'hex',     'amber',   'k-breathe',     'h-punch',   'fx-dots'],
  ['Diamond Lock',  'diamond', 'aqua',    'k-tilt',        'h-spin',    'fx-vortex'],
  ['Sunburst',      'circle',  'amber',   'k-zoomio',      'h-flash',   'fx-sunburst'],
  ['Hypno',         'circle',  'violet',  'k-breathe',     'h-spin',    'fx-spiral'],
  ['Plasma',        'circle',  'pink',    'k-jelly',       'h-invert',  'fx-plasma'],
  ['Iris',          'circle',  'white',   'k-breatheDeep', 'h-drop',    'fx-aperture'],
  ['Ladder',        'tall',    'red',     'k-shiver',      'h-tremble', 'fx-ladder'],
  ['Sonar',         'circle',  'green',   'k-breatheDeep', 'h-punch',   'fx-sonar'],
  ['Grid Drift',    'rect',    'green',   'k-breathe',     'h-shake',   'fx-grid'],
  ['Spotlight',     'circle',  'white',   'k-glowPulse',   'h-flash',   'fx-spot'],
  ['Flow Bars',     'pill',    'lime',    'k-breathe',     'h-pop',     'fx-bars'],
  ['Reticle',       'square',  'red',     'k-breathe',     'h-zap',     'fx-cross'],
  ['Waveform',      'rect',    'cyan',    'k-breathe',     'h-shake',   'fx-wave'],
  ['Eclipse',       'circle',  'orange',  'k-swing',       'h-spin',    'fx-eclipse'],
  ['Star Flare',    'star',    'yellow',  'k-twinkle',     'h-punch',   'fx-flare'],
  ['Beacon',        'tri',     'aqua',    'k-blink',       'h-pop',     'fx-ping'],
  ['Shield',        'octa',    'blue',    'k-breathe',     'h-shake',   'fx-dash'],
  ['Ripple Pond',   'circle',  'teal',    'k-floaty',      'h-drop',    'fx-pond'],
  ['Slot Sync',     'rect',    'magenta', 'k-squeeze',     'h-shake',   'fx-slot'],
  ['Pulse Rings',   'circle',  'blue',    'k-breathe',     'h-punch',   'fx-rings'],
  ['Hard Square',   'square',  'white',   'k-breathe',     'h-squash',  'fx-inner'],
  ['Comet',         'pill',    'orange',  'k-floaty',      'h-spin',    'fx-comet'],
  ['Matrix',        'rect',    'lime',    'k-flicker',     'h-glitch',  'fx-matrix'],
  ['Vortex',        'circle',  'violet',  'k-zoomio',      'h-invert',  'fx-vortex'],
  ['Chase Light',   'round',   'cyan',    'k-breathe',     'h-punch',   'fx-chase'],
  ['Tremble Box',   'square',  'amber',   'k-shiver',      'h-tremble', 'fx-grid'],
  ['Heart Monitor', 'rect',    'red',     'k-breathe',     'h-zap',     'fx-wave'],
  ['Twin Strobe',   'pill',    'magenta', 'k-breathe',     'h-flash',   'fx-strobe'],
  ['Chevron Ticker','pill',    'amber',   'k-breathe',     'h-pop',     'fx-chevron'],
  ['Goo Blob',      'round',   'teal',    'k-jelly',       'h-zap',     'fx-goo'],
  ['Vert Scan',     'tall',    'white',   'k-breathe',     'h-glitch',  'fx-vscan'],
  ['Halftone',      'rect',    'violet',  'k-breathe',     'h-blow',    'fx-halftone']
];

const SHAPE_LABEL = {
  circle:'circle', round:'round box', square:'square', rect:'rectangle',
  pill:'pill', tall:'tall box', hex:'hexagon', diamond:'diamond',
  octa:'octagon', star:'star', tri:'triangle'
};

const SFX = (() => {
  let ctx = null, master = null, on = true, voices = 0;
  /* where the wav files live, relative to the page */
  let AUDIO_DIR = 'audio/';
  /* Embedded audio: name -> base64 (no extension). Set by setEmbedded() so a
     single-file build needs no fetch at all and works from file:// offline. */
  let EMBED = null;
  const buf = {};
  // short, mostly-dry variants: 40 simultaneous hits must not turn to mush
  const FILES = ['click-01-pop','click-02-tick','click-03-coin','click-04-zap',
                 'click-05-snap','click-06-blip','click-07-thud','click-08-chime',
                 'click-09-glitch','click-10-volley','click-11-tock','click-12-clack',
                 'click-13-marimba','click-14-bubble','click-15-digi','click-16-steel',
                 'ui-01-soft-press','ui-03-toggle-on','ui-05-confirm',
                 'zap-01-pew','zap-02-laser-sweep','zap-04-warp-down',
                 'hit-01-impact','hit-03-metal-clank','hit-05-shield',
                 'reward-02-combo','reward-06-arpeggio','reward-07-level-up',
                 'reward-09-gem','reward-10-streak','reward-12-star-get',
                 'reward-13-achievement','reward-15-chime-cluster','reward-17-perfect'];

  function init() {
    if (ctx) { if (ctx.state === 'suspended') ctx.resume(); return ctx; }
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    master = ctx.createGain();
    master.gain.value = 0.62;
    // gentle limiter so rapid volleys never clip
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -14; comp.knee.value = 12;
    comp.ratio.value = 4; comp.attack.value = 0.003; comp.release.value = 0.12;
    master.connect(comp).connect(ctx.destination);
    FILES.forEach(load);
    return ctx;
  }

  const pending = new Set();

  function b64ToBuffer(b64) {
    const bin = atob(b64);
    const n = bin.length;
    const out = new Uint8Array(n);
    for (let i = 0; i < n; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }

  function load(name) {
    if (buf[name] || pending.has(name)) return;
    pending.add(name);

    if (EMBED && EMBED[name]) {
      /* inlined build: decode straight from the page, no network, no CORS */
      try {
        ctx.decodeAudioData(b64ToBuffer(EMBED[name]))
          .then(b => { buf[name] = b; })
          .catch(err => console.warn('[sfx] decode', name, err.message));
      } catch (err) {
        console.warn('[sfx] decode', name, err.message);
      }
      pending.delete(name);
      return;
    }

    /* prefer wav (lossless), fall back to mp3 for the light-weight repo build */
    const tryLoad = (exts) => {
      if (!exts.length) { pending.delete(name); return; }
      const ext = exts[0];
      fetch(AUDIO_DIR + name + ext)
        .then(r => { if (!r.ok) throw new Error(r.status); return r.arrayBuffer(); })
        .then(ab => ctx.decodeAudioData(ab))
        .then(b => { buf[name] = b; pending.delete(name); })
        .catch(() => tryLoad(exts.slice(1)));
    };
    tryLoad(['.wav', '.mp3', '.ogg']);
  }

  /* queue extra sounds; safe before the context exists */
  function preload(names) {
    (names || []).forEach(n => { if (ctx) load(n); else if (!FILES.includes(n)) FILES.push(n); });
    return names ? names.length : 0;
  }

  /* switch to a self-contained build: {name: base64} — also preloads everything */
  function setEmbedded(map) {
    if (!map || !Object.keys(map).length) return 0;
    EMBED = map;
    AUDIO_DIR = '';                    // never hit the filesystem again
    FILES.length = 0;                  // the map is the source of truth
    const names = Object.keys(map);
    names.forEach(n => FILES.push(n));
    if (ctx) names.forEach(load);      // already running: decode now
    return names.length;
  }

  function play(name, { gain = 1, rate = 1, pan = 0, delay = 0 } = {}) {
    if (!on) return;
    const c = init();
    if (!c || !buf[name]) return;
    if (voices > 14) return;                      // runaway guard
    const src = c.createBufferSource();
    src.buffer = buf[name];
    src.playbackRate.value = rate;
    const g = c.createGain();
    g.gain.value = gain;
    let node = src;
    if (c.createStereoPanner) {
      const p = c.createStereoPanner();
      p.pan.value = pan;
      src.connect(p); node = p;
    }
    node.connect(g).connect(master);
    src.start(c.currentTime + delay);
    voices++;
    src.onended = () => { voices--; };
  }

  return {
    play,
    preload,
    unlock: init,
    setDir(d) { if (!EMBED) AUDIO_DIR = d.slice(-1) === '/' ? d : d + '/'; return AUDIO_DIR; },
    setEmbedded,
    get embedded() { return !!EMBED; },
    get dir() { return AUDIO_DIR; },
    has(name) { return !!buf[name]; },
    list() { return Object.keys(buf); },
    setOn(v) { on = v; if (v) init(); },
    get on() { return on; },
    ready() { return Object.keys(buf).length; }
  };
})();

const SFX_FOR_FX = {
  'fx-glowCore':'click-01-pop',   'fx-rings':'click-06-blip',
  'fx-radar':'click-11-tock',     'fx-scan':'click-02-tick',
  'fx-orbit':'click-14-bubble',   'fx-dash':'click-12-clack',
  'fx-stripes':'click-05-snap',   'fx-sheen':'click-04-zap',
  'fx-dots':'click-16-steel',     'fx-vortex':'zap-02-laser-sweep',
  'fx-sunburst':'click-08-chime', 'fx-spiral':'click-06-blip',
  'fx-plasma':'click-07-thud',    'fx-aperture':'ui-01-soft-press',
  'fx-ladder':'click-05-snap',    'fx-sonar':'click-06-blip',
  'fx-grid':'click-02-tick',      'fx-spot':'ui-03-toggle-on',
  'fx-bars':'click-03-coin',      'fx-cross':'zap-01-pew',
  'fx-wave':'click-15-digi',      'fx-eclipse':'click-07-thud',
  'fx-flare':'click-08-chime',    'fx-ping':'click-13-marimba',
  'fx-pond':'click-14-bubble',    'fx-slot':'click-12-clack',
  'fx-inner':'click-16-steel',    'fx-comet':'zap-04-warp-down',
  'fx-matrix':'click-09-glitch',  'fx-chase':'click-02-tick',
  'fx-strobe':'click-09-glitch',  'fx-chevron':'click-11-tock',
  'fx-goo':'ui-01-soft-press',    'fx-halftone':'ui-05-confirm'
};
/* per-shape pitch offset: big slow boxes sound lower, tiny circles higher */
const SFX_PITCH = { circle:1.06, round:1.02, square:.98, rect:.92, pill:.96,
                    tall:.9, hex:.95, diamond:1.1, octa:1.0, star:1.14, tri:1.08 };



  /* ---- entrance variants: keyframe + duration, cycled per index ---- */
  const ENTRANCES = [
    ['in-pop',     .74], ['in-unfold',  .70], ['in-drop',    .86],
    ['in-spin',    .82], ['in-iris',    .78], ['in-bloom',   .76],
    ['in-glitch',  .66], ['in-shutter', .72], ['in-tilt',    .80],
    ['in-rise',    .80], ['in-split',   .74], ['in-stamp',   .70],
    ['in-warp',    .68], ['in-flicker', .95], ['in-slide',   .78]
  ];

  const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------------------------------------------------------- create
     Build one target. Returns the .stage element, with:
       _count, _shape, _sfx, _in  (already used by hit/enter)
     opts.entrance : index into ENTRANCES (default: target index)
     opts.solo     : adds .solo (own box, settable --w/--h)
  */
  function create(index, opts) {
    opts = opts || {};
    const [name, shape, color, idle, hitAnim, fx] = TARGETS[index];
    const [inName, inDur] = ENTRANCES[(opts.entrance ?? index) % ENTRANCES.length];

    const stage = document.createElement('div');
    stage.className = 'stage sh-' + shape + ' c-' + color + (opts.solo ? ' solo' : '');
    stage.style.setProperty('--in', inName);
    stage.style.setProperty('--in-dur', inDur + 's');
    stage.innerHTML =
      '<span class="halo"></span>' +
      '<button class="t ' + fx + '" style="--idle:' + idle + ';--hit:' + hitAnim + '"' +
      ' aria-label="' + name + ' target, click to hit">' +
        '<span class="plate"><span class="fx"></span><span class="fx2"></span></span>' +
        '<span class="core"></span>' +
        '<span class="flash"></span>' +
      '</button>' +
      '<span class="shock"></span>' +
      '<span class="sp">' + '<b></b>'.repeat(SPARKS) + '</span>';

    const sparks = stage.querySelectorAll('.sp b');
    sparks.forEach((b, k) => b.style.setProperty('--a', (k * 360 / SPARKS) + 'deg'));

    stage._count  = 0;
    stage._index  = index;
    stage._name   = name;
    stage._shape  = shape;
    stage._color  = color;
    stage._fx     = fx;
    stage._sfx    = SFX_FOR_FX[fx] || 'click-01-pop';
    stage._cx     = 0;
    stage._in     = inName;
    return stage;
  }

  /* ------------------------------------------------------------- hit */
  function hit(stage, opts) {
    opts = opts || {};
    stage.classList.remove('is-hit');
    void stage.offsetWidth;                 /* restart mid-animation */
    stage.classList.add('is-hit');
    stage._count = (stage._count || 0) + 1;
    if (stage._cntEl) stage._cntEl.textContent = stage._count;
    clearTimeout(stage._timer);
    stage._timer = setTimeout(() => stage.classList.remove('is-hit'), 760);

    if (opts.silent) return;

    const jitter = 1 + (Math.random() - 0.5) * 0.07;
    const cx = opts.cx ?? stage._cx ?? 0;
    const pan = Math.max(-0.7, Math.min(0.7, (cx / window.innerWidth - 0.5) * 1.4));
    const pitch = opts.pitch ?? (SFX_PITCH[stage._shape] || 1);

    SFX.play(stage._sfx, { rate: pitch * jitter, gain: opts.gain ?? .9, pan: opts.pan ?? pan });

    if (opts.accent !== false) {
      /* accents: milestone every 5th hit, rare colouring otherwise */
      if (stage._count % 5 === 0) {
        const heavy = ['hit-01-impact', 'hit-03-metal-clank', 'hit-05-shield'][
          Math.floor(Math.random() * 3)];
        SFX.play(heavy, { gain: .62, pan, delay: .012, rate: jitter });
      } else if (stage._count % 10 === 0) {
        const big = ['reward-06-arpeggio', 'reward-07-level-up', 'reward-12-star-get',
                     'reward-17-perfect', 'reward-13-achievement'];
        SFX.play(big[Math.floor(Math.random() * big.length)],
                 { gain: .5, pan, delay: .03, rate: jitter });
      } else if (Math.random() < .08) {
        const small = ['reward-09-gem', 'reward-10-streak', 'reward-12-star-get'];
        SFX.play(small[Math.floor(Math.random() * small.length)],
                 { gain: .34, pan, delay: .02, rate: jitter });
      }
    }
    if (opts.onHit) opts.onHit(stage);
  }

  /* ----------------------------------------------------------- enter */
  let entering = 0;

  function enter(stage, delay, sound) {
    delay = delay || 0;
    const host = stage.parentElement || stage;

    if (REDUCED) {
      stage.classList.add('is-in', 'in-done');
      host.classList.add('is-in');
      return;
    }

    stage.style.setProperty('--in-delay', Math.round(delay) + 'ms');
    stage.classList.remove('is-in', 'in-done');
    host.classList.remove('is-in');
    void stage.offsetWidth;
    stage.classList.add('is-in');

    clearTimeout(host._inTimer);
    host._inTimer = setTimeout(() => host.classList.add('is-in'), delay + 120);

    const total = (parseFloat(getComputedStyle(stage).animationDuration) || .8) * 1000;
    entering++;
    clearTimeout(stage._inDone);
    stage._inDone = setTimeout(() => stage.classList.add('in-done'), delay + total);
    setTimeout(() => { entering--; }, delay + total + 60);

    if (sound) {
      SFX.play('ui-03-toggle-on', {
        gain: .22, rate: (SFX_PITCH[stage._shape] || 1) * .92
      });
    }
  }

  function enterAll(stages, opts) {
    opts = opts || {};
    const stagger = opts.stagger ?? 55;
    stages.forEach((s, i) => enter(s, i * stagger, false));
    if (opts.sound !== false && stages.length) {
      SFX.play('amb-03-woosh', { gain: .30 });
      SFX.play('zap-02-laser-sweep', { gain: .16, delay: .05 });
    }
  }

  global.FXKIT = {
    TARGETS, SHAPE_LABEL, ENTRANCES, SFX_FOR_FX, SFX_PITCH, SPARKS,
    SFX, create, hit, enter, enterAll,
    get entering() { return entering; },
    reduced: REDUCED
  };
})(window);
