// Developer dashboard and AI coding dashboard.

import { get, store } from '../api.js';
import { live } from '../live.js';
import { clear, emptyState, fmt, h, icon, segmented, sheet } from '../ui.js';
import { TOOL_NAMES, prRow, statePill, usageRings } from '../widgets.js';

const section = (title, content, aside) => h('div', { class: 'section' }, h('div', { class: 'head' }, h('span', { class: 'label' }, title), aside), content);
const code = (text) => h('code', null, text);

export function devView(shell) {
  const projects = h('div'), ports = h('div'), apps = h('div'), github = h('div');
  const root = h('div', { class: 'screen' }, h('div', { class: 'topbar' }, h('h1', { class: 'grow' }, 'Developer'), shell.status(), shell.settingsButton()),
    h('div', { class: 'scroll' }, h('div', { class: 'twocol' }, h('div', null, section('GitHub', github), section('Local projects', projects)), h('div', null, section('Dev servers', ports), section('Running tools', apps)))));
  const stops = [];

  async function openProject(project) {
    const view = sheet(project.name, () => emptyState('git', 'Loading…'));
    try {
      const info = await get(`/dev/projects/${encodeURIComponent(project.id)}/git`);
      clear(view.content,
        h('div', { class: 'row', style: 'flex-wrap:wrap;margin-bottom:12px' },
          h('span', { class: 'pill accent' }, icon('git'), info.branch || 'detached'),
          h('span', { class: `pill ${info.dirty ? 'warn' : 'ok'}` }, info.dirty ? `${info.changed_files} changed · ${info.untracked_files} new` : 'clean'),
          (info.ahead || info.behind) ? h('span', { class: 'pill' }, `↑${info.ahead} ↓${info.behind}`) : null),
        h('div', { class: 'label', style: 'margin-bottom:6px' }, 'Recent commits'),
        info.commits.length ? h('div', { class: 'card rows' }, info.commits.map((c) => h('div', { class: 'rowitem' },
          h('span', { class: 'num muted', style: 'font-size:12px' }, c.hash), h('div', { class: 'grow' }, h('div', { class: 't ellipsis' }, c.subject), h('div', { class: 'd' }, `${c.author} · ${c.when}`)))))
          : emptyState('git', 'No commits yet'));
    } catch (error) { clear(view.content, emptyState('alert', error.message)); }
  }

  if (live.hasFeature('github') && live.supports('github.overview')) {
    stops.push(live.subscribe('github.overview', (p) => {
      if (p.error) return clear(github, h('div', { class: 'card' }, emptyState('git', 'GitHub could not be reached.')));
      const block = (title, rows) => rows.length ? [h('div', { class: 'label', style: 'margin:12px 0 6px' }, title), h('div', { class: 'card', style: 'padding:4px 14px' }, h('div', { class: 'list' }, rows))] : null;
      clear(github,
        block(`Waiting for your review`, p.review_requests.map((pr) => prRow(pr, { mine: false }))),
        p.prs.length ? h('div', { class: 'card', style: 'padding:4px 14px' }, h('div', { class: 'list' }, p.prs.map((pr) => prRow(pr)))) : h('div', { class: 'card' }, emptyState('git', 'You have no open pull requests.')),
        block('Recent activity', p.activity.slice(0, 8).map((a) => h('div', { class: 'item' }, h('span', { class: 'grow ellipsis' }, h('b', null, a.repo.split('/').pop()), h('span', { class: 'muted' }, `  ${a.text}`)), h('span', { class: 'num faint', style: 'font-size:11.5px' }, fmt.ago(a.at))))),
        block('Recently pushed', p.repos.slice(0, 5).map((r) => h('div', { class: 'item' }, h('span', { class: 'grow ellipsis' }, h('b', null, r.name.split('/').pop()), h('span', { class: 'muted num' }, r.branch ? `  ${r.branch}` : '')), r.private && h('span', { class: 'pill' }, 'private'), h('span', { class: 'num faint', style: 'font-size:11.5px' }, fmt.ago(r.pushed_at))))));
    }));
  } else clear(github, h('div', { class: 'card' }, emptyState('git', 'GitHub is not connected.', ['Install the GitHub CLI and run ', code('gh auth login'), ' on the PC, then restart the agent.'])));

  if (live.hasFeature('git')) {
    stops.push(live.subscribe('dev.projects', (p) => clear(projects, p.projects.length
      ? h('div', { class: 'card rows' }, p.projects.map((project) => h('button', { class: 'rowitem', onclick: () => !project.error && openProject(project) },
        icon('git'), h('div', { class: 'grow' }, h('div', { class: 't ellipsis' }, project.name),
          h('div', { class: 'd num ellipsis' }, project.error ? 'not a git repository' : `${project.branch}${project.ahead ? ` ↑${project.ahead}` : ''}${project.behind ? ` ↓${project.behind}` : ''}`)),
        project.error ? h('span', { class: 'pill danger' }, 'error') : h('span', { class: `pill ${project.dirty ? 'warn' : 'ok'}` }, project.dirty ? `${project.changed_files + project.untracked_files} changed` : 'clean'),
        icon('right'))))
      : h('div', { class: 'card' }, emptyState('git', 'No projects registered.', ['Add them under ', code('[[dev.projects]]'), ' in config.toml on the PC.'])))));
  } else clear(projects, h('div', { class: 'card' }, emptyState('git', 'Git integration is turned off on the PC.')));

  if (live.hasFeature('ports')) {
    stops.push(live.subscribe('dev.ports', (p) => {
      clear(ports, p.listeners.length ? h('div', { class: 'card rows' }, p.listeners.map((l) => h('div', { class: 'rowitem' },
        h('span', { class: 'mid', style: 'width:70px' }, l.port), h('div', { class: 'grow' }, h('div', { class: 't ellipsis' }, l.process), h('div', { class: 'd num' }, `pid ${l.pid}`)), h('span', { class: 'pill ok' }, h('i'), 'listening'))))
        : h('div', { class: 'card' }, emptyState('wifi', 'No dev servers are listening.')));
      clear(apps, p.apps.length ? h('div', { class: 'row', style: 'flex-wrap:wrap' }, p.apps.map((a) => h('span', { class: 'pill', style: 'height:28px;padding:0 12px;font-size:12px' }, a.name, a.count > 1 && h('span', { class: 'faint' }, `×${a.count}`))))
        : h('div', { class: 'muted', style: 'font-size:13px' }, 'None of the configured tools is running.'));
    }));
  } else { clear(ports, h('div', { class: 'card' }, emptyState('wifi', 'Port monitoring is turned off on the PC.'))); }

  return { el: root, destroy: () => stops.forEach((stop) => stop()) };
}

const EVENT_ICON = { started: 'play', prompt: 'bolt', finished: 'check', needs_input: 'alert', ended: 'stop' };

export function aiView(shell) {
  const sessions = h('div'), feed = h('div'), usage = h('div'), approvals = h('div');
  let events = [];
  const root = h('div', { class: 'screen' }, h('div', { class: 'topbar' }, h('h1', { class: 'grow' }, 'AI coding'), shell.status(), shell.settingsButton()),
    h('div', { class: 'scroll' }, h('div', { class: 'twocol' }, h('div', null, section('Claude plan usage', usage), section('Sessions', sessions), approvals), section('Recent activity', feed))));
  const stops = [];

  if (!live.hasFeature('claude_code') && !live.hasFeature('codex')) {
    clear(sessions, h('div', { class: 'card' }, emptyState('spark', 'AI integrations are turned off on the PC.')));
    return { el: root, destroy() {} };
  }

  if (live.supports('ai.usage')) {
    const rings = usageRings();
    clear(usage, h('div', { class: 'card', style: 'padding:16px 12px;min-height:150px;display:flex' }, rings.el));
    stops.push(live.subscribe('ai.usage', rings.update), rings.destroy);
  } else usage.parentElement?.remove();

  if (live.hasFeature('claude_permissions')) {
    clear(approvals, section('Permission prompts', h('div', { class: 'card', style: 'padding:14px' },
      h('div', { class: 'muted', style: 'font-size:13px;margin-bottom:10px;line-height:1.4' }, 'When Claude Code asks to run something, answer it here. If you do not answer in time, the terminal asks as usual.'),
      segmented([['on', 'Answer on this phone'], ['off', 'Terminal only']], store.get('pbx.approve', true) ? 'on' : 'off', (value) => { store.set('pbx.approve', value === 'on'); window.dispatchEvent(new Event('pbx-approve-changed')); }))));
  }

  const renderFeed = () => clear(feed, events.length ? h('div', { class: 'card rows' }, events.slice(0, 30).map((e) => h('div', { class: 'rowitem' },
    h('span', { class: e.kind === 'needs_input' ? 'star on' : e.kind === 'finished' ? 'pill ok' : 'muted', style: 'background:none;padding:0' }, icon(EVENT_ICON[e.kind] || 'info')),
    h('div', { class: 'grow' }, h('div', { class: 't ellipsis' }, e.project), h('div', { class: 'd' }, `${TOOL_NAMES[e.tool] || e.tool} · ${e.summary}`)),
    h('span', { class: 'num faint', style: 'font-size:12px' }, fmt.ago(e.ts)))))
    : h('div', { class: 'muted', style: 'font-size:13px' }, 'Nothing yet.'));

  stops.push(live.subscribe('ai.sessions', (p) => clear(sessions, p.sessions.length
    ? h('div', { class: 'card rows' }, p.sessions.map((s) => h('div', { class: 'rowitem' }, icon('spark'),
      h('div', { class: 'grow' }, h('div', { class: 't ellipsis' }, s.project), h('div', { class: 'd' }, `${TOOL_NAMES[s.tool] || s.tool} · active ${fmt.ago(s.last_activity_at)}`)), statePill(s.state))))
    : h('div', { class: 'card' }, emptyState('spark', 'No active sessions.', 'To connect Claude Code, run this on the PC:', code('python -m app hooks install claude-code'))))));
  stops.push(live.subscribe('ai.events', (event) => { events.unshift(event); renderFeed(); }));
  get('/ai/events').then((r) => { events = r.events.reverse(); renderFeed(); }).catch(renderFeed);
  const refresh = setInterval(renderFeed, 30000); // keeps the "2m ago" labels honest

  return { el: root, destroy() { stops.forEach((stop) => stop()); clearInterval(refresh); } };
}
