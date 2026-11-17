// Dashboard: pages of widgets on a grid, driven entirely by the layout document stored on the agent.

import { get, put, store } from '../api.js';
import { live } from '../live.js';
import { clear, confirmDialog, field, h, icon, segmented, sheet, toast } from '../ui.js';
import { mountWidget, pack, widgetSpec, widgetTypes } from '../widgets.js';

const CACHE = 'pbx.layout';
let uid = 0;
const newId = (prefix) => `${prefix}${Date.now().toString(36)}${(uid++).toString(36)}`;

function widget(type, w, hgt, name) {
  const spec = widgetSpec(type);
  return { id: newId('w'), type, name: name || spec.name, enabled: true, position: { x: 0, y: 0 }, size: { w, h: hgt }, config: {} };
}

// The Desk page fills a landscape phone exactly: 8 columns by 4 rows.
export function defaultLayout() {
  return { schema: 1, pages: [
    { id: 'desk', name: 'Desk', widgets: [widget('clock', 4, 2), widget('nowplaying', 4, 3), widget('usage', 4, 2), widget('ai', 4, 1)] },
    { id: 'system', name: 'System', widgets: [widget('cpu', 2, 2), widget('memory', 2, 2), widget('network', 4, 2), widget('disk', 4, 2), widget('processes', 4, 3)] },
    { id: 'work', name: 'Work', widgets: [widget('github', 4, 3), widget('projects', 4, 2), widget('tasks', 4, 2), widget('volume', 4, 1)] },
  ] };
}

// The layout that shipped before the desk-panel design. Still untouched? Then it is safe to replace.
const isFirstDefault = (layout) => layout.pages.length === 2 && layout.pages[0].id === 'system' && layout.pages[1].id === 'work'
  && layout.pages[0].widgets.map((w) => w.type).join() === 'cpu,memory,network,disk,processes';

const columns = () => Number(getComputedStyle(document.documentElement).getPropertyValue('--cols')) || 4;

export function dashboardView(shell) {
  let layout = store.get(CACHE) || defaultLayout();
  if (isFirstDefault(layout)) layout = defaultLayout();
  let pageIndex = Math.min(store.get('pbx.page', 0), layout.pages.length - 1);
  let editing = false;
  let mounted = [];

  const chips = h('div', { class: 'pages' });
  const dots = h('div', { class: 'dots' });
  const grid = h('div', { class: 'grid' });
  const editButton = h('button', { class: 'iconbtn', 'aria-label': 'Edit layout', onclick: () => { editing = !editing; if (!editing) save(); render(); } }, icon('pencil'));
  const scroll = h('div', { class: 'scroll' }, grid);
  const root = h('div', { class: 'screen' }, h('div', { class: 'topbar' }, chips, shell.status(), editButton, shell.menuButton(), shell.settingsButton()), scroll, dots);

  const page = () => layout.pages[pageIndex];

  async function save() {
    layout.pages.forEach((p) => { p.widgets = pack(p.widgets, 4); }); // stored positions are for the 4-column phone grid
    store.set(CACHE, layout);
    try { await put('/layout', layout); } catch (error) { toast(`Layout not saved: ${error.message}`, 'error'); }
  }

  function unmount() { mounted.forEach((m) => m.destroy()); mounted = []; }

  function render() {
    unmount();
    root.classList.toggle('editing', editing);
    editButton.classList.toggle('on', editing);
    editButton.replaceChildren(icon(editing ? 'check' : 'pencil'));
    clear(chips, layout.pages.map((p, i) => h('button', {
      class: `chip ${i === pageIndex ? 'active' : ''}`,
      onclick: () => { if (editing && i === pageIndex) return editPage(); pageIndex = i; store.set('pbx.page', i); render(); },
    }, p.name)), editing && h('button', { class: 'chip', 'aria-label': 'Add page', onclick: addPage }, '+ Page'));

    clear(dots, layout.pages.length > 1 && !editing && layout.pages.map((_, i) => h('button', { class: i === pageIndex ? 'on' : '', 'aria-label': `Page ${i + 1}`, onclick: () => { pageIndex = i; store.set('pbx.page', i); render(); } })));
    const visible = page().widgets.filter((w) => editing || w.enabled);
    const placed = pack(visible, columns());
    clear(grid, placed.map((w, index) => {
      const m = mountWidget(w);
      mounted.push(m);
      if (editing) m.el.append(tools(visible[index]));
      if (!w.enabled) m.el.style.opacity = '.45';
      return m.el;
    }), editing && h('button', { class: 'card widget key add', style: 'grid-column: span 2; grid-row: span 1; aspect-ratio:auto', onclick: addWidget }, icon('plus'), 'Add widget'));
  }

  function tools(target) {
    const list = page().widgets, at = list.indexOf(target);
    const move = (delta) => { const to = at + delta; if (to < 0 || to >= list.length) return; list.splice(at, 1); list.splice(to, 0, target); render(); };
    const resize = () => {
      const sizes = widgetSpec(target.type)?.sizes || [[2, 2]];
      const current = sizes.findIndex(([w, hgt]) => w === target.size.w && hgt === target.size.h);
      const [w, hgt] = sizes[(current + 1) % sizes.length];
      target.size = { w, h: hgt }; render();
    };
    const button = (name, label, fn, cls = '') => h('button', { class: cls, 'aria-label': label, onclick: fn }, icon(name));
    return h('div', { class: 'edit-tools' }, h('span', { class: 'label nm' }, target.name),
      button('left', 'Move earlier', () => move(-1)), button('right', 'Move later', () => move(1)), button('resize', 'Change size', resize),
      button('gear', 'Widget settings', () => widgetSettings(target)),
      button('trash', 'Remove', () => { list.splice(at, 1); render(); }, 'rm'));
  }

  function widgetSettings(target) {
    const spec = widgetSpec(target.type);
    const draft = { ...(target.config || {}) };
    let enabled = target.enabled;
    sheet(`${spec?.name || 'Widget'} settings`, (close) => {
      const name = h('input', { class: 'input', value: target.name, maxLength: 40 });
      const controls = (spec?.options || []).map((option) => {
        const current = draft[option.key] ?? option.default;
        if (option.type === 'bool') return field(option.label, segmented([['on', 'On'], ['off', 'Off']], current ? 'on' : 'off', (v) => { draft[option.key] = v === 'on'; }));
        const choices = typeof option.choices === 'function' ? option.choices() : option.choices;
        // Keep a saved value selectable even if it is not present right now (an unplugged adapter or drive).
        if (current && !choices.some(([value]) => value === current)) choices.push([current, `${current} (not present)`]);
        const select = h('select', { class: 'input', onchange: () => { draft[option.key] = select.value; } },
          choices.map(([value, label]) => h('option', { value, selected: value === current }, label)));
        return field(option.label, select);
      });
      return [field('Name', name), controls,
        field('Shown on the page', segmented([['on', 'Shown'], ['off', 'Hidden']], enabled ? 'on' : 'off', (v) => { enabled = v === 'on'; })),
        h('button', { class: 'btn primary block', onclick: () => {
          target.name = name.value.trim() || target.name; target.config = draft; target.enabled = enabled; close(); render();
        } }, 'Done')];
    });
  }

  function addWidget() {
    sheet('Add widget', (close) => h('div', { class: 'card rows' }, widgetTypes().map((spec) => {
      const unavailable = spec.topics.some((topic) => !live.supports(topic));
      return h('button', { class: 'rowitem', onclick: () => { const [w, hgt] = spec.sizes[0]; page().widgets.push(widget(spec.type, w, hgt)); close(); render(); } },
        icon(spec.icon), h('div', { class: 'grow' }, h('div', { class: 't' }, spec.name), unavailable && h('div', { class: 'd' }, 'Not available on this PC')), icon('plus'));
    })));
  }

  function addPage() {
    layout.pages.push({ id: newId('p'), name: `Page ${layout.pages.length + 1}`, widgets: [] });
    pageIndex = layout.pages.length - 1;
    render();
    editPage();
  }

  function editPage() {
    const current = page();
    sheet('Page', (close) => {
      const name = h('input', { class: 'input', value: current.name, maxLength: 30 });
      const shift = (delta) => { const to = pageIndex + delta; if (to < 0 || to >= layout.pages.length) return; layout.pages.splice(pageIndex, 1); layout.pages.splice(to, 0, current); pageIndex = to; render(); };
      return [field('Name', name),
        h('div', { class: 'row', style: 'margin-bottom:12px' },
          h('button', { class: 'btn grow', onclick: () => shift(-1) }, icon('left'), 'Move left'), h('button', { class: 'btn grow', onclick: () => shift(1) }, 'Move right', icon('right'))),
        h('button', { class: 'btn primary block', onclick: () => { current.name = name.value.trim() || current.name; close(); render(); } }, 'Done'),
        layout.pages.length > 1 && h('button', { class: 'btn ghost-danger block', style: 'margin-top:10px', onclick: async () => {
          if (!(await confirmDialog({ title: `Delete “${current.name}”?`, message: 'Its widgets are removed with it.', confirm: 'Delete', danger: true }))) return;
          layout.pages.splice(pageIndex, 1); pageIndex = Math.max(0, pageIndex - 1); close(); render();
        } }, 'Delete page')];
    });
  }

  // Swipe left / right to change page.
  let startX = 0, startY = 0;
  scroll.addEventListener('touchstart', (e) => { startX = e.touches[0].clientX; startY = e.touches[0].clientY; }, { passive: true });
  scroll.addEventListener('touchend', (e) => {
    const dx = e.changedTouches[0].clientX - startX, dy = e.changedTouches[0].clientY - startY;
    if (editing || Math.abs(dx) < 70 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
    const next = pageIndex + (dx < 0 ? 1 : -1);
    if (next >= 0 && next < layout.pages.length) { pageIndex = next; store.set('pbx.page', next); render(); }
  }, { passive: true });

  async function refresh() {
    try {
      const remote = (await get('/layout')).layout;
      if (remote && isFirstDefault(remote)) { layout = defaultLayout(); pageIndex = 0; render(); return save(); }
      if (remote && !editing && JSON.stringify(remote) !== JSON.stringify(layout)) {
        layout = remote; store.set(CACHE, layout); pageIndex = Math.min(pageIndex, layout.pages.length - 1); render();
      } else if (!remote) save(); // first run: give the agent the default layout
    } catch { /* offline: keep showing the cached layout */ }
  }

  const onResize = () => { if (columnsNow !== columns()) { columnsNow = columns(); render(); } };
  let columnsNow = columns();
  window.addEventListener('resize', onResize);
  const stopSync = live.subscribe('sync', (p) => p.what === 'layout' && refresh());
  const onState = () => { if (live.state === 'online') render(); }; // capabilities may have changed
  live.addEventListener('state', onState);

  const onReset = () => { layout = defaultLayout(); pageIndex = 0; editing = false; render(); save(); };
  window.addEventListener('pbx-reset-layout', onReset);
  render();
  refresh();
  return { el: root, destroy() { unmount(); stopSync(); window.removeEventListener('pbx-reset-layout', onReset); window.removeEventListener('resize', onResize); live.removeEventListener('state', onState); } };
}
