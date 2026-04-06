/* ==========================================================================
   Invisible Piano — Script
   Audio synthesis, string vibration, keyboard input, WebSocket bridge
   ========================================================================== */

const FINGERS = [
  { name: 'thumb',  note: 'C4', freq: 261.63, keys: ['1','a'], color: '#B87333', y: 20  },
  { name: 'index',  note: 'D4', freq: 293.66, keys: ['2','s'], color: '#C9A84C', y: 85  },
  { name: 'middle', note: 'E4', freq: 329.63, keys: ['3','d'], color: '#D4956A', y: 150 },
  { name: 'ring',   note: 'F4', freq: 349.23, keys: ['4','f'], color: '#A89880', y: 215 },
  { name: 'pinky',  note: 'G4', freq: 392.00, keys: ['5','g'], color: '#8B7355', y: 280 },
];


/* ======================== AUDIO ENGINE ======================== */

let audioCtx = null;

function initAudio() {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') audioCtx.resume();
}

/**
 * Piano-like synthesis: 6 harmonics with individual decay rates,
 * a noise burst "hammer click", and a decaying lowpass filter
 * to simulate the brightness loss of real piano strings.
 */
function playNote(finger, velocity) {
  initAudio();
  const t = audioCtx.currentTime;
  const v = Math.max(0.1, Math.min(1, velocity));
  const dur = 2.8;

  // Master envelope
  const master = audioCtx.createGain();
  master.gain.setValueAtTime(v * 0.22, t);
  master.gain.exponentialRampToValueAtTime(v * 0.12, t + 0.06);
  master.gain.exponentialRampToValueAtTime(v * 0.04, t + 0.6);
  master.gain.exponentialRampToValueAtTime(0.001, t + dur);
  master.connect(audioCtx.destination);

  // Harmonic partials (1-6)
  const gains = [1, 0.45, 0.25, 0.12, 0.06, 0.03];
  for (let h = 1; h <= 6; h++) {
    const osc = audioCtx.createOscillator();
    const gn = audioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.value = finger.freq * h;
    osc.detune.value = (Math.random() - 0.5) * 3; // slight detuning
    gn.gain.setValueAtTime(gains[h - 1] * v, t);
    gn.gain.exponentialRampToValueAtTime(0.001, t + dur - (h - 1) * 0.2);
    osc.connect(gn);
    gn.connect(master);
    osc.start(t);
    osc.stop(t + dur);
  }

  // Hammer click — short noise burst
  const buf = audioCtx.createBuffer(1, audioCtx.sampleRate * 0.015, audioCtx.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * 0.4;

  const nSrc = audioCtx.createBufferSource();
  nSrc.buffer = buf;
  const nGain = audioCtx.createGain();
  nGain.gain.setValueAtTime(v * 0.25, t);
  nGain.gain.exponentialRampToValueAtTime(0.001, t + 0.02);
  const nFilt = audioCtx.createBiquadFilter();
  nFilt.type = 'highpass';
  nFilt.frequency.value = 2500;
  nSrc.connect(nFilt);
  nFilt.connect(nGain);
  nGain.connect(master);
  nSrc.start(t);
  nSrc.stop(t + 0.02);
}


/* ======================== BUILD STRINGS ======================== */

const area = document.getElementById('strings');
const logEl = document.getElementById('log');

FINGERS.forEach((f, i) => {
  const g = document.createElement('div');
  g.className = 'string-group';
  g.id = `s-${f.name}`;
  g.style.top = f.y + 'px';
  g.style.setProperty('--glow', f.color);

  // Lower strings get a second wire (copper wound bass strings)
  const wire2 = i <= 1 ? '<div class="wire-2"></div>' : '';

  g.innerHTML = `
    <div class="key-hint">${f.keys[0]}</div>
    <div class="note-label">${f.note}</div>
    <div class="pin-left"></div>
    <div class="wire-container">
      <div class="wire" style="height:${1.8 - i * 0.2}px"></div>
      ${wire2}
      <canvas class="vib-canvas" width="560" height="50"></canvas>
      <div class="glow"></div>
    </div>
    <div class="pin-right"></div>
    <div class="finger-label">${f.name}</div>
  `;

  g.addEventListener('click', () => trigger(f.name, 0.65));
  area.appendChild(g);
});


/* ======================== STRING VIBRATION ======================== */

/**
 * Canvas-based vibration animation.
 * Uses a standing wave with exponential decay,
 * matching the string's visual color.
 */
function vibrateString(el, color, vel, duration) {
  const canvas = el.querySelector('.vib-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  const mid = h / 2;
  const amp = vel * 8;
  const freq = 3 + Math.random() * 2;
  const start = performance.now();

  function draw(now) {
    const elapsed = (now - start) / 1000;
    if (elapsed > duration) {
      ctx.clearRect(0, 0, w, h);
      return;
    }

    const a = amp * Math.exp(-elapsed * 3.5);
    ctx.clearRect(0, 0, w, h);

    // Glow pass
    if (a > 1) {
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = 5;
      ctx.globalAlpha = a / (amp * 3);
      ctx.filter = 'blur(4px)';
      ctx.beginPath();
      for (let x = 0; x < w; x += 3) {
        const env = Math.sin(Math.PI * x / w);
        const y = mid + a * env * Math.sin(freq * Math.PI * x / w + elapsed * 60);
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      ctx.restore();
    }

    // Sharp string pass
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.2;
    ctx.globalAlpha = 0.75;
    ctx.beginPath();
    for (let x = 0; x < w; x++) {
      const env = Math.sin(Math.PI * x / w);
      const y = mid + a * env * Math.sin(freq * Math.PI * x / w + elapsed * 60);
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;

    requestAnimationFrame(draw);
  }

  requestAnimationFrame(draw);
}


/* ======================== TRIGGER ======================== */

const activeTimers = {};

function trigger(name, vel) {
  const f = FINGERS.find(x => x.name === name);
  if (!f) return;

  const el = document.getElementById(`s-${name}`);

  // Reset active state
  if (activeTimers[name]) clearTimeout(activeTimers[name]);
  el.classList.add('active');

  // Sound + visual
  playNote(f, vel);
  vibrateString(el, f.color, vel, 1.5);

  // Event log
  const item = document.createElement('div');
  item.className = 'log-item';
  item.textContent = `${f.name} → ${f.note}`;
  item.style.color = f.color;
  item.style.borderColor = f.color + '20';
  logEl.appendChild(item);
  while (logEl.children.length > 8) logEl.removeChild(logEl.firstChild);
  setTimeout(() => { if (item.parentNode) item.style.opacity = '0.25'; }, 2500);

  // Deactivate after decay
  activeTimers[name] = setTimeout(() => {
    el.classList.remove('active');
  }, 900);
}


/* ======================== KEYBOARD INPUT ======================== */

const keyMap = {};
FINGERS.forEach(f => f.keys.forEach(k => keyMap[k.toLowerCase()] = f.name));

document.addEventListener('keydown', e => {
  if (!e.repeat && keyMap[e.key.toLowerCase()]) {
    trigger(keyMap[e.key.toLowerCase()], 0.5 + Math.random() * 0.4);
  }
});


/* ======================== MINI WAVEFORM DISPLAYS ======================== */

const wBufs = { A: [], B: [] };

function drawWave(canvasId, buf, color) {
  const canvas = document.getElementById(canvasId);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, 72, 20);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (let i = 0; i < buf.length; i++) {
    const y = 10 + buf[i] * 8;
    i === 0 ? ctx.moveTo(i, y) : ctx.lineTo(i, y);
  }
  ctx.stroke();
}

function animWave() {
  ['A', 'B'].forEach(ch => {
    wBufs[ch].push((Math.random() - 0.5) * 0.3);
    if (wBufs[ch].length > 72) wBufs[ch].shift();
  });
  drawWave('wA', wBufs.A, '#6B4423');
  drawWave('wB', wBufs.B, '#5C4A1E');
  requestAnimationFrame(animWave);
}

animWave();


/* ======================== WEBSOCKET ======================== */

let ws = null;
let reconnTimer = null;

function connectWS() {
  try {
    ws = new WebSocket('ws://localhost:8765');

    ws.onopen = () => {
      document.getElementById('dot').className = 'dot on';
      document.getElementById('stxt').textContent = 'sEMG connected';
      if (reconnTimer) {
        clearInterval(reconnTimer);
        reconnTimer = null;
      }
    };

    ws.onmessage = e => {
      try {
        const d = JSON.parse(e.data);

        // Note event from Python bridge
        if (d.finger) {
          trigger(d.finger, d.velocity || 0.6);
        }

        // Live waveform data
        if (d.envA !== undefined) {
          const norm = Math.min(1, d.envA / 60);
          wBufs.A.push(norm - 0.5);
          if (wBufs.A.length > 72) wBufs.A.shift();
          drawWave('wA', wBufs.A, '#B87333');
        }
        if (d.envB !== undefined) {
          const norm = Math.min(1, d.envB / 60);
          wBufs.B.push(norm - 0.5);
          if (wBufs.B.length > 72) wBufs.B.shift();
          drawWave('wB', wBufs.B, '#C9A84C');
        }
      } catch (err) { /* ignore parse errors */ }
    };

    ws.onclose = () => {
      document.getElementById('dot').className = 'dot off';
      document.getElementById('stxt').textContent = 'press 1\u20135 or A S D F G';
      if (!reconnTimer) reconnTimer = setInterval(connectWS, 3000);
    };

    ws.onerror = () => ws.close();

  } catch (e) {
    if (!reconnTimer) reconnTimer = setInterval(connectWS, 3000);
  }
}

connectWS();