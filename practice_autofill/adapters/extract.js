(root, cfg) => {
    const fail = reason => ({reason});
    const visible = el => !!(el.getClientRects().length) &&
        getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
    if (!visible(root)) return fail('invisible_container');
    if ([...root.querySelectorAll('*')].some(e=>e.shadowRoot)) return fail('shadow_dom_requires_adapter');
    if (root.querySelector(cfg.container)) return fail('nested_container');
    const stems = [...root.querySelectorAll(cfg.stem)].filter(visible);
    if (stems.length !== 1) return fail('ambiguous_stem');
    function text(el) {
        if (el.nodeType === Node.TEXT_NODE) return el.nodeValue;
        if (el.nodeType !== Node.ELEMENT_NODE) return '';
        const tag = el.tagName.toLowerCase();
        if (['script','style','input','textarea'].includes(tag) || el.getAttribute('aria-hidden') === 'true')
            return '';
        if (tag === 'math') {
            const annotation = el.querySelector('annotation[encoding*="tex"]');
            return annotation?.textContent || el.getAttribute('alttext') || el.textContent;
        }
        if (el.hasAttribute('data-tex')) return el.getAttribute('data-tex');
        if (tag === 'br') return '\n';
        let out = [...el.childNodes].map(text).join('');
        if (['div','p','li','section','legend'].includes(tag)) out += '\n';
        return out;
    }
    const stem = text(stems[0]).replace(/\r/g,'').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
    if (!stem) return fail('missing_stem');
    if (root.querySelector('img, canvas, svg:not(math svg)')) return fail('image_requires_manual');
    const controls = [...root.querySelectorAll(cfg.control)];
    if (!controls.length) return fail('missing_controls');
    if (controls.some(e => !visible(e))) return fail('hidden_control');
    if (controls.some(e => e.disabled || e.readOnly || e.closest('[inert]') ||
            e.getAttribute('aria-disabled')==='true' || e.getAttribute('aria-readonly')==='true'))
        return fail('unavailable_control');
    if (controls.some((e,i) => controls.some((other,j) => i!==j && other.contains(e))))
        return fail('nested_controls');
    function kind(e) {
        if (e.matches('input[type=radio]')) return 'radio';
        if (e.matches('input[type=checkbox]')) return 'checkbox';
        if (e.matches('[role=radio], [role=checkbox]')) {
            if (!['true','false'].includes(e.getAttribute('aria-checked')) ||
                ['BUTTON','A','INPUT'].includes(e.tagName)) return 'unsupported';
            return e.getAttribute('role') === 'radio' ? 'aria_radio' : 'aria_checkbox';
        }
        if (e.matches('iframe[data-blank]')) return 'frame_blank';
        if (e.matches('input:not([type]),input[type=text],textarea,[contenteditable=true]')) return 'blank';
        return 'unsupported';
    }
    const kinds=controls.map(kind);
    if (kinds.includes('unsupported')) return fail('unsupported_control');
    let type;
    if (kinds.every(k=>k==='radio') || kinds.every(k=>k==='aria_radio')) type='single';
    else if (kinds.every(k=>k==='checkbox') || kinds.every(k=>k==='aria_checkbox')) type='multiple';
    else if (kinds.every(k=>k==='blank' || k==='frame_blank')) type='blank';
    else return fail('mixed_question_types');
    if (kinds[0]==='aria_radio') {
        const groups=controls.map(e=>e.closest('[role=radiogroup]'));
        if (!groups[0] || new Set(groups).size!==1 || !root.contains(groups[0]))
            return fail('ambiguous_radio_group');
        if (groups[0].querySelectorAll('[role=radio]').length!==controls.length)
            return fail('radio_group_outside_question');
    }
    if (kinds[0]==='radio') {
        const names=new Set(controls.map(e=>e.name));
        if (names.size!==1 || !controls[0].name || new Set(controls.map(e=>e.form)).size!==1)
            return fail('ambiguous_radio_group');
        const group = [...root.ownerDocument.querySelectorAll('input[type=radio]')]
            .filter(e=>e.name===controls[0].name && e.form===controls[0].form);
        if (group.length!==controls.length) return fail('radio_group_outside_question');
    }
    function label(e) {
        if (e.hasAttribute('aria-label')) return e.getAttribute('aria-label').trim();
        const refs=(e.getAttribute('aria-labelledby')||'').trim().split(/\s+/).filter(Boolean);
        if (refs.length) {
            const nodes=refs.map(id=>root.ownerDocument.getElementById(id));
            if (nodes.some(n=>!n || !root.contains(n))) return '';
            return nodes.map(text).join(' ').trim();
        }
        const labels=[...(e.labels || [])].filter(l=>root.contains(l));
        if (labels.length===1) return text(labels[0]).trim();
        return e.matches('[role=radio],[role=checkbox]') ? text(e).trim() : '';
    }
    const options=type==='blank' ? [] : controls.map((e,i)=>({
        id:e.getAttribute('data-option-id') || (i<26 ? String.fromCharCode(65+i) : 'O'+(i+1)),
        text:label(e)
    }));
    if (options.some(o=>!o.text || !/^[A-Za-z0-9_-]{1,32}$/.test(o.id)) ||
        new Set(options.map(o=>o.id)).size !== options.length) return fail('ambiguous_options');
    const descriptors=controls.map((e,i)=>({
        kind:kinds[i], tag:e.tagName, id:e.id, name:e.getAttribute('name'),
        type:e.getAttribute('type'), value:type==='blank'?null:e.getAttribute('value'),
        role:e.getAttribute('role'), label:type==='blank'?null:options[i].text,
        source:kinds[i]==='frame_blank'?e.getAttribute('src'):null
    }));
    return {
        identity:root.getAttribute('data-question-id') || root.id || '',
        type, stem, options, blank_count:type==='blank'?controls.length:0,
        descriptors, kinds
    };
}
