// The live WebSocket: one connection, a small state machine, automatic reconnection.
//
//   offline -> connecting -> online -> (drop) -> connecting ... with backoff 1s, 2s, 4s ... 30s
//   unauthorized / incompatible / replaced are terminal until the user acts.

import { session } from './api.js';

const PROTOCOL = '1.0';
const PING_MS = 15000;
const SILENCE_MS = 35000; // the agent answers every ping, so this much silence means the link is dead
const HISTORY = 60;

class Live extends EventTarget {
  state = 'offline';
  agent = null;          // hello payload: name, versions, capabilities
  #ws = null;
  #attempt = 0;
  #retryTimer = 0;
  #pingTimer = 0;
  #lastMessage = 0;
  #topics = new Map();   // topic -> Set of listeners
  #latest = new Map();   // topic -> last payload
  #history = new Map();  // topic -> recent payloads
  #seq = 0;

  start() {
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.#wake(); });
    window.addEventListener('online', () => this.#wake());
    window.addEventListener('pageshow', () => this.#wake());
    this.connect();
  }

  connect() {
    clearTimeout(this.#retryTimer);
    this.#close();
    if (!session.token) return this.#set('unauthorized');
    this.#set('connecting');
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = this.#ws = new WebSocket(`${scheme}://${location.host}/api/v1/ws`);
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: session.token, protocol_version: PROTOCOL }));
    ws.onmessage = (event) => { if (ws === this.#ws) this.#receive(event.data); };
    ws.onclose = (event) => { if (ws === this.#ws) this.#closed(event.code); };
  }

  stop() { clearTimeout(this.#retryTimer); this.#close(); this.#set('offline'); }

  /** Listen to a topic. Returns a function that stops listening. The listener gets (payload, history). */
  subscribe(topic, listener) {
    let listeners = this.#topics.get(topic);
    if (!listeners) {
      this.#topics.set(topic, (listeners = new Set()));
      this.#send({ type: 'subscribe', id: String(++this.#seq), topics: [topic] });
    }
    listeners.add(listener);
    if (this.#latest.has(topic)) listener(this.#latest.get(topic), this.#history.get(topic) || []);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0 && this.#topics.get(topic) === listeners) {
        this.#topics.delete(topic);
        this.#latest.delete(topic);
        this.#history.delete(topic);
        this.#send({ type: 'unsubscribe', id: String(++this.#seq), topics: [topic] });
      }
    };
  }

  /** The most recent payload of a topic someone is subscribed to, or undefined. */
  latest(topic) { return this.#latest.get(topic); }

  supports(topic) { return !this.agent || this.agent.capabilities.topics.includes(topic); }
  hasFeature(name) { return !this.agent || this.agent.capabilities.features.includes(name); }

  // ---- internals ----
  #set(state) {
    if (state === this.state) return;
    this.state = state;
    this.dispatchEvent(new CustomEvent('state', { detail: state }));
  }

  #send(message) {
    if (this.#ws?.readyState === WebSocket.OPEN && this.state === 'online') this.#ws.send(JSON.stringify(message));
  }

  #receive(raw) {
    this.#lastMessage = Date.now();
    let message;
    try { message = JSON.parse(raw); } catch { return; }
    switch (message.type) {
      case 'hello':
        this.agent = message;
        this.#attempt = 0;
        this.#set('online');
        if (this.#topics.size) this.#send({ type: 'subscribe', id: String(++this.#seq), topics: [...this.#topics.keys()] });
        clearInterval(this.#pingTimer);
        this.#pingTimer = setInterval(() => this.#heartbeat(), PING_MS);
        break;
      case 'data': {
        const { topic, payload } = message;
        if (message.history) {
          this.#history.set(topic, payload.slice(-HISTORY));
          break;
        }
        const history = this.#history.get(topic) || [];
        history.push(payload);
        if (history.length > HISTORY) history.shift();
        this.#history.set(topic, history);
        this.#latest.set(topic, payload);
        this.#topics.get(topic)?.forEach((listener) => listener(payload, history));
        break;
      }
      case 'event':
        this.#topics.get(message.topic)?.forEach((listener) => listener(message.payload, []));
        break;
      default: // ack, pong, error and anything newer than this client: nothing to do
    }
  }

  #heartbeat() {
    if (Date.now() - this.#lastMessage > SILENCE_MS) return this.#ws?.close();
    this.#send({ type: 'ping' });
  }

  #closed(code) {
    clearInterval(this.#pingTimer);
    this.#ws = null;
    if (code === 4401) { session.token = null; return this.#set('unauthorized'); }
    if (code === 4426) return this.#set('incompatible');
    if (code === 4409) return this.#set('replaced');
    this.#set('connecting');
    const delay = code === 4408 ? 0 : Math.min(30000, 1000 * 2 ** this.#attempt++) * (0.7 + Math.random() * 0.6);
    this.#retryTimer = setTimeout(() => this.connect(), delay);
  }

  #close() {
    clearInterval(this.#pingTimer);
    const ws = this.#ws;
    this.#ws = null;
    if (ws) { ws.onclose = ws.onmessage = ws.onopen = null; try { ws.close(); } catch { /* already closed */ } }
  }

  #wake() {
    // Back in the foreground or back on Wi-Fi: do not wait out the backoff.
    if (this.state === 'connecting' && this.#ws?.readyState !== WebSocket.OPEN) { this.#attempt = 0; this.connect(); }
    else if (this.state === 'online' && Date.now() - this.#lastMessage > SILENCE_MS) this.connect();
  }
}

export const live = new Live();
