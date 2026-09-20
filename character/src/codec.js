/**
 * A character as a string you can put in a URL.
 *
 * Base64url over UTF-8 JSON. Not compressed: a full document is around 1.4 kB
 * of JSON and roughly 1.9 kB encoded, which is inside every URL limit that
 * matters, and a compressed payload would need a decompressor shipped on the
 * read side forever — for a saving nobody can perceive.
 *
 * `decode()` never throws. The thing on the other end of it is a URL a person
 * pasted, and a pasted URL is truncated, re-encoded by a chat client, or from a
 * different tool about half the time. A bad payload returns null and the caller
 * falls back to a default character; it does not put a stack trace on screen.
 */

import { normalize } from "./schema.js";

function toBytes(text) {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
  return Uint8Array.from(Buffer.from(text, "utf8"));
}

function fromBytes(bytes) {
  if (typeof TextDecoder !== "undefined") return new TextDecoder().decode(bytes);
  return Buffer.from(bytes).toString("utf8");
}

function bytesToB64(bytes) {
  if (typeof btoa === "function") {
    let s = "";
    // Chunked, because `String.fromCharCode(...bytes)` on a long payload blows
    // the argument limit — at a size well inside what a real document reaches.
    for (let i = 0; i < bytes.length; i += 0x8000) {
      s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    }
    return btoa(s);
  }
  return Buffer.from(bytes).toString("base64");
}

function b64ToBytes(b64) {
  if (typeof atob === "function") {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  return Uint8Array.from(Buffer.from(b64, "base64"));
}

/** Document -> base64url payload. */
export function encode(doc) {
  const json = JSON.stringify(doc);
  return bytesToB64(toBytes(json)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/**
 * base64url payload -> normalised document, or null.
 *
 * Standard base64 is accepted as well as base64url, and missing padding is
 * restored, because payloads come back through URL shorteners and chat clients
 * that rewrite one into the other.
 */
export function decode(payload) {
  if (typeof payload !== "string" || !payload) return null;
  try {
    let b64 = payload.trim().replace(/-/g, "+").replace(/_/g, "/");
    b64 += "=".repeat((4 - (b64.length % 4)) % 4);
    const text = fromBytes(b64ToBytes(b64));
    const parsed = JSON.parse(text);
    if (!parsed || typeof parsed !== "object") return null;
    return normalize(parsed);
  } catch {
    return null;
  }
}

/**
 * Read a character out of a URL, wherever it is hiding.
 *
 * `d` is what the reference builder uses and what every link in the wild
 * carries; `c` is ours. Query string first, then the hash — a hash payload
 * never reaches a server, which is the right default for something that
 * encodes a person's own likeness.
 */
export function fromURL(url, keys = ["c", "d"]) {
  try {
    const u = new URL(String(url), "https://local.invalid/");
    for (const k of keys) {
      const q = u.searchParams.get(k);
      if (q) {
        const doc = decode(q);
        if (doc) return doc;
      }
    }
    const hash = u.hash.replace(/^#/, "");
    if (hash) {
      const params = new URLSearchParams(hash.indexOf("=") >= 0 ? hash : `${keys[0]}=${hash}`);
      for (const k of keys) {
        const q = params.get(k);
        if (q) {
          const doc = decode(q);
          if (doc) return doc;
        }
      }
    }
  } catch {
    return null;
  }
  return null;
}

/** Put a character into a URL, replacing any character already in it. */
export function toURL(doc, base, key = "c") {
  const payload = encode(doc);
  try {
    const u = new URL(String(base), "https://local.invalid/");
    u.searchParams.set(key, payload);
    return u.toString();
  } catch {
    const sep = String(base).indexOf("?") >= 0 ? "&" : "?";
    return `${base}${sep}${key}=${payload}`;
  }
}
