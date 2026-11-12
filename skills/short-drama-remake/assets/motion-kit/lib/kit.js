/* Motion kit: ready-made scenes for narrated chart-and-text films.
 *
 * The page gives it window.FILM (written by `animation.py build` from film.json and 时间表.json):
 * every scene and every cue already carries its time in seconds, so nothing here guesses at
 * timing. It builds the picture inside #root and registers one paused GSAP timeline that the
 * renderer drives. Everything is deterministic: no clocks, no unseeded randomness.
 *
 * Scene types: title, kline, points, number, bars, compare, statement, end, custom.
 * A custom scene calls window.CustomScenes[scene.fn](scene, ctx); ctx gives the timeline, the
 * scene's layer, the frame size and the helpers below. A film with a style (FILM.style, one of the
 * looks in the skill's style library) is all custom scenes: no grid behind them, and scenes
 * dissolve into each other unless one asks for another transition. */
(function () {
  "use strict";
  var root = document.getElementById("root");
  var W = Number(root.dataset.width), H = Number(root.dataset.height);
  var PORTRAIT = H > W;
  var U = Math.min(W, H) / 1080;
  var FILM = window.FILM || { total: Number(root.dataset.duration) || 10, scenes: [], captions: [] };
  var palette = Object.assign({ bg: "#050607", panel: "#11161c", up: "#ff3b3b", down: "#2fe0d0", key: "#ffd23f",
    text: "#f5f7fa", dim: "rgba(245,247,250,0.55)", grid: "rgba(255,255,255,0.06)" }, FILM.palette || {});
  var fonts = Object.assign({ display: "Ma Shan Zheng", body: "Noto Sans SC" }, FILM.fonts || {});
  /* A styled film paints its own backdrop: without a panel colour of its own, the glow behind it is the
     backdrop colour itself rather than the kit's dark one. */
  if (FILM.style && !(FILM.palette || {}).panel) palette.panel = palette.bg;
  Object.keys(palette).forEach(function (k) { root.style.setProperty("--" + k, palette[k]); });
  root.style.setProperty("--display", '"' + fonts.display + '", "' + fonts.body + '", sans-serif');
  root.style.setProperty("--body", '"' + fonts.body + '", sans-serif');

  var tl = gsap.timeline({ paused: true });
  var px = function (n) { return Math.round(n * U * 100) / 100 + "px"; };

  function el(parent, cls, css, html) {
    var node = document.createElement("div");
    if (cls) node.className = cls;
    if (css) Object.assign(node.style, css);
    if (html != null) node.innerHTML = html;
    parent.appendChild(node);
    return node;
  }
  function rng(seed) {
    var s = (Math.abs(Math.floor(seed)) % 2147483646) + 1;
    return function () { s = (s * 16807) % 2147483647; return s / 2147483647; };
  }
  function escapeHtml(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  /* **word** in film.json becomes the accent colour. */
  function rich(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
  }

  var stage = el(root, "mk-fill mk-stage");
  var backdrop = el(stage, "mk-fill mk-backdrop");
  var scenesLayer = el(stage, "mk-fill");
  var flash = el(stage, "mk-fill mk-flash", { opacity: 0 });
  var captionLayer = el(stage, "mk-fill mk-captions");
  for (var g = 1; g < 6 && !FILM.style; g++) {
    el(backdrop, "mk-gridline", { top: Math.round(H * g / 6) + "px" });
  }

  /* ---------------------------------------------------------------- scene shell */
  /* How a scene comes on: "slide" (the kit's own push), "fade" (a dissolve) or "cut". A styled film
     dissolves unless a scene asks for something else. */
  function entrance(s) { return (s && s.transition) || (FILM.style ? "fade" : "slide"); }
  function shell(scene, index) {
    var layer = el(scenesLayer, "mk-fill mk-scene", { opacity: index === 0 ? 1 : 0 });
    var t0 = scene.start, t1 = scene.end;
    var move = PORTRAIT ? { y: H * 0.06 } : { x: W * 0.05 };
    var next = index < FILM.scenes.length - 1 ? FILM.scenes[index + 1] : null;
    /* A chart that carries on from the scene before it must not jump: those two scenes cross-fade in place. */
    var carriesOn = function (s) { return !!s && s.type === "kline" && s.continue; };
    if (index > 0 && carriesOn(scene)) {
      tl.fromTo(layer, { opacity: 0 }, { opacity: 1, duration: 0.22, ease: "none" }, Math.max(0, t0 - 0.12));
    } else if (index > 0 && entrance(scene) === "cut") {
      tl.set(layer, { opacity: 1 }, t0);
    } else if (index > 0 && entrance(scene) === "fade") {
      tl.fromTo(layer, { opacity: 0 }, { opacity: 1, duration: 0.4, ease: "sine.inOut" }, Math.max(0, t0 - 0.12));
    } else if (index > 0) {
      tl.fromTo(layer, Object.assign({ opacity: 0, scale: 1.04 }, move),
        { opacity: 1, scale: 1, x: 0, y: 0, duration: 0.38, ease: "power3.out" }, Math.max(0, t0 - 0.12));
    }
    if (next && carriesOn(next)) {
      tl.to(layer, { opacity: 0, duration: 0.22, ease: "none" }, Math.max(t0 + 0.2, t1 - 0.05));
    } else if (next && entrance(next) === "cut") {
      tl.set(layer, { opacity: 0 }, t1);
    } else if (next && entrance(next) === "fade") {
      tl.to(layer, { opacity: 0, duration: 0.4, ease: "sine.inOut" }, Math.max(t0 + 0.2, t1 - 0.12));
    } else if (next) {
      tl.to(layer, { opacity: 0, scale: 0.96, duration: 0.26, ease: "power2.in" }, Math.max(t0 + 0.2, t1 - 0.2));
    }
    return layer;
  }
  /* Drawn from the time alone, the way a style's reference code draws (renderAt(t)): draw(t, p) runs for
     every frame this scene is on screen, with t the seconds since the scene began (0 while it fades in)
     and p its progress from 0 to 1. Draw from t and nothing else -- no requestAnimationFrame, no clock, no
     Math.random -- so a frame comes out the same in whatever order the renderer asks for it. A WebGL
     canvas needs preserveDrawingBuffer: true, or the frame is taken from an empty buffer. */
  function frames(scene, draw) {
    var lead = Math.min(0.12, scene.start), span = Math.max(0.001, scene.end - scene.start);
    var clock = { t: -lead };
    var paint = function () { var t = Math.max(0, clock.t); draw(t, Math.min(1, t / span)); };
    tl.fromTo(clock, { t: -lead }, { t: span, duration: span + lead, ease: "none", immediateRender: false, onUpdate: paint },
      scene.start - lead);
    paint();
  }
  function header(layer, text, t0) {
    if (!text) return null;
    var node = el(layer, "mk-header", {
      left: px(PORTRAIT ? 70 : 110), top: px(PORTRAIT ? 170 : 70), fontSize: px(PORTRAIT ? 78 : 64),
      maxWidth: Math.round(W - 2 * (PORTRAIT ? 70 : 110) * U) + "px",
    }, rich(text));
    tl.fromTo(node, { opacity: 0, x: -30 * U }, { opacity: 1, x: 0, duration: 0.35, ease: "power3.out" }, t0 + 0.05);
    return node;
  }
  /* Something visible must happen every couple of seconds: fill the quiet stretches. */
  function keepAlive(scene, busy, action) {
    var marks = busy.slice().sort(function (a, b) { return a - b; });
    marks.unshift(scene.start); marks.push(scene.end);
    for (var i = 0; i < marks.length - 1; i++) {
      var gap = marks[i + 1] - marks[i];
      var beats = Math.floor(gap / 2.2);
      for (var b = 1; b <= beats; b++) action(marks[i] + gap * b / (beats + 1), b);
    }
  }

  /* --------------------------------------------------------------------- title */
  function titleScene(scene, layer) {
    var t0 = scene.start;
    var size = PORTRAIT ? 190 : 230;
    var lines = String(scene.text || "").split("\n");
    var longest = Math.max.apply(null, lines.map(function (line) { return line.replace(/\*\*/g, "").length; }));
    var room = H * (PORTRAIT ? 0.42 : 0.57) / U - (scene.sub ? 110 : 0);
    size = Math.min(size, (W / U * 0.86) / Math.max(1, longest), room / (1.25 * lines.length));
    var title = el(layer, "mk-title", { top: Math.round(H * (PORTRAIT ? 0.3 : 0.28)) + "px", fontSize: px(size) }, rich(scene.text || ""));
    var sub = scene.sub ? el(layer, "mk-title-sub", {
      top: Math.round(H * (PORTRAIT ? 0.3 : 0.28) + size * U * 1.25 * lines.length + 30 * U) + "px", fontSize: px(PORTRAIT ? 64 : 56),
    }, rich(scene.sub)) : null;
    /* Visible from the very first frame, and whole: a feed shows frame one before anything moves. */
    var slam = Math.min(1.5, Math.max(1.1, W * 0.97 / (Math.max(1, longest) * size * U)));
    tl.fromTo(title, { scale: slam, opacity: 0.55 }, { scale: 1, opacity: 1, duration: 0.42, ease: "power4.in" }, t0);
    tl.fromTo(flash, { opacity: 0 }, { opacity: 1, duration: 0.08, ease: "none" }, t0 + 0.44);
    tl.to(flash, { opacity: 0, duration: 0.9, ease: "power2.out" }, t0 + 0.52);
    tl.fromTo(title, { x: -10 * U }, { x: 0, duration: 0.3, ease: "elastic.out(1, 0.3)" }, t0 + 0.46);
    var rand = rng(scene.seed || 7);
    for (var k = 0; k < 26; k++) {
      var spark = el(layer, "mk-spark", { left: W / 2 + "px", top: Math.round(H * (PORTRAIT ? 0.36 : 0.42)) + "px" });
      var angle = rand() * Math.PI * 2, reach = (260 + rand() * 520) * U;
      tl.fromTo(spark, { x: 0, y: 0, opacity: 0, scale: 0.6 + rand() },
        { x: Math.cos(angle) * reach, y: Math.sin(angle) * reach * 0.6, opacity: 0, duration: 1.2 + rand() * 0.8, ease: "power3.out",
          keyframes: { "0%": { opacity: 1 }, "100%": { opacity: 0 } } }, t0 + 0.46);
    }
    if (sub) tl.fromTo(sub, { opacity: 0, y: 24 * U }, { opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }, t0 + 0.9);
    /* The opening seconds decide whether a viewer stays: keep them moving after the slam too. */
    /* A glint crosses the letters themselves: a copy of the words inside the title (so it moves and
       pulses with it) that only shows where the light is. */
    var glint = el(title, "mk-glint", { opacity: 0 }, rich(scene.text || ""));
    glint.setAttribute("data-layout-allow-overlap", "");
    title.setAttribute("data-layout-allow-overlap", "");
    tl.fromTo(glint, { backgroundPosition: "100% 0%", opacity: 1 },
      { backgroundPosition: "0% 0%", opacity: 1, duration: 0.85, ease: "power1.inOut", immediateRender: false }, t0 + 1.15);
    tl.set(glint, { opacity: 0 }, t0 + 2.01);
    if (scene.end - t0 > 2.5) tl.to(title, { scale: 1.05, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, t0 + 2.1);
    keepAlive(scene, [t0 + 2.7], function (at) {
      tl.to(title, { scale: 1.04, duration: 0.35, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* --------------------------------------------------------------------- kline */
  /* [open, high, low, close, volume] rows shaped like the thing being explained. */
  function series(shape, count, seed) {
    var rand = rng(seed || 11), n = Math.max(12, count || 40), rows = [], price = 10, key = Math.round(n * 0.68);
    var drift = function (i) {
      var p = i / n;
      if (shape === "up") return 0.007;
      if (shape === "down") return -0.007;
      if (shape === "range") return 0;
      if (shape === "top") return i < key ? 0.008 : -0.01;
      if (shape === "bottom") return i < key ? -0.008 : 0.01;
      if (shape === "fakeout") return i < key ? 0.0005 : (i < key + 2 ? 0.006 : -0.014);
      return p < 0.66 ? 0.0003 : 0.006; /* breakout */
    };
    for (var i = 0; i < n; i++) {
      var open = price, move = drift(i) + (rand() - 0.5) * 0.034, volume = 30 + rand() * 22;
      if (i === key) {
        if (shape === "breakout" || shape === "fakeout") { move = 0.075; volume = 96; }
        if (shape === "top") { move = 0.004; volume = 92; }
        if (shape === "bottom") { move = -0.003; volume = 90; }
      }
      var close = open * (1 + move);
      var high = Math.max(open, close) * (1 + rand() * 0.012), low = Math.min(open, close) * (1 - rand() * 0.012);
      if (i === key && shape === "top") high = Math.max(open, close) * 1.07;
      if (i === key && shape === "bottom") low = Math.min(open, close) * 0.93;
      if (i === key && shape === "fakeout") high = close * 1.012;
      rows.push([open, high, low, close, volume + Math.abs(move) * 420]);
      price = close;
    }
    return { rows: rows, key: key };
  }
  function chart(layer, box, rows, clip) {
    /* With clip {top, bottom} the chart lives in a window of its own: zoomed in, it is cut off softly
       at the window's edges instead of running under the heading or the captions. */
    var view = clip ? el(layer, "mk-view", { top: clip.top + "px", height: clip.bottom - clip.top + "px" }) : layer;
    var window_ = clip || { top: box.y, bottom: box.y + box.h };
    var cam = el(view, "mk-fill", { transformOrigin: "0 0", top: clip ? -clip.top + "px" : "0px" });
    cam.setAttribute("data-layout-allow-overflow", "");
    var marks = el(layer, "mk-fill");
    var volumeH = box.h * 0.2, priceH = box.h - volumeH - box.h * 0.05;
    var low = Math.min.apply(null, rows.map(function (r) { return r[2]; }));
    var high = Math.max.apply(null, rows.map(function (r) { return r[1]; }));
    var pad = (high - low) * 0.06; low -= pad; high += pad;
    var vmax = Math.max.apply(null, rows.map(function (r) { return r[4]; }));
    /* A handful of candles keeps the width candles have in a chart of sixteen, centred, instead of
       swelling to fill the box. */
    var step = Math.min(box.w / rows.length, box.w / 16), bodyW = Math.max(3, step * 0.58);
    var inset = (box.w - step * rows.length) / 2;
    var y = function (p) { return box.y + (high - p) / (high - low) * priceH; };
    var cx = function (i) { return box.x + inset + step * (i + 0.5); };
    var parts = rows.map(function (r, i) {
      var kind = r[3] >= r[0] ? "mk-up" : "mk-down";
      var top = y(Math.max(r[0], r[3])), bottom = y(Math.min(r[0], r[3]));
      var body = el(cam, "mk-candle " + kind, { left: cx(i) - bodyW / 2 + "px", top: top + "px", width: bodyW + "px",
        height: Math.max(3, bottom - top) + "px", transformOrigin: kind === "mk-up" ? "50% 100%" : "50% 0%" });
      var wick = el(cam, "mk-wick " + kind, { left: cx(i) - Math.max(1, bodyW * 0.09) + "px", width: Math.max(2, bodyW * 0.18) + "px",
        top: y(r[1]) + "px", height: Math.max(2, y(r[2]) - y(r[1])) + "px" });
      var vh = r[4] / vmax * volumeH;
      var vol = el(cam, "mk-vol " + kind, { left: cx(i) - bodyW / 2 + "px", width: bodyW + "px",
        top: box.y + box.h - vh + "px", height: vh + "px", transformOrigin: "50% 100%" });
      return { body: body, wick: wick, vol: vol, all: [body, wick, vol] };
    });
    var volTop = function (i) { return box.y + box.h - rows[i][4] / vmax * volumeH; };
    return { cam: cam, marks: marks, parts: parts, y: y, cx: cx, rows: rows, bodyW: bodyW, low: low, high: high,
      step: step, volTop: volTop, view: window_ };
  }
  /* The chart of the kline scene before this one, so that a scene can carry on from it. */
  var lastChart = null;
  function priceOf(value, rows, key) {
    var before = rows.slice(Math.max(0, key - 14), key);
    if (!before.length) before = rows;
    if (value === "support") return Math.min.apply(null, before.map(function (r) { return r[2]; }));
    if (value === "resistance") return Math.max.apply(null, before.map(function (r) { return r[1]; }));
    return Number(value);
  }
  function klineScene(scene, layer) {
    var t0 = scene.start, t1 = scene.end;
    var heading = header(layer, scene.header, t0);
    /* "continue": true carries on with the chart of the kline scene before it: what is already on
       screen stays where it is, and only the bars that are new grow. "show": n stops at the nth bar. */
    var carried = scene.continue && lastChart ? lastChart : null;
    /* While two such scenes cross-fade, the same heading and labels sit on top of themselves: meant. */
    var meant = function (node) {
      node.setAttribute("data-layout-allow-overlap", "");
      node.setAttribute("data-layout-allow-occlusion", "");
    };
    if (carried && heading) meant(heading);
    var made = carried ? { rows: carried.rows, key: carried.key }
      : scene.data ? { rows: scene.data, key: scene.key == null ? scene.data.length - 1 : scene.key }
      : series(scene.shape || "breakout", scene.bars, scene.seed);
    var key = scene.key == null ? made.key : scene.key;
    var count = made.rows.length;
    /* A bar is named by its number from 0, or relative to the one that matters: "key", "key+3", "last-2". */
    var barAt = function (value) {
      if (typeof value === "number") return value;
      var named = /^(key|last|first)\s*([+-]\s*\d+)?$/.exec(String(value == null ? "key" : value));
      if (!named) return key;
      var base = named[1] === "key" ? key : (named[1] === "last" ? count - 1 : 0);
      return base + (named[2] ? parseInt(named[2].replace(/\s/g, ""), 10) : 0);
    };
    var already = carried ? carried.shown : 0;
    var wanted = scene.show == null ? count : barAt(scene.show) + 1;   /* up to and including that bar */
    var shown = Math.max(already, Math.min(count, Math.max(1, wanted)));
    var box = carried ? carried.box : PORTRAIT
      ? { x: 60 * U, y: H * 0.2, w: W - 120 * U, h: H * 0.385 }
      : { x: 110 * U, y: H * (scene.header ? 0.2 : 0.11), w: W - 220 * U, h: H * (scene.header ? 0.62 : 0.72) };
    var c = chart(layer, box, made.rows, { top: Math.max(0, box.y - H * 0.035), bottom: Math.min(H, box.y + box.h + H * (PORTRAIT ? 0.012 : 0.03)) });
    var clampBar = function (i) { return Math.max(0, Math.min(shown - 1, Math.round(i))); };
    var cueTimes = (scene.lines || []).concat(scene.focus || []).map(function (cue) { return cue.at; });
    var firstCue = cueTimes.length ? Math.min.apply(null, cueTimes) : t1;
    /* The chart has finished growing just before the first thing on it is pointed at. */
    var fresh = shown - already;
    var grow = fresh ? Math.min(2.2, Math.max(0.6, Math.min((t1 - t0) * 0.3, firstCue - t0 - 0.9))) : 0;
    var each = fresh ? grow / fresh : 0;
    c.parts.forEach(function (p, i) {
      if (i < already) return;
      if (i >= shown) { gsap.set(p.all, { opacity: 0 }); return; }
      var at = t0 + 0.15 + (i - already) * each;
      tl.fromTo(p.body, { scaleY: 0 }, { scaleY: 1, duration: 0.26, ease: "power2.out" }, at);
      tl.fromTo(p.wick, { scaleY: 0 }, { scaleY: 1, duration: 0.2, ease: "none" }, at + 0.06);
      tl.fromTo(p.vol, { scaleY: 0 }, { scaleY: 1, duration: 0.28, ease: "back.out(2)" }, at + 0.04);
    });
    var busy = [t0 + 0.15 + grow], tags = [];
    var inherited = carried && scene.keepLines !== false ? carried.lines : [];
    var drawn = inherited.slice();
    inherited.concat(scene.lines || []).forEach(function (line, order) {
      var old = order < inherited.length;
      var price = priceOf(line.price, made.rows, key), lineY = c.y(price), node;
      if (line.to != null) {
        /* A zone between two prices, drawn under the candles. */
        var otherY = c.y(priceOf(line.to, made.rows, key)), zoneTop = Math.min(lineY, otherY);
        node = el(c.cam, "mk-zone", { left: box.x + "px", top: zoneTop + "px", width: box.w + "px", height: Math.max(6 * U, Math.abs(otherY - lineY)) + "px" });
        c.cam.insertBefore(node, c.cam.firstChild);
        lineY = zoneTop;
      } else {
        node = el(c.cam, "mk-line", { left: box.x + "px", top: lineY - 2 * U + "px", width: box.w + "px", height: Math.max(3, 4 * U) + "px" });
      }
      if (!old) tl.fromTo(node, { scaleX: 0 }, { scaleX: 1, duration: 0.7, ease: "power2.inOut" }, line.at);
      if (line.label) {
        /* Readable on a phone, and at whichever end of the line has fewer candles behind it. */
        var letters = String(line.label).replace(/\*\*/g, "").length;
        var tagSize = Math.min(50, (box.w / U - 40) / Math.max(1, letters)), tagW = (letters * tagSize + 40) * U;
        var crowd = function (from, to) {
          return made.rows.filter(function (r, i) {
            var x = c.cx(i);
            return i < shown && x >= from && x <= to && c.y(r[1]) < lineY - 8 * U && c.y(r[2]) > lineY - 80 * U;
          }).length;
        };
        var atRight = crowd(box.x + box.w - tagW, box.x + box.w) <= crowd(box.x, box.x + tagW);
        var tag = el(c.cam, "mk-tag", Object.assign({ top: lineY - (tagSize * 1.2 + 18) * U + "px", fontSize: px(tagSize), opacity: old ? 1 : 0 },
          atRight ? { right: W - box.x - box.w + "px" } : { left: box.x + "px" }), rich(line.label));
        tag._at = old ? t0 : line.at;
        meant(tag);   /* the next scene may carry this chart on and draw the same label over it */
        tags.push(tag);
        if (!old) tl.fromTo(tag, { opacity: 0, x: 20 * U }, { opacity: 1, x: 0, duration: 0.3 }, line.at + 0.5);
      }
      if (!old) { busy.push(line.at, line.at + 0.7); drawn.push({ price: line.price, to: line.to, label: line.label }); }
    });
    lastChart = { rows: made.rows, key: key, shown: shown, lines: drawn, box: box };
    var LEAD = 0.7, view = c.view;
    var focuses = scene.focus || [];
    focuses.forEach(function (f, n) {
      var ends = Array.isArray(f.bars) ? [barAt(f.bars[0]), barAt(f.bars[1])] : null;
      var span = ends ? [clampBar(Math.min(ends[0], ends[1])), clampBar(Math.max(ends[0], ends[1]))] : null;
      var bar = span ? Math.round((span[0] + span[1]) / 2) : clampBar(barAt(f.bar));
      var onVolume = f.point === "volume";
      var inFocus = function (i) { return span ? i >= span[0] && i <= span[1] : i === bar; };
      /* `at` is the moment the note is readable, so it can be the moment the voice says it: the
         camera sets off a little earlier. A chart still growing is given time to get there first. */
      var from = Math.max(f.at - LEAD, t0 + 0.15 + grow * 0.6, n ? focuses[n - 1]._until + 0.05 : t0);
      var noteAt = Math.max(f.at, from + LEAD);
      var next = focuses[n + 1];
      var until = Math.min(f.until != null ? f.until : (next ? next.at - LEAD - 0.15 : t1 - 0.5), t1 - 0.3);
      if (until - noteAt < 1.2) until = noteAt + 1.2;
      f._until = until;
      var focusPoint = PORTRAIT ? { x: W * 0.5, y: H * (onVolume ? 0.47 : 0.36) } : { x: W * 0.36, y: H * (onVolume ? 0.62 : 0.47) };
      var rows = span ? made.rows.slice(span[0], span[1] + 1) : [made.rows[bar]];
      var highY = c.y(Math.max.apply(null, rows.map(function (r) { return r[1]; })));
      var lowY = c.y(Math.min.apply(null, rows.map(function (r) { return r[2]; })));
      if (onVolume) { highY = Math.min.apply(null, rows.map(function (r, k) { return c.volTop((span ? span[0] : bar) + k); })); lowY = box.y + box.h; }
      var bx = span ? (c.cx(span[0]) + c.cx(span[1])) / 2 : c.cx(bar), by = (highY + lowY) / 2;
      var spanW = span ? c.cx(span[1]) - c.cx(span[0]) + c.step : c.step;
      var zoom = f.zoom || Math.max(1, Math.min(PORTRAIT ? 1.55 : 1.75, (PORTRAIT ? 0.7 : 0.34) * W / spanW, (view.bottom - view.top) * 0.62 / Math.max(1, lowY - highY)));
      /* What is in focus is lit and the rest dimmed: a focus that follows another one lights bars the first had dimmed. */
      c.parts.forEach(function (p, i) { if (i < shown) tl.to(p.all, { opacity: inFocus(i) ? 1 : 0.2, duration: 0.35 }, from); });
      if (tags.length) tl.to(tags, { opacity: 0, duration: 0.25 }, from);
      tl.to(c.cam, { scale: zoom, x: focusPoint.x - zoom * bx, y: focusPoint.y - zoom * by, duration: 0.8, ease: "power3.inOut" }, from);
      /* One bar gets a ring on the end that matters; a run of bars, or a volume bar, gets a frame. */
      var mark, markW, markH, markY;
      if (span || onVolume) {
        markW = spanW * zoom + 36 * U; markH = (lowY - highY) * zoom + 36 * U; markY = focusPoint.y;
        mark = el(c.marks, "mk-frame", { left: focusPoint.x - markW / 2 + "px", top: markY - markH / 2 + "px", width: markW + "px", height: markH + "px", opacity: 0 });
      } else {
        markW = markH = Math.min(420 * U, Math.max(150 * U, c.bodyW * zoom * 3.2));
        markY = f.point === "low" ? focusPoint.y + zoom * (lowY - by) : focusPoint.y - zoom * (by - highY);
        mark = el(c.marks, "mk-ring", { left: focusPoint.x - markW / 2 + "px", top: markY - markH / 2 + "px", width: markW + "px", height: markH + "px", opacity: 0 });
      }
      tl.fromTo(mark, { scale: 0.3, opacity: 0 }, { scale: 1, opacity: 1, duration: 0.42, ease: "back.out(2.2)" }, from + 0.45);
      tl.to(mark, { scale: span || onVolume ? 1.04 : 1.12, duration: 0.5, ease: "sine.inOut", yoyo: true, repeat: Math.max(1, Math.floor((until - from - 1.0) / 0.5 / 2) * 2 + 1) }, from + 0.9);
      if (f.note) {
        var noteRows = String(f.note).replace(/\*\*/g, "").split("\n");
        var widest = Math.max.apply(null, noteRows.map(function (row) { return row.length; }).concat([1])), note;
        if (PORTRAIT) {
          /* Upright, the note sits between the chart and the caption line: more lines, smaller letters. */
          note = el(c.marks, "mk-note mk-center", { left: Math.round(W * 0.08) + "px", width: Math.round(W * 0.84) + "px", top: Math.round(H * 0.602) + "px",
            fontSize: px(noteRows.length > 2 ? 60 : (noteRows.length === 2 ? 78 : 84)), opacity: 0 }, rich(f.note));
        } else {
          /* Beside the mark, and never past the right edge: a long note gets smaller letters. */
          var noteLeft = focusPoint.x + markW / 2 + 150 * U, noteSize = Math.min(84, (W - noteLeft - 70 * U) / U / widest / 1.08);
          note = el(c.marks, "mk-note", { left: noteLeft + "px", top: markY - noteSize * U * 0.62 * noteRows.length + "px", fontSize: px(noteSize), opacity: 0 }, rich(f.note));
          var arrow = el(c.marks, "mk-arrow", { left: focusPoint.x + markW / 2 + 12 * U + "px", top: markY + "px", width: 120 * U + "px", opacity: 0 });
          tl.fromTo(arrow, { scaleX: 0, opacity: 1 }, { scaleX: 1, opacity: 1, duration: 0.3, ease: "power2.out" }, noteAt - 0.4);
          tl.to(arrow, { opacity: 0, duration: 0.25 }, until);
        }
        tl.fromTo(note, { scale: 0.6, opacity: 0, y: 20 * U }, { scale: 1, opacity: 1, y: 0, duration: 0.4, ease: "back.out(1.8)" }, noteAt - 0.2);
        tl.to(note, { opacity: 0, duration: 0.25 }, until);
      }
      tl.to(mark, { opacity: 0, duration: 0.25 }, until);
      if (!next || next.at - LEAD - until > 0.6) {
        tl.to(c.cam, { scale: 1, x: 0, y: 0, duration: 0.7, ease: "power3.inOut" }, until);
        c.parts.forEach(function (p, i) { if (i < shown) tl.to(p.all, { opacity: 1, duration: 0.35 }, until + 0.25); });
        tags.forEach(function (tag) { if (tag._at < until) tl.to(tag, { opacity: 1, duration: 0.3 }, until + 0.5); });
      }
      busy.push(from, noteAt, until, until + 0.7);
    });
    var sweep = el(layer, "mk-sweep", { top: box.y + "px", height: box.h + "px", width: Math.round(W * 0.16) + "px", left: "0px", opacity: 0 });
    sweep.setAttribute("data-layout-allow-overflow", "");
    keepAlive(scene, busy, function (at) {
      tl.fromTo(sweep, { x: -W * 0.2, skewX: -14, opacity: 0.9 }, { x: W * 1.05, skewX: -14, opacity: 0.9, duration: 1.1, ease: "power1.inOut" }, at - 0.5);
      tl.set(sweep, { opacity: 0 }, at + 0.62);
    });
  }

  /* -------------------------------------------------------------------- points */
  function pointsScene(scene, layer) {
    var t0 = scene.start;
    header(layer, scene.header, t0);
    var items = scene.items || [], columns = PORTRAIT || items.length <= 3 ? 1 : 2;
    var top = H * (PORTRAIT ? 0.2 : (scene.header ? 0.25 : 0.14)), left = (PORTRAIT ? 70 : 110) * U;
    var areaW = W - 2 * left, areaH = H * (PORTRAIT ? 0.5 : 0.56);
    var rowsCount = Math.ceil(items.length / columns), gap = 26 * U;
    var cardW = (areaW - gap * (columns - 1)) / columns, cardH = Math.min((areaH - gap * (rowsCount - 1)) / rowsCount, 300 * U);
    var busy = [], cards = [];
    items.forEach(function (item, i) {
      var col = i % columns, row = Math.floor(i / columns);
      var card = el(layer, "mk-card", { left: left + col * (cardW + gap) + "px", top: top + row * (cardH + gap) + "px", width: cardW + "px", height: cardH + "px", opacity: 0 });
      el(card, "mk-card-no", { left: 30 * U + "px", top: (cardH - 60 * U) / 2 + "px", fontSize: px(60) }, String(i + 1).padStart(2, "0"));
      var words = el(card, "mk-card-text", { left: 128 * U + "px", top: "0px", width: cardW - 156 * U + "px", height: cardH + "px", opacity: 0 });
      /* An empty slot first: the viewer sees how many points are coming, and the frame is never bare. */
      tl.fromTo(card, { opacity: 0, y: 40 * U, scale: 0.94 }, { opacity: 0.5, y: 0, scale: 1, duration: 0.4, ease: "back.out(1.6)" }, Math.min(t0 + 0.06 + i * 0.1, item.at));
      /* One line for the title; the small print shrinks until it fits under it. */
      var textW = cardW / U - 164, titleLetters = String(item.title || "").replace(/\*\*/g, "").length;
      var titleSize = Math.min(72, cardH / U * (item.body ? 0.34 : 0.4), textW / Math.max(1, titleLetters));
      el(words, "mk-card-title", { fontSize: px(titleSize) }, rich(item.title || ""));
      if (item.body) {
        var bodyLetters = String(item.body).replace(/\*\*/g, "").length, bodySize = Math.min(44, cardH / U * 0.21);
        var bodyRoom = cardH / U - titleSize * 1.25 - 30;
        while (bodySize > 24 && Math.ceil(bodyLetters * bodySize / textW) * bodySize * 1.3 > bodyRoom) bodySize -= 2;
        el(words, "mk-card-body", { fontSize: px(bodySize) }, rich(item.body));
      }
      tl.to(card, { opacity: 1, duration: 0.25 }, item.at);
      tl.fromTo(words, { opacity: 0, x: -30 * U }, { opacity: 1, x: 0, duration: 0.35, ease: "power3.out" }, item.at);
      tl.fromTo(card, { boxShadow: "0 0 0px rgba(255,210,63,0)" }, { boxShadow: "0 0 " + 46 * U + "px rgba(255,210,63,0.55)", duration: 0.3, yoyo: true, repeat: 1 }, item.at + 0.35);
      cards.push(card); busy.push(item.at, item.at + 0.6);
    });
    /* The cards sit in the order they were written, and may light in another: each one dims those lit before it. */
    items.forEach(function (item, i) {
      items.forEach(function (other, j) { if (j !== i && other.at < item.at) tl.to(cards[j], { opacity: 0.55, duration: 0.3 }, item.at); });
    });
    if (cards.length) tl.to(cards, { opacity: 1, duration: 0.4 }, Math.max(scene.end - 1.4, items[items.length - 1].at + 1.0));
    keepAlive(scene, busy, function (at, beat) {
      var latest = -1;
      items.forEach(function (item, i) { if (item.at <= at && (latest < 0 || item.at >= items[latest].at)) latest = i; });
      if (latest >= 0) tl.to(cards[latest], { scale: 1.03, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* -------------------------------------------------------------------- number */
  function wheel(parent, value, size, at) {
    var digits = String(value).split(""), box = el(parent, "mk-wheel", { fontSize: size + "px", height: size * 1.1 + "px" });
    digits.forEach(function (ch, i) {
      if (!/[0-9]/.test(ch)) { el(box, "mk-wheel-fixed", null, escapeHtml(ch)); return; }
      var col = el(box, "mk-wheel-col", { height: size * 1.1 + "px" });
      col.setAttribute("data-layout-allow-overflow", "");
      var strip = el(col, "mk-wheel-strip");
      var turns = 2 + (i % 2), rows = [];
      for (var r = 0; r <= turns * 10 + Number(ch); r++) rows.push(r % 10);
      strip.innerHTML = rows.map(function (d) { return '<div style="height:' + size * 1.1 + 'px;line-height:' + size * 1.1 + 'px">' + d + "</div>"; }).join("");
      tl.fromTo(strip, { y: 0 }, { y: -(rows.length - 1) * size * 1.1, duration: 1.1 + i * 0.12, ease: "power3.out" }, at);
    });
    return box;
  }
  function numberScene(scene, layer) {
    var t0 = scene.start, at = scene.at == null ? t0 + 0.3 : scene.at;
    header(layer, scene.header, t0);
    var text = String(scene.value), notes = scene.notes || [];
    /* The number, its label and its notes stack between the heading and the captions; when the stack
       would not fit, the number is the one that gives way. */
    var labelSize = (PORTRAIT ? 66 : 60) * U, noteStep = 84 * U;
    var zoneTop = H * (PORTRAIT ? 0.2 : (scene.header ? 0.19 : 0.1)), zoneBottom = H * (PORTRAIT ? 0.7 : 0.85);
    var labelLines = scene.label ? Math.max(1, Math.ceil(String(scene.label).replace(/\*\*/g, "").length * labelSize / (W * 0.84))) : 0;
    var labelH = labelLines * labelSize * 1.3, notesH = notes.length ? H * 0.025 + notes.length * noteStep : 0;
    var room = zoneBottom - zoneTop - labelH - notesH;
    var size = Math.max(90 * U, Math.min((PORTRAIT ? 300 : 340) * U, room / 1.22,
      W * 0.8 / Math.max(2, text.length + (scene.prefix || "").length * 0.6 + (scene.suffix || "").length * 0.6) * 1.5));
    var rowTop = zoneTop + Math.max(0, room - size * 1.22) * 0.4;
    var row = el(layer, "mk-number-row", { top: Math.round(rowTop) + "px", width: W + "px" });
    if (scene.prefix) el(row, "mk-number-affix", { fontSize: size * 0.5 + "px" }, escapeHtml(scene.prefix));
    var box = wheel(row, text, size, at);
    if (scene.suffix) el(row, "mk-number-affix", { fontSize: size * 0.5 + "px" }, escapeHtml(scene.suffix));
    tl.fromTo(row, { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1, duration: 0.35, ease: "back.out(1.6)" }, at - 0.1);
    tl.to(box, { scale: 1.08, duration: 0.22, ease: "power2.out", yoyo: true, repeat: 1 }, at + 1.45);
    var busy = [at, at + 1.6];
    if (scene.label) {
      var label = el(layer, "mk-number-label", { top: Math.round(rowTop + size * 1.22) + "px", fontSize: labelSize + "px", lineHeight: 1.3 }, rich(scene.label));
      tl.fromTo(label, { opacity: 0, y: 26 * U }, { opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }, at + 1.2);
    }
    var notesTop = rowTop + size * (scene.label ? 1.22 : 1.1) + labelH + H * 0.025;
    notes.forEach(function (note, i) {
      var node = el(layer, "mk-number-note", { top: Math.round(notesTop + i * noteStep) + "px", fontSize: px(52), opacity: 0 }, rich(note.text));
      tl.fromTo(node, { opacity: 0, x: -30 * U }, { opacity: 1, x: 0, duration: 0.35, ease: "power3.out" }, note.at);
      busy.push(note.at);
    });
    keepAlive(scene, busy, function (at2) {
      tl.to(row, { scale: 1.04, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, at2);
    });
  }

  /* ------------------------------------------------------------------- compare */
  function compareScene(scene, layer) {
    var t0 = scene.start;
    header(layer, scene.header, t0);
    var sides = [scene.left || {}, scene.right || {}], busy = [];
    var top = H * (PORTRAIT ? 0.2 : (scene.header ? 0.24 : 0.13)), left = (PORTRAIT ? 60 : 100) * U, gap = 36 * U;
    var panelW = PORTRAIT ? W - 2 * left : (W - 2 * left - gap) / 2, panelH = PORTRAIT ? H * 0.245 : H * 0.56;
    sides.forEach(function (side, i) {
      var x = PORTRAIT ? left : left + i * (panelW + gap), y = PORTRAIT ? top + i * (panelH + gap) : top;
      var panel = el(layer, "mk-panel", { left: x + "px", top: y + "px", width: panelW + "px", height: panelH + "px", opacity: 0 });
      /* The verdict stamp sits in the title row, off the chart; the title gives way to it. */
      var titleLetters = String(side.title || "").replace(/\*\*/g, "").length, stampSize = PORTRAIT ? 58 : 62;
      var stampRoom = side.verdict ? String(side.verdict).replace(/\*\*/g, "").length * stampSize + 90 : 0;
      el(panel, "mk-panel-title", { fontSize: px(Math.min(PORTRAIT ? 50 : 48, (panelW / U - 70 - stampRoom) / Math.max(1, titleLetters))) }, rich(side.title || ""));
      var inner = el(panel, "mk-fill");
      var made = side.data ? { rows: side.data, key: side.key == null ? side.data.length - 1 : side.key }
        : series(side.shape || "breakout", side.bars || 30, side.seed || (i + 3));
      var c = chart(inner, { x: 26 * U, y: 112 * U, w: panelW - 52 * U, h: panelH - 146 * U }, made.rows);
      var at = t0 + 0.25 + i * 0.35;
      if (side.line != null) {
        var rule = el(c.cam, "mk-line", { left: 26 * U + "px", top: c.y(priceOf(side.line, made.rows, made.key)) - 2 * U + "px", width: panelW - 52 * U + "px", height: Math.max(3, 4 * U) + "px" });
        tl.fromTo(rule, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: "power2.inOut" }, at + 0.35 + made.rows.length * 0.03);
      }
      tl.fromTo(panel, { opacity: 0, y: 40 * U }, { opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }, at);
      c.parts.forEach(function (p, k) {
        tl.fromTo(p.body, { scaleY: 0 }, { scaleY: 1, duration: 0.22, ease: "power2.out" }, at + 0.25 + k * 0.03);
        tl.fromTo(p.wick, { scaleY: 0 }, { scaleY: 1, duration: 0.15 }, at + 0.3 + k * 0.03);
        tl.fromTo(p.vol, { scaleY: 0 }, { scaleY: 1, duration: 0.22, ease: "back.out(2)" }, at + 0.28 + k * 0.03);
      });
      if (side.verdict) {
        var verdictAt = side.at == null ? at + 1.8 : side.at;
        var stamp = el(panel, "mk-verdict " + (side.good === false ? "mk-bad" : "mk-good"), { fontSize: px(stampSize), opacity: 0 }, rich(side.verdict));
        tl.fromTo(stamp, { opacity: 0, scale: 1.8, rotation: -8 }, { opacity: 1, scale: 1, rotation: -8, duration: 0.3, ease: "power4.in" }, verdictAt);
        tl.fromTo(panel, { x: -8 * U }, { x: 0, duration: 0.3, ease: "elastic.out(1, 0.3)" }, verdictAt + 0.3);
        busy.push(verdictAt, verdictAt + 0.5);
      }
      side._panel = panel;
    });
    keepAlive(scene, busy.concat([t0 + 1.6]), function (at, beat) {
      var panel = sides[beat % 2]._panel;
      tl.to(panel, { scale: 1.025, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* ---------------------------------------------------------------------- bars */
  function barsScene(scene, layer) {
    var t0 = scene.start, items = scene.items || [], unit = scene.unit || "";
    header(layer, scene.header, t0);
    var values = items.map(function (item) { return Number(item.value) || 0; });
    var most = Math.max.apply(null, values.concat([0])), top = most > 0 ? most : 1;
    var chosen = scene.emphasize === "max" ? values.indexOf(most) : (scene.emphasize ? scene.emphasize - 1 : -1);
    if (chosen >= items.length) chosen = -1;
    /* Roughly how wide a string sets, in type sizes: a digit or Latin letter about 0.56, a Han character twice that. */
    var setWidth = function (text) { return String(text).replace(/[^\x00-\xff]/g, "aa").length * 0.56; };
    var widestValue = Math.max.apply(null, items.map(function (item) { return setWidth(item.value + unit); }).concat([1]));
    var busy = [], bars = [];
    var size = PORTRAIT ? 52 : 46;
    var longestLabel = Math.max.apply(null, items.map(function (item) { return String(item.label || "").replace(/\*\*/g, "").length; }).concat([1]));
    items.forEach(function (item, i) {
      var share = Math.max(0.04, values[i] / top), accent = i === chosen ? " mk-bar-key" : "";
      var bar, value, label;
      if (PORTRAIT) {
        /* The label column is as wide as the longest label needs, within reason; past that the labels shrink.
           The rows sit a little above the middle of the space between the heading and the captions. */
        var trackX = Math.min(W * 0.42, Math.max(W * 0.24, (longestLabel * size + 90) * U));
        var trackW = W - trackX - (widestValue * size + 70) * U;   /* the number after the longest bar stays in the frame */
        var zoneH = H * 0.5, rowH = Math.min(210 * U, zoneH / items.length), y = H * 0.2 + (zoneH - rowH * items.length) * 0.35 + i * rowH;
        label = el(layer, "mk-bar-label", { left: 60 * U + "px", width: trackX - 80 * U + "px", top: y + "px", height: rowH * 0.7 + "px", lineHeight: rowH * 0.7 + "px", fontSize: px(Math.min(size, (trackX / U - 84) / longestLabel)), textAlign: "right" }, rich(item.label || ""));
        bar = el(layer, "mk-bar" + accent, { left: trackX + "px", top: y + rowH * 0.12 + "px", width: trackW * share + "px", height: rowH * 0.46 + "px", transformOrigin: "0 50%" });
        value = el(layer, "mk-bar-value" + accent, { left: trackX + trackW * share + 18 * U + "px", top: y + "px", height: rowH * 0.7 + "px", lineHeight: rowH * 0.7 + "px", fontSize: px(size), opacity: 0 }, escapeHtml(item.value + unit));
        tl.fromTo(bar, { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: "power3.out" }, item.at);
      } else {
        var left = 170 * U, areaW = W - 2 * left, colW = areaW / items.length, barW = Math.min(colW * 0.56, 210 * U);
        var base = H * 0.76, fullH = H * (scene.header ? 0.42 : 0.5), x = left + colW * i + (colW - barW) / 2;
        label = el(layer, "mk-bar-label", { left: left + colW * i + "px", width: colW + "px", top: base + 18 * U + "px", fontSize: px(Math.min(size, (colW / U - 16) / longestLabel)), textAlign: "center" }, rich(item.label || ""));
        bar = el(layer, "mk-bar" + accent, { left: x + "px", top: base - fullH * share + "px", width: barW + "px", height: fullH * share + "px", transformOrigin: "50% 100%" });
        value = el(layer, "mk-bar-value" + accent, { left: left + colW * i + "px", width: colW + "px", top: base - fullH * share - 76 * U + "px", fontSize: px(size + 6), textAlign: "center", opacity: 0 }, escapeHtml(item.value + unit));
        tl.fromTo(bar, { scaleY: 0 }, { scaleY: 1, duration: 0.5, ease: "power3.out" }, item.at);
      }
      /* The label is there from the start, dimmed: the frame is never bare while the voice leads in. */
      tl.fromTo(label, { opacity: 0 }, { opacity: 0.45, duration: 0.3 }, Math.min(t0 + 0.06 + i * 0.08, item.at));
      tl.to(label, { opacity: 1, duration: 0.25 }, item.at);
      tl.fromTo(value, { opacity: 0, scale: 0.6 }, { opacity: 1, scale: 1, duration: 0.3, ease: "back.out(2)" }, item.at + 0.35);
      bars.push(bar); busy.push(item.at, item.at + 0.6);
    });
    if (chosen >= 0 && bars[chosen]) {
      var after = Math.max.apply(null, items.map(function (item) { return item.at; })) + 1.0;
      /* Sideways (upright: in thickness), so the bar never grows into its own number. */
      tl.to(bars[chosen], Object.assign({ duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 3 }, PORTRAIT ? { scaleY: 1.14 } : { scaleX: 1.08 }), Math.min(after, scene.end - 1.4));
      busy.push(after, after + 1.2);
    }
    keepAlive(scene, busy, function (at) {
      var shown = bars.filter(function (bar, i) { return items[i].at <= at; });
      var bar = chosen >= 0 && items[chosen].at <= at ? bars[chosen] : shown[shown.length - 1];
      if (bar) tl.to(bar, { opacity: 0.6, duration: 0.25, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* ----------------------------------------------------------------- statement */
  function statementScene(scene, layer) {
    var lines = scene.lines || [], busy = [];
    var size = (PORTRAIT ? 104 : 112);
    var longest = Math.max.apply(null, lines.map(function (line) { return String(line.text).replace(/\*\*/g, "").length; }).concat([1]));
    size = Math.min(size, (W / U * 0.86) / longest);
    var block = lines.length * size * 1.45 * U, top = (H - block) / 2 - H * 0.04;
    var nodes = lines.map(function (line, i) {
      var node = el(layer, "mk-statement", { top: top + i * size * 1.45 * U + "px", fontSize: px(size), opacity: 0 }, rich(line.text));
      tl.fromTo(node, { opacity: 0, y: 50 * U, scale: 0.92 }, { opacity: 1, y: 0, scale: 1, duration: 0.4, ease: "back.out(1.7)" }, line.at);
      busy.push(line.at, line.at + 0.5);
      return node;
    });
    keepAlive(scene, busy, function (at, beat) {
      var shown = nodes.filter(function (node, i) { return lines[i].at <= at; });
      var node = shown[shown.length - 1];
      if (node) tl.to(node, { scale: 1.05, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* ----------------------------------------------------------------------- end */
  function endScene(scene, layer) {
    var t0 = scene.start;
    var endSize = PORTRAIT ? 64 : 60, endLines = String(scene.text || "").split("\n").length;
    var text = el(layer, "mk-end-text", { top: Math.round(H * 0.38) + "px", fontSize: px(endSize), opacity: 0 }, rich(scene.text || ""));
    tl.fromTo(text, { opacity: 0, y: 30 * U }, { opacity: 1, y: 0, duration: 0.45, ease: "power3.out" }, t0 + 0.15);
    if (scene.badge) {
      var badge = el(layer, "mk-badge", { top: Math.round(H * 0.38 + (endLines * endSize * 1.4 + 56) * U) + "px", fontSize: px(40), opacity: 0 }, escapeHtml(scene.badge));
      tl.fromTo(badge, { opacity: 0, scale: 0.8 }, { opacity: 1, scale: 1, duration: 0.35, ease: "back.out(1.8)" }, t0 + 0.7);
    }
    keepAlive(scene, [t0 + 1.1], function (at) {
      tl.to(text, { scale: 1.03, duration: 0.3, ease: "sine.inOut", yoyo: true, repeat: 1 }, at);
    });
  }

  /* ------------------------------------------------------------------ captions */
  function captions() {
    var keywords = (FILM.keywords || []).slice().sort(function (a, b) { return b.length - a.length; });
    (FILM.captions || []).forEach(function (line) {
      var html = escapeHtml(line.text);
      keywords.forEach(function (word) {
        if (word) html = html.split(escapeHtml(word)).join("<b>" + escapeHtml(word) + "</b>");
      });
      /* On a phone the lowest quarter belongs to the platform: account name, description, music. */
      /* One line, always: a line a little too long for the frame gets slightly smaller letters. */
      var size = Math.min(PORTRAIT ? 58 : 54, W * 0.86 / U / Math.max(1, String(line.text).length));
      var node = el(captionLayer, "mk-caption", { bottom: px(PORTRAIT ? 440 : 78), fontSize: px(size), opacity: 0 }, html);
      tl.fromTo(node, { opacity: 0, y: 12 * U }, { opacity: 1, y: 0, duration: 0.12, ease: "none" }, line.start);
      tl.to(node, { opacity: 0, duration: 0.1, ease: "none" }, Math.max(line.start + 0.2, line.end - 0.1));
    });
  }

  var builders = { title: titleScene, kline: klineScene, points: pointsScene, number: numberScene,
    bars: barsScene, compare: compareScene, statement: statementScene, end: endScene };
  var ctx = { tl: tl, W: W, H: H, U: U, portrait: PORTRAIT, el: el, px: px, rich: rich, rng: rng, header: header,
    keepAlive: keepAlive, chart: chart, series: series, wheel: wheel, palette: palette, frames: frames,
    style: FILM.style || null, total: FILM.total };
  /* A styled film's own backdrop (an aurora, a paper grain, a grid), under every scene for the whole film
     instead of fading with each one: window.CustomBackdrop(ctx) is called once, with ctx.layer the
     backdrop. Draw it over time with ctx.frames({ start: 0, end: ctx.total }, draw). */
  if (typeof window.CustomBackdrop === "function") {
    window.CustomBackdrop(Object.assign({}, ctx, { layer: backdrop }));
  }
  FILM.scenes.forEach(function (scene, index) {
    var layer = shell(scene, index);
    if (scene.type === "custom") {
      var fn = (window.CustomScenes || {})[scene.fn];
      if (typeof fn !== "function") throw new Error("custom scene needs window.CustomScenes." + scene.fn);
      fn(scene, Object.assign({ layer: layer }, ctx));
    } else if (builders[scene.type]) {
      builders[scene.type](scene, layer);
    } else {
      throw new Error("unknown scene type: " + scene.type);
    }
  });
  captions();
  /* The timeline must run exactly as long as the film, or the last frames would have nothing to show. */
  tl.set({}, {}, FILM.total);
  /* index.html registers this timeline with the renderer, in the page itself where its checker looks. */
  window.MotionKit = ctx;
})();
