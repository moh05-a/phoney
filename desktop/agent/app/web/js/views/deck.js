// Control deck: groups of touch buttons. Each button points at an action on the PC's allowlist.

import { del, get, patch, post, runAction } from '../api.js';
import { live } from '../live.js';
import { BUTTON_ICONS, COLORS, clear, confirmDialog, emptyState, field, h, icon, sheet, toast } from '../ui.js';

export function deckView(shell) {
  let groups = [], actions = [], editing = false;
  const body = h('div', { class: 'scroll' });
  const editButton = h('button', { class: 'iconbtn', 'aria-label': 'Edit deck', onclick: () => { editing = !editing; render(); } }, icon('pencil'));
  const root = h('div', { class: 'screen' }, h('div', { class: 'topbar' }, h('h1', { class: 'grow' }, 'Control deck'), shell.status(), editButton, shell.settingsButton()), body);

  async function load() {
    try {
      [groups, actions] = await Promise.all([get('/deck').then((r) => r.groups), get('/actions').then((r) => r.actions)]);
      render();
    } catch (error) {
      if (!groups.length) clear(body, emptyState('alert', error.message));
    }
  }

  async function press(button, el) {
    if (!button.available) return toast('This action was removed from the PC allowlist.', 'warn');
    el.classList.add('busy');
    try {
      const result = await runAction(button.action_id, (message) => confirmDialog({ title: message, message: 'This runs on your PC right away.', confirm: button.label, danger: true }));
      if (result?.status === 'completed') toast(`${button.label}: exit code ${result.exit_code}`, result.exit_code === 0 ? 'ok' : 'warn', 4000);
      else if (result) { el.classList.add('pressed'); setTimeout(() => el.classList.remove('pressed'), 220); }
    } catch (error) {
      toast(error.details?.reason ? `${button.label} failed: ${error.details.reason.replaceAll('_', ' ')}` : error.message, 'error', 4000);
    } finally {
      el.classList.remove('busy');
    }
  }

  function render() {
    editButton.classList.toggle('on', editing);
    editButton.replaceChildren(icon(editing ? 'check' : 'pencil'));
    clear(body, h('div', { class: 'twocol' }, groups.map((group) => h('div', { class: 'section' },
      h('div', { class: 'head' }, h('span', { class: 'label' }, group.name),
        editing && h('button', { class: 'btn small', onclick: () => editGroup(group) }, 'Edit group')),
      h('div', { class: 'deck' },
        group.buttons.map((button) => {
          const el = h('button', { class: `key ${button.available ? '' : 'off'}`, dataset: { color: button.color }, onclick: () => (editing ? editButtonSheet(button, group) : press(button, el)) },
            icon(button.icon), h('span', { class: 'lbl' }, button.label), button.danger && h('i', { class: 'flag' }));
          return el;
        }),
        editing && h('button', { class: 'key add', onclick: () => editButtonSheet(null, group) }, icon('plus'), h('span', { class: 'lbl' }, 'Add')))))),
    editing && h('button', { class: 'btn block', style: 'margin-top:20px', onclick: () => editGroup(null) }, icon('plus'), 'New group'),
    !groups.length && !editing && emptyState('keys', 'No buttons yet.', 'Tap the pencil to add some.'));
  }

  const guard = async (work) => { try { await work(); } catch (error) { toast(error.message, 'error'); } await load(); };

  function editGroup(group) {
    sheet(group ? 'Edit group' : 'New group', (close) => {
      const name = h('input', { class: 'input', value: group?.name || '', maxLength: 40, placeholder: 'Group name' });
      const shift = (delta) => guard(async () => {
        const at = groups.indexOf(group), other = groups[at + delta];
        if (!other) return;
        await Promise.all([patch(`/deck/groups/${group.id}`, { order: at + delta }), patch(`/deck/groups/${other.id}`, { order: at })]);
        close();
      });
      return [field('Name', name),
        group && h('div', { class: 'row', style: 'margin-bottom:12px' }, h('button', { class: 'btn grow', onclick: () => shift(-1) }, icon('up'), 'Move up'), h('button', { class: 'btn grow', onclick: () => shift(1) }, icon('down'), 'Move down')),
        h('button', { class: 'btn primary block', onclick: () => guard(async () => {
          const value = name.value.trim();
          if (!value) return;
          await (group ? patch(`/deck/groups/${group.id}`, { name: value }) : post('/deck/groups', { name: value }));
          close();
        }) }, 'Save'),
        group && h('button', { class: 'btn ghost-danger block', style: 'margin-top:10px', onclick: async () => {
          if (!(await confirmDialog({ title: `Delete “${group.name}”?`, message: 'Its buttons are deleted too.', confirm: 'Delete', danger: true }))) return;
          guard(async () => { await del(`/deck/groups/${group.id}`); close(); });
        } }, 'Delete group')];
    });
  }

  function editButtonSheet(button, group) {
    const draft = { label: button?.label || '', icon: button?.icon || 'bolt', color: button?.color || 'accent', action_id: button?.action_id || actions[0]?.id, group_id: button?.group_id || group.id };
    sheet(button ? 'Edit button' : 'New button', (close) => {
      const label = h('input', { class: 'input', value: draft.label, maxLength: 40, placeholder: 'Label' });
      const action = h('select', { class: 'input', onchange: () => {
        draft.action_id = action.value;
        if (!label.value) label.value = actions.find((a) => a.id === action.value)?.name || '';
      } }, actions.map((a) => h('option', { value: a.id, selected: a.id === draft.action_id }, `${a.name}${a.danger ? '  (asks first)' : ''}`)));
      const groupSelect = h('select', { class: 'input', onchange: () => { draft.group_id = groupSelect.value; } }, groups.map((g) => h('option', { value: g.id, selected: g.id === draft.group_id }, g.name)));
      const picker = (items, key, render) => {
        const el = h('div', { class: 'picker' });
        const draw = () => clear(el, items.map((item) => h('button', { class: draft[key] === item ? 'active' : '', 'aria-label': item, onclick: () => { draft[key] = item; draw(); } }, render(item))));
        draw();
        return el;
      };
      const shift = (delta) => guard(async () => {
        const at = group.buttons.indexOf(button), other = group.buttons[at + delta];
        if (!other) return;
        await Promise.all([patch(`/deck/buttons/${button.id}`, { order: at + delta }), patch(`/deck/buttons/${other.id}`, { order: at })]);
        close();
      });
      return [
        field('Action (defined on the PC)', action), field('Label', label),
        field('Icon', picker(BUTTON_ICONS, 'icon', (name) => icon(name))),
        field('Color', picker(COLORS, 'color', (name) => h('span', { class: 'swatch', dataset: { color: name } }))),
        groups.length > 1 && field('Group', groupSelect),
        button && h('div', { class: 'row', style: 'margin-bottom:12px' }, h('button', { class: 'btn grow', onclick: () => shift(-1) }, icon('left'), 'Earlier'), h('button', { class: 'btn grow', onclick: () => shift(1) }, 'Later', icon('right'))),
        h('button', { class: 'btn primary block', onclick: () => guard(async () => {
          const payload = { ...draft, label: label.value.trim() || actions.find((a) => a.id === draft.action_id)?.name || 'Button' };
          await (button ? patch(`/deck/buttons/${button.id}`, payload) : post('/deck/buttons', payload));
          close();
        }) }, 'Save'),
        button && h('button', { class: 'btn ghost-danger block', style: 'margin-top:10px', onclick: () => guard(async () => { await del(`/deck/buttons/${button.id}`); close(); }) }, 'Delete button'),
        h('p', { class: 'muted', style: 'font-size:12.5px;margin:14px 0 0' }, 'To make a new action available, add it to actions.toml on the PC and restart the agent.'),
      ];
    });
  }

  const stopSync = live.subscribe('sync', (p) => p.what === 'deck' && load());
  load();
  return { el: root, destroy: stopSync };
}
