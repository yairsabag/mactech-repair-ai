/**
 * feedback-widget.js
 * ==================
 * Feedback widget for MacTech beta.
 * Drop this <script> at the bottom of app.html,
 * then call FeedbackWidget.attachToMessage(el, context) after each AI reply.
 *
 * context = { board, symptom, de_step, conversation }
 */

window.FeedbackWidget = (() => {

  // ── Styles (injected once) ────────────────────────────────────────────────
  const CSS = `
    .fb-bar {
      display: flex; align-items: center; gap: 6px;
      margin-top: 8px; padding-top: 8px;
      border-top: 1px solid rgba(255,255,255,.07);
      flex-wrap: wrap;
    }
    .fb-btn {
      display: inline-flex; align-items: center; gap: 4px;
      padding: 3px 10px; border-radius: 20px; border: 1px solid rgba(255,255,255,.12);
      background: transparent; color: rgba(255,255,255,.45);
      font-size: 11px; cursor: pointer; transition: all .15s;
      font-family: inherit; white-space: nowrap;
    }
    .fb-btn:hover { border-color: rgba(255,255,255,.3); color: rgba(255,255,255,.8); }
    .fb-btn.selected-helpful  { border-color: #4ade80; color: #4ade80; background: rgba(74,222,128,.08); }
    .fb-btn.selected-wrong_diagnosis { border-color: #fb923c; color: #fb923c; background: rgba(251,146,60,.08); }
    .fb-btn.selected-missing_schematic { border-color: #f87171; color: #f87171; background: rgba(248,113,113,.08); }
    .fb-btn.selected-need_better_guidance { border-color: #60a5fa; color: #60a5fa; background: rgba(96,165,250,.08); }
    .fb-thanks { font-size: 11px; color: rgba(255,255,255,.35); margin-left: 4px; }
    .fb-note-wrap {
      width: 100%; margin-top: 6px;
      display: flex; gap: 6px; align-items: flex-end;
    }
    .fb-note-input {
      flex: 1; background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.12); border-radius: 8px;
      color: rgba(255,255,255,.8); font-size: 12px; font-family: inherit;
      padding: 6px 10px; resize: none; outline: none;
      transition: border-color .15s; min-height: 34px; max-height: 80px;
    }
    .fb-note-input:focus { border-color: rgba(255,255,255,.3); }
    .fb-note-input::placeholder { color: rgba(255,255,255,.25); }
    .fb-send {
      padding: 6px 12px; border-radius: 8px; border: none;
      background: rgba(255,255,255,.1); color: rgba(255,255,255,.7);
      font-size: 12px; cursor: pointer; font-family: inherit;
      transition: all .15s; white-space: nowrap;
    }
    .fb-send:hover { background: rgba(255,255,255,.18); color: #fff; }
    .fb-send:disabled { opacity: .4; cursor: default; }
  `;

  let styleInjected = false;
  function injectStyles() {
    if (styleInjected) return;
    const s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
    styleInjected = true;
  }

  // ── Button definitions ────────────────────────────────────────────────────
  const BUTTONS = [
    { cat: 'helpful',              icon: '✓', label: 'Helpful' },
    { cat: 'wrong_diagnosis',      icon: '✗', label: 'Wrong diagnosis' },
    { cat: 'missing_schematic',    icon: '⊘', label: 'Missing schematic' },
    { cat: 'need_better_guidance', icon: '?', label: 'Need better guidance' },
  ];

  // ── Core: attach widget to a message element ──────────────────────────────
  function attachToMessage(msgEl, context = {}) {
    injectStyles();

    const bar = document.createElement('div');
    bar.className = 'fb-bar';

    let selectedCat = null;
    let submitted = false;

    // Render buttons
    BUTTONS.forEach(({ cat, icon, label }) => {
      const btn = document.createElement('button');
      btn.className = 'fb-btn';
      btn.dataset.cat = cat;
      btn.innerHTML = `<span>${icon}</span><span>${label}</span>`;

      btn.addEventListener('click', () => {
        if (submitted) return;
        selectedCat = cat;

        // Toggle selection style
        bar.querySelectorAll('.fb-btn').forEach(b => {
          b.className = 'fb-btn' + (b.dataset.cat === cat ? ` selected-${cat}` : '');
        });

        // Show note input if not helpful
        const noteWrap = bar.querySelector('.fb-note-wrap');
        if (cat === 'helpful') {
          if (noteWrap) noteWrap.style.display = 'none';
          submitFeedback('');    // submit immediately for "helpful"
        } else {
          if (noteWrap) noteWrap.style.display = 'flex';
          const input = noteWrap.querySelector('.fb-note-input');
          if (input) input.focus();
        }
      });

      bar.appendChild(btn);
    });

    // Note input (hidden initially)
    const noteWrap = document.createElement('div');
    noteWrap.className = 'fb-note-wrap';
    noteWrap.style.display = 'none';

    const noteInput = document.createElement('textarea');
    noteInput.className = 'fb-note-input';
    noteInput.placeholder = 'What went wrong? (optional)';
    noteInput.rows = 1;
    noteInput.addEventListener('input', () => {
      noteInput.style.height = 'auto';
      noteInput.style.height = Math.min(noteInput.scrollHeight, 80) + 'px';
    });
    noteInput.addEventListener('keydown', e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        submitFeedback(noteInput.value);
      }
    });

    const sendBtn = document.createElement('button');
    sendBtn.className = 'fb-send';
    sendBtn.textContent = 'Send';
    sendBtn.addEventListener('click', () => submitFeedback(noteInput.value));

    noteWrap.appendChild(noteInput);
    noteWrap.appendChild(sendBtn);
    bar.appendChild(noteWrap);

    msgEl.appendChild(bar);

    // ── Submit ──────────────────────────────────────────────────────────────
    async function submitFeedback(note) {
      if (!selectedCat || submitted) return;
      submitted = true;

      // Disable UI
      bar.querySelectorAll('.fb-btn').forEach(b => b.disabled = true);
      sendBtn.disabled = true;

      const token = localStorage.getItem('mactech_token') || '';
      const payload = {
        category:     selectedCat,
        board:        context.board     || '',
        symptom:      context.symptom   || '',
        note:         note.trim().slice(0, 500),
        de_step:      context.de_step   || null,
        conversation: context.conversation || null,
      };

      try {
        const r = await fetch('/api/feedback', {
          method:  'POST',
          headers: {
            'Content-Type':  'application/json',
            'Authorization': `Bearer ${token}`,
          },
          body: JSON.stringify(payload),
        });

        const thanks = document.createElement('span');
        thanks.className = 'fb-thanks';

        if (r.ok) {
          thanks.textContent = selectedCat === 'helpful' ? '— thanks!' : '— feedback sent';
          noteWrap.style.display = 'none';
        } else {
          thanks.textContent = '— could not send';
        }
        bar.appendChild(thanks);

      } catch (_) {
        const err = document.createElement('span');
        err.className = 'fb-thanks';
        err.textContent = '— offline';
        bar.appendChild(err);
      }
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────
  return { attachToMessage };

})();
