/** Inert ordinary-configuration composition. No Worktrees/Python/runtime dependency. */
import path from 'node:path';
import fs from 'node:fs';

export class ConfigurationError extends Error {}
const tiers = {defaults: 0, repository: 1, machine: 2, repository_machine: 3};
const origins = {
  defaults: ['defaults'], repository: ['base', 'knowledge'],
  machine: ['named_machine', 'local_machine'], repository_machine: ['repository_machine'],
};
const contextFields = ['repository_id', 'execution_machine', 'installation_cell', 'binding',
  'base_root', 'knowledge_root'];
const layerFields = ['id', 'tier', 'origin', 'values', 'source',
  'repository_id', 'execution_machine', 'installation_cell'];
const fail = message => { throw new ConfigurationError(message); };
const plain = value => value !== null && typeof value === 'object'
  && !Array.isArray(value) && [Object.prototype, null].includes(Object.getPrototypeOf(value));
const has = (value, key) => Object.hasOwn(value, key);

function json(value, ancestors = [], depth = 0) {
  if (depth > 128) fail('configuration nesting exceeds 128');
  if (value === null || typeof value === 'boolean') return;
  if (typeof value === 'string') {
    // Require paired UTF-16 surrogates, the same strings accepted by the Python SDK.
    for (let i = 0; i < value.length; i++) {
      const c = value.charCodeAt(i);
      if (c >= 0xD800 && c <= 0xDBFF) {
        const next = value.charCodeAt(++i);
        if (!(next >= 0xDC00 && next <= 0xDFFF)) fail('unpaired surrogate in string');
      } else if (c >= 0xDC00 && c <= 0xDFFF) fail('unpaired surrogate in string');
    }
    return;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || Math.abs(value) > Number.MAX_SAFE_INTEGER)
      fail('number is not portable JSON');
    return;
  }
  if ((!plain(value) && !Array.isArray(value)) || ancestors.includes(value))
    fail('configuration must be acyclic JSON data');
  if (Object.getOwnPropertySymbols(value).length) fail('symbol keys are not JSON');
  const chain = [...ancestors, value];
  if (Array.isArray(value)) {
    for (let i = 0; i < value.length; i++) {
      if (!has(value, i)) fail('sparse array is not JSON');
      const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
      if (!has(descriptor, 'value') || !descriptor.enumerable) fail('array accessors are not JSON');
      json(descriptor.value, chain, depth + 1);
    }
    if (Object.keys(value).length !== value.length) fail('array properties are not JSON');
  } else {
    for (const key of Object.getOwnPropertyNames(value)) {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!has(descriptor, 'value') || !descriptor.enumerable)
        fail('accessors and hidden properties are not JSON');
      json(key, chain, depth + 1);
      json(descriptor.value, chain, depth + 1);
    }
  }
}
function object(value, allowed) {
  json(value);
  if (!plain(value)) fail('document must be an object');
  if (allowed && Object.keys(value).some(key => !allowed.includes(key)))
    fail('unknown contract field');
}
function identity(value) {
  if (typeof value !== 'string' || !value || value.trim() !== value)
    fail('identity must be a nonempty exact string');
}
function root(value) {
  if (typeof value !== 'string' || value.includes('\0') || !path.isAbsolute(value))
    fail('source root must be absolute');
  return path.normalize(value);
}
function relative(value, allowDots = false) {
  if (typeof value !== 'string' || !value || value.includes('\0'))
    fail('source path must be a nonempty relative path');
  const parts = value.replaceAll('\\', '/').split('/');
  if (/^[\\/]/.test(value) || /^[a-z]:/i.test(value))
    fail('absolute source path is not allowed');
  if (parts.some(part => part === '' || (!allowDots && ['.', '..'].includes(part))
    || /[<>:"|?*\x00-\x1f]/.test(part)
    || (!['.', '..'].includes(part) && /[ .]$/.test(part))
    || /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)/i.test(part)))
    fail('unsafe relative source path');
  return path.join(...parts);
}
function source(value) {
  object(value, ['root', 'path']);
  if (!has(value, 'root') || !has(value, 'path')) fail('source requires root and path');
  return {root: root(value.root), path: relative(value.path)};
}
const clone = value => JSON.parse(JSON.stringify(value));
const rootKey = value => process.platform === 'win32' ? root(value).toLowerCase() : root(value);

export function plan_layers(context, layers) {
  object(context, contextFields);
  for (const field of ['repository_id', 'execution_machine', 'installation_cell'])
    identity(context[field]);
  if (context.binding === 'unresolved') fail('explicit knowledge binding is unresolved');
  if (!['bound', 'unbound'].includes(context.binding)) fail('invalid binding');
  for (const field of ['base_root', 'knowledge_root']) if (has(context, field)) root(context[field]);
  if (context.binding === 'bound' && !has(context, 'knowledge_root'))
    fail('bound knowledge requires a resolved source root');
  if (context.binding === 'unbound' && has(context, 'knowledge_root'))
    fail('unbound context cannot supply knowledge root');
  if (!Array.isArray(layers)) fail('layers must be an array');
  const seen = new Set(), selected = [];
  for (const layer of layers) {
    object(layer, layerFields);
    for (const field of ['id', 'tier', 'origin']) identity(layer[field]);
    if (seen.has(layer.id)) fail('duplicate or conflicting layer id');
    seen.add(layer.id);
    if (!has(tiers, layer.tier) || !origins[layer.tier].includes(layer.origin))
      fail('invalid tier/origin');
    object(layer.values);
    const required = {
      defaults: [], repository: ['repository_id'], machine: ['execution_machine'],
      repository_machine: ['repository_id', 'execution_machine'],
    }[layer.tier];
    const scopes = ['repository_id', 'execution_machine', 'installation_cell']
      .filter(field => has(layer, field));
    if (required.some(field => !scopes.includes(field)) || scopes.some(field =>
      !required.includes(field) && !(field === 'installation_cell' && layer.tier !== 'defaults')))
      fail('missing or conflicting layer scope');
    for (const field of scopes) identity(layer[field]);
    const entry = clone(layer);
    if (has(layer, 'source')) entry.source = source(layer.source);
    if (layer.tier === 'defaults' && has(layer, 'source'))
      fail('built-in defaults have no source file');
    let matches = scopes.every(field => layer[field] === context[field]);
    if (layer.origin === 'knowledge' && context.binding === 'unbound') matches = false;
    if (matches && ['base', 'knowledge'].includes(layer.origin) && entry.source) {
      const field = layer.origin === 'base' ? 'base_root' : 'knowledge_root';
      if (has(context, field) && rootKey(entry.source.root) !== rootKey(context[field]))
        fail('repository source has the wrong root owner');
    }
    if (matches) selected.push(entry);
  }
  return selected.sort((a, b) => tiers[a.tier] - tiers[b.tier]
    || Number(a.origin === 'knowledge') - Number(b.origin === 'knowledge'));
}

const pointerKey = (parent, key) => `${parent}/${key.replaceAll('~', '~0').replaceAll('/', '~1')}`;
const put = (object, key, value) => Object.defineProperty(object, key,
  {value, writable: true, enumerable: true, configurable: true});

export function compose(context, layers) {
  const plan = plan_layers(context, layers), provenance = {};
  let values = {};
  function remove(pointer) {
    for (const key of Object.keys(provenance))
      if (key === pointer || key.startsWith(`${pointer}/`)) delete provenance[key];
  }
  function merge(target, incoming, pointer, owner) {
    if (plain(incoming)) {
      if (!plain(target)) { remove(pointer); target = {}; }
      if (Object.keys(incoming).length) {
        delete provenance[pointer];
        for (const [key, child] of Object.entries(incoming))
          put(target, key, merge(has(target, key) ? target[key] : undefined,
            child, pointerKey(pointer, key), owner));
      } else if (!Object.keys(target).length && !has(provenance, pointer))
        put(provenance, pointer, clone(owner));
      return target;
    }
    remove(pointer);
    put(provenance, pointer, clone(owner));
    return clone(incoming);
  }
  for (const layer of plan) {
    const owner = Object.fromEntries(Object.entries(layer).filter(([key]) => key !== 'values'));
    values = merge(values, layer.values, '', owner);
  }
  return {values, provenance, layers: plan.map(layer => layer.id)};
}

function contained(baseRoot, relativePath, allowDots = false) {
  const base = fs.realpathSync(root(baseRoot));
  if (!fs.statSync(base).isDirectory()) fail('source root is not a directory');
  const lexical = path.join(base, relative(relativePath, allowDots));
  // Resolve the existing prefix, including links, even for an absent target.
  let prefix = lexical;
  const suffix = [];
  while (true) {
    try {
      fs.lstatSync(prefix);
      prefix = fs.realpathSync(prefix);
      break;
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
      // A present dangling link is malformed, not absent.
      try { if (fs.lstatSync(prefix).isSymbolicLink()) fail('broken source link'); }
      catch (probe) { if (probe instanceof ConfigurationError || probe.code !== 'ENOENT') throw probe; }
      suffix.unshift(path.basename(prefix));
      const parent = path.dirname(prefix);
      if (parent === prefix) throw error;
      prefix = parent;
    }
  }
  const target = path.join(prefix, ...suffix), remainder = path.relative(base, target);
  if (remainder === '..' || remainder.startsWith(`..${path.sep}`) || path.isAbsolute(remainder))
    fail('source path escapes its owning root');
  return target;
}

function strictJson(text) {
  // Validate duplicate object keys while JSON.parse remains the syntax/number parser.
  let i = 0;
  const whitespace = () => { while (/\s/.test(text[i] ?? '') && i < text.length) i++; };
  function string() {
    const start = i++;
    while (i < text.length) {
      if (text[i] === '\\') i += 2;
      else if (text[i++] === '"') return JSON.parse(text.slice(start, i));
    }
    fail('unterminated JSON string');
  }
  function value(depth = 0) {
    if (depth > 128) fail('configuration nesting exceeds 128');
    whitespace();
    if (text[i] === '"') { string(); return; }
    if (text[i] === '{' || text[i] === '[') {
      const isObject = text[i++] === '{', close = isObject ? '}' : ']', keys = new Set();
      whitespace();
      if (text[i] === close) { i++; return; }
      while (i < text.length) {
        whitespace();
        if (isObject) {
          if (text[i] !== '"') fail('invalid JSON object key');
          const key = string();
          if (keys.has(key)) fail('duplicate document key');
          keys.add(key); whitespace();
          if (text[i++] !== ':') fail('invalid JSON object');
        }
        value(depth + 1); whitespace();
        if (text[i] === close) { i++; return; }
        if (text[i++] !== ',') fail('invalid JSON container');
      }
      fail('unterminated JSON container');
    }
    const start = i;
    while (i < text.length && !/[\s,\]}]/.test(text[i])) i++;
    if (i === start) fail('invalid JSON value');
    JSON.parse(text.slice(start, i));
  }
  value(); whitespace();
  if (i !== text.length) fail('trailing JSON content');
  return JSON.parse(text);
}

export function read_source(baseRoot, relativePath, {parser, optional = true} = {}) {
  try {
    const target = contained(baseRoot, relativePath);
    let bytes;
    try { bytes = fs.readFileSync(target); }
    catch (error) { if (optional && error.code === 'ENOENT') return null; throw error; }
    const text = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true}).decode(bytes);
    const values = (parser ?? strictJson)(text);
    object(values);
    return {values, source: source({root: baseRoot, path: relativePath})};
  } catch (error) {
    throw new ConfigurationError(`cannot read selected source: ${error.message}`);
  }
}

export function resolve_path(result, pointer) {
  try {
    const owner = result.provenance[pointer], selectedSource = source(owner.source);
    let value = result.values;
    if (pointer) {
      if (!pointer.startsWith('/')) fail('invalid JSON pointer');
      for (const token of pointer.slice(1).split('/'))
        value = value[token.replaceAll('~1', '/').replaceAll('~0', '~')];
    }
    const local = path.join(path.dirname(selectedSource.path), relative(value, true));
    return contained(selectedSource.root, local, true);
  } catch (error) {
    throw new ConfigurationError(`path requires a winning source-owned string leaf: ${error.message}`);
  }
}
