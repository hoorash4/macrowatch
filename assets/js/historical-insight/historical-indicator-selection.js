(() => {
  'use strict';
  function create(){
    let available=[],checked=new Set(),active=null;
    function reconcile(items,autoSelect=true){available=items.map(item=>item.meta.code);checked=new Set([...checked].filter(code=>available.includes(code)));if(autoSelect&&!checked.size&&available.length)checked.add(available[0]);if(!active||!checked.has(active))active=available.find(code=>checked.has(code))||null;return snapshot();}
    function toggle(code,value){if(!available.includes(code))return snapshot();value?checked.add(code):checked.delete(code);if(!active||!checked.has(active))active=available.find(item=>checked.has(item))||null;return snapshot();}
    function activate(code){if(checked.has(code))active=code;return snapshot();}
    const snapshot=()=>Object.freeze({available:Object.freeze([...available]),checked:Object.freeze([...checked]),active});
    return Object.freeze({reconcile,toggle,activate,snapshot});
  }
  window.MacroWatchHistoricalIndicatorSelection=Object.freeze({create});
})();
