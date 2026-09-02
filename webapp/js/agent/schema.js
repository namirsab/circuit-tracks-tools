// Minimal JSON Schema validator (draft 2020-12 subset) for checking tool
// arguments before they reach a tool. It covers what pydantic emits for the
// song schema plus the hand-written tool schemas: type, enum, const, numeric
// and string bounds, arrays, objects (required / additionalProperties /
// patternProperties / propertyNames), $ref into $defs, and anyOf / oneOf /
// allOf / not. Errors carry a path and a message written for a language model
// to read and act on ("unknown key ... did you mean ...").

const MAX_DEPTH = 64;

export function validate(schema, data, root = schema) {
  const errors = [];
  check(schema, data, root, '$', errors, 0);
  return errors;
}

export function formatErrors(errors) {
  return errors.map((e) => `${e.path}: ${e.message}`).join('\n');
}

export function resolveRef(ref, root) {
  if (typeof ref !== 'string' || !ref.startsWith('#')) throw new Error(`Unsupported $ref: ${ref}`);
  let cur = root;
  for (const raw of ref.slice(1).split('/').filter(Boolean)) {
    const key = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    cur = cur?.[key];
    if (cur === undefined) throw new Error(`Unresolvable $ref: ${ref}`);
  }
  return cur;
}

function jsonType(v) {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
  return typeof v;
}

function matchesType(t, v) {
  switch (t) {
    case 'integer': return typeof v === 'number' && Number.isInteger(v);
    case 'number': return typeof v === 'number' && Number.isFinite(v);
    case 'null': return v === null;
    case 'array': return Array.isArray(v);
    case 'object': return v !== null && typeof v === 'object' && !Array.isArray(v);
    case 'string': case 'boolean': return typeof v === t;
    default: return false;
  }
}

function show(v) {
  let s;
  try { s = JSON.stringify(v); } catch { s = String(v); }
  if (s === undefined) s = String(v);
  return s.length > 60 ? `${s.slice(0, 57)}...` : s;
}

const deepEqual = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function childPath(path, key) {
  return /^[A-Za-z_$][\w$]*$/.test(key) ? `${path}.${key}` : `${path}[${JSON.stringify(key)}]`;
}

// Small edit distance for "did you mean" hints.
function distance(a, b) {
  const m = a.length; const n = b.length;
  if (!m) return n; if (!n) return m;
  let prev = Array.from({ length: n + 1 }, (_, j) => j);
  for (let i = 1; i <= m; i++) {
    const cur = [i];
    for (let j = 1; j <= n; j++) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = cur;
  }
  return prev[n];
}

export function suggest(key, candidates) {
  let best = null;
  for (const c of candidates) {
    const d = distance(key.toLowerCase(), c.toLowerCase());
    if (d <= Math.max(2, Math.floor(c.length / 4)) && (!best || d < best.d)) best = { c, d };
  }
  return best?.c ?? null;
}

function check(schema, v, root, path, errors, depth) {
  if (schema === true || schema == null) return;
  if (schema === false) { errors.push({ path, message: 'no value is allowed here' }); return; }
  if (depth > MAX_DEPTH) return;

  if (schema.$ref) check(resolveRef(schema.$ref, root), v, root, path, errors, depth + 1);

  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((t) => matchesType(t, v))) {
      errors.push({ path, message: `expected ${types.join(' or ')}, got ${jsonType(v)} ${show(v)}` });
      return;
    }
  }
  if (schema.enum && !schema.enum.some((e) => deepEqual(e, v))) {
    const hint = typeof v === 'string' ? suggest(v, schema.enum.filter((e) => typeof e === 'string')) : null;
    errors.push({
      path,
      message: `must be one of ${schema.enum.map(show).join(', ')}; got ${show(v)}${hint ? ` (did you mean ${show(hint)}?)` : ''}`,
    });
  }
  if (schema.const !== undefined && !deepEqual(schema.const, v)) {
    errors.push({ path, message: `must be ${show(schema.const)}; got ${show(v)}` });
  }

  if (typeof v === 'number') checkNumber(schema, v, path, errors);
  else if (typeof v === 'string') checkString(schema, v, path, errors);
  else if (Array.isArray(v)) checkArray(schema, v, root, path, errors, depth);
  else if (v !== null && typeof v === 'object') checkObject(schema, v, root, path, errors, depth);

  if (schema.allOf) for (const s of schema.allOf) check(s, v, root, path, errors, depth + 1);
  if (schema.anyOf) checkUnion(schema.anyOf, v, root, path, errors, depth);
  if (schema.oneOf) checkUnion(schema.oneOf, v, root, path, errors, depth);
  if (schema.not !== undefined) {
    const e = [];
    check(schema.not, v, root, path, e, depth + 1);
    if (!e.length) errors.push({ path, message: `value ${show(v)} is not allowed here` });
  }
}

function checkNumber(s, v, path, errors) {
  if (s.minimum !== undefined && v < s.minimum) errors.push({ path, message: `must be >= ${s.minimum}; got ${v}` });
  if (s.maximum !== undefined && v > s.maximum) errors.push({ path, message: `must be <= ${s.maximum}; got ${v}` });
  if (s.exclusiveMinimum !== undefined && v <= s.exclusiveMinimum) errors.push({ path, message: `must be > ${s.exclusiveMinimum}; got ${v}` });
  if (s.exclusiveMaximum !== undefined && v >= s.exclusiveMaximum) errors.push({ path, message: `must be < ${s.exclusiveMaximum}; got ${v}` });
  if (s.multipleOf && Math.abs(v / s.multipleOf - Math.round(v / s.multipleOf)) > 1e-9) {
    errors.push({ path, message: `must be a multiple of ${s.multipleOf}; got ${v}` });
  }
}

function checkString(s, v, path, errors) {
  if (s.minLength !== undefined && v.length < s.minLength) errors.push({ path, message: `must be at least ${s.minLength} characters; got ${show(v)}` });
  if (s.maxLength !== undefined && v.length > s.maxLength) errors.push({ path, message: `must be at most ${s.maxLength} characters; got ${v.length}` });
  if (s.pattern !== undefined && !new RegExp(s.pattern, 'u').test(v)) errors.push({ path, message: `must match /${s.pattern}/; got ${show(v)}` });
}

function checkArray(s, v, root, path, errors, depth) {
  if (s.minItems !== undefined && v.length < s.minItems) errors.push({ path, message: `must have at least ${s.minItems} items; got ${v.length}` });
  if (s.maxItems !== undefined && v.length > s.maxItems) errors.push({ path, message: `must have at most ${s.maxItems} items; got ${v.length}` });
  if (s.uniqueItems) {
    const seen = new Set();
    for (const item of v) {
      const k = JSON.stringify(item);
      if (seen.has(k)) { errors.push({ path, message: `items must be unique; ${show(item)} repeats` }); break; }
      seen.add(k);
    }
  }
  const prefix = s.prefixItems ?? [];
  v.forEach((item, i) => {
    const sub = i < prefix.length ? prefix[i] : s.items;
    if (sub !== undefined) check(sub, item, root, `${path}[${i}]`, errors, depth + 1);
  });
}

function checkObject(s, v, root, path, errors, depth) {
  const props = s.properties ?? {};
  const propNames = Object.keys(props);
  for (const key of s.required ?? []) {
    if (!Object.hasOwn(v, key)) errors.push({ path, message: `missing required key ${show(key)}` });
  }
  const patterns = Object.entries(s.patternProperties ?? {}).map(([p, sub]) => [new RegExp(p, 'u'), sub]);
  for (const [key, val] of Object.entries(v)) {
    const child = childPath(path, key);
    let matched = false;
    if (Object.hasOwn(props, key)) {
      matched = true;
      check(props[key], val, root, child, errors, depth + 1);
    }
    for (const [re, sub] of patterns) {
      if (re.test(key)) { matched = true; check(sub, val, root, child, errors, depth + 1); }
    }
    if (!matched) {
      if (s.additionalProperties === false) {
        const hint = suggest(key, propNames);
        const allowed = propNames.length ? ` (allowed keys: ${propNames.join(', ')})` : '';
        errors.push({ path: child, message: `unknown key ${show(key)}${hint ? `, did you mean ${show(hint)}?` : ''}${allowed}` });
      } else if (s.additionalProperties && typeof s.additionalProperties === 'object') {
        check(s.additionalProperties, val, root, child, errors, depth + 1);
      }
    }
    if (s.propertyNames !== undefined) {
      const e = [];
      check(s.propertyNames, key, root, child, e, depth + 1);
      if (e.length) errors.push({ path: child, message: `invalid key ${show(key)}: ${e[0].message}` });
    }
  }
  if (s.minProperties !== undefined && Object.keys(v).length < s.minProperties) {
    errors.push({ path, message: `must have at least ${s.minProperties} entries` });
  }
  if (s.maxProperties !== undefined && Object.keys(v).length > s.maxProperties) {
    errors.push({ path, message: `must have at most ${s.maxProperties} entries` });
  }
}

// anyOf / oneOf: pass if any branch passes; otherwise report the branch that
// got furthest (deepest error path, then fewest errors). Pure type unions such
// as [integer, null] collapse into one "expected integer or null" message.
function checkUnion(branches, v, root, path, errors, depth) {
  const results = [];
  for (const b of branches) {
    const e = [];
    check(b, v, root, path, e, depth + 1);
    if (!e.length) return;
    results.push(e);
  }
  const baseDepth = pathDepth(path);
  const typeOnly = results.every((e) => e.length === 1 && e[0].path === path && e[0].message.startsWith('expected '));
  if (typeOnly) {
    const wanted = results.map((e) => e[0].message.replace(/^expected (.*?), got .*$/, '$1'));
    errors.push({ path, message: `expected ${[...new Set(wanted)].join(' or ')}, got ${jsonType(v)} ${show(v)}` });
    return;
  }
  let best = null;
  for (const e of results) {
    const score = Math.max(...e.map((x) => pathDepth(x.path) - baseDepth)) * 1000 - e.length;
    if (!best || score > best.score) best = { e, score };
  }
  errors.push(...best.e);
}

function pathDepth(path) {
  return (path.match(/\.|\[/g) ?? []).length;
}
