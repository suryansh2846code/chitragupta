#!/usr/bin/env node
/**
 * The bundler. All of it.
 *
 * This package has no dependencies, and that includes its build. Pulling in a
 * real bundler to produce one 60 kB file would add a lockfile, a toolchain
 * version to keep current, and a supply chain — to a package whose entire
 * selling point is that you can drop it into a page with a `<script>` tag.
 *
 * What it does: walk the import graph from the entry points, wrap each module
 * in a function that returns its exports, and emit them in dependency order
 * inside one IIFE that hangs the public names off `globalThis.Character`.
 *
 * The per-module wrapper is the important part and is not just tidiness. Plain
 * concatenation puts every module's private helpers in one scope, and two
 * modules that each declare a private `const BY_ID` — which two of these do —
 * produce a `SyntaxError` at load time, in the built file only, long after the
 * source that caused it was written.
 *
 * What it deliberately does NOT support, because nothing here uses it:
 * default exports, `import * as ns`, dynamic `import()`, circular imports, and
 * re-exporting a name under a different one. Each is caught and reported rather
 * than mis-compiled — a bundler that silently drops an export is worse than one
 * that refuses to build.
 */

import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..");

const ENTRIES = [
  { path: "src/index.js", exposeAll: true },
  { path: "ui/editor.js", exposeAll: true },
];

const IMPORT_RE = /^\s*import\s*\{([^}]*)\}\s*from\s*["'](\.[^"']+)["']\s*;?\s*$/;
const REEXPORT_RE = /^\s*export\s*\{([^}]*)\}\s*from\s*["'](\.[^"']+)["']\s*;?\s*$/;
const EXPORT_DECL_RE = /^(\s*)export\s+(const|let|var|function|class|async\s+function)\s+/;
const EXPORT_LIST_RE = /^\s*export\s*\{([^}]*)\}\s*;?\s*$/;
const BANNED = [
  [/^\s*export\s+default\b/m, "default export"],
  [/^\s*import\s+\*\s+as\b/m, "namespace import"],
  [/\bimport\s*\(/, "dynamic import"],
];

const modules = new Map();   // id -> {id, code, deps, exports, reexports}

function idFor(file) {
  return relative(root, file).split("\\").join("/");
}

/**
 * Collapse a multi-line specifier list onto one line, padding the lines it ate
 * so everything after it keeps its original line number.
 *
 * The parser below is line-based, which is the right size of tool for a file
 * format this constrained — but a long re-export wrapped across three lines is
 * normal, readable source, and a parser that silently passed it through emitted
 * a bare `export {}` inside the IIFE. That is a `SyntaxError` in the built file
 * and in nothing else, so every test that imports from `src/` still passed.
 */
function flattenSpecifierLists(source) {
  return source.replace(
    /(^|\n)([ \t]*(?:import|export)[ \t]*\{)([^}]*)(\}[ \t]*(?:from[ \t]*["'][^"']+["'])?[ \t]*;?)/g,
    (_m, lead, open, names, tail) => {
      const eaten = (names.match(/\n/g) || []).length;
      return lead + open + names.replace(/\s*\n\s*/g, " ") + tail + "\n".repeat(eaten);
    },
  );
}

function parseNames(list) {
  return list.split(",").map((s) => s.trim()).filter(Boolean).map((s) => {
    if (/\bas\b/.test(s)) throw new Error(`renamed export/import is not supported: "${s}"`);
    return s;
  });
}

function load(file) {
  const id = idFor(file);
  if (modules.has(id)) return modules.get(id);

  const raw = readFileSync(file, "utf8");
  for (const [re, what] of BANNED) {
    if (re.test(raw)) throw new Error(`${id}: ${what} is not supported by this bundler`);
  }
  const source = flattenSpecifierLists(raw);

  const deps = [];        // {id, names}
  const reexports = [];   // {id, names}
  const exports = [];
  const out = [];

  for (const line of source.split("\n")) {
    let m = line.match(IMPORT_RE);
    if (m) {
      const depFile = resolve(dirname(file), m[2]);
      deps.push({ id: idFor(depFile), names: parseNames(m[1]), file: depFile });
      out.push("");   // keep line numbers honest for anyone reading the bundle
      continue;
    }
    m = line.match(REEXPORT_RE);
    if (m) {
      const depFile = resolve(dirname(file), m[2]);
      const names = parseNames(m[1]);
      reexports.push({ id: idFor(depFile), names, file: depFile });
      exports.push(...names);
      out.push("");
      continue;
    }
    m = line.match(EXPORT_LIST_RE);
    if (m) {
      exports.push(...parseNames(m[1]));
      out.push("");
      continue;
    }
    m = line.match(EXPORT_DECL_RE);
    if (m) {
      const rest = line.slice(m[0].length);
      const name = (rest.match(/^([A-Za-z_$][\w$]*)/) || [])[1];
      if (!name) throw new Error(`${id}: could not read the name out of: ${line.trim()}`);
      exports.push(name);
      out.push(m[1] + line.trim().replace(/^export\s+/, ""));
      continue;
    }
    out.push(line);
  }

  const mod = { id, file, code: out.join("\n"), deps, reexports, exports };
  modules.set(id, mod);
  for (const d of deps.concat(reexports)) load(d.file);
  return mod;
}

/** Depth-first topological order, with a cycle check that names the cycle. */
function order(entryIds) {
  const seen = new Map();   // id -> "visiting" | "done"
  const out = [];
  const walk = (id, trail) => {
    const state = seen.get(id);
    if (state === "done") return;
    if (state === "visiting") {
      throw new Error(`circular import: ${trail.concat(id).join(" -> ")}`);
    }
    seen.set(id, "visiting");
    const mod = modules.get(id);
    for (const d of mod.deps.concat(mod.reexports)) walk(d.id, trail.concat(id));
    seen.set(id, "done");
    out.push(mod);
  };
  for (const id of entryIds) walk(id, []);
  return out;
}

function emit(mod) {
  const binds = [];
  for (const d of mod.deps) {
    binds.push(`  const { ${d.names.join(", ")} } = M[${JSON.stringify(d.id)}];`);
  }
  for (const r of mod.reexports) {
    binds.push(`  const { ${r.names.join(", ")} } = M[${JSON.stringify(r.id)}];`);
  }
  const returned = Array.from(new Set(mod.exports));
  return [
    `M[${JSON.stringify(mod.id)}] = (function () {`,
    binds.join("\n"),
    mod.code,
    `  return { ${returned.join(", ")} };`,
    "})();",
  ].filter(Boolean).join("\n");
}

function build() {
  const entryIds = ENTRIES.map((e) => load(resolve(root, e.path)).id);
  const ordered = order(entryIds);

  const exposed = new Map();
  for (const e of ENTRIES) {
    const mod = modules.get(idFor(resolve(root, e.path)));
    for (const name of new Set(mod.exports)) exposed.set(name, mod.id);
  }

  const body = ordered.map(emit).join("\n\n");
  const surface = Array.from(exposed.entries())
    .map(([name, id]) => `    ${name}: M[${JSON.stringify(id)}].${name},`)
    .join("\n");

  const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
  const out = `/**
 * character ${pkg.version} — geometric 3D avatars in SVG.
 * ${pkg.license} licensed. Generated by scripts/build.js — edit src/, not this.
 *
 * Drop it in a page and use the \`Character\` global:
 *   <script src="character.global.js"></script>
 *   <script>Character.createCharacter("#a", Character.generateScene("hello"))</script>
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.Character = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  var M = {};

${body}

  return {
${surface}
  };
});
`;

  mkdirSync(join(root, "dist"), { recursive: true });
  writeFileSync(join(root, "dist", "character.global.js"), out);
  // The package is ESM ("type": "module"), so without this Node would read the
  // UMD bundle as an ES module, find no `export`, and hand a caller an empty
  // object with no error anywhere. One file, and `require()` works again.
  writeFileSync(join(root, "dist", "package.json"), '{ "type": "commonjs" }\n');

  // The stylesheet has one source — the string the editor injects — so a
  // consumer who prefers a real `<link>` cannot end up with a copy that has
  // drifted from what the editor actually applies.
  const css = extractCSS();
  if (css) writeFileSync(join(root, "ui", "editor.css"), css);

  const kb = (out.length / 1024).toFixed(1);
  process.stdout.write(`dist/character.global.js  ${kb} kB  (${ordered.length} modules, ${exposed.size} exports)\n`);
  if (css) process.stdout.write(`ui/editor.css             ${(css.length / 1024).toFixed(1)} kB\n`);
}

function extractCSS() {
  try {
    const src = readFileSync(join(root, "ui", "editor-style.js"), "utf8");
    const m = src.match(/export const EDITOR_CSS = `([\s\S]*?)`;/);
    if (!m) return null;
    return `/* Generated from ui/editor-style.js by scripts/build.js. Do not edit. */\n${m[1].replace(/\\`/g, "`")}`;
  } catch {
    return null;
  }
}

try {
  build();
} catch (err) {
  process.stderr.write(`build failed: ${err.message}\n`);
  process.exit(1);
}
