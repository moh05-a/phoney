// App shell: pairing screen, tab navigation, connection status, settings, global alerts.

import { ApiError, authEvents, del, get, post, session, store } from './api.js';
import { live } from './live.js';
import { chime, clear, confirmDialog, field, h, icon, segmented, sheet, toast } from './ui.js';
import { dashboardView } from './views/dashboard.js';
import { deckView } from './views/deck.js';
import { focusView } from './views/focus.js';
import { aiView, devView } from './views/work.js';
import { TOOL_NAMES } from './widgets.js';

const app = document.getElementById('app');
const TABS = [
  ['dash', 'Panel', 'grid', dashboardView], ['deck', 'Deck', 'keys', deckView], ['dev', 'Dev', 'code', devView],
  ['ai', 'AI', 'spark', aiView], ['focus', 'Focus', 'target', focusView],
];
const STATE_TEXT = { online: 'Live', connecting: 'Reconnecting', offline: 'Offline', replaced: 'Open elsewhere', unauthorized: 'Not paired', incompatible: 'Update needed' };

// ---- theme ----
function applyTheme() {
  const choice = store.get('pbx.theme', 'dark');
  const theme = choice === 'auto' ? (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark') : choice;
  document.documentElement.dataset.theme = theme;
  document.querySelector('meta[name=theme-color]').content = theme === 'light' ? '#eef0f4' : '#0a0c0f';
}
matchMedia('(prefers-color-scheme: light)').addEventListener('change', applyTheme);
applyTheme();

// ---- keep the screen on while the panel is showing (needs a secure context; otherwise use Auto-Lock: Never) ----
let wakeLock = null;
async function keepAwake() {
  if (!('wakeLock' in navigator) || document.hidden || wakeLock) return;
  try { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); } catch { /* not allowed right now */ }
}
document.addEventListener('visibilitychange', keepAwake);

// ---- pairing ----
function pairScreen(note) {
  live.stop();
  const fromLink = new URLSearchParams(location.hash.slice(1)).get('pair') || '';
  const code = h('input', { class: 'input code-input', value: fromLink, placeholder: 'XXXX-XXXX', maxLength: 9, autocapitalize: 'characters', autocomplete: 'off', autocorrect: 'off', spellcheck: false });
  const name = h('input', { class: 'input', value: store.get('pbx.deviceName', /iPhone/.test(navigator.userAgent) ? 'iPhone' : 'Browser'), maxLength: 60 });
  const error = h('div', { class: 'error-text' }, note || '');
  const button = h('button', { class: 'btn primary block', onclick: pair }, 'Pair');
  const standalone = navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
  const isIos = /iPhone|iPad|iPod/.test(navigator.userAgent);

  async function pair() {
    error.textContent = '';
    button.disabled = true;
    try {
      const result = await post('/pair', { pairing_code: code.value, device_name: name.value.trim() || 'Phone', device_model: (navigator.userAgent.match(/\(([^;)]+)/) || [])[1] || '' });
      session.token = result.token;
      store.set('pbx.deviceName', name.value.trim());
      history.replaceState(null, '', location.pathname);
      mainScreen();
    } catch (err) {
      error.textContent = err instanceof ApiError ? err.message : 'Something went wrong.';
      button.disabled = false;
    }
  }
  code.addEventListener('keydown', (e) => e.key === 'Enter' && pair());

  clear(app, h('div', { class: 'screen' }, h('div', { class: 'center-screen' },
    h('div', { class: 'logo' }, icon('grid')),
    h('h1', null, 'Phoney'),
    h('p', { class: 'muted', style: 'margin:0 0 20px' }, 'Pair this phone with your PC. On the PC, open the agent page and choose “Pair a phone” to get a code.'),
    isIos && !standalone && h('div', { class: 'hint' }, h('b', null, 'Install first: '), 'tap Share, then “Add to Home Screen”, and open Phoney from the Home Screen. Pairing here in Safari does not carry over to the installed app.'),
    field('Pairing code', code), field('Name for this device', name), error, button)));
}

// ---- main ----
let current = null;

function mainScreen() {
  let active = store.get('pbx.tab', 'dash');
  const content = h('div', { style: 'flex:1;min-height:0;display:flex;flex-direction:column;min-width:0' });
  const tabbar = h('nav', { class: 'tabbar' });
  const attention = new Set(); // tabs with something new to look at
  const chips = new Set();

  const shell = {
    status() {
      const chip = h('button', { class: 'conn', dataset: { state: live.state }, 'aria-label': 'Connection', onclick: () => (live.state === 'online' ? openSettings() : live.connect()) }, h('i'), h('span', null, STATE_TEXT[live.state]));
      chips.add(chip);
      return chip;
    },
    settingsButton: () => h('button', { class: 'iconbtn only-tabs', 'aria-label': 'Settings', onclick: openSettings }, icon('gear')),
    // Desk mode hides the tab bar on the panel; this button is the way to everything else.
    menuButton: () => h('button', { class: 'iconbtn only-desk', 'aria-label': 'Menu', onclick: openMenu }, icon('list')),
  };

  function show(tab) {
    active = tab;
    document.body.classList.toggle('desk', tab === 'dash' && store.get('pbx.desk', true));
    store.set('pbx.tab', tab);
    attention.delete(tab);
    current?.destroy();
    chips.clear();
    current = TABS.find((t) => t[0] === tab)[3](shell);
    clear(content, current.el);
    renderTabs();
  }

  function renderTabs() {
    clear(tabbar, TABS.map(([id, label, iconName]) => h('button', { class: `tab ${id === active ? 'active' : ''}`, onclick: () => show(id) }, icon(iconName), label, attention.has(id) && h('i', { class: 'dot' }))));
  }

  const onState = () => {
    document.body.classList.toggle('stale', live.state !== 'online');
    chips.forEach((chip) => { chip.dataset.state = live.state; chip.lastChild.textContent = STATE_TEXT[live.state]; });
    if (live.state === 'unauthorized') { teardown(); pairScreen('This phone was unpaired. Pair it again to continue.'); }
    if (live.state === 'online') {
      syncPrompts();
      // The agent was updated while this panel was open: load the new panel files.
      const build = live.agent?.panel_build, known = store.get('pbx.build');
      if (build && build !== known) { store.set('pbx.build', build); if (known) return location.reload(); }
    }
    if (live.state === 'incompatible') toast('The PC agent uses a newer protocol. Reload this page.', 'warn', 8000);
  };

  // AI alerts reach you on any tab.
  const stopAlerts = live.subscribe('ai.events', (event) => {
    if (event.kind !== 'finished' && event.kind !== 'needs_input') return;
    if (event.kind === 'needs_input' && promptBox.children.length) return; // the permission card already says it
    const tool = TOOL_NAMES[event.tool] || event.tool;
    toast(`${tool} · ${event.project}: ${event.kind === 'finished' ? 'finished' : 'needs your input'}`, event.kind === 'finished' ? 'ok' : 'warn', 7000);
    chime(event.kind === 'finished' ? [880, 1320] : [660, 660]);
    if (active !== 'ai') { attention.add('ai'); renderTabs(); }
  });

  function teardown() {
    live.removeEventListener('state', onState);
    authEvents.removeEventListener('unauthorized', onUnauthorized);
    stopAlerts();
    stopPrompts?.();
    promptBox.remove();
    window.removeEventListener('pbx-approve-changed', syncPrompts);
    document.body.classList.remove('desk');
    current?.destroy();
    current = null;
  }
  const onUnauthorized = () => { teardown(); pairScreen('This phone was unpaired. Pair it again to continue.'); };

  function openMenu() {
    sheet('Phoney', (close) => [
      h('div', { class: 'card rows', style: 'margin-bottom:14px' }, TABS.filter(([id]) => id !== 'dash').map(([id, label, iconName]) =>
        h('button', { class: 'rowitem', onclick: () => { close(); show(id); } }, icon(iconName), h('div', { class: 'grow t' }, { deck: 'Control deck', dev: 'Developer', ai: 'AI coding', focus: 'Focus' }[id] || label), attention.has(id) && h('span', { class: 'pill warn' }, 'new'), icon('right')))),
      h('button', { class: 'btn block', onclick: () => { close(); openSettings(); } }, icon('gear'), 'Settings'),
    ]);
  }

  // ---- Claude Code permission prompts: a card over whatever is showing ----
  const promptBox = h('div', { class: 'prompts' });
  document.getElementById('overlay-root').append(promptBox);
  const seenPrompts = new Set();
  let stopPrompts = null;
  const answer = async (request, decision, card) => {
    card.classList.add('busy');
    try { await post(`/ai/permissions/${request.id}`, { decision }); toast(decision === 'allow' ? 'Allowed' : 'Denied', decision === 'allow' ? 'ok' : 'warn'); }
    catch (error) { toast(error.message, 'error'); card.classList.remove('busy'); }
  };
  const renderPrompts = (payload) => {
    clear(promptBox, payload.requests.map((request) => {
      const left = Math.max(0, request.expires_in_s - (Date.now() - Date.parse(request.created_at)) / 1000);
      const card = h('div', { class: 'prompt' },
        h('div', { class: 'row' }, h('span', { class: 'prompt-mark' }, icon('spark')),
          h('div', { class: 'grow', style: 'min-width:0' }, h('div', { class: 'label', style: 'color:var(--claude)' }, `${TOOL_NAMES[request.tool] || request.tool} · ${request.project}`),
            h('div', { class: 't', style: 'font-size:16px;font-weight:650' }, `Allow ${request.tool_name}?`))),
        h('div', { class: 'prompt-what' }, request.summary),
        h('div', { class: 'bar thin prompt-timer' }, h('i', { style: `animation-duration:${left}s` })),
        h('div', { class: 'row', style: 'gap:10px' },
          h('button', { class: 'btn grow', onclick: () => answer(request, 'deny', card) }, 'Deny'),
          h('button', { class: 'btn primary grow', onclick: () => answer(request, 'allow', card) }, 'Allow')));
      if (!seenPrompts.has(request.id)) { seenPrompts.add(request.id); chime([660, 880, 660]); }
      return card;
    }));
  };
  const syncPrompts = () => {
    const wanted = store.get('pbx.approve', true) && live.hasFeature('claude_permissions');
    if (wanted && !stopPrompts) stopPrompts = live.subscribe('ai.permissions', renderPrompts);
    if (!wanted && stopPrompts) { stopPrompts(); stopPrompts = null; clear(promptBox); }
  };
  window.addEventListener('pbx-approve-changed', syncPrompts);

  function openSettings() {
    sheet('Settings', (close) => {
      const agent = live.agent;
      const row = (title, value) => h('div', { class: 'rowitem' }, h('div', { class: 'grow t', style: 'font-weight:500' }, title), h('span', { class: 'muted num', style: 'font-size:13px' }, value));
      return [
        field('Appearance', segmented([['dark', 'Dark'], ['light', 'Light'], ['auto', 'Auto']], store.get('pbx.theme', 'dark'), (value) => { store.set('pbx.theme', value); applyTheme(); show(active); })),
        field('Desk mode (panel fills the screen, tabs move into a menu)', segmented([['on', 'On'], ['off', 'Off']], store.get('pbx.desk', true) ? 'on' : 'off', (value) => { store.set('pbx.desk', value === 'on'); show(active); })),
        field('Alert sound', segmented([['on', 'On'], ['off', 'Off']], localStorage.getItem('pbx.sound') === 'off' ? 'off' : 'on', (value) => { localStorage.setItem('pbx.sound', value); if (value === 'on') chime(); })),
        h('div', { class: 'label', style: 'margin:4px 0 6px' }, 'Connection'),
        h('div', { class: 'card rows', style: 'margin-bottom:14px' },
          row('PC', agent?.name || '–'), row('Status', STATE_TEXT[live.state]), row('Agent version', agent?.agent_version || '–'), row('Protocol', agent?.protocol_version || '–'),
          row('Screen stays on', 'wakeLock' in navigator ? 'yes' : 'set Auto-Lock to Never')),
        h('button', { class: 'btn block', style: 'margin-bottom:10px', onclick: () => location.reload() }, icon('restart'), 'Reload panel'),
        h('button', { class: 'btn block', style: 'margin-bottom:10px', onclick: async () => {
          if (!(await confirmDialog({ title: 'Reset the panel layout?', message: 'Pages and widgets go back to the default desk layout.', confirm: 'Reset', danger: true }))) return;
          close(); show('dash'); window.dispatchEvent(new Event('pbx-reset-layout'));
        } }, icon('grid'), 'Reset panel layout'),
        h('button', { class: 'btn ghost-danger block', onclick: async () => {
          if (!(await confirmDialog({ title: 'Unpair this phone?', message: 'You will need a new pairing code from the PC.', confirm: 'Unpair', danger: true }))) return;
          close();
          try { await del('/devices/me'); } catch { /* the PC may be unreachable: forget the token anyway */ }
          session.token = null; teardown(); pairScreen();
        } }, 'Unpair this phone'),
      ];
    });
  }

  live.addEventListener('state', onState);
  authEvents.addEventListener('unauthorized', onUnauthorized);
  clear(app, content, tabbar);
  live.connect();
  show(TABS.some((t) => t[0] === active) ? active : 'dash');
  onState();
  keepAwake();
  get('/agent').catch(() => {}); // an early, cheap check that the token still works
}

live.start();
if (session.token) mainScreen(); else pairScreen();
