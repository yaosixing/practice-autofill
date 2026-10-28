(root, cfg) => {
    const fail=reason=>({reason});
    const visible=e=>!!e.getClientRects().length &&
        getComputedStyle(e).display!=='none' && getComputedStyle(e).visibility!=='hidden';
    if(!visible(root)) return fail('invisible_container');
    if([...root.querySelectorAll('*')].some(e=>e.shadowRoot)) return fail('shadow_dom_requires_adapter');
    if(root.querySelector(cfg.selectors.container)) return fail('nested_container');
    const stems=[...root.querySelectorAll(cfg.selectors.stem)].filter(visible);
    if(stems.length!==1) return fail('ambiguous_stem');
    const combined=root.matches('.reading_answer');
    let material=null,identity=root.getAttribute('data-question-id') || root.id || '';
    if(combined){
        const parent=root.closest('.questionLi');
        if(!parent) return fail('missing_combined_context');
        const materials=[...parent.children].filter(e=>e.matches('.mark_name') && visible(e));
        if(materials.length!==1) return fail('ambiguous_combined_context');
        material=materials[0];
        const parentId=parent.getAttribute('data-question-id') || parent.id;
        const childId=root.getAttribute('itemid');
        if(!parentId || !childId) return fail('missing_combined_identity');
        identity=parentId+'::'+childId;
    }
    function text(e){
        if(e.nodeType===Node.TEXT_NODE) return e.nodeValue;
        if(e.nodeType!==Node.ELEMENT_NODE) return '';
        const tag=e.tagName.toLowerCase();
        if(['script','style','input','textarea'].includes(tag)) return '';
        // This layout hides duplicated accessibility text with aria-hidden even when
        // the actual question/option is visually displayed. Use CSS visibility here.
        const style=getComputedStyle(e);
        if(e.hidden || style.display==='none' || style.visibility==='hidden') return '';
        if(tag==='math'){
            const a=e.querySelector('annotation[encoding*="tex"]');
            return a?.textContent || e.getAttribute('alttext') || e.textContent;
        }
        if(e.hasAttribute('data-tex')) return e.getAttribute('data-tex');
        if(tag==='br') return '\n';
        if(tag==='tr') return [...e.children].map(text).map(v=>v.trim()).join(' | ')+'\n';
        const value=[...e.childNodes].map(text).join('');
        return value+(['div','p','li','section','h3'].includes(tag)?'\n':'');
    }
    const clean=e=>text(e).replace(/\r/g,'').replace(/[ \t]+/g,' ').replace(/\n{3,}/g,'\n\n').trim();
    const subStem=clean(stems[0]);
    if(!subStem || (material && !clean(material))) return fail('missing_stem');
    const stem=material?clean(material)+'\n\n'+subStem:subStem;
    if(!stem) return fail('missing_stem');
    if(root.querySelector('img,canvas,svg:not(math svg)') ||
        material?.querySelector('img,canvas,svg:not(math svg)')) return fail('image_requires_manual');
    const controls=[...root.querySelectorAll(cfg.selectors.control)];
    if(!controls.length) return fail('unsupported_question_type');
    if(controls.some(e=>!visible(e))) return fail('hidden_control');
    if(controls.some(e=>e.matches('button,a,input') || e.querySelector('input,button,a,textarea,[contenteditable=true]')))
        return fail('unsupported_control');
    if(controls.some(e=>e.closest('[inert]') || e.getAttribute('aria-disabled')==='true'))
        return fail('unavailable_control');
    const roles=controls.map(e=>e.getAttribute('role'));
    const roleless=combined && roles.every(r=>r===null);
    let type;
    if(roles.every(r=>r==='radio')) type='single';
    else if(roles.every(r=>r==='checkbox')) type='multiple';
    else if(roleless){
        // This observed layout has no option role. Require an explicit subquestion
        // type plus the configured markers/handler below; do not infer from appearance.
        const labels=[...root.querySelectorAll('.reader_answer_tit > .read_type')].filter(visible);
        if(labels.length!==1) return fail('unsupported_question_type');
        const label=clean(labels[0]).replace(/[\s()（）]/g,'');
        if(label==='单选题') type='single';
        else if(label==='多选题') type='multiple';
        else return fail('unsupported_question_type');
    }
    else return fail('mixed_question_types');
    // Do not silently omit a differently marked answer control or a mixed blank.
    const allChoices=[...root.querySelectorAll('[role=radio],[role=checkbox]')];
    if(allChoices.length!==(roleless?0:controls.length)) return fail('ambiguous_options');
    const allMarkers=root.querySelectorAll(cfg.choices.single_marker+', '+cfg.choices.multiple_marker);
    if(allMarkers.length!==controls.length) return fail('ambiguous_options');
    if(root.querySelector('textarea,input:not([type]),input[type=text],[contenteditable=true],iframe'))
        return fail('mixed_question_types');
    const kind=type==='single'?'class_radio':'class_checkbox';
    const markerSelector=type==='single'?cfg.choices.single_marker:cfg.choices.multiple_marker;
    const selectedClass=type==='single'?cfg.choices.single_selected_class:cfg.choices.multiple_selected_class;
    const otherClass=type==='single'?cfg.choices.multiple_selected_class:cfg.choices.single_selected_class;
    const expectedHandler=type==='single'?cfg.choices.single_handler:cfg.choices.multiple_handler;
    const options=[],descriptors=[],states=[];
    for(const e of controls){
        const markers=[...e.querySelectorAll(markerSelector)];
        const texts=[...e.querySelectorAll(cfg.choices.option_text)];
        if(markers.length!==1 || texts.length!==1 || !visible(markers[0]) || !visible(texts[0]))
            return fail('ambiguous_options');
        const marker=markers[0];
        const id=clean(marker).replace(/[.、．：:]$/,'').trim();
        const label=clean(texts[0]);
        if(!/^[A-Z]$/.test(id) || !label) return fail('ambiguous_options');
        const handler=e.getAttribute('onclick') || '';
        if(handler.match(/^\s*([A-Za-z_$][\w$]*)\s*\(/)?.[1]!==expectedHandler)
            return fail('unsupported_choice_handler');
        const selected=marker.classList.contains(selectedClass);
        const aria=e.getAttribute('aria-checked');
        if(marker.classList.contains(otherClass) ||
            (aria!==null && (!['true','false'].includes(aria) || (aria==='true')!==selected)))
            return fail('ambiguous_checked_state');
        options.push({id,text:label});
        states.push(selected);
        descriptors.push({
            kind,tag:e.tagName,id:e.id,role:e.getAttribute('role'),handler,
            markerTag:marker.tagName,markerId:marker.id,
            markerClasses:[...marker.classList].filter(c=>c!==selectedClass).sort(),
            optionId:id,label
        });
    }
    if(new Set(options.map(o=>o.id)).size!==options.length) return fail('ambiguous_options');
    if(type==='single' && states.filter(Boolean).length>1) return fail('ambiguous_checked_state');
    return {
        identity,
        type,stem,options,blank_count:0,kinds:controls.map(()=>kind),descriptors
    };
}
