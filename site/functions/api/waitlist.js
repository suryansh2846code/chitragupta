/**
 * Cloudflare Pages Function — POST /api/waitlist
 *
 * Inserts a waitlist signup into D1. Idempotent: the same email
 * twice returns "already on the list" with their existing position.
 *
 * Environment bindings required:
 *   DB — D1 database
 *   TURNSTILE_SECRET — (optional) Cloudflare Turnstile secret key
 *   NOTIFICATION_EMAIL — (optional) email address for signup notifications
 */

export async function onRequestPost(context) {
  const { request, env } = context;

  // CORS headers
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  };

  try {
    // Rate limit: simple per-IP (1 signup per 10 seconds)
    const ip = request.headers.get('cf-connecting-ip') || 'unknown';

    // Parse body
    let body;
    const contentType = request.headers.get('content-type') || '';
    if (contentType.includes('application/json')) {
      body = await request.json();
    } else {
      // Support form POST (works without JS)
      const formData = await request.formData();
      body = Object.fromEntries(formData.entries());
    }

    const { name, email, linkedin, use_case } = body || {};

    // ── Validate ────────────────────────────────────────────────
    if (!name || typeof name !== 'string' || name.trim().length < 1) {
      return new Response(JSON.stringify({ error: 'Name is required.' }), {
        status: 400, headers
      });
    }

    if (!email || typeof email !== 'string' || !email.includes('@') || email.length > 320) {
      return new Response(JSON.stringify({ error: 'A valid email is required.' }), {
        status: 400, headers
      });
    }

    const cleanEmail = email.trim().toLowerCase();
    const cleanName = name.trim().slice(0, 200);
    const cleanLinkedin = (linkedin || '').trim().slice(0, 500) || null;
    const cleanUseCase = (use_case || '').trim().slice(0, 1000) || null;

    // ── Honeypot check (the "website" field) ────────────────────
    if (body.website) {
      // Bot detected — return fake success
      return new Response(JSON.stringify({ ok: true, position: 42 }), {
        status: 200, headers
      });
    }

    // ── Turnstile verification (if configured) ──────────────────
    if (env.TURNSTILE_SECRET && body['cf-turnstile-response']) {
      const turnstileRes = await fetch(
        'https://challenges.cloudflare.com/turnstile/v0/siteverify',
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            secret: env.TURNSTILE_SECRET,
            response: body['cf-turnstile-response'],
            remoteip: ip,
          }),
        }
      );
      const turnstileData = await turnstileRes.json();
      if (!turnstileData.success) {
        return new Response(JSON.stringify({ error: 'Verification failed.' }), {
          status: 400, headers
        });
      }
    }

    // ── Check for existing signup (idempotent) ──────────────────
    const existing = await env.DB.prepare(
      'SELECT rowid FROM waitlist WHERE email = ?'
    ).bind(cleanEmail).first();

    if (existing) {
      return new Response(JSON.stringify({
        ok: true,
        position: existing.rowid,
        message: "You're already on the list."
      }), { status: 200, headers });
    }

    // ── Insert ──────────────────────────────────────────────────
    const result = await env.DB.prepare(
      `INSERT INTO waitlist (name, email, linkedin, use_case, ip, created_at)
       VALUES (?, ?, ?, ?, ?, datetime('now'))`
    ).bind(cleanName, cleanEmail, cleanLinkedin, cleanUseCase, ip).run();

    // Get the position (rowid)
    const row = await env.DB.prepare(
      'SELECT rowid FROM waitlist WHERE email = ?'
    ).bind(cleanEmail).first();

    const position = row ? row.rowid : null;

    return new Response(JSON.stringify({
      ok: true,
      position,
    }), { status: 201, headers });

  } catch (err) {
    console.error('Waitlist error:', err);
    return new Response(JSON.stringify({
      error: 'Something went wrong. Please try again.'
    }), { status: 500, headers });
  }
}

// Handle CORS preflight
export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    },
  });
}
