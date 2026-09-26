// Tiny DOM builder. Content is only ever set as text; links are http(s) only.

export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "text") el.textContent = v;
      else if (k === "dataset") Object.assign(el.dataset, v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "href") {
        if (/^(https?:\/\/|#)/.test(String(v))) el.setAttribute("href", v);
      } else el.setAttribute(k, v === true ? "" : v);
    }
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c == null || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

export function replaceChildren(el, ...children) {
  clear(el);
  append(el, children);
  return el;
}

/** Render parseInline() tokens. */
export function inline(tokens) {
  return tokens.map((t) => {
    if (t.t === "b") return h("strong", { text: t.text });
    if (t.t === "code") return h("code", { text: t.text });
    if (t.t === "link") return h("a", { href: t.href, target: "_blank", rel: "noopener noreferrer", text: t.text });
    return document.createTextNode(t.text);
  });
}

/** Render parseMarkdown() blocks. */
export function markdown(blocks) {
  const out = [];
  let list = null;
  for (const b of blocks) {
    if (b.t === "li") {
      if (!list) { list = h("ul"); out.push(list); }
      list.appendChild(h("li", { class: b.indent ? "sub" : null }, inline(b.inl)));
      continue;
    }
    list = null;
    if (b.t === "code") out.push(h("pre", null, h("code", { text: b.text })));
    else if (b.t === "h") out.push(h("h4", null, inline(b.inl)));
    else if (b.t === "quote") out.push(h("blockquote", null, inline(b.inl)));
    else out.push(h("p", null, inline(b.inl)));
  }
  return out;
}

export async function copyText(btn, text) {
  try {
    await navigator.clipboard.writeText(text);
    const was = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = was; }, 1200);
  } catch (e) {
    btn.textContent = "Copy failed";
  }
}
