// Productivity: tasks and notes live on the agent; timers run on the phone.

import { del, get, patch, post, store } from '../api.js';
import { live } from '../live.js';
import { chime, clear, emptyState, field, fmt, h, icon, segmented, sheet, toast } from '../ui.js';

// ---- timers: stored as timestamps, so they stay correct while the app is in the background ----
const MIN = 60000;
const POMODORO = { focus: 25 * MIN, break: 5 * MIN, long: 15 * MIN };
const timers = store.get('pbx.timers') || {
  pomodoro: { phase: 'focus', endsAt: null, remaining: POMODORO.focus, done: 0 },
  stopwatch: { startedAt: null, elapsed: 0 },
  countdown: { endsAt: null, remaining: 10 * MIN, total: 10 * MIN },
};
const persist = () => store.set('pbx.timers', timers);
const left = (t) => (t.endsAt ? Math.max(0, t.endsAt - Date.now()) : t.remaining);
const toggle = (t) => { if (t.endsAt) { t.remaining = left(t); t.endsAt = null; } else if (t.remaining > 0) t.endsAt = Date.now() + t.remaining; persist(); };

function advancePomodoro() {
  const p = timers.pomodoro;
  if (p.phase === 'focus') { p.done += 1; p.phase = p.done % 4 === 0 ? 'long' : 'break'; } else p.phase = 'focus';
  p.endsAt = null; p.remaining = POMODORO[p.phase];
}

// Runs for the whole session, whichever tab is showing, so a finished timer always announces itself.
setInterval(() => {
  const p = timers.pomodoro, c = timers.countdown;
  if (p.endsAt && left(p) === 0) {
    const wasFocus = p.phase === 'focus';
    advancePomodoro(); persist(); chime([660, 880, 1320]);
    toast(wasFocus ? 'Focus session done. Take a break.' : 'Break over. Back to it.', 'ok', 6000);
  }
  if (c.endsAt && left(c) === 0) { c.endsAt = null; c.remaining = 0; persist(); chime([880, 880, 1320]); toast('Countdown finished.', 'ok', 6000); }
}, 500);

export function focusView(shell) {
  let tab = store.get('pbx.focusTab', 'tasks');
  let tasks = [], notes = [], ticker = 0;
  const body = h('div', { class: 'scroll' });
  const root = h('div', { class: 'screen' }, h('div', { class: 'topbar' }, h('h1', { class: 'grow' }, 'Focus'), shell.status(), shell.settingsButton()),
    h('div', { style: 'padding:0 16px 8px' }, segmented([['tasks', 'Tasks'], ['notes', 'Notes'], ['timers', 'Timers']], tab, (value) => { tab = value; store.set('pbx.focusTab', value); render(); })),
    body);

  const guard = async (work) => { try { await work(); } catch (error) { toast(error.message, 'error'); } };
  const load = async () => guard(async () => {
    [tasks, notes] = await Promise.all([get('/tasks').then((r) => r.tasks), get('/notes').then((r) => r.notes)]);
    if (tab !== 'timers') render();
  });

  function renderTasks() {
    const input = h('input', { class: 'input', placeholder: 'Add a task', maxLength: 200, enterKeyHint: 'done' });
    const add = () => guard(async () => { const title = input.value.trim(); if (!title) return; input.value = ''; await post('/tasks', { title }); await load(); });
    input.addEventListener('keydown', (e) => e.key === 'Enter' && add());
    const row = (task) => h('div', { class: `rowitem ${task.done ? 'done' : ''}` },
      h('button', { class: `check ${task.done ? 'on' : ''}`, 'aria-label': 'Toggle done', onclick: () => guard(async () => { await patch(`/tasks/${task.id}`, { done: !task.done }); await load(); }) }, task.done && icon('check')),
      h('div', { class: 'grow t', style: 'font-weight:500;overflow-wrap:anywhere' }, task.title),
      !task.done && h('button', { class: `iconbtn star ${task.priority ? 'on' : ''}`, 'aria-label': 'Priority', onclick: () => guard(async () => { await patch(`/tasks/${task.id}`, { priority: !task.priority }); await load(); }) }, icon('star')),
      h('button', { class: 'iconbtn', 'aria-label': 'Delete', onclick: () => guard(async () => { await del(`/tasks/${task.id}`); await load(); }) }, icon('x')));
    const priorities = tasks.filter((t) => !t.done && t.priority), open = tasks.filter((t) => !t.done && !t.priority), done = tasks.filter((t) => t.done);
    const block = (title, items) => items.length ? h('div', { class: 'section' }, h('div', { class: 'head' }, h('span', { class: 'label' }, title)), h('div', { class: 'card rows' }, items.map(row))) : null;
    clear(body, h('div', { class: 'row' }, h('div', { class: 'grow' }, input), h('button', { class: 'btn primary', 'aria-label': 'Add task', onclick: add }, icon('plus'))),
      block("Today's priorities", priorities), block('Open', open), block('Done', done.slice(0, 20)),
      !tasks.length && emptyState('check', 'No tasks yet.', 'Star a task to make it a priority for today.'));
  }

  function editNote(note) {
    sheet(note ? 'Edit note' : 'New note', (close) => {
      const text = h('textarea', { class: 'input', maxLength: 4000, placeholder: 'Write something…' });
      text.value = note?.text || '';
      return [field('Note', text),
        h('button', { class: 'btn primary block', onclick: () => guard(async () => {
          const value = text.value.trim(); if (!value) return;
          await (note ? patch(`/notes/${note.id}`, { text: value }) : post('/notes', { text: value })); close(); await load();
        }) }, 'Save'),
        note && h('button', { class: 'btn ghost-danger block', style: 'margin-top:10px', onclick: () => guard(async () => { await del(`/notes/${note.id}`); close(); await load(); }) }, 'Delete note')];
    });
  }

  function renderNotes() {
    clear(body, h('button', { class: 'btn block', style: 'margin-bottom:12px', onclick: () => editNote(null) }, icon('plus'), 'New note'),
      notes.map((note) => h('button', { class: 'card note', onclick: () => editNote(note) }, note.text, h('div', { class: 'd faint', style: 'font-size:11.5px;margin-top:6px' }, fmt.ago(note.updated_at)))),
      !notes.length && emptyState('note', 'No notes yet.'));
  }

  function renderTimers() {
    const p = timers.pomodoro, s = timers.stopwatch, c = timers.countdown;
    const pClock = h('div', { class: 'clock' }), sClock = h('div', { class: 'clock' }), cClock = h('div', { class: 'clock' });
    const names = { focus: 'Focus', break: 'Short break', long: 'Long break' };
    const pomodoro = h('div', { class: `card timer ${p.endsAt ? (p.phase === 'focus' ? 'running' : 'break') : ''}` },
      h('div', { class: 'row', style: 'justify-content:center' }, h('span', { class: 'label' }, 'Pomodoro'), h('span', { class: `pill ${p.phase === 'focus' ? 'accent' : 'ok'}` }, names[p.phase]), h('span', { class: 'pill' }, `${p.done} done`)),
      pClock, h('div', { class: 'actions' },
        h('button', { class: 'btn primary', onclick: () => { toggle(p); renderTimers(); } }, icon(p.endsAt ? 'pause' : 'play'), p.endsAt ? 'Pause' : 'Start'),
        h('button', { class: 'btn', onclick: () => { advancePomodoro(); persist(); renderTimers(); } }, 'Skip'),
        h('button', { class: 'btn', onclick: () => { Object.assign(p, { phase: 'focus', endsAt: null, remaining: POMODORO.focus, done: 0 }); persist(); renderTimers(); } }, 'Reset')));
    const stopwatch = h('div', { class: `card timer ${s.startedAt ? 'running' : ''}` }, h('span', { class: 'label' }, 'Stopwatch'), sClock, h('div', { class: 'actions' },
      h('button', { class: 'btn primary', onclick: () => { if (s.startedAt) { s.elapsed += Date.now() - s.startedAt; s.startedAt = null; } else s.startedAt = Date.now(); persist(); renderTimers(); } }, icon(s.startedAt ? 'pause' : 'play'), s.startedAt ? 'Stop' : 'Start'),
      h('button', { class: 'btn', onclick: () => { s.startedAt = null; s.elapsed = 0; persist(); renderTimers(); } }, 'Reset')));
    const setCountdown = (minutes) => { c.total = c.remaining = minutes * MIN; c.endsAt = null; persist(); renderTimers(); };
    const countdown = h('div', { class: `card timer ${c.endsAt ? 'running' : ''}` }, h('span', { class: 'label' }, 'Countdown'), cClock,
      h('div', { class: 'actions', style: 'margin-bottom:10px' }, [1, 5, 10, 15, 30, 60].map((m) => h('button', { class: 'btn small', onclick: () => setCountdown(m) }, `${m}m`))),
      h('div', { class: 'actions' },
        h('button', { class: 'btn primary', onclick: () => { if (!c.endsAt && c.remaining === 0) c.remaining = c.total; toggle(c); renderTimers(); } }, icon(c.endsAt ? 'pause' : 'play'), c.endsAt ? 'Pause' : 'Start'),
        h('button', { class: 'btn', onclick: () => setCountdown(c.total / MIN) }, 'Reset')));
    const phase = p.phase, running = [!!p.endsAt, !!c.endsAt];
    const paint = () => {
      if (p.phase !== phase || !!p.endsAt !== running[0] || !!c.endsAt !== running[1]) return renderTimers(); // a timer finished
      pClock.textContent = fmt.clock(Math.ceil(left(p) / 1000));
      sClock.textContent = fmt.clock((s.elapsed + (s.startedAt ? Date.now() - s.startedAt : 0)) / 1000);
      cClock.textContent = fmt.clock(Math.ceil(left(c) / 1000));
    };
    clearInterval(ticker);
    clear(body, h('div', { class: 'twocol' }, pomodoro, h('div', null, stopwatch, countdown)));
    paint();
    ticker = setInterval(paint, 250);
  }

  function render() {
    clearInterval(ticker);
    ({ tasks: renderTasks, notes: renderNotes, timers: renderTimers })[tab]();
  }

  const stopSync = live.subscribe('sync', (p) => (p.what === 'tasks' || p.what === 'notes') && load());
  render();
  load();
  return { el: root, destroy() { clearInterval(ticker); stopSync(); } };
}
