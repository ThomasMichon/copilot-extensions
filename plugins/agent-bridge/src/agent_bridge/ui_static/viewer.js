// Session viewer: streams a live session's represented events and renders them
// as a readable conversation. Tool calls fold into one collapsed "work" block
// per stretch of activity; only failures and the running tool stay visible.

import { h, clear, replaceChildren, markdown, copyText } from "./dom.js";
import { SessionModel, parseSseBlock, parseMarkdown, summarizeSteps, duration, ago, followContinuity } from "./model.js";

const PAGE = 60;  // blocks rendered per "show earlier" step

const DELIVERY = [
  ["steer", "Steer", "Arrives at the worker's next step, without stopping current work"],
  ["queue", "Queue", "Runs after the current turn ends"],
  ["interrupt", "Interrupt", "Stops the current turn and sends this now"],
];

function clock(ts) {
  return ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
}

export class SessionViewer {
  constructor({ api, request }) {
    this.api = api;          // (path) -> Promise<json>
    this.request = request;  // (path, init) -> Promise<Response>, adds auth
    this.watch = null;
    this.el = this._build();
    this.timer = null;
  }

  _build() {
    this.headEl = h("div", { class: "v-head" });
    this.blocksEl = h("div", { class: "v-blocks" });
    this.earlierBtn = h("button", { class: "v-earlier ghost", hidden: true, onclick: () => this._earlier() },
      "Show earlier activity");
    this.emptyEl = h("div", { class: "v-empty muted", text: "Connecting…" });
    this.feedEl = h("div", { class: "v-feed", tabindex: "0" }, this.earlierBtn, this.emptyEl, this.blocksEl);
    this.feedEl.addEventListener("scroll", () => {
      if (this._atBottom()) this._setUnseen(0);
    });
    this.jumpBtn = h("button", { class: "v-jump", hidden: true, onclick: () => this._toBottom() }, "Jump to latest");
    this.msgEl = h("textarea", {
      rows: "2", placeholder: "Message this session — Enter to send, Shift+Enter for a new line",
      "aria-label": "Message",
    });
    this.msgEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this._send(); }
    });
    this.deliveryEls = DELIVERY.map(([value, label, title]) =>
      h("button", {
        type: "button", class: "seg" + (value === "steer" ? " on" : ""), title, dataset: { value },
        onclick: (e) => this._setDelivery(e.currentTarget.dataset.value),
      }, label));
    this.kindEl = h("select", { "aria-label": "Message kind", title: "Message kind" },
      h("option", { value: "prompt" }, "prompt"),
      h("option", { value: "notify" }, "notify"),
      h("option", { value: "status-check" }, "status-check"));
    this.sendBtn = h("button", { type: "button", class: "primary", onclick: () => this._send() }, "Send");
    this.sendStatus = h("span", { class: "muted small", role: "status" });
    const composer = h("div", { class: "v-composer" },
      this.msgEl,
      h("div", { class: "v-composer-row" },
        h("div", { class: "segmented", role: "group", "aria-label": "Delivery" }, this.deliveryEls),
        this.kindEl, this.sendStatus, h("span", { class: "grow" }), this.sendBtn));
    this.composerEl = composer;
    return h("section", { class: "viewer" }, this.headEl, this.feedEl, this.jumpBtn, composer);
  }

  _setDelivery(value) {
    this.delivery = value;
    for (const b of this.deliveryEls) b.classList.toggle("on", b.dataset.value === value);
  }

  /** Start watching one live session (a LiveSessionInfo row). */
  open(session) {
    if (this.watch && this.watch.id === session.session_id) { this.update(session); return; }
    this.close();
    this._reset();
    this.session = session;
    this.composerEl.hidden = false;
    this.emptyEl.hidden = false;
    this.emptyEl.textContent = "Connecting…";
    this.earlierBtn.hidden = true;
    this.sendStatus.textContent = "";
    this.watch = { id: session.session_id, ctrl: new AbortController(), state: "connecting" };
    // History replays over the same stream as live events. Fold it without
    // painting until the stream reaches the session's current end, then render
    // the newest blocks once -- so opening a long session never scrolls
    // through its whole past.
    this.catchUp = { done: false, head: null, idle: null };
    // If nothing arrives at all (an empty or unreachable stream), stop waiting.
    this.catchUp.idle = setTimeout(() => this._finishCatchUp(), 1500);
    this._renderHead();
    this._fetchHead(this.watch);
    this._stream(this.watch);
    this.timer = setInterval(() => this._renderHead(), 1000);
  }

  _reset() {
    this.model = new SessionModel();
    this.nodes = [];
    this.renderFrom = 0;
    this.maxRendered = PAGE;
    this.expanded = new Set();
    this.openSteps = new Set();
    this.pending = [];
    this.unseen = 0;
    this.archive = null;
    this._setDelivery("steer");
    clear(this.blocksEl);
  }

  /** The bridge is replaying the current log from its start (a merge it
   * couldn't translate, see followContinuity): show only the replay. */
  _replayHistory(model) {
    this.model = model;
    this.nodes = [];
    this.renderFrom = 0;
    this.maxRendered = PAGE;
    this.unseen = 0;
    clear(this.blocksEl);
  }

  /**
   * Show an ended session's transcript, read-only: no stream and no composer.
   * `events` are `{event, data, ts}` in the live stream's own kinds (the
   * bridge maps a cold-store transcript to them), oldest first.
   */
  openArchive(meta, events) {
    this.close();
    this._reset();
    this.session = null;
    this.archive = meta || {};
    this.composerEl.hidden = true;
    for (const ev of events || []) this.model.apply(ev.event, ev.data, ev.ts, null);
    this.catchUp = { done: true, head: null, idle: null };
    this.earlierBtn.hidden = true;
    this._renderChanged(new Set(this.model.blocks.keys()), true);
    this.emptyEl.hidden = this.model.blocks.length > 0;
    if (!this.model.blocks.length) this.emptyEl.textContent = "This session left no messages.";
    this._renderHead();
    requestAnimationFrame(() => this._toBottom());
  }

  /** The newest event id right now, from the bounded result snapshot. */
  async _fetchHead(w) {
    try {
      const r = await this.request(
        `/api/v1/live-sessions/${encodeURIComponent(w.id)}/result?max_items=1&max_text_chars=256`,
        { signal: w.ctrl.signal });
      if (!r.ok) return;
      const items = ((await r.json()).incremental || {}).items || [];
      const last = items[items.length - 1];
      if (this.watch !== w) return;
      if (!last) { this._finishCatchUp(); return; }
      if (Number.isFinite(last.event_id)) {
        this.catchUp.head = last.event_id;
        if (this.model.lastId >= last.event_id) this._finishCatchUp();
      }
    } catch (e) { /* the quiet-stream fallback below still ends the catch-up */ }
  }

  _finishCatchUp() {
    if (this.catchUp.done || !this.watch) return;
    this.catchUp.done = true;
    clearTimeout(this.catchUp.idle);
    const all = new Set(this.model.blocks.keys());
    this._renderChanged(all, true);
    this.emptyEl.hidden = this.model.blocks.length > 0;
    if (!this.model.blocks.length) this.emptyEl.textContent = "No activity yet.";
    this._toBottom();
    this._renderHead();
  }

  update(session) {
    this.session = session;
    this._renderHead();
  }

  close() {
    if (this.watch) this.watch.ctrl.abort();
    this.watch = null;
    if (this.catchUp) clearTimeout(this.catchUp.idle);
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  // -- streaming ------------------------------------------------------------

  async _stream(w) {
    while (this.watch === w) {
      try {
        // Fold what the last connection already delivered before naming its
        // cursor: a delayed animation-frame flush (a background tab) would
        // otherwise leave those events pending while the server sends them
        // again from the older cursor, and both copies would be folded.
        if (this.pending.length) this._flush();
        const cont = this.model.continuity ? `&continuity_id=${encodeURIComponent(this.model.continuity)}` : "";
        const r = await this.request(
          `/api/v1/live-sessions/${encodeURIComponent(w.id)}/events?after=${this.model.lastId}${cont}`,
          { headers: { Accept: "text/event-stream" }, signal: w.ctrl.signal });
        // open() may have switched sessions while this request was in flight:
        // its cursor and continuity belong to the old session's model.
        if (this.watch !== w) return;
        if (!r.ok) throw new Error("events " + r.status);
        // lastId is numbered on this log; a reconnect names it so the bridge can
        // translate the cursor if a session-id change merges the log meanwhile.
        // The pair changes together: the bridge echoes the (translated) start.
        const echoed = r.headers.get("X-Agent-Bridge-Continuity");
        if (echoed) {
          const cursor = r.headers.get("X-Agent-Bridge-Cursor");
          const next = followContinuity(this.model, echoed, cursor == null ? NaN : Number(cursor));
          if (next.replay) {
            this.pending = [];  // blocks from the previous connection, numbered on the old log
            this._replayHistory(next.model);
          }
        }
        w.state = "live";
        this._renderHead();
        const reader = r.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done || this.watch !== w) break;
          buf += dec.decode(value, { stream: true }).replace(/\r\n/g, "\n");
          let i;
          while ((i = buf.indexOf("\n\n")) >= 0) {
            this.pending.push(buf.slice(0, i));
            buf = buf.slice(i + 2);
          }
          this._schedule();
        }
      } catch (e) {
        if (w.ctrl.signal.aborted) return;
        w.state = "reconnecting";
        w.error = e.message;
        this._renderHead();
      }
      if (this.watch !== w) return;
      if (this.model.events === 0) this.emptyEl.textContent = "No activity yet.";
      await new Promise((res) => setTimeout(res, 2000));
    }
  }

  _schedule() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => { this.frame = null; this._flush(); });
  }

  _flush() {
    if (!this.pending.length || !this.watch) return;
    const catching = !this.catchUp.done;
    const stick = catching || this._atBottom();
    let before = this.model.blocks.length;
    const changed = new Set();
    for (const block of this.pending.splice(0)) {
      const ev = parseSseBlock(block);
      if (ev.type === "continuity") {  // the stream followed a merge: ids are renumbered
        if (ev.data && ev.data.continuity_id) {
          const next = followContinuity(this.model, ev.data.continuity_id, ev.data.after);
          if (next.replay) {
            this._replayHistory(next.model);
            changed.clear();
            before = 0;
          }
        }
        continue;
      }
      if (!ev.data || ev.type === "bridge_control" || ev.type === "heartbeat") {
        if (ev.id != null) this.model.lastId = Math.max(this.model.lastId, ev.id);
        continue;
      }
      for (const i of this.model.apply(ev.type, ev.data, ev.ts, ev.id)) changed.add(i);
    }
    if (catching) {
      const head = this.catchUp.head;
      if (head != null && this.model.lastId >= head) { this._finishCatchUp(); return; }
      this.emptyEl.hidden = false;
      this.emptyEl.textContent = head
        ? `Loading history… ${Math.min(99, Math.round((100 * this.model.lastId) / head))}%`
        : `Loading history… ${this.model.events} events`;
      // A quiet stream means the backlog is through, whether or not the head is known.
      clearTimeout(this.catchUp.idle);
      this.catchUp.idle = setTimeout(() => this._finishCatchUp(), 400);
      return;
    }
    const added = this.model.blocks.length - before;
    // The block before new ones may have stopped being the "live" one.
    if (added > 0 && before > 0) changed.add(before - 1);
    this._renderChanged(changed, stick);
    if (stick) this._toBottom();
    else if (added > 0 && before > 0) this._setUnseen(this.unseen + added);
    this._renderHead();
  }

  // -- rendering ------------------------------------------------------------

  _renderChanged(changed, stick = true) {
    const blocks = this.model.blocks;
    this.emptyEl.hidden = blocks.length > 0;
    // Keep the DOM bounded however long the session runs: only the newest
    // `limit` blocks are rendered ("Show earlier" raises it). Blocks that would
    // fall off the cap are never rendered at all.
    const limit = stick ? this.maxRendered : this.maxRendered * 4;
    const start = Math.max(this.renderFrom, blocks.length - limit);
    while (this.nodes.length && this.renderFrom < start) {
      this.nodes.shift().remove();
      this.renderFrom += 1;
    }
    if (!this.nodes.length) this.renderFrom = start;
    for (const i of changed) {
      const slot = i - this.renderFrom;
      if (slot < 0 || slot >= this.nodes.length) continue;
      const node = this._renderBlock(blocks[i], i);
      this.blocksEl.replaceChild(node, this.nodes[slot]);
      this.nodes[slot] = node;
    }
    const frag = document.createDocumentFragment();
    while (this.renderFrom + this.nodes.length < blocks.length) {
      const i = this.renderFrom + this.nodes.length;
      const node = this._renderBlock(blocks[i], i);
      this.nodes.push(node);
      frag.appendChild(node);
    }
    this.blocksEl.appendChild(frag);
    this.earlierBtn.hidden = this.renderFrom === 0;
    if (this.renderFrom > 0) this.earlierBtn.textContent = `Show earlier activity (${this.renderFrom})`;
  }

  _rerender(i) {
    const slot = i - this.renderFrom;
    if (slot < 0 || slot >= this.nodes.length) return;
    const node = this._renderBlock(this.model.blocks[i], i);
    this.blocksEl.replaceChild(node, this.nodes[slot]);
    this.nodes[slot] = node;
  }

  _earlier() {
    const from = Math.max(0, this.renderFrom - PAGE);
    const height = this.feedEl.scrollHeight;
    const first = this.blocksEl.firstChild;
    const fresh = [];
    for (let i = from; i < this.renderFrom; i++) fresh.push(this._renderBlock(this.model.blocks[i], i));
    for (const n of fresh) this.blocksEl.insertBefore(n, first);
    this.nodes = [...fresh, ...this.nodes];
    this.renderFrom = from;
    this.maxRendered = this.nodes.length;
    this.earlierBtn.hidden = from === 0;
    this.earlierBtn.textContent = `Show earlier activity (${from})`;
    this.feedEl.scrollTop += this.feedEl.scrollHeight - height;
  }

  _renderBlock(b, i) {
    switch (b.type) {
      case "user": {
        if (b.relay) return h("div", { class: "b-relay" }, h("span", { text: "↳ " + b.text }));
        // A long prompt (a scheduled tick, a handoff brief) is folded to a few lines.
        const long = b.text.length > 600 || b.text.split("\n").length > 8;
        const open = !long || this.expanded.has("u" + i);
        return h("article", { class: "b b-user" + (open ? "" : " clamped") },
          h("header", null, h("span", { class: "who", text: "Prompt" }), h("time", { text: clock(b.ts) })),
          h("div", { class: "md" }, markdown(parseMarkdown(b.text))),
          long ? h("button", { class: "ghost small b-more", onclick: () => {
            if (open) this.expanded.delete("u" + i); else this.expanded.add("u" + i);
            this._rerender(i);
          } }, open ? "Show less" : "Show the whole prompt") : null);
      }
      case "sent":
        return h("article", { class: "b b-sent" },
          h("header", null, h("span", { class: "who", text: "You" }),
            b.delivery ? h("span", { class: "tag", text: b.delivery }) : null,
            h("time", { text: clock(b.ts) })),
          h("div", { class: "md" }, markdown(parseMarkdown(b.text))));
      case "agent":
        return h("article", { class: "b b-agent" },
          h("header", null, h("span", { class: "who", text: b.agentId ? "Sub-agent" : "Agent" }),
            h("time", { text: clock(b.ts) })),
          h("div", { class: "md" }, markdown(parseMarkdown(b.text))));
      case "ask":
        return h("article", { class: "b b-attn" },
          h("header", null, h("span", { class: "who", text: "Asking you" }), h("time", { text: clock(b.ts) })),
          h("div", { class: "md" }, markdown(parseMarkdown(b.text))),
          h("p", { class: "muted small", text: "Answer it in the session's own terminal." }));
      case "permission":
        return h("article", { class: "b b-attn" },
          h("header", null, h("span", { class: "who", text: "Permission requested" }),
            h("time", { text: clock(b.ts) })),
          h("p", { class: "mono", text: b.text }),
          h("p", { class: "muted small", text: "Approve or deny it in the session's own terminal." }));
      case "note":
        return h("div", { class: "b-relay" }, h("span", { text: "⟲ " + b.text }),
          h("time", { class: "muted", text: " · " + clock(b.ts) }));
      case "work":
        return this._renderWork(b, i);
      default:
        return h("div");
    }
  }

  _renderWork(b, i) {
    const open = this.expanded.has(i);
    const tools = b.steps.filter((s) => s.kind === "tool");
    const failed = tools.filter((s) => s.status === "failed");
    const running = tools.filter((s) => s.status === "running");
    const last = this.model.blocks.length - 1 === i;
    const counts = summarizeSteps(b.steps);
    const span = b.start && b.end && b.end - b.start >= 1 ? duration(b.end - b.start) : "";
    const label = running.length && last ? "Working" : "Worked";
    const head = h("button", {
      class: "w-head", "aria-expanded": open ? "true" : "false",
      onclick: () => { open ? this.expanded.delete(i) : this.expanded.add(i); this._rerender(i); },
    },
      h("span", { class: "caret", text: open ? "▾" : "▸" }),
      h("span", { class: "w-title", text: label + (span ? " " + span : "") }),
      b.intent ? h("span", { class: "w-intent", text: b.intent }) : null,
      h("span", { class: "w-counts muted", text: counts.slice(0, 3).join(" · ") +
        (counts.length > 3 ? " · …" : "") || (b.steps.length ? "thinking" : "") }),
      failed.length ? h("span", { class: "tag bad", text: failed.length + " failed" }) : null,
      h("time", { text: clock(b.start) }));
    let body;
    if (open) {
      body = h("ol", { class: "w-steps" }, b.steps.map((st) => this._renderStep(st, i)));
    } else {
      const visible = [...failed.slice(-3), ...(last ? running.slice(-1) : [])];
      body = visible.length ? h("ol", { class: "w-steps" }, visible.map((st) => this._renderStep(st, i))) : null;
    }
    return h("section", { class: "b b-work" + (open ? " open" : "") }, head, body);
  }

  _renderStep(st, i) {
    const key = st.kind === "tool" ? st.id : "t:" + st.ts + ":" + st.text.length;
    const open = this.openSteps.has(key);
    const toggle = () => { open ? this.openSteps.delete(key) : this.openSteps.add(key); this._rerender(i); };
    if (st.kind === "thought") {
      const first = st.text.replace(/\*\*/g, "").split("\n").find((l) => l.trim()) || "";
      return h("li", { class: "step thought" },
        h("button", { class: "s-row", onclick: toggle, "aria-expanded": open ? "true" : "false" },
          h("span", { class: "s-icon", text: "💭" }),
          h("span", { class: "s-verb", text: "Thought" }),
          h("span", { class: "s-label", text: first })),
        open ? h("div", { class: "s-detail md" }, markdown(parseMarkdown(st.text))) : null);
    }
    const icon = st.status === "running" ? h("span", { class: "s-icon spin", "aria-label": "running" })
      : h("span", { class: "s-icon " + (st.status === "failed" ? "bad" : "ok"),
                    text: st.status === "failed" ? "✕" : "✓" });
    const took = st.end && st.start ? duration(st.end - st.start)
      : (st.status === "running" && st.start ? duration(Date.now() / 1000 - st.start) : "");
    return h("li", { class: "step " + st.status },
      h("button", { class: "s-row", onclick: toggle, "aria-expanded": open ? "true" : "false" },
        icon,
        h("span", { class: "s-verb", text: st.verb }),
        h("span", { class: "s-label mono", text: st.label || st.name }),
        st.agentId ? h("span", { class: "tag", text: "sub-agent" }) : null,
        h("span", { class: "s-took muted", text: took })),
      open ? this._stepDetail(st) : null);
  }

  _stepDetail(st) {
    const input = st.input == null ? "" : (typeof st.input === "string" ? st.input
      : JSON.stringify(st.input, null, 2));
    const copyBtn = h("button", { class: "ghost small", onclick: (e) => copyText(e.currentTarget, st.output) },
      "Copy output");
    return h("div", { class: "s-detail" },
      h("div", { class: "s-sub muted small" }, st.name, st.output ? copyBtn : null),
      input ? h("pre", { class: "s-in", text: input }) : null,
      h("pre", { class: "s-out", text: st.output || (st.status === "running" ? "Still running…" : "(no output)") }));
  }

  _renderHead() {
    const m = this.model;
    if (this.archive) {
      const a = this.archive;
      replaceChildren(this.headEl, [
        h("span", { class: "chip", text: "ended · read-only" }),
        a.session_id ? h("span", { class: "chip mono", title: a.session_id, text: String(a.session_id).slice(0, 8) }) : null,
        m && m.usage.model ? h("span", { class: "chip", text: m.usage.model }) : null,
        a.truncated_before ? h("span", { class: "muted small", text: "showing the latest part" }) : null,
        m && m.lastTs ? h("span", { class: "muted small", text: "last event " + ago(m.lastTs) }) : null,
      ]);
      return;
    }
    if (!this.session) return;
    const s = this.session;
    const run = m ? m.runningTool() : null;
    const pct = m ? m.contextPct() : null;
    const w = this.watch;
    const conn = !w ? "closed" : w.state === "live" ? "live" : w.state;
    const chips = [
      // The connection is worth a chip only when it isn't streaming normally.
      conn !== "live" ? h("span", { class: "chip conn-" + conn, title: w && w.error ? w.error : "",
                  text: conn === "connecting" ? "connecting…" : conn === "closed" ? "not connected" : "reconnecting…" }) : null,
      s.liveness ? h("span", { class: "chip l-" + s.liveness, text: s.liveness }) : null,
      run ? h("span", { class: "chip running", title: run.label },
        h("span", { class: "spin" }), `${run.verb} ${run.label || run.name}`.slice(0, 60) +
          (run.start ? " · " + duration(Date.now() / 1000 - run.start) : "")) : null,
      pct != null ? h("span", { class: "chip", title: "Context window used", text: `context ${pct}%` }) : null,
      m && m.usage.model ? h("span", { class: "chip", text: m.usage.model }) : null,
      m && m.lastTs ? h("span", { class: "muted small", text: "last event " + ago(m.lastTs) }) : null,
    ];
    replaceChildren(this.headEl, chips);
  }

  // -- scrolling ------------------------------------------------------------

  _atBottom() {
    const f = this.feedEl;
    return f.scrollHeight - f.scrollTop - f.clientHeight < 60;
  }

  _toBottom() {
    this.feedEl.scrollTop = this.feedEl.scrollHeight;
    this._setUnseen(0);
  }

  _setUnseen(n) {
    this.unseen = n;
    this.jumpBtn.hidden = n === 0;
    this.jumpBtn.textContent = n ? `Jump to latest (${n} new)` : "Jump to latest";
  }

  // -- sending --------------------------------------------------------------

  async _send() {
    const body = this.msgEl.value.trim();
    if (!body || !this.watch) return;
    const delivery = this.delivery || "steer";
    const key = "ui:" + (crypto.randomUUID ? crypto.randomUUID() : Date.now() + ":" + Math.random());
    this.sendBtn.disabled = true;
    this.sendStatus.textContent = "Sending…";
    try {
      const r = await this.request(`/api/v1/live-sessions/${encodeURIComponent(this.watch.id)}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sender: "bridge-ui", body, kind: this.kindEl.value, delivery, idempotency_key: key }),
      });
      const text = await r.text();
      if (!r.ok) throw new Error(r.status + " " + text.slice(0, 200));
      this.msgEl.value = "";
      this.sendStatus.textContent = {
        steer: "Sent — arrives at the next step",
        queue: "Queued — runs after the current turn",
        interrupt: "Sent — interrupting the current turn",
      }[delivery];
      this._renderChanged(new Set(this.model.apply("local_sent", { text: body, delivery }, Date.now() / 1000)));
      this._toBottom();
    } catch (e) {
      this.sendStatus.textContent = "Send failed: " + e.message;
    } finally {
      this.sendBtn.disabled = false;
    }
  }
}
