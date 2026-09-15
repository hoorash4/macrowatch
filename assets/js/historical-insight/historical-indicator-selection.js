(() => {
  'use strict';
  const MAX_SELECTED=5;
  function create(){
    let available=[],checked=new Set(),active=null;
    function reconcile(items){available=items.map(item=>item.meta.code);checked=new Set([...checked].filter(code=>available.includes(code)));if(!active||!checked.has(active))active=available.find(code=>checked.has(code))||null;return snapshot();}
    function toggle(code,value){if(!available.includes(code))return snapshot();if(value&&!checked.has(code)&&checked.size>=MAX_SELECTED)return snapshot(true);value?checked.add(code):checked.delete(code);if(!active||!checked.has(active))active=available.find(item=>checked.has(item))||null;return snapshot();}
    function activate(code){if(checked.has(code))active=code;return snapshot();}
    function clear(){checked.clear();active=null;return snapshot();}
    const snapshot=(blocked=false)=>Object.freeze({available:Object.freeze([...available]),checked:Object.freeze([...checked]),active,blocked,max:MAX_SELECTED});
    return Object.freeze({reconcile,toggle,activate,clear,snapshot});
  }
  window.MacroWatchHistoricalIndicatorSelection=Object.freeze({MAX_SELECTED,create});
})();
