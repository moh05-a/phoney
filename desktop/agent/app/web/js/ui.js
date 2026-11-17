// Small DOM toolkit: element builder, icons, sheets, dialogs, toasts, formatters.
// Data from the agent only ever becomes text nodes, never HTML.

const ICONS = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  keys: '<rect x="3" y="5" width="18" height="14" rx="3"/><path d="M7 9h2M11 9h2M15 9h2M7 15h10"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13.5 5l-3 14"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8zM19 16l.8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r=".6"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.3l2-1.5-2-3.4-2.3.9a7 7 0 0 0-2.2-1.3L14 3h-4l-.4 2.4a7 7 0 0 0-2.2 1.3l-2.3-.9-2 3.4 2 1.5a7 7 0 0 0 0 2.6l-2 1.5 2 3.4 2.3-.9a7 7 0 0 0 2.2 1.3L10 21h4l.4-2.4a7 7 0 0 0 2.2-1.3l2.3.9 2-3.4-2-1.5c.1-.4.1-.9.1-1.3z"/>',
  pencil: '<path d="M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>', x: '<path d="M6 6l12 12M18 6 6 18"/>',
  left: '<path d="m14 6-6 6 6 6"/>', right: '<path d="m10 6 6 6-6 6"/>', up: '<path d="m6 14 6-6 6 6"/>', down: '<path d="m6 10 6 6 6-6"/>',
  resize: '<path d="M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7"/>', trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/>',
  play: '<path d="M7 4.5v15l12-7.5z"/>', pause: '<path d="M8 5v14M16 5v14"/>', next: '<path d="M5 5v14l10-7zM19 5v14"/>', prev: '<path d="M19 5v14L9 12zM5 5v14"/>', stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
  'vol-up': '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5zM15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>',
  'vol-down': '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5zM15.5 9a4 4 0 0 1 0 6"/>',
  mute: '<path d="M4 9.5v5h3.5L12 18.5v-13L7.5 9.5zM16 9.5l5 5M21 9.5l-5 5"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2.5"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  power: '<path d="M12 3v9M6.3 6.8a8 8 0 1 0 11.4 0"/>', restart: '<path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v5h-5"/>', moon: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.500 10.5z"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m7 9 3 3-3 3M13 15h4"/>', folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>', bolt: '<path d="M13 3 5 13.500h6L10 21l8-10.500h-6z"/>',
  app: '<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M9 12h6M12 9v6"/>', git: '<circle cx="6" cy="6" r="2.2"/><circle cx="6" cy="18" r="2.2"/><circle cx="18" cy="9" r="2.2"/><path d="M6 8.200v7.600M18 11.200c0 3.500-6 2.500-9.500 5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.500" y="9.500" width="5" height="5" rx=".8"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  chip: '<rect x="3" y="8" width="18" height="8" rx="2"/><path d="M7 8V5M12 8V5M17 8V5M7 19v-3M12 19v-3M17 19v-3"/>',
  wifi: '<path d="M3 9.500a13 13 0 0 1 18 0M6 13a9 9 0 0 1 12 0M9 16.500a5 5 0 0 1 6 0"/><circle cx="12" cy="19.500" r=".6"/>',
  disk: '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.700 3.600 3 8 3s8-1.300 8-3V6M4 12c0 1.700 3.600 3 8 3s8-1.300 8-3"/>',
  battery: '<rect x="2.500" y="8" width="17" height="9" rx="2.500"/><path d="M22 11v3M6 11v3"/>', clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.500 2"/>',
  list: '<path d="M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01"/>', star: '<path d="m12 3.500 2.600 5.400 5.900.800-4.300 4.100 1 5.900L12 16.900 6.800 19.700l1-5.900-4.300-4.100 5.900-.8z"/>',
  note: '<path d="M5 4h14v11l-5 5H5zM14 20v-5h5M8 9h8M8 13h4"/>', alert: '<path d="M12 4 2.500 20h19zM12 10v4.500M12 17.500v.01"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.500v.01"/>',
  music: '<path d="M9 18V5l11-2v13"/><circle cx="6.500" cy="18" r="2.500"/><circle cx="17.500" cy="16" r="2.500"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.400 1.400M17.600 17.600 19 19M5 19l1.400-1.400M17.600 6.400 19 5"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.700 0l3-3a4 4 0 0 0-5.700-5.700l-1 1M14 10a4 4 0 0 0-5.700 0l-3 3A4 4 0 0 0 11 18.700l1-1"/>', home: '<path d="M4 11 12 4l8 7v8a1 1 0 0 1-1 1h-4v-6h-6v6H5a1 1 0 0 1-1-1z"/>',
  chat: '<path d="M4 6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-7l-5 4v-4H6a2 2 0 0 1-2-2z"/>', camera: '<path d="M4 8h3l1.500-2.500h7L17 8h3v11H4z"/><circle cx="12" cy="13" r="3.500"/>',
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.500"/><path d="m4 7 8 6 8-6"/>', game: '<rect x="2.500" y="7" width="19" height="10" rx="5"/><path d="M7 10.500v3M5.500 12h3M15.500 11v.01M18 13v.01"/>',
};
export const BUTTON_ICONS = ['bolt', 'app', 'code', 'terminal', 'folder', 'globe', 'git', 'play', 'pause', 'next', 'prev', 'stop', 'music',
  'vol-up', 'vol-down', 'mute', 'lock', 'moon', 'restart', 'power', 'home', 'chat', 'mail', 'camera', 'game', 'star', 'note', 'clock', 'cpu', 'link'];
export const COLORS = ['accent', 'ok', 'warn', 'danger', 'violet', 'plain'];

export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.innerHTML = ICONS[name] || ICONS.bolt; // static table above, never agent data
  return svg;
}

/** h('div', {class: 'x', onclick: fn}, child, 'text', [more]) */
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value == null || value === false) continue;
    if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (key === 'class') el.className = value;
    else if (key === 'dataset') Object.assign(el.dataset, value);
    else if (key in el && key !== 'list') el[key] = value;
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const child of children) {
    if (child == null || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
}
export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

// ---- formatters ----
export const fmt = {
  bytes(n, digits = 1) {
    if (n == null) return '–';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(i === 0 || n >= 100 ? 0 : digits)} ${units[i]}`;
  },
  rate(n) { return n == null ? '–' : `${fmt.bytes(n)}/s`; },
  pct(n) { return n == null ? '–' : Math.round(n).toString(); },
  clock(totalSeconds) {
    const s = Math.max(0, Math.floor(totalSeconds));
    const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
    const pad = (v) => String(v).padStart(2, '0');
    return hh ? `${hh}:${pad(mm)}:${pad(ss)}` : `${pad(mm)}:${pad(ss)}`;
  },
  ago(isoTime) {
    if (!isoTime) return '';
    const s = Math.max(0, (Date.now() - Date.parse(isoTime)) / 1000);
    if (s < 10) return 'now';
    if (s < 60) return `${Math.floor(s)}s ago`;
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  },
};

// ---- overlays ----
const root = () => document.getElementById('overlay-root');
let toastBox;

export function toast(message, kind = 'info', ms = 2600) {
  if (!toastBox) root().append((toastBox = h('div', { class: 'toasts' })));
  const names = { ok: 'check', error: 'alert', warn: 'alert', info: 'info' };
  const el = h('div', { class: `toast ${kind}` }, icon(names[kind] || 'info'), h('span', null, message));
  toastBox.append(el);
  while (toastBox.children.length > 3) toastBox.firstChild.remove();
  setTimeout(() => el.remove(), ms);
}

/** Bottom sheet. Returns {close, content}. `build(close)` returns the nodes to show. */
export function sheet(title, build) {
  const close = () => backdrop.remove();
  const content = h('div', { class: 'content' });
  const backdrop = h('div', { class: 'backdrop', onclick: (e) => e.target === backdrop && close() },
    h('div', { class: 'sheet' },
      h('header', null, h('h2', null, title), h('button', { class: 'iconbtn', onclick: close, 'aria-label': 'Close' }, icon('x'))),
      content));
  append(content, [build(close)]);
  root().append(backdrop);
  return { close, content };
}

export function confirmDialog({ title, message, confirm = 'Confirm', danger = false }) {
  return new Promise((resolve) => {
    const done = (answer) => { backdrop.remove(); resolve(answer); };
    const backdrop = h('div', { class: 'backdrop center' },
      h('div', { class: 'dialog' },
        danger && h('div', { class: 'mark' }, icon('alert')),
        h('h2', null, title), h('p', null, message || ''),
        h('div', { class: 'actions' },
          h('button', { class: 'btn', onclick: () => done(false) }, 'Cancel'),
          h('button', { class: `btn ${danger ? 'danger' : 'primary'}`, onclick: () => done(true) }, confirm))));
    root().append(backdrop);
  });
}

export function field(label, control) {
  return h('label', { class: 'field' }, h('span', { class: 'label' }, label), control);
}

export function segmented(options, value, onChange) {
  const el = h('div', { class: 'seg' });
  const render = (current) => clear(el, options.map(([key, label]) =>
    h('button', { class: key === current ? 'active' : '', onclick: () => { render(key); onChange(key); } }, label)));
  render(value);
  return el;
}

export function emptyState(iconName, ...lines) {
  return h('div', { class: 'empty' }, icon(iconName), ...lines.map((line) => h('div', null, line)));
}

// ---- sound: a short two-tone chime; iOS allows audio only after the first touch ----
let audio;
document.addEventListener('pointerdown', () => {
  try { audio = audio || new (window.AudioContext || window.webkitAudioContext)(); audio.resume(); } catch { /* no audio */ }
}, { passive: true });
export function chime(tones = [880, 1320]) {
  if (!audio || localStorage.getItem('pbx.sound') === 'off') return;
  tones.forEach((freq, i) => {
    const osc = audio.createOscillator(), gain = audio.createGain();
    const at = audio.currentTime + i * 0.16;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.exponentialRampToValueAtTime(0.18, at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.3);
    osc.connect(gain).connect(audio.destination);
    osc.start(at);
    osc.stop(at + 0.32);
  });
}
