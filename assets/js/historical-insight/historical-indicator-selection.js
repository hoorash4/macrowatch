(() => {
  'use strict';
  function create(){
    let available=[],selected=null;
    function reconcile(items){available=items.map(item=>item.meta.code);if(!available.includes(selected))selected=null;return snapshot();}
    function select(code){if(available.includes(code))selected=code;return snapshot();}
    function clear(){selected=null;return snapshot();}
    const snapshot=()=>Object.freeze({available:Object.freeze([...available]),selected});
    return Object.freeze({reconcile,select,clear,snapshot});
  }
  window.MacroWatchHistoricalIndicatorSelection=Object.freeze({create});
})();
