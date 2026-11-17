// Widget registry. A widget type is one entry here: adding a type never touches the dashboard code.
//
//   define('type', { name, icon, topics, sizes: [[w, h], ...], options?, make(widget) -> { body, aside?, on?, destroy? } })
//
// `on` maps a topic to a function (payload, history) that updates the widget's own DOM nodes.
// `options` describes the widget's settings: [{ key, label, type: 'bool' | 'choice', default?, choices? }].
// Values are stored in widget.config; `choices` is a list of [value, label] or a function returning one.

import { get, getBlob, patch, runAction } from './api.js';
import { live } from './live.js';
import { clear, confirmDialog, emptyState, fmt, h, icon, toast } from './ui.js';

const registry = new Map();
export function define(type, spec) { registry.set(type, spec); }
export const widgetTypes = () => [...registry.entries()].map(([type, spec]) => ({ type, ...spec }));
export const widgetSpec = (type) => registry.get(type);

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Filled line chart on a canvas, sized for the device pixel ratio. */
export function drawSpark(canvas, values, { max, color = '--accent' } = {}) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  if (!width || !height) return;
  if (canvas.width !== Math.round(width * ratio)) { canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio); }
  const g = canvas.getContext('2d');
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, width, height);
  if (values.length < 2) return;
  const top = max ?? Math.max(1, ...values) * 1.15;
  const step = width / (60 - 1);
  const x0 = width - (values.length - 1) * step;
  const y = (v) => height - 2 - (Math.min(v, top) / top) * (height - 6);
  g.beginPath();
  values.forEach((v, i) => (i ? g.lineTo(x0 + i * step, y(v)) : g.moveTo(x0, y(v))));
  const stroke = cssVar(color);
  g.strokeStyle = stroke; g.lineWidth = 1.6; g.lineJoin = 'round'; g.stroke();
  g.lineTo(width, height); g.lineTo(x0, height); g.closePath();
  const fill = g.createLinearGradient(0, 0, 0, height);
  fill.addColorStop(0, stroke + '55'); fill.addColorStop(1, stroke + '00');
  g.fillStyle = fill; g.fill();
}

const big = (unit) => { const value = document.createTextNode('–'); return { el: h('div', { class: 'big over' }, value, h('small', null, unit)), value }; };
const sub = () => h('div', { class: 'sub over ellipsis' });
const level = (percent) => (percent >= 90 ? 'bar danger' : percent >= 75 ? 'bar warn' : 'bar');

define('cpu', {
  name: 'CPU', icon: 'cpu', topics: ['system.cpu'], sizes: [[2, 2], [4, 2], [2, 1]],
  options: [{ key: 'cores', label: 'Per-core bars (wide size)', type: 'bool', default: true }],
  make(widget) {
    const number = big('%'), detail = sub(), canvas = h('canvas', { class: 'spark' }), aside = h('span');
    const cores = widget.size.w >= 4 && widget.config.cores !== false ? h('div', { class: 'cores over' }) : null;
    return {
      aside, body: [canvas, number.el, widget.size.h > 1 && detail, cores],
      on: { 'system.cpu': (p, history) => {
        number.value.data = fmt.pct(p.percent);
        detail.textContent = `${p.physical_cores ?? '?'}C / ${p.logical_cores}T`;
        aside.textContent = p.freq_mhz ? `${(p.freq_mhz / 1000).toFixed(1)} GHz` : '';
        drawSpark(canvas, history.map((s) => s.percent), { max: 100 });
        if (cores) {
          if (cores.children.length !== p.per_core.length) clear(cores, p.per_core.map(() => h('i')));
          p.per_core.forEach((v, i) => { cores.children[i].style.height = `${Math.max(6, v)}%`; });
        }
      } },
    };
  },
});

define('memory', {
  name: 'Memory', icon: 'chip', topics: ['system.memory'], sizes: [[2, 2], [4, 2], [2, 1]],
  make(widget) {
    const number = big('%'), detail = sub(), canvas = h('canvas', { class: 'spark' }), aside = h('span');
    return {
      aside, body: [canvas, number.el, widget.size.h > 1 && detail],
      on: { 'system.memory': (p, history) => {
        number.value.data = fmt.pct(p.percent);
        detail.textContent = `${fmt.bytes(p.used)} of ${fmt.bytes(p.total)}`;
        aside.textContent = p.swap_total ? `swap ${fmt.pct(p.swap_percent)}%` : '';
        drawSpark(canvas, history.map((s) => s.percent), { max: 100, color: '--violet' });
      } },
    };
  },
});

define('network', {
  name: 'Network', icon: 'wifi', topics: ['system.network'], sizes: [[4, 2], [2, 2]],
  options: [{ key: 'nic', label: 'Adapter', type: 'choice', default: '',
    choices: () => [['', 'All adapters'], ...(live.latest('system.network')?.nics || []).map((n) => [n.name, n.name])] }],
  make(widget) {
    const down = h('div', { class: 'mid' }, '–'), up = h('div', { class: 'mid' }, '–'), canvas = h('canvas', { class: 'spark' });
    const column = (label, value) => h('div', { class: 'grow' }, h('div', { class: 'sub' }, label), value);
    const nic = widget.config.nic || '';
    // One adapter, or the total. An adapter that is unplugged simply reads zero.
    const pick = (p) => (nic ? (p.nics || []).find((n) => n.name === nic) || { down_bps: 0, up_bps: 0 } : p);
    return {
      aside: nic ? h('span', null, nic.slice(0, 16)) : null,
      body: [canvas, h('div', { class: 'row over' }, column('↓ Down', down), column('↑ Up', up))],
      on: { 'system.network': (p, history) => {
        const now = pick(p);
        down.textContent = fmt.rate(now.down_bps); up.textContent = fmt.rate(now.up_bps);
        drawSpark(canvas, history.map((s) => pick(s).down_bps), { color: '--ok' });
      } },
    };
  },
});

define('disk_io', {
  name: 'Disk activity', icon: 'disk', topics: ['system.disk_io'], sizes: [[2, 2], [4, 2]],
  make() {
    const read = h('div', { class: 'mid' }, '–'), write = h('div', { class: 'mid' }, '–'), canvas = h('canvas', { class: 'spark' });
    return {
      body: [canvas, h('div', { class: 'over' }, h('div', { class: 'sub' }, 'Read'), read, h('div', { class: 'sub' }, 'Write'), write)],
      on: { 'system.disk_io': (p, history) => {
        read.textContent = fmt.rate(p.read_bps); write.textContent = fmt.rate(p.write_bps);
        drawSpark(canvas, history.map((s) => s.read_bps + s.write_bps), { color: '--warn' });
      } },
    };
  },
});

define('disk', {
  name: 'Storage', icon: 'disk', topics: ['system.disk'], sizes: [[4, 2], [2, 2], [4, 1]],
  options: [{ key: 'mount', label: 'Drive', type: 'choice', default: '',
    choices: () => [['', 'All drives'], ...(live.latest('system.disk')?.volumes || []).map((v) => [v.mount, v.mount])] }],
  make(widget) {
    const list = h('div', { class: 'list', style: 'justify-content:space-evenly;flex:1' });
    const only = widget.config.mount || '';
    return {
      body: list,
      on: { 'system.disk': (p) => clear(list, p.volumes.filter((v) => !only || v.mount === only).map((v) => h('div', null,
        h('div', { class: 'row', style: 'font-size:12.5px;margin-bottom:4px' },
          h('b', { class: 'grow ellipsis' }, v.mount), h('span', { class: 'num muted' }, `${fmt.bytes(v.used, 0)} / ${fmt.bytes(v.total, 0)}`)),
        h('div', { class: level(v.percent) }, h('i', { style: `width:${v.percent}%` }))))) },
    };
  },
});

define('battery', {
  name: 'Battery', icon: 'battery', topics: ['system.battery'], sizes: [[2, 1], [2, 2]],
  make() {
    const number = big('%'), aside = h('span');
    return { aside, body: number.el, on: { 'system.battery': (p) => {
      number.value.data = fmt.pct(p.percent);
      aside.textContent = p.plugged ? 'charging' : p.secs_left ? `${fmt.clock(p.secs_left)} left` : 'on battery';
    } } };
  },
});

define('gpu', {
  name: 'GPU', icon: 'chip', topics: ['system.gpu'], sizes: [[2, 2], [4, 2]],
  make() {
    const number = big('%'), detail = sub(), canvas = h('canvas', { class: 'spark' }), aside = h('span');
    return {
      aside, body: [canvas, number.el, detail],
      on: { 'system.gpu': (p, history) => {
        const gpu = p.gpus[0];
        if (!gpu) return;
        number.value.data = fmt.pct(gpu.util_percent);
        aside.textContent = gpu.temp_c != null ? `${gpu.temp_c}°C` : '';
        detail.textContent = [gpu.mem_used != null && `${fmt.bytes(gpu.mem_used)} VRAM`, gpu.power_w != null && `${gpu.power_w} W`].filter(Boolean).join(' · ');
        drawSpark(canvas, history.map((s) => s.gpus[0]?.util_percent ?? 0), { max: 100, color: '--ok' });
      } },
    };
  },
});

define('processes', {
  name: 'Top processes', icon: 'list', topics: ['system.processes'], sizes: [[4, 3], [4, 2], [4, 4]],
  make(widget) {
    const list = h('div', { class: 'list' });
    const rows = widget.size.h * 3 - 1;
    return { body: h('div', { style: 'flex:1;min-height:0' }, list), on: { 'system.processes': (p) => clear(list, p.items.slice(0, rows).map((item) =>
      h('div', { class: 'item' }, h('span', { class: 'grow ellipsis' }, item.name),
        h('span', { class: 'num muted', style: 'width:74px;text-align:right' }, fmt.bytes(item.mem_bytes)),
        h('span', { class: 'num', style: 'width:52px;text-align:right' }, `${item.cpu_percent.toFixed(1)}%`)))) } };
  },
});

define('clock', {
  name: 'Clock', icon: 'clock', topics: [], sizes: [[4, 2], [4, 1], [2, 1], [2, 2]],
  options: [{ key: 'hour24', label: '24-hour time', type: 'bool', default: false }],
  make(widget) {
    const hero = widget.size.w >= 4 && widget.size.h >= 2;
    const time = h('div', { class: `clockface ${hero ? 'hero' : ''}` }), suffix = h('span', { class: 'ampm' }), date = h('div', { class: 'sub', style: hero ? 'font-size:15px;margin-top:6px' : '' });
    const tick = () => {
      const now = new Date();
      const parts = new Intl.DateTimeFormat([], { hour: 'numeric', minute: '2-digit', hour12: !widget.config.hour24 }).formatToParts(now);
      time.replaceChildren(parts.filter((x) => x.type !== 'dayPeriod' && !(x.type === 'literal' && !x.value.trim())).map((x) => x.value).join('').trim(), suffix);
      suffix.textContent = parts.find((x) => x.type === 'dayPeriod')?.value || '';
      date.textContent = now.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long' });
    };
    tick();
    const timer = setInterval(tick, 5000);
    return { bare: true, body: h('div', { style: 'flex:1;display:flex;flex-direction:column;justify-content:center' }, time, widget.size.h > 1 || widget.size.w >= 4 ? date : null), destroy: () => clearInterval(timer) };
  },
});

const STATE_PILL = { working: ['accent working', 'Working'], needs_input: ['warn', 'Needs you'], idle: ['ok', 'Ready'] };
export function statePill(state) {
  const [cls, label] = STATE_PILL[state] || ['', state];
  return h('span', { class: `pill ${cls}` }, h('i'), label);
}
export const TOOL_NAMES = { 'claude-code': 'Claude Code', codex: 'Codex' };

define('ai', {
  name: 'AI sessions', icon: 'spark', topics: ['ai.sessions'], sizes: [[4, 2], [4, 3], [4, 1]],
  make() {
    const box = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' });
    return { body: box, on: { 'ai.sessions': (p) => clear(box, p.sessions.length
      ? h('div', { class: 'list' }, p.sessions.map((s) => h('div', { class: 'item' },
        h('span', { class: 'grow ellipsis' }, h('b', null, s.project), h('span', { class: 'muted' }, `  ${TOOL_NAMES[s.tool] || s.tool}`)), statePill(s.state))))
      : emptyState('spark', 'No active AI coding sessions')) } };
  },
});

function resetsIn(isoTime) {
  if (!isoTime) return '';
  const minutes = Math.round((Date.parse(isoTime) - Date.now()) / 60000);
  if (minutes <= 0) return 'resetting';
  const d = Math.floor(minutes / 1440), hrs = Math.floor((minutes % 1440) / 60), m = minutes % 60;
  return `resets in ${d ? `${d}d ${hrs}h` : hrs ? `${hrs}h ${m}m` : `${m}m`}`;
}

const SVG = 'http://www.w3.org/2000/svg';
/** A ring gauge. Returns {el, set(percent)}; the arc turns amber at 75% and red at 90%. */
export function ring(label) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('class', 'ring');
  const circle = (cls) => { const c = document.createElementNS(SVG, 'circle'); c.setAttribute('cx', 50); c.setAttribute('cy', 50); c.setAttribute('r', 42); c.setAttribute('class', cls); svg.append(c); return c; };
  circle('ring-track');
  const arc = circle('ring-arc');
  const length = 2 * Math.PI * 42;
  arc.style.strokeDasharray = length;
  const value = h('b'), detail = h('div', { class: 'sub ellipsis' });
  const el = h('div', { class: 'gauge' }, h('div', { class: 'gauge-dial' }, svg, h('div', { class: 'gauge-num' }, value, h('small', null, '%'))), h('div', { class: 'gauge-label ellipsis' }, label), detail);
  return { el, set(percent, text) {
    const shown = Math.max(0, Math.min(100, percent));
    arc.style.strokeDashoffset = length * (1 - shown / 100);
    arc.style.stroke = `var(${shown >= 90 ? '--danger' : shown >= 75 ? '--warn' : '--claude'})`;
    value.textContent = Math.round(percent);
    detail.textContent = text || '';
  } };
}

/** Two rings for Claude plan usage; shared by the widget and the AI tab. */
export function usageRings() {
  const five = ring('5-hour session'), week = ring('Weekly');
  const rings = h('div', { class: 'gauges' }, five.el, week.el);
  const box = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column;justify-content:center' });
  let last = null;
  const paint = () => {
    if (!last) return;
    if (!last.five_hour && !last.seven_day) return clear(box, emptyState('spark', 'No usage data yet.', 'It appears after the first reply in a Claude Code terminal session on a subscription plan.'));
    if (!rings.isConnected) clear(box, rings);
    five.el.hidden = !last.five_hour; week.el.hidden = !last.seven_day;
    if (last.five_hour) five.set(last.five_hour.percent, resetsIn(last.five_hour.resets_at));
    if (last.seven_day) week.set(last.seven_day.percent, resetsIn(last.seven_day.resets_at));
  };
  const timer = setInterval(paint, 60000); // keeps "resets in" current
  return { el: box, update(p) { last = p; paint(); }, destroy: () => clearInterval(timer) };
}

define('usage', {
  name: 'Claude usage', icon: 'spark', topics: ['ai.usage'], sizes: [[4, 2], [2, 2]],
  make() {
    const rings = usageRings();
    return { body: rings.el, destroy: rings.destroy, on: { 'ai.usage': rings.update } };
  },
});

const CHECK_PILL = { passing: ['ok', 'checks pass'], failing: ['danger', 'checks fail'], running: ['warn', 'checks running'] };
const REVIEW_PILL = { approved: ['ok', 'approved'], changes_requested: ['danger', 'changes asked'], review_required: ['', 'needs review'] };
const shortRepo = (name) => (name || '').split('/').pop();

/** Rows for pull requests; shared by the widget and the Dev tab. */
export function prRow(pr, { mine = true } = {}) {
  const check = CHECK_PILL[pr.checks], review = REVIEW_PILL[pr.review];
  return h('div', { class: 'item pr' },
    h('span', { class: `prdot ${pr.draft ? 'draft' : pr.checks || 'none'}` }),
    h('div', { class: 'grow', style: 'min-width:0' }, h('div', { class: 'ellipsis', style: 'font-weight:600' }, pr.title),
      h('div', { class: 'sub ellipsis', style: 'margin-top:1px' }, `${shortRepo(pr.repo)} #${pr.number}${mine ? '' : ` · ${pr.author || ''}`} · ${fmt.ago(pr.updated_at)}`)),
    pr.draft ? h('span', { class: 'pill' }, 'draft') : review ? h('span', { class: `pill ${review[0]}` }, review[1]) : check ? h('span', { class: `pill ${check[0]}` }, check[1]) : null);
}

define('github', {
  name: 'GitHub', icon: 'git', topics: ['github.overview'], sizes: [[4, 3], [4, 2], [4, 4]],
  options: [{ key: 'show', label: 'Show', type: 'choice', default: 'all', choices: [['all', 'Pull requests and activity'], ['prs', 'Pull requests only'], ['activity', 'Activity only']] }],
  make(widget) {
    const box = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column;overflow:hidden' }), aside = h('span');
    const show = widget.config.show || 'all';
    return { aside, body: box, on: { 'github.overview': (p) => {
      aside.textContent = p.login ? `@${p.login}` : '';
      if (p.error) return clear(box, emptyState('git', 'GitHub could not be reached.'));
      const rows = [];
      if (show !== 'activity') {
        p.review_requests.forEach((pr) => rows.push(prRow(pr, { mine: false })));
        p.prs.forEach((pr) => rows.push(prRow(pr)));
      }
      if (show !== 'prs') p.activity.forEach((a) => rows.push(h('div', { class: 'item' },
        h('span', { class: 'grow ellipsis' }, h('b', null, shortRepo(a.repo)), h('span', { class: 'muted' }, `  ${a.text}`)), h('span', { class: 'num faint', style: 'font-size:11.5px' }, fmt.ago(a.at)))));
      clear(box, rows.length ? h('div', { class: 'list' }, rows) : emptyState('git', 'No open pull requests or recent activity.'));
    } } };
  },
});

define('projects', {
  name: 'Git projects', icon: 'git', topics: ['dev.projects'], sizes: [[4, 2], [4, 3], [4, 1]],
  make() {
    const box = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' });
    return { body: box, on: { 'dev.projects': (p) => clear(box, p.projects.length
      ? h('div', { class: 'list' }, p.projects.map((project) => h('div', { class: 'item' },
        h('b', { class: 'ellipsis' }, project.name), h('span', { class: 'grow ellipsis muted num' }, project.branch || project.error || ''),
        project.error ? null : h('span', { class: `pill ${project.dirty ? 'warn' : 'ok'}` }, project.dirty ? `${project.changed_files + project.untracked_files} changed` : 'clean'))))
      : emptyState('git', 'Add projects in config.toml on the PC')) } };
  },
});

define('tasks', {
  name: 'Tasks', icon: 'check', topics: ['sync'], sizes: [[4, 2], [4, 3], [2, 2]],
  make(widget) {
    const list = h('div', { class: 'list' }), box = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column' }, list);
    const aside = h('span');
    const load = async () => {
      let tasks;
      try { tasks = (await get('/tasks')).tasks.filter((t) => !t.done); } catch { return; }
      aside.textContent = tasks.length ? `${tasks.length} open` : '';
      clear(box, tasks.length ? clear(list, tasks.slice(0, widget.size.h * 3 - 1).map((task) => h('div', { class: 'item' },
        h('button', { class: 'check', style: 'width:20px;height:20px', 'aria-label': 'Done', onclick: async () => { await patch(`/tasks/${task.id}`, { done: true }).catch(() => {}); load(); } }),
        h('span', { class: 'grow ellipsis' }, task.title), task.priority && h('span', { class: 'star on' }, icon('star')))))
        : emptyState('check', 'Nothing open. Add tasks in Focus.'));
    };
    load();
    return { aside, body: box, on: { sync: (p) => p.what === 'tasks' && load() } };
  },
});

define('media', {
  name: 'Media', icon: 'music', topics: [], sizes: [[4, 1], [2, 2]],
  make() {
    const press = (actionId) => act(actionId);
    const keys = [['media.prev', 'prev'], ['media.play_pause', 'play'], ['media.next', 'next'], ['volume.down', 'vol-down'], ['volume.mute', 'mute'], ['volume.up', 'vol-up']];
    return { bare: true, body: h('div', { class: 'row', style: 'flex:1;justify-content:space-around;flex-wrap:wrap' },
      keys.map(([actionId, name]) => h('button', { class: 'iconbtn', style: 'color:var(--text)', 'aria-label': actionId, onclick: () => press(actionId) }, icon(name)))) };
  },
});

const act = async (actionId, params) => {
  try { await runAction(actionId, (message) => confirmDialog({ title: message, danger: true }), params); } catch (error) { toast(error.message, 'error'); }
};

define('volume', {
  name: 'Volume', icon: 'vol-up', topics: ['media.state'], sizes: [[4, 1]],
  make() {
    const slider = h('input', { type: 'range', class: 'slider', min: 0, max: 100, step: 1, value: 0, 'aria-label': 'Volume' });
    const value = h('span', { class: 'num', style: 'width:38px;text-align:right' }, '–');
    const mute = h('button', { class: 'iconbtn', style: 'color:var(--text)', 'aria-label': 'Mute', onclick: () => act('volume.mute') }, icon('vol-up'));
    const row = h('div', { class: 'row', style: 'flex:1' }, mute, slider, value);
    const box = h('div', { style: 'flex:1;display:flex' }, row);
    let touched = 0, pending = null, timer = 0;
    const paint = () => { slider.style.setProperty('--fill', `${slider.value}%`); value.textContent = slider.value; };
    // While a finger is on the slider, send at most one request every 120 ms; the last position always wins.
    const flush = () => { timer = 0; if (pending == null) return; const level = pending; pending = null; act('volume.set', { level }); timer = setTimeout(flush, 120); };
    slider.addEventListener('input', () => { touched = Date.now(); pending = Number(slider.value); paint(); if (!timer) flush(); });
    return { bare: true, body: box, destroy: () => clearTimeout(timer), on: { 'media.state': (p) => {
      if (p.volume == null) return clear(box, emptyState('vol-up', 'Volume level is not available on this PC'));
      if (!row.isConnected) clear(box, row);
      mute.replaceChildren(icon(p.muted ? 'mute' : 'vol-up'));
      if (Date.now() - touched > 1500) { slider.value = p.volume; paint(); } // do not fight the finger
    } } };
  },
});

define('nowplaying', {
  name: 'Now playing', icon: 'music', topics: ['media.state'], sizes: [[4, 3], [4, 2], [4, 1]],
  make(widget) {
    const rows = widget.size.h;
    const cover = h('div', { class: 'cover' }, icon('music'));
    const app = h('div', { class: 'np-app ellipsis' }), title = h('div', { class: 'np-title ellipsis' }), detail = h('div', { class: 'np-detail ellipsis' });
    const fill = h('i'), elapsed = h('span'), total = h('span');
    const bar = h('div', { class: 'np-progress' }, h('div', { class: 'bar thin' }, fill), h('div', { class: 'np-times num' }, elapsed, total));
    const key = (name, label, actionId, cls = 'iconbtn') => h('button', { class: cls, 'aria-label': label, onclick: () => act(actionId) }, icon(name));
    const play = key('play', 'Play or pause', 'media.play_pause', 'playbtn');
    const controls = h('div', { class: 'np-keys' }, key('prev', 'Previous', 'media.prev'), play, key('next', 'Next', 'media.next'));

    // Volume lives in the tall version, next to the transport keys.
    const slider = h('input', { type: 'range', class: 'slider', min: 0, max: 100, step: 1, value: 0, 'aria-label': 'Volume' });
    const level = h('span', { class: 'num muted', style: 'width:30px;text-align:right;font-size:12.5px' });
    const mute = h('button', { class: 'iconbtn', 'aria-label': 'Mute', onclick: () => act('volume.mute') }, icon('vol-up'));
    const volume = h('div', { class: 'row grow', style: 'min-width:0' }, mute, slider, level);
    let touched = 0, pending = null, timer = 0;
    const paintVolume = () => { slider.style.setProperty('--fill', `${slider.value}%`); level.textContent = slider.value; };
    const flush = () => { timer = 0; if (pending == null) return; const value = pending; pending = null; act('volume.set', { level: value }); timer = setTimeout(flush, 120); };
    slider.addEventListener('input', () => { touched = Date.now(); pending = Number(slider.value); paintVolume(); if (!timer) flush(); });

    // Progress keeps moving between samples: the agent reports every 2 s, the bar updates 4 times a second.
    let state = null, artId = null, artUrl = null;
    const paintProgress = () => {
      if (!state || state.duration == null) { bar.style.visibility = 'hidden'; return; }
      bar.style.visibility = '';
      const position = Math.min(state.duration, state.position + (state.playing ? (Date.now() - state.at) / 1000 : 0));
      fill.style.width = `${(100 * position) / state.duration}%`;
      elapsed.textContent = fmt.clock(position); total.textContent = fmt.clock(state.duration);
    };
    const ticker = setInterval(paintProgress, 250);
    const setArt = async (id) => {
      if (id === artId) return;
      artId = id;
      const blob = id ? await getBlob(`/media/art?v=${id}`) : null;
      if (id !== artId) return; // the track changed again while this one was loading
      if (artUrl) URL.revokeObjectURL(artUrl);
      artUrl = blob ? URL.createObjectURL(blob) : null;
      cover.replaceChildren(artUrl ? h('img', { src: artUrl, alt: '' }) : icon('music'));
    };

    const text = h('div', { class: 'grow', style: 'min-width:0' }, app, title, detail);
    const body = rows === 1
      ? h('div', { class: 'np np-compact' }, cover, text, controls)
      : h('div', { class: 'np' }, h('div', { class: 'np-top' }, cover, text), bar, h('div', { class: 'np-bottom' }, controls, rows >= 3 ? volume : null));
    return {
      bare: true, body,
      destroy() { clearInterval(ticker); clearTimeout(timer); if (artUrl) URL.revokeObjectURL(artUrl); },
      on: { 'media.state': (p) => {
        app.textContent = p.title ? (p.app || 'Now playing') : '';
        title.textContent = p.title || 'Nothing playing';
        detail.textContent = [p.artist, p.album].filter(Boolean).join(' · ');
        play.replaceChildren(icon(p.playing ? 'pause' : 'play'));
        state = p.duration_s == null ? null : { position: p.position_s, duration: p.duration_s, playing: p.playing, at: Date.now() };
        paintProgress();
        setArt(p.art);
        volume.style.visibility = p.volume == null ? 'hidden' : '';
        if (p.volume != null) {
          mute.replaceChildren(icon(p.muted ? 'mute' : 'vol-up'));
          if (Date.now() - touched > 1500) { slider.value = p.volume; paintVolume(); }
        }
      } },
    };
  },
});

/** Builds the card for one widget and wires its topics. Returns {el, destroy}. */
export function mountWidget(widget) {
  const spec = registry.get(widget.type);
  const { x, y } = widget.position;
  const el = h('section', { class: 'card widget', style: `grid-column: ${x + 1} / span ${widget.size.w}; grid-row: ${y + 1} / span ${widget.size.h}` });
  if (!spec) {
    el.append(h('header', null, h('span', { class: 'label' }, widget.name)), emptyState('info', 'This widget needs a newer Phoney.'));
    return { el, destroy() {} };
  }
  const missing = spec.topics.find((topic) => live.agent && !live.supports(topic));
  if (missing) {
    el.append(h('header', null, h('span', { class: 'label' }, widget.name)), emptyState(spec.icon, 'Not available on this PC'));
    return { el, destroy() {} };
  }
  const made = spec.make({ ...widget, config: widget.config || {} });
  if (spec.topics.some((topic) => topic !== 'sync')) el.dataset.live = '1';
  if (!made.bare) el.append(h('header', null, h('span', { class: 'label ellipsis' }, widget.name), made.aside && h('span', { class: 'aside' }, made.aside)));
  el.append(h('div', { class: 'body' }, made.body));
  const stops = Object.entries(made.on || {}).map(([topic, listener]) => live.subscribe(topic, listener));
  return { el, destroy() { stops.forEach((stop) => stop()); made.destroy?.(); } };
}

/** Places widgets left to right, top to bottom, into the first free slot. Returns them with positions. */
export function pack(widgets, cols = 4) {
  const taken = new Set();
  const free = (x, y, w, hgt) => { for (let i = x; i < x + w; i++) for (let j = y; j < y + hgt; j++) if (taken.has(`${i},${j}`)) return false; return true; };
  return widgets.map((widget) => {
    const w = Math.min(widget.size.w, cols), hgt = widget.size.h;
    for (let y = 0; ; y++) for (let x = 0; x + w <= cols; x++) if (free(x, y, w, hgt)) {
      for (let i = x; i < x + w; i++) for (let j = y; j < y + hgt; j++) taken.add(`${i},${j}`);
      return { ...widget, position: { x, y } };
    }
  });
}
